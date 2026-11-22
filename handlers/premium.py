"""Premium konkursi (bot): Boshlash → obuna → Tasdiqlash → captcha → ishtirok raqami → telefon (ixtiyoriy).

Bu router bot.py da user.router dan OLDIN ulanadi: /start premium va kontakt xabarlari shu yerga keladi.
"""
import logging
import random
import time
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, User,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from services import channels, premium
from services.subscription import missing_channels

router = Router()

PHONE_BTN = "📱 Raqamni ulashish"
SKIP_BTN = "O'tkazib yuborish"
CAPTCHA_TTL = 3600
# captcha: user_id -> to'g'ri javob; o'tganlar -> amal qilish muddati (so'rovnoma captchasidan alohida)
_cap_answer: dict[int, int] = {}
_cap_ok: dict[int, float] = {}
_last_click: dict[int, float] = {}


def _throttled(user_id: int, gap: float = 0.7) -> bool:
    now = time.monotonic()
    if now - _last_click.get(user_id, 0) < gap:
        return True
    _last_click[user_id] = now
    if len(_last_click) > 50000:
        _last_click.clear()
    return False


def _name(user: User) -> str:
    return (user.full_name or "")[:128]


async def _subscribe_kb():
    kb = InlineKeyboardBuilder()
    for _, kind, title, target, url, *_ in await channels.active():
        kb.button(text=f"{channels.emoji(kind)} {title}", url=channels.button_url(kind, target, url))
    kb.button(text="✅ Tasdiqlash", callback_data="pr:ok")
    kb.adjust(1)
    return kb.as_markup()


def _share_kb(bot_username: str):
    url = (f"https://t.me/share/url?url={quote(premium.deep_link(bot_username), safe='')}"
           f"&text={quote(premium.SHARE_TEXT, safe='')}")
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Do'stlarga ulashish", url=url)
    return kb.as_markup()


def _phone_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=PHONE_BTN, request_contact=True)], [KeyboardButton(text=SKIP_BTN)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def _captcha(user_id: int):
    a, b = random.randint(2, 9), random.randint(2, 9)
    answer = a + b
    _cap_answer[user_id] = answer
    options = {answer}
    while len(options) < 4:
        options.add(max(1, answer + random.randint(-4, 4)))
    kb = InlineKeyboardBuilder()
    for o in random.sample(sorted(options), 4):
        kb.button(text=str(o), callback_data=f"prc:{o}")
    kb.adjust(4)
    return f"🤖 Siz botmisiz? Tekshiramiz: <b>{a} + {b} = ?</b>", kb.as_markup()


async def _begin(bot: Bot, user: User, answer):
    """Konkursga kirish: ishtirokchi bo'lsa — raqami, aks holda obuna sahifalari."""
    await db.upsert_user(user.id, user.username)
    g = await db.gw_active()
    if not g:
        await answer(premium.NO_GIVEAWAY)
        return
    entry = await db.gw_entry(g["id"], user.id)
    if entry and entry["status"] == "joined":
        me = await bot.me()
        await answer(premium.joined_text(entry["number"], g, again=True), reply_markup=_share_kb(me.username))
        return
    if premium.is_ended(g):
        await answer(premium.ENDED_TEXT)
        return
    await db.gw_touch(g["id"], user.id, _name(user), user.username)
    text = premium.INVITE_TEXT
    if entry and entry["status"] == "left":
        text += "\n\n" + premium.LEFT_NOTE
    await answer(text, reply_markup=await _subscribe_kb())


async def _finish(cb: CallbackQuery, bot: Bot, g):
    """Barcha tekshiruvlardan o'tdi: ishtirok raqami va tabrik."""
    user = cb.from_user
    number = await db.gw_join(g["id"], user.id, _name(user), user.username)
    me = await bot.me()
    await cb.message.edit_text(premium.joined_text(number, g), reply_markup=_share_kb(me.username))
    entry = await db.gw_entry(g["id"], user.id)
    if entry and not entry["phone"]:
        await cb.message.answer(premium.PHONE_TEXT, reply_markup=_phone_kb())


@router.message(CommandStart(deep_link=True, magic=F.args == premium.PAYLOAD))
async def start_premium(msg: Message, bot: Bot):
    await _begin(bot, msg.from_user, msg.answer)


@router.message(Command("premium"))
async def premium_cmd(msg: Message, bot: Bot):
    """Konkurs postini botda qayta ko'rsatadi."""
    await db.upsert_user(msg.from_user.id, msg.from_user.username)
    g = await db.gw_active()
    if not g:
        await msg.answer(premium.NO_GIVEAWAY)
        return
    await premium.send_post(bot, msg.chat.id, g, record=msg.chat.type == "private")


@router.callback_query(F.data == "pr:go")
async def go(cb: CallbackQuery, bot: Bot):
    if _throttled(cb.from_user.id):
        await cb.answer()
        return
    await _begin(bot, cb.from_user, cb.message.answer)
    await cb.answer()


@router.callback_query(F.data == "pr:ok")
async def confirm(cb: CallbackQuery, bot: Bot):
    if _throttled(cb.from_user.id):
        await cb.answer()
        return
    g = await db.gw_active()
    if not g or premium.is_ended(g):
        await cb.answer(premium.ENDED_TEXT if g else premium.NO_GIVEAWAY, show_alert=True)
        return
    missing = await missing_channels(bot, cb.from_user.id)
    if missing:
        titles = {c[3]: c[2] for c in await channels.active() if c[1] == "telegram"}
        await cb.answer("Avval quyidagi kanallarga obuna bo'ling: " + ", ".join(titles.get(m, m) for m in missing),
                        show_alert=True)
        return
    if _cap_ok.get(cb.from_user.id, 0) > time.monotonic():
        await _finish(cb, bot, g)
    else:
        text, kb = _captcha(cb.from_user.id)
        await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("prc:"))
async def captcha(cb: CallbackQuery, bot: Bot):
    if _throttled(cb.from_user.id, 0.4):
        await cb.answer()
        return
    correct = _cap_answer.get(cb.from_user.id)
    if correct is None:
        await cb.answer("Qaytadan «🚀 Boshlash» tugmasini bosing.", show_alert=True)
        return
    try:
        picked = int(cb.data.split(":")[1])
    except ValueError:
        picked = None
    if picked != correct:
        text, kb = _captcha(cb.from_user.id)
        await cb.message.edit_text("❌ Noto'g'ri. Yana urinib ko'ring.\n\n" + text, reply_markup=kb)
        await cb.answer()
        return
    _cap_answer.pop(cb.from_user.id, None)
    _cap_ok[cb.from_user.id] = time.monotonic() + CAPTCHA_TTL
    if len(_cap_ok) > 50000:
        _cap_ok.clear()
    g = await db.gw_active()
    if not g or premium.is_ended(g):
        await cb.answer(premium.ENDED_TEXT if g else premium.NO_GIVEAWAY, show_alert=True)
        return
    await _finish(cb, bot, g)
    await cb.answer()


@router.message(F.chat.type == "private", F.contact)
async def contact(msg: Message):
    c = msg.contact
    if c.user_id != msg.from_user.id:
        await msg.answer("Faqat o'zingizning raqamingizni yuboring — «📱 Raqamni ulashish» tugmasini bosing.",
                         reply_markup=_phone_kb())
        return
    phone = c.phone_number if c.phone_number.startswith("+") else "+" + c.phone_number
    g = await db.gw_active()
    saved = bool(g) and await db.gw_set_phone(g["id"], msg.from_user.id, phone[:32])
    await msg.answer("✅ Rahmat! Raqamingiz saqlandi. Omad tilaymiz! 🍀" if saved else "Rahmat!",
                     reply_markup=ReplyKeyboardRemove())


@router.message(F.chat.type == "private", F.text == SKIP_BTN)
async def skip_phone(msg: Message):
    await msg.answer("Mayli. Omad tilaymiz! 🍀", reply_markup=ReplyKeyboardRemove())


@router.chat_member()
async def track_membership(event: ChatMemberUpdated, bot: Bot):
    """Kanaldan chiqish/qaytishni belgilaydi, so'ng hodisani so'rovnoma handleriga ham uzatadi."""
    try:
        await premium.on_member_update(bot, event)
    except Exception:
        logging.exception("premium chat_member")
    raise SkipHandler()
