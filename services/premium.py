"""Premium konkursi: post, kanalga joylash, botda tarqatish, nusxalarni yangilash, g'olib aniqlash."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, ChatMemberUpdated, InputMediaPhoto
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import RESULTS_CHANNEL, TZ_OFFSET
from services import channels
from services.subscription import OK_STATUSES

TZ = timezone(timedelta(hours=TZ_OFFSET))
PAYLOAD = "premium"  # t.me/<bot>?start=premium
DEFAULT_BUTTON = "🚀 Boshlash"
SEND_GAP = 0.05  # xabarlar orasidagi tanaffus: soniyasiga ~20 ta (Telegram umumiy limiti ~30)

INVITE_TEXT = ("🎁 <b>Telegram Premium yutib olish imkoniyati!</b>\n\n"
               "Buning uchun quyidagi sahifalarimizga obuna bo'ling, so'ng «✅ Tasdiqlash» tugmasini bosing.")
LEFT_NOTE = ("⚠️ Siz majburiy kanallardan chiqib ketgansiz. Qayta obuna bo'lib, «✅ Tasdiqlash»ni bosing — "
             "ishtirok raqamingiz saqlanib qoladi.")
ENDED_TEXT = "⏳ Konkurs yakunlangan. G'olib tez orada aniqlanadi."
NO_GIVEAWAY = "Hozircha faol konkurs yo'q."
PHONE_TEXT = ("📱 G'olib bo'lsangiz, siz bilan bog'lanishimiz uchun raqamingizni ulashing.\n"
              "Bu ixtiyoriy — pastdagi tugmani bosing yoki «O'tkazib yuborish»ni tanlang.")
SHARE_TEXT = "🎁 Telegram Premium yutib olish imkoniyati! Qatnashing:"
WIN_TEXT = ("🏆 Tabriklaymiz! Siz Telegram Premium konkursida g'olib bo'ldingiz!\n"
            "Ishtirok raqamingiz: #{number}\n\n"
            "Sovg'ani topshirish uchun tez orada siz bilan bog'lanamiz.")


def deep_link(bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start={PAYLOAD}"


def ends_text(g) -> str:
    return g["ends_at"].astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def is_ended(g) -> bool:
    return datetime.now(timezone.utc) >= g["ends_at"]


def joined_text(number: int, g, again: bool = False) -> str:
    if again:
        head = "✅ Siz allaqachon konkurs ishtirokchisiz."
    else:
        head = "🎉 <b>Tabriklaymiz!</b> Siz o'yinda ishtirok etish imkoniyatini qo'lga kiritdingiz."
    return (f"{head}\n\nSizning ishtirok raqamingiz: <b>#{number}</b>\n\n"
            f"🗓 G'olib {ends_text(g)} dan keyin aniqlanadi.\n"
            "⚠️ Kanallardan chiqib ketsangiz, ishtirokingiz bekor bo'ladi.")


def win_text(number: int) -> str:
    return WIN_TEXT.format(number=number)


def post_text(g) -> str:
    """Post matni: admin yozgan matn, bo'sh bo'lsa sarlavha."""
    desc = (g["description"] or "").strip()
    return html.escape(desc) if desc else f"🎁 <b>{html.escape(g['title'])}</b>"


def _markup(g, url: str | None = None):
    kb = InlineKeyboardBuilder()
    text = g["button_text"] or DEFAULT_BUTTON
    if url:
        kb.button(text=text, url=url)
    else:
        kb.button(text=text, callback_data="pr:go")
    return kb.as_markup()


async def channel_markup(bot: Bot, g):
    """Kanal postidagi tugma botga olib boradi."""
    return _markup(g, deep_link((await bot.me()).username))


def bot_markup(g):
    """Botdagi postda tugma konkursni shu yerning o'zida boshlaydi."""
    return _markup(g)


# --- rasm: bir marta yuklanadi, keyin Telegram file_id si ishlatiladi ---
async def _photo(g):
    if not g["has_image"]:
        return None
    row = await db.gw_file(g["id"])
    if row["file_id"]:
        return row["file_id"]
    return BufferedInputFile(row["image"], filename=f"premium-{g['id']}.jpg")


async def _remember(g, msg) -> str | None:
    photos = getattr(msg, "photo", None)
    if not photos:
        return None
    file_id = photos[-1].file_id
    await db.gw_set_file_id(g["id"], g["image_ver"], file_id)
    return file_id


async def _call(factory, tries: int = 3):
    """Telegram «biroz kuting» (RetryAfter) desa — kutib, qayta urinadi."""
    for i in range(tries):
        try:
            return await factory()
        except TelegramRetryAfter as e:
            if i == tries - 1:
                raise
            await asyncio.sleep(e.retry_after + 1)


async def _send(bot: Bot, chat_id, text: str, photo, markup):
    if photo is not None:
        return await bot.send_photo(chat_id, photo, caption=text, reply_markup=markup)
    return await bot.send_message(chat_id, text, reply_markup=markup)


def _gone(e: TelegramBadRequest) -> bool:
    s = str(e).lower()
    return "not found" in s or "message_id_invalid" in s


async def _edit(bot: Bot, text: str, photo, markup, media: bool, **target):
    """Bitta xabarni yangilaydi. Xabar matnli yoki rasmli ekanini bilmaymiz — mos kelmasa keyingi usul."""
    async def as_media():
        return await bot.edit_message_media(
            media=InputMediaPhoto(media=photo, caption=text, parse_mode="HTML"), reply_markup=markup, **target)

    async def as_caption():
        return await bot.edit_message_caption(caption=text, reply_markup=markup, **target)

    async def as_text():
        return await bot.edit_message_text(text=text, reply_markup=markup, **target)

    if photo is not None:
        steps = ([as_media] if media else []) + [as_caption, as_text]
    else:
        steps = [as_text, as_caption]
    err = None
    for step in steps:
        try:
            return await step()
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return None
            if _gone(e):
                raise
            err = e
    raise err


async def send_post(bot: Bot, chat_id: int, g, record: bool = True):
    """/premium buyrug'i: postni botda ko'rsatadi (shaxsiy chatda keyingi tahrirlar ham yetib boradi)."""
    photo = await _photo(g)
    msg = await _send(bot, chat_id, post_text(g), photo, bot_markup(g))
    if isinstance(photo, BufferedInputFile):
        await _remember(g, msg)
    if record:
        await db.gw_add_message(g["id"], chat_id, msg.message_id)
    return msg


async def publish_channel(bot: Bot, gid: int) -> int:
    if not RESULTS_CHANNEL:
        raise RuntimeError(".env faylida RESULTS_CHANNEL ko'rsatilmagan")
    g = await db.gw_get(gid)
    photo = await _photo(g)
    msg = await _send(bot, RESULTS_CHANNEL, post_text(g), photo, await channel_markup(bot, g))
    if isinstance(photo, BufferedInputFile):
        await _remember(g, msg)
    await db.gw_set_message_id(gid, msg.message_id)
    return msg.message_id


# --- fon vazifalari: botda tarqatish va nusxalarni yangilash ---
_tasks: set[asyncio.Task] = set()
_send_jobs: dict[int, dict] = {}
_edit_tasks: dict[int, asyncio.Task] = {}
_media_pending: set[int] = set()
# post o'zgargan sari oshadi: tarqatish davom etayotgan bo'lsa, yangi matnga o'tadi
_rev: dict[int, int] = {}


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def sending(gid: int) -> dict | None:
    """Botga yuborish davom etayotgan bo'lsa — uning holati."""
    job = _send_jobs.get(gid)
    return job if job and job["running"] else None


def last_job(gid: int) -> dict | None:
    return _send_jobs.get(gid)


async def broadcast(bot: Bot, gid: int) -> bool:
    """Postni hali olmagan barcha bot foydalanuvchilariga yuboradi. False — allaqachon ketmoqda."""
    if sending(gid):
        return False
    users = await db.gw_unsent_users(gid)
    job = {"running": True, "total": len(users), "done": 0, "sent": 0, "blocked": 0, "failed": 0, "error": ""}
    _send_jobs[gid] = job
    _spawn(_broadcast(bot, gid, users, job))
    return True


async def _broadcast(bot: Bot, gid: int, users: list[int], job: dict):
    rev, text, photo, markup, g = None, "", None, None, None
    streak = 0  # ketma-ket kutilmagan xatolar
    try:
        for uid in users:
            if _rev.get(gid, 0) != rev:
                rev = _rev.get(gid, 0)
                g = await db.gw_get(gid)
                text, photo, markup = post_text(g), await _photo(g), bot_markup(g)
            try:
                msg = await _call(lambda: _send(bot, uid, text, photo, markup))
                if isinstance(photo, BufferedInputFile):
                    photo = await _remember(g, msg) or photo
                await db.gw_add_message(gid, uid, msg.message_id)
                job["sent"] += 1
                streak = 0
            except TelegramForbiddenError:
                # botni bloklagan yoki akkaunt o'chirilgan — qayta urinilmaydi
                await db.gw_add_message(gid, uid, None)
                job["blocked"] += 1
            except TelegramBadRequest as e:
                job["failed"] += 1
                if "chat not found" in str(e).lower():
                    await db.gw_add_message(gid, uid, None)
                else:
                    streak += 1
                    logging.warning("premium broadcast %s: %s", uid, e)
            except Exception:
                job["failed"] += 1
                streak += 1
                logging.exception("premium broadcast")
            job["done"] += 1
            if streak >= 20:
                job["error"] = "Ketma-ket xatolar sabab to'xtatildi — bot loglarini tekshiring."
                break
            await asyncio.sleep(SEND_GAP)
    finally:
        job["running"] = False


def refresh(bot: Bot, gid: int, media: bool = False):
    """Post tahrirlandi: kanal posti va botdagi barcha nusxalar fonda yangilanadi."""
    _rev[gid] = _rev.get(gid, 0) + 1
    if media:
        _media_pending.add(gid)
    old = _edit_tasks.get(gid)
    if old and not old.done():
        old.cancel()
    _edit_tasks[gid] = _spawn(_refresh(bot, gid))


async def _refresh(bot: Bot, gid: int):
    media = gid in _media_pending
    g = await db.gw_get(gid)
    text, photo = post_text(g), await _photo(g)
    targets = []
    if g["message_id"] and RESULTS_CHANNEL:
        targets.append((RESULTS_CHANNEL, g["message_id"], await channel_markup(bot, g), False))
    markup = bot_markup(g)
    targets += [(uid, mid, markup, True) for uid, mid in await db.gw_messages(gid)]
    for chat_id, mid, kb, private in targets:
        try:
            msg = await _call(lambda: _edit(bot, text, photo, kb, media, chat_id=chat_id, message_id=mid))
            if media and isinstance(photo, BufferedInputFile):
                photo = await _remember(g, msg) or photo
        except TelegramForbiddenError:
            if private:
                await db.gw_drop_message(gid, chat_id)
        except TelegramBadRequest as e:
            if private and _gone(e):
                await db.gw_drop_message(gid, chat_id)
            else:
                logging.warning("premium edit %s: %s", chat_id, e)
        except Exception:
            logging.exception("premium edit")
        await asyncio.sleep(SEND_GAP)
    _media_pending.discard(gid)


# --- obuna va g'olib ---
async def is_subscribed(bot: Bot, user_id: int) -> bool | None:
    """Barcha majburiy Telegram kanallarda bormi (jonli tekshiruv). None — tekshirib bo'lmadi."""
    for ch in await channels.telegram_targets():
        try:
            member = await bot.get_chat_member(ch, user_id)
        except Exception as e:
            logging.warning("premium get_chat_member %s: %s", ch, e)
            return None
        if member.status not in OK_STATUSES:
            return False
    return True


async def on_member_update(bot: Bot, event: ChatMemberUpdated):
    """Majburiy kanaldan chiqsa — «Chiqib ketgan», qaytib to'liq obuna bo'lsa — yana «Ishtirokchi»."""
    targets = {t.lower() for t in await channels.telegram_targets()}
    names = {str(event.chat.id)}
    if event.chat.username:
        names.add(f"@{event.chat.username}".lower())
    if not names & targets:
        return
    g = await db.gw_active()
    if not g:
        return
    uid = event.new_chat_member.user.id
    status = event.new_chat_member.status
    if status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        await db.gw_mark_left(g["id"], uid)
    elif status in OK_STATUSES:
        entry = await db.gw_entry(g["id"], uid)
        if entry and entry["status"] == "left" and await is_subscribed(bot, uid):
            await db.gw_mark_back(g["id"], uid)


async def draw(bot: Bot, gid: int):
    """Tasodifiy g'olib: (qator, None) yoki (None, 'nobody' | 'tgerr')."""
    for _ in range(50):
        row = await db.gw_random_entry(gid)
        if not row:
            return None, "nobody"
        ok = await is_subscribed(bot, row["user_id"])
        if ok is None:
            return None, "tgerr"
        if not ok:
            # tanlov paytida kanalda yo'q — chetlatiladi va boshqasi tanlanadi
            await db.gw_mark_left(gid, row["user_id"])
            continue
        await db.gw_mark_winner(gid, row["user_id"])
        return row, None
    return None, "nobody"


async def notify(bot: Bot, gid: int, user_id: int, text: str) -> str | None:
    """G'olibga bot orqali xabar. None — yuborildi, aks holda 'blocked' | 'error'."""
    try:
        await bot.send_message(user_id, html.escape(text))
    except TelegramForbiddenError:
        return "blocked"
    except Exception as e:
        logging.warning("premium notify %s: %s", user_id, e)
        return "error"
    await db.gw_set_notified(gid, user_id)
    return None
