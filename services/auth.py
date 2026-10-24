import asyncio
import hashlib
import hmac
import logging
import os
import secrets

import db
from config import ADMIN_PASSWORD, ADMIN_USER

_N, _R, _P = 2 ** 14, 8, 1
MIN_PASSWORD = 8


def _hash(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${h.hex()}"


def _verify(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), h)


# scrypt sekin (ataylab) — event loop'ni to'xtatmaslik uchun alohida oqimda
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash, password)


async def verify_password(password: str, stored: str) -> bool:
    return await asyncio.to_thread(_verify, password, stored)


# login topilmaganda ham vaqt bir xil ketishi uchun
_DUMMY = _hash(secrets.token_hex(8))


async def check_login(username: str, password: str):
    """To'g'ri bo'lsa (id, username, password_hash, is_super), aks holda None."""
    admin = await db.admin_by_name(username.strip())
    ok = await verify_password(password, admin[2] if admin else _DUMMY)
    return admin if admin and ok else None


async def ensure_first_admin():
    """Adminlar jadvali bo'sh bo'lsa, .env dagi ADMIN_USER/ADMIN_PASSWORD dan bosh admin yaratadi."""
    if await db.admins_count():
        return
    if ADMIN_PASSWORD in ("", "change-me"):
        raise SystemExit("Birinchi admin uchun .env faylida ADMIN_PASSWORD ni o'rnating!")
    await db.add_admin(ADMIN_USER, await hash_password(ADMIN_PASSWORD), True)
    logging.info("Bosh admin yaratildi: %s", ADMIN_USER)


async def session_secret() -> bytes:
    """Sessiya cookie'larini imzolash kaliti (bazada saqlanadi — qayta ishga tushganda ham amal qiladi)."""
    value = await db.get_setting("session_secret")
    if not value:
        value = secrets.token_hex(32)
        await db.set_setting("session_secret", value)
    return bytes.fromhex(value)
