import html
import random
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, ChosenInlineResult, InlineQuery, InlineQueryResultArticle,
    InlineQueryResultPhoto, InputTextMessageContent, Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from services import channels
from services.results import notify_change, post_image, post_keyboard, post_text, render, send_post
from services.subscription import forget, missing_channels

router = Router()

# captcha: user_id -> to'g'ri javob; muvaffaqiyatli o'tganlar -> amal qilish vaqti
_captcha_answer: dict[int, int] = {}
_captcha_ok: dict[int, float] = {}
CAPTCHA_TTL = 3600
_last_click: dict[int, float] = {}
# post tugmasidan oldindan tanlangan nomzod: user_id -> candidate_id
_picked: dict[int, int] = {}


def _throttled(user_id: int, gap: float = 0.7) -> bool:
    """Tugmani juda tez-tez bosishdan himoya."""
    now = time.monotonic()
    if now - _last_click.get(user_id, 0) < gap:
        return True
    _last_click[user_id] = now
    if len(_last_click) > 50000:
        _last_click.clear()
    return False


async def subscribe_kb(social_done: bool):
    kb = InlineKeyboardBuilder()
    items = await channels.active()
    for _, kind, title, target, url, *_ in items:
        kb.button(text=f"{channels.emoji(kind)} {title}", url=channels.button_url(kind, target, url))
    if any(c[1] != "telegram" for c in items) and not social_done:
        kb.button(text="☑️ Ijtimoiy tarmoqlarga obuna bo'ldim", callback_data="social_ok")
    kb.button(text="✅ Tekshirish", callback_data="check")
    kb.button(text="📊 Natijalarni ko'rish", callback_data="res")
    kb.adjust(1)
    return kb.as_markup()


def results_open_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Natijalarni ko'rish", callback_data="res")
    return kb.as_markup()


def results_refresh_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Yangilash", callback_data="res:refresh")
    return kb.as_markup()


async def candidates_kb(poll_id: int):
    kb = InlineKeyboardBuilder()
    for cid, name in await db.candidates(poll_id):
        kb.button(text=name, callback_data=f"vote:{poll_id}:{cid}")
    kb.adjust(1)
    return kb.as_markup()


def captcha_question(user_id: int):
    a, b = random.randint(2, 9), random.randint(2, 9)
    answer = a + b
    _captcha_answer[user_id] = answer
    options = {answer}
    while len(options) < 4:
        options.add(max(1, answer + random.randint(-4, 4)))
    kb = InlineKeyboardBuilder()
    for o in random.sample(sorted(options), 4):
        kb.button(text=str(o), callback_data=f"cap:{o}")
    kb.adjust(4)
    return f"🤖 Siz botmisiz? Tekshiramiz: <b>{a} + {b} = ?</b>", kb.as_markup()


async def _can_vote(bot: Bot, user_id: int) -> str | None:
    """None — ruxsat, aks holda sabab matni."""
    missing = await missing_channels(bot, user_id)
    if missing:
        return "Avval quyidagi kanallarga obuna bo'ling: " + ", ".join(missing)
    if await channels.has_links() and not await db.is_social_confirmed(user_id):
        return "Ijtimoiy tarmoqlardagi sahifalarimizga obuna bo'lib, «obuna bo'ldim» tugmasini bosing."
    return None


async def _show_candidates(cb: CallbackQuery, poll):
    cid = _picked.get(cb.from_user.id)
    name = dict(await db.candidates(poll[0])).get(cid)
    if name:
        kb = InlineKeyboardBuilder()
        kb.button(text=f"✅ Ovoz berish: {name}", callback_data=f"vote:{poll[0]}:{cid}")
        kb.button(text="↩️ Boshqa nomzodni tanlash", callback_data="repick")
        kb.adjust(1)
        await cb.message.edit_text(
            f"Hammasi tayyor! <b>{html.escape(name)}</b> uchun ovozingizni tasdiqlang:",
            reply_markup=kb.as_markup(),
        )
        return
    await cb.message.edit_text(
        await render(poll[0], poll[1])
        + "\n\nNomzodni tanlang (faqat 1 marta ovoz berish mumkin):",
        reply_markup=await candidates_kb(poll[0]),
    )


@router.message(Command("natija"))
async def results_cmd(msg: Message):
    poll = await db.last_poll()
    if not poll:
        await msg.answer("Hozircha ovoz berish yo'q.")
        return
    await msg.answer(await render(poll[0], poll[1], closed=not poll[4]))


async def _ask_subscribe(user_id: int, name: str):
    social = await db.is_social_confirmed(user_id)
    text = (f"Siz <b>{html.escape(name)}</b> uchun ovoz bermoqchisiz.\n\n"
            "Ovoz berish uchun quyidagi sahifalarimizga obuna bo'ling, so'ng «Tekshirish»ni bosing.")
    return text, await subscribe_kb(social)


@router.message(CommandStart())
async def start(msg: Message, bot: Bot, command: CommandObject):
    await db.upsert_user(msg.from_user.id, msg.from_user.username)
    poll = await db.active_poll()
    if not poll:
        # yakunlangan ovoz berish bo'lsa, uning natijasini ko'rish mumkin
        kb = results_open_kb() if await db.last_poll() else None
        await msg.answer("Hozircha faol ovoz berish yo'q.", reply_markup=kb)
        return
    arg = command.args or ""
    # kanal postidagi nomzod tugmasi: /start c<id>
    if arg.startswith("c") and arg[1:].isdigit():
        cid = int(arg[1:])
        name = dict(await db.candidates(poll[0])).get(cid)
        if name:
            _picked[msg.from_user.id] = cid
            text, kb = await _ask_subscribe(msg.from_user.id, name)
            await msg.answer(text, reply_markup=kb)
            return
    # admin paneldagi «Botda ochish» (/start post): tarqatish uchun ko'rsatma
    hint = ("📤 «Ulashish» tugmasini bosib, postni kanal yoki guruhga yuboring — "
            "u yerdagi sonlar ham jonli yangilanadi." if arg == "post" else "👇 Nomzodni tanlang:")
    await send_post(bot, msg.chat.id, poll[0], await post_text(poll[0], poll[1]) + "\n\n" + hint,
                    await post_keyboard(bot, poll[0], in_bot=True))


@router.callback_query(F.data.startswith("pick:"))
async def pick(cb: CallbackQuery):
    poll = await db.active_poll()
    cid = int(cb.data.split(":")[1])
    name = dict(await db.candidates(poll[0])).get(cid) if poll else None
    if not name:
        await cb.answer("Bu ovoz berish yakunlangan.", show_alert=True)
        return
    _picked[cb.from_user.id] = cid
    text, kb = await _ask_subscribe(cb.from_user.id, name)
    # post joyida qoladi — yangi xabar
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "repick")
async def repick(cb: CallbackQuery, bot: Bot):
    _picked.pop(cb.from_user.id, None)
    poll = await db.active_poll()
    if not poll:
        await cb.answer("Ovoz berish yakunlangan.", show_alert=True)
        return
    # rasmli postni matnli xabarga aylantirib bo'lmaydi — postni yangidan yuboramiz
    await cb.message.delete()
    await send_post(bot, cb.message.chat.id, poll[0], await post_text(poll[0], poll[1]) + "\n\n👇 Nomzodni tanlang:",
                    await post_keyboard(bot, poll[0], in_bot=True))
    await cb.answer()


@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer("Ovoz berish yakunlangan.")


@router.inline_query()
async def share_post(q: InlineQuery, bot: Bot):
    """«📤 Ulashish»: postning bot nomidan yuboriladigan nusxasi."""
    poll = await db.active_poll()
    if not poll:
        await q.answer([], cache_time=0, is_personal=True)
        return
    text = await post_text(poll[0], poll[1])
    markup = await post_keyboard(bot, poll[0])
    url = await post_image(poll[0])
    if url:
        item = InlineQueryResultPhoto(
            id=f"poll{poll[0]}", photo_url=url, thumbnail_url=url,
            title=poll[1][:64], caption=text, parse_mode="HTML", reply_markup=markup,
        )
    else:
        item = InlineQueryResultArticle(
            id=f"poll{poll[0]}",
            title=poll[1][:64],
            description="So'rovnomani yuborish",
            input_message_content=InputTextMessageContent(message_text=text),
            reply_markup=markup,
        )
    await q.answer([item], cache_time=0, is_personal=True)


@router.chosen_inline_result()
async def shared(r: ChosenInlineResult):
    # inline_message_id faqat @BotFather'da /setinlinefeedback yoqilganda keladi
    if r.inline_message_id and r.result_id.startswith("poll") and r.result_id[4:].isdigit():
        await db.add_shared(r.inline_message_id, int(r.result_id[4:]))


@router.callback_query(F.data == "social_ok")
async def social_ok(cb: CallbackQuery):
    await db.upsert_user(cb.from_user.id, cb.from_user.username)
    await db.confirm_social(cb.from_user.id)
    await cb.message.edit_reply_markup(reply_markup=await subscribe_kb(True))
    await cb.answer("Rahmat! Endi «Tekshirish»ni bosing.")


@router.callback_query(F.data == "check")
async def check(cb: CallbackQuery, bot: Bot):
    if _throttled(cb.from_user.id):
        await cb.answer()
        return
    poll = await db.active_poll()
    if not poll:
        await cb.answer("Ovoz berish yakunlangan.", show_alert=True)
        return
    reason = await _can_vote(bot, cb.from_user.id)
    if reason:
        await cb.answer(reason, show_alert=True)
        return
    if _captcha_ok.get(cb.from_user.id, 0) > time.monotonic():
        await _show_candidates(cb, poll)
    else:
        text, kb = captcha_question(cb.from_user.id)
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("cap:"))
async def captcha(cb: CallbackQuery):
    if _throttled(cb.from_user.id, 0.4):
        await cb.answer()
        return
    correct = _captcha_answer.get(cb.from_user.id)
    if correct is None:
        await cb.answer("Qaytadan /start bosing.", show_alert=True)
        return
    if int(cb.data.split(":")[1]) != correct:
        text, kb = captcha_question(cb.from_user.id)
        await cb.message.edit_text("❌ Noto'g'ri. Yana urinib ko'ring.\n\n" + text, reply_markup=kb)
        await cb.answer()
        return
    _captcha_answer.pop(cb.from_user.id, None)
    _captcha_ok[cb.from_user.id] = time.monotonic() + CAPTCHA_TTL
    poll = await db.active_poll()
    if not poll:
        await cb.answer("Ovoz berish yakunlangan.", show_alert=True)
        return
    await _show_candidates(cb, poll)
    await cb.answer()


@router.callback_query(F.data.startswith("vote:"))
async def vote(cb: CallbackQuery, bot: Bot):
    if _throttled(cb.from_user.id):
        await cb.answer()
        return
    if _captcha_ok.get(cb.from_user.id, 0) <= time.monotonic():
        await cb.answer("Qaytadan /start bosing va tekshiruvdan o'ting.", show_alert=True)
        return
    _, poll_id, cid = cb.data.split(":")
    poll_id, cid = int(poll_id), int(cid)
    poll = await db.active_poll()
    if not poll or poll[0] != poll_id:
        await cb.answer("Bu ovoz berish yakunlangan.", show_alert=True)
        return
    reason = await _can_vote(bot, cb.from_user.id)
    if reason:
        await cb.answer(reason, show_alert=True)
        return
    if not await db.add_vote(cb.from_user.id, poll_id, cid):
        await cb.answer("Siz allaqachon ovoz bergansiz.", show_alert=True)
        return
    notify_change()
    _picked.pop(cb.from_user.id, None)
    name = dict(await db.candidates(poll_id)).get(cid, "")
    await cb.message.edit_text(
        f"✅ Ovozingiz qabul qilindi: <b>{html.escape(name)}</b>\nRahmat!\n\n"
        + await render(poll_id, poll[1]),
        reply_markup=results_open_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "res")
async def results_show(cb: CallbackQuery):
    if _throttled(cb.from_user.id):
        await cb.answer()
        return
    poll = await db.last_poll()
    if not poll:
        await cb.answer("Hozircha ovoz berish yo'q.", show_alert=True)
        return
    # yangi xabar: ovoz berish oynasi joyida qoladi
    await cb.message.answer(await render(poll[0], poll[1], closed=not poll[4]), reply_markup=results_refresh_kb())
    await cb.answer()


@router.callback_query(F.data == "res:refresh")
async def results_refresh(cb: CallbackQuery):
    if _throttled(cb.from_user.id, 2):
        await cb.answer("Biroz kuting…")
        return
    poll = await db.last_poll()
    if not poll:
        await cb.answer("Hozircha ovoz berish yo'q.", show_alert=True)
        return
    closed = not poll[4]
    try:
        await cb.message.edit_text(
            await render(poll[0], poll[1], closed=closed),
            reply_markup=None if closed else results_refresh_kb(),
        )
        await cb.answer("Yangilandi")
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise
        await cb.answer("Natijalar o'zgarmagan")


@router.chat_member()
async def on_leave(event: ChatMemberUpdated):
    """Majburiy kanaldan chiqib ketgan foydalanuvchining ovozini bekor qilish."""
    targets = {t.lower() for t in await channels.telegram_targets()}
    names = {str(event.chat.id)}
    if event.chat.username:
        names.add(f"@{event.chat.username}".lower())
    if not names & targets:
        return
    if event.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        uid = event.new_chat_member.user.id
        forget(uid)
        poll = await db.active_poll()
        if poll:
            await db.remove_user_votes(uid, poll[0])
            notify_change()
