import html
import random
import time

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from services import channels
from services.results import notify_change, render
from services.subscription import forget, missing_channels

router = Router()

# captcha: user_id -> to'g'ri javob; muvaffaqiyatli o'tganlar -> amal qilish vaqti
_captcha_answer: dict[int, int] = {}
_captcha_ok: dict[int, float] = {}
CAPTCHA_TTL = 3600
_last_click: dict[int, float] = {}


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
    kb.adjust(1)
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


@router.message(CommandStart())
async def start(msg: Message):
    await db.upsert_user(msg.from_user.id, msg.from_user.username)
    poll = await db.active_poll()
    if not poll:
        await msg.answer("Hozircha faol ovoz berish yo'q.")
        return
    social = await db.is_social_confirmed(msg.from_user.id)
    await msg.answer(
        f"🗳 <b>{html.escape(poll[1])}</b>\n\n"
        "Ovoz berish uchun quyidagi sahifalarimizga obuna bo'ling, so'ng «Tekshirish»ni bosing.",
        reply_markup=await subscribe_kb(social),
    )


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
    name = dict(await db.candidates(poll_id)).get(cid, "")
    await cb.message.edit_text(
        f"✅ Ovozingiz qabul qilindi: <b>{html.escape(name)}</b>\nRahmat!\n\n"
        + await render(poll_id, poll[1])
    )
    await cb.answer()


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
