import logging
import time

from aiogram import Bot
from aiogram.enums import ChatMemberStatus

from services.channels import telegram_targets

OK_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.CREATOR,
}

CACHE_TTL = 300  # soniya: obuna bo'lgan deb tasdiqlangan foydalanuvchi qayta tekshirilmaydi
_ok_until: dict[int, float] = {}


async def missing_channels(bot: Bot, user_id: int) -> list[str]:
    """Foydalanuvchi obuna bo'lmagan Telegram kanallar ro'yxati."""
    if _ok_until.get(user_id, 0) > time.monotonic():
        return []
    missing = []
    for ch in await telegram_targets():
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status not in OK_STATUSES:
                missing.append(ch)
        except Exception as e:  # bot kanalda admin emas yoki kanal noto'g'ri
            logging.warning("get_chat_member %s: %s", ch, e)
            missing.append(ch)
    if not missing:
        _ok_until[user_id] = time.monotonic() + CACHE_TTL
    return missing


def forget(user_id: int):
    """Kanaldan chiqib ketganda keshni tozalash."""
    _ok_until.pop(user_id, None)


def reset():
    """Obunalar ro'yxati o'zgarganda — hamma qayta tekshiriladi."""
    _ok_until.clear()
