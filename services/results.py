import asyncio
import html
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from config import RESULTS_CHANNEL, UPDATE_INTERVAL

_last_text: dict[int, str] = {}


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
        lines.append(f"🕒 yangilandi {datetime.now():%H:%M:%S}")
    return "\n".join(lines)


def vote_keyboard(bot_username: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="🗳 Ovoz berish", url=f"https://t.me/{bot_username}?start=vote")
    return kb.as_markup()


async def publish(bot: Bot, poll_id: int, title: str) -> int:
    me = await bot.get_me()
    text = await render(poll_id, title)
    msg = await bot.send_message(RESULTS_CHANNEL, text, reply_markup=vote_keyboard(me.username))
    await db.set_message_id(poll_id, msg.message_id)
    _last_text[poll_id] = text
    return msg.message_id


async def refresh(bot: Bot, poll_id: int, title: str, message_id: int, closed=False):
    text = await render(poll_id, title, closed)
    # vaqt qatorisiz solishtiramiz — ovozlar o'zgarmasa tahrirlamaymiz
    core = text.split("🕒")[0]
    if not closed and _last_text.get(poll_id, "").split("🕒")[0] == core:
        return
    me = await bot.get_me()
    try:
        await bot.edit_message_text(
            text, chat_id=RESULTS_CHANNEL, message_id=message_id,
            reply_markup=None if closed else vote_keyboard(me.username),
        )
        _last_text[poll_id] = text
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            logging.warning("edit failed: %s", e)


async def close_and_publish_final(bot: Bot, poll):
    """poll = (id, title, message_id, ...)"""
    await db.close_poll(poll[0])
    if poll[2]:
        await refresh(bot, poll[0], poll[1], poll[2], closed=True)


async def updater_loop(bot: Bot):
    while True:
        try:
            poll = await db.active_poll()
            if poll:
                ends_at = poll[3]
                if ends_at and datetime.now(timezone.utc) >= ends_at:
                    await close_and_publish_final(bot, poll)
                elif poll[2]:
                    await refresh(bot, poll[0], poll[1], poll[2])
        except Exception:
            logging.exception("updater")
        await asyncio.sleep(UPDATE_INTERVAL)
