"""Post joylanadigan kanal va guruhlar (Premium va Reklama uchun umumiy ro'yxat).

Manbalar: natijalar kanali (RESULTS_CHANNEL), Obunalar bo'limidagi Telegram kanallar va qo'lda qo'shilganlar.
"""
import asyncio
import re
import time

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType

import db
from config import RESULTS_CHANNEL

TARGET = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-\d{5,})$")
LINK = re.compile(r"^(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?$")
CACHE_TTL = 120
_cache: dict[str, tuple[float, dict]] = {}


def normalize(raw: str) -> str | None:
    """@username, -100… ID yoki t.me/… havolani chat_id ga aylantiradi; noto'g'ri bo'lsa None."""
    s = (raw or "").strip()
    m = LINK.match(s)
    if m:
        s = "@" + m.group(1)
    elif s and s[0] not in "@-":
        s = "@" + s
    return s if TARGET.match(s) else None


async def check(bot: Bot, chat_id: str, fresh: bool = False) -> dict:
    """{'state': 'ok' | 'noadmin' | 'missing', 'role': 'admin' | 'member' | '', 'title': str, 'group': bool}

    Kanalda bot admin bo'lishi shart; guruhda a'zo bo'lishi yetarli.
    """
    key = chat_id.lower()
    hit = _cache.get(key)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    info = {"state": "missing", "role": "", "title": "", "group": False}
    try:
        chat = await asyncio.wait_for(bot.get_chat(chat_id), 5)
        me = await bot.me()
        member = await asyncio.wait_for(bot.get_chat_member(chat_id, me.id), 5)
        group = chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            role = "admin"
        elif group and member.status == ChatMemberStatus.MEMBER:
            role = "member"
        else:
            role = ""
        info = {"state": "ok" if role else "noadmin", "role": role, "title": chat.title or chat_id, "group": group}
    except Exception:
        pass
    _cache[key] = (time.monotonic(), info)
    return info


async def available(bot: Bot) -> list[dict]:
    """[{chat_id, title, source: 'results'|'obuna'|'added', id, state, role, group}, ...] takrorlarsiz."""
    items, seen = [], set()

    def add(chat_id, title, source, tid=None):
        if not chat_id or chat_id.lower() in seen:
            return
        seen.add(chat_id.lower())
        items.append({"chat_id": chat_id, "title": title, "source": source, "id": tid})

    if RESULTS_CHANNEL:
        add(RESULTS_CHANNEL, "Natijalar kanali", "results")
    for _, kind, title, target, *_ in await db.channels():
        if kind == "telegram":
            add(target, title, "obuna")
    for tid, chat_id, title in await db.targets_list():
        add(chat_id, title, "added", tid)
    infos = await asyncio.gather(*(check(bot, i["chat_id"]) for i in items))
    for item, info in zip(items, infos):
        item.update(state=info["state"], role=info["role"], group=info["group"])
        if item["source"] == "results" and info["title"]:
            item["title"] = info["title"]
    return items


def pick(items: list[dict], chosen) -> list[str]:
    """Formada belgilanganlardan faqat bot yoza oladigan joylar (chat_id lar)."""
    wanted = {c.lower() for c in chosen}
    return [i["chat_id"] for i in items if i["state"] == "ok" and i["chat_id"].lower() in wanted]
