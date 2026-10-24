"""Obuna bo'linadigan kanal va sahifalar (admin panelda boshqariladi)."""
import re
import time
from datetime import datetime, timezone

import db
from config import FB_URL, I_URL, REQUIRED_CHANNELS, X_URL

# kind -> (botdagi tugma belgisi, nomi)
KINDS = {
    "telegram": ("📢", "Telegram kanal"),
    "facebook": ("📘", "Facebook"),
    "x": ("🐦", "X (Twitter)"),
    "instagram": ("📸", "Instagram"),
    "youtube": ("▶️", "YouTube"),
    "tiktok": ("🎵", "TikTok"),
    "other": ("🔗", "Boshqa havola"),
}

TG_TARGET = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{3,31}|-100\d{5,})$")
URL = re.compile(r"^https?://[^\s<>\"']+$")

CACHE_TTL = 30
_cache: list[tuple] | None = None
_cached_at = 0.0


async def active() -> list[tuple]:
    """Faol obunalar: [(id, kind, title, target, url, position, is_active), ...]"""
    global _cache, _cached_at
    if _cache is None or time.monotonic() - _cached_at > CACHE_TTL:
        _cache = await db.channels(active_only=True)
        _cached_at = time.monotonic()
    return _cache


def invalidate():
    global _cache
    _cache = None


async def telegram_targets() -> list[str]:
    return [c[3] for c in await active() if c[1] == "telegram"]


async def has_links() -> bool:
    return any(c[1] != "telegram" for c in await active())


def emoji(kind: str) -> str:
    return KINDS.get(kind, KINDS["other"])[0]


def channel_url(target: str) -> str:
    return f"https://t.me/{target.lstrip('@')}" if target.startswith("@") else target


def button_url(kind: str, target: str, url: str) -> str:
    return url or channel_url(target)


async def seed_from_env():
    """Birinchi ishga tushishda .env dagi eski sozlamalarni bazaga ko'chirish (bir marta)."""
    if await db.get_setting("channels_seeded"):
        return
    if not await db.channels_count():
        # eski foydalanuvchilarning «obuna bo'ldim» tasdig'i saqlanib qolishi uchun eski sana
        old = datetime(2000, 1, 1, tzinfo=timezone.utc)
        for ch in REQUIRED_CHANNELS:
            await db.add_channel("telegram", ch, ch, "", old)
        for kind, title, url in (("facebook", "Facebook", FB_URL), ("x", "X (Twitter)", X_URL),
                                 ("instagram", "Instagram", I_URL)):
            if url:
                await db.add_channel(kind, title, "", url, old)
    await db.set_setting("channels_seeded", "1")
