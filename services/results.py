import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import RESULTS_CHANNEL, TZ_OFFSET, UPDATE_INTERVAL

TZ = timezone(timedelta(hours=TZ_OFFSET))
# kanal postini tahrirlash oralig'i (s): Telegram bitta xabarni daqiqasiga ~20 martadan ko'p tahrirlashga yo'l qo'ymaydi
LIVE_GAP = 4

# poll_id -> oxirgi tahrirlangan holat (matn + sonlar)
_last_sig: dict[int, str] = {}
# ovozlar o'zgarganda o'rnatiladi — updater_loop UPDATE_INTERVAL ni kutmasdan yangilaydi
_dirty = asyncio.Event()


def notify_change():
    _dirty.set()


def _bar(pct: float, width: int = 10) -> str:
    full = round(pct / 100 * width)
    return "█" * full + "░" * (width - full)


async def render(poll_id: int, title: str, closed: bool = False) -> str:
    rows = await db.results(poll_id)
    total = sum(n for _, _, n in rows)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🗳 <b>{html.escape(title)}</b> — {'yakuniy' if closed else 'jonli'} natijalar\n"]
    for i, (_, name, n) in enumerate(rows):
        pct = n / total * 100 if total else 0
        mark = medals[i] if i < 3 and n else f"{i + 1}."
        lines.append(f"{mark} {html.escape(name)}\n<code>{_bar(pct)}</code> {pct:.0f}% ({n})")
    lines.append(f"\nJami: <b>{total}</b> ovoz")
    if not closed:
        lines.append(f"🕒 yangilandi {datetime.now(TZ):%H:%M:%S}")
    return "\n".join(lines)


async def post_text(poll_id: int, title: str, closed: bool = False) -> str:
    """Kanal posti matni: admin yozgan tavsif, natijalar esa tugmalarda."""
    desc = (await db.poll_description(poll_id)).strip()
    text = html.escape(desc) if desc else f"🗳 <b>{html.escape(title)}</b>"
    if closed:
        text += "\n\n🏁 <b>Ovoz berish yakunlandi.</b>"
    return text


def _post_markup(rows, bot_username: str, poll_id: int, *, closed: bool, in_bot: bool):
    kb = InlineKeyboardBuilder()
    for cid, name, n in rows:
        label = f"🟢 {name} ({n})"
        if closed:
            kb.button(text=label, callback_data="noop")
        elif in_bot:
            kb.button(text=label, callback_data=f"pick:{cid}")
        else:
            kb.button(text=label, url=f"https://t.me/{bot_username}?start=c{cid}")
    if not closed:
        kb.button(text="📤 Ulashish", switch_inline_query=f"poll{poll_id}")
    kb.adjust(1)
    return kb.as_markup()


async def post_keyboard(bot: Bot, poll_id: int, *, closed: bool = False, in_bot: bool = False):
    """Har nomzod — alohida tugma, qavsda ovoz soni (qo'shilgan tartibda)."""
    rows = await db.counts_by_id(poll_id)
    return _post_markup(rows, (await bot.me()).username, poll_id, closed=closed, in_bot=in_bot)


async def publish(bot: Bot, poll_id: int, title: str) -> int:
    text = await post_text(poll_id, title)
    msg = await bot.send_message(RESULTS_CHANNEL, text, reply_markup=await post_keyboard(bot, poll_id))
    await db.set_message_id(poll_id, msg.message_id)
    _last_sig.pop(poll_id, None)
    return msg.message_id


def _gone(e: TelegramBadRequest) -> bool:
    s = str(e).lower()
    return "message_id_invalid" in s or "not found" in s or "message can't be edited" in s


async def _edit_shared(bot: Bot, ids: list[str], text: str, markup):
    """Ulashilgan nusxalarni yangilaydi: 20 tadan, soniyasiga ~20 tahrir (Telegram umumiy limiti ~30)."""
    async def one(iid):
        try:
            await bot.edit_message_text(text, inline_message_id=iid, reply_markup=markup)
        except TelegramBadRequest as e:
            if _gone(e):
                await db.delete_shared(iid)
            elif "not modified" not in str(e):
                logging.warning("shared edit failed: %s", e)

    for i in range(0, len(ids), 20):
        try:
            await asyncio.gather(*(one(iid) for iid in ids[i:i + 20]))
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return
        await asyncio.sleep(1)


async def refresh(bot: Bot, poll_id: int, title: str, message_id: int | None, closed=False):
    text = await post_text(poll_id, title, closed)
    rows = await db.counts_by_id(poll_id)
    # matn ham, sonlar ham o'zgarmasa — tahrirlamaymiz
    sig = text + repr(rows)
    if not closed and _last_sig.get(poll_id) == sig:
        return
    markup = _post_markup(rows, (await bot.me()).username, poll_id, closed=closed, in_bot=False)
    if message_id:
        try:
            await bot.edit_message_text(text, chat_id=RESULTS_CHANNEL, message_id=message_id, reply_markup=markup)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                logging.warning("edit failed: %s", e)
    _last_sig[poll_id] = sig
    await _edit_shared(bot, await db.shared_posts(poll_id), text, markup)


async def close_and_publish_final(bot: Bot, poll):
    """poll = (id, title, message_id, ...)"""
    await db.close_poll(poll[0])
    await refresh(bot, poll[0], poll[1], poll[2], closed=True)


async def updater_loop(bot: Bot):
    while True:
        try:
            await asyncio.wait_for(_dirty.wait(), timeout=UPDATE_INTERVAL)
        except asyncio.TimeoutError:
            pass
        _dirty.clear()
        try:
            poll = await db.active_poll()
            if poll:
                ends_at = poll[3]
                if ends_at and datetime.now(timezone.utc) >= ends_at:
                    await close_and_publish_final(bot, poll)
                else:
                    await refresh(bot, poll[0], poll[1], poll[2])
        except Exception:
            logging.exception("updater")
        # shu orada kelgan ovozlar keyingi bitta tahrirga birlashadi
        await asyncio.sleep(LIVE_GAP)
