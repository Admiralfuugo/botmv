"""Reklama: matnni tozalash, tugmalar, auditoriya, yuborish navbati, pauza/to'xtatish, o'chirish, rejalashtiruvchi.

Rejalashtiruvchi (scheduler) panel ishga tushganda web_ads.setup() orqali boshlanadi.
Bir vaqtda bitta reklama yuboriladi; qabul qiluvchilar bazada turadi — qayta ishga tushganda davom etadi.
"""
import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

import db
from config import PUBLIC_URL
from services.premium import _call  # Telegram «kuting» desa — kutib qayta urinadi

SEND_GAP = 0.04  # soniyasiga ~25 xabar
CAPTION_LIMIT, TEXT_LIMIT = 1024, 4096
DELETE_HOURS = (1, 3, 6, 12, 24)
AUDIENCES = {
    "all": "Barcha foydalanuvchilar",
    "voted": "Oxirgi so'rovnomada ovoz berganlar",
    "not_voted": "Oxirgi so'rovnomada ovoz bermaganlar",
    "premium": "Premium ishtirokchilari",
    "not_premium": "Premium'da qatnashmaganlar",
    "new": "So'nggi N kunda qo'shilganlar",
    "users": "Aniq akkauntlar",
}
MEDIA_KINDS = {"photo": "Rasm", "video": "Video", "animation": "GIF", "document": "Fayl"}


# --- matn: faqat Telegram qo'llaydigan HTML teglar qoladi ---
_ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "tg-spoiler", "a"}
_HREF = re.compile(r"^(?:https?|tg)://\S+$", re.I)


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED:
            return
        a = dict(attrs)
        if tag == "a":
            href = (a.get("href") or "").strip()
            if not _HREF.match(href):
                return
            self.out.append(f'<a href="{html.escape(href)}">')
        elif tag == "blockquote" and "expandable" in a:
            self.out.append("<blockquote expandable>")
        else:
            self.out.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        # teglar to'g'ri yopilishi uchun ichkaridagilar ham yopiladi
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        self.close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def clean(text: str) -> str:
    c = _Cleaner()
    c.feed((text or "").replace("\r\n", "\n"))
    return c.result().strip()


def visible_len(clean_html: str) -> int:
    """Telegram hisoblaydigan uzunlik (teglarsiz, UTF-16 birliklarda)."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", clean_html))
    return len(plain.encode("utf-16-le")) // 2


# --- tugmalar: «Matn - https://…», bir qatorda bir nechtasi «|» bilan ---
_BTN = re.compile(r"^(.+?)\s*[-–—]\s*((?:https?|tg)://\S+)$", re.I)


def parse_buttons(raw: str) -> list[list[tuple[str, str]]]:
    """Qatorlar ro'yxati; xato bo'lsa ValueError(qator_raqami)."""
    rows = []
    for n, line in enumerate((raw or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        row = []
        for part in line.split("|"):
            m = _BTN.match(part.strip())
            if not m:
                raise ValueError(n)
            row.append((m.group(1).strip()[:64], m.group(2)[:1000]))
        if len(row) > 4:
            raise ValueError(n)
        rows.append(row)
    if len(rows) > 10:
        raise ValueError(11)
    return rows


def button_list(raw: str) -> list[tuple[str, str]]:
    try:
        return [b for row in parse_buttons(raw) for b in row]
    except ValueError:
        return []


def button_url(raw: str, idx: int) -> str | None:
    flat = button_list(raw)
    return flat[idx][1] if 0 <= idx < len(flat) else None


def tracking() -> bool:
    return bool(PUBLIC_URL)


def markup(ad_id: int, raw: str, track: bool) -> InlineKeyboardMarkup | None:
    """track=True va PUBLIC_URL bo'lsa, havola panel orqali o'tadi va bosish sanaladi."""
    try:
        rows = parse_buttons(raw)
    except ValueError:
        return None
    if not rows:
        return None
    kb, idx = [], 0
    for row in rows:
        line = []
        for text, url in row:
            if track and PUBLIC_URL:
                url = f"{PUBLIC_URL}/r/{ad_id}/{idx}"
            line.append(InlineKeyboardButton(text=text, url=url))
            idx += 1
        kb.append(line)
    return InlineKeyboardMarkup(inline_keyboard=kb)


def media_kind(filename: str, content_type: str) -> str:
    name, ct = (filename or "").lower(), (content_type or "").lower()
    if ct == "image/gif" or name.endswith(".gif"):
        return "animation"
    if ct in ("image/jpeg", "image/png", "image/webp") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "photo"
    if ct.startswith("video/") or name.endswith((".mp4", ".mov", ".m4v", ".webm")):
        return "video"
    return "document"


# --- auditoriya ---
async def resolve_accounts(raw: str) -> tuple[list[int], list[str]]:
    """«Aniq akkauntlar»: (botda bor user_id lar, topilmaganlar). ID, @username yoki t.me/username."""
    ids, names, seen = [], [], set()
    bad = []
    for token in re.split(r"[\s,;]+", raw or ""):
        t = token.strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        if re.fullmatch(r"\d{3,15}", t):
            ids.append(int(t))
            continue
        m = re.fullmatch(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,32})/?", t)
        name = (m.group(1) if m else t.lstrip("@")).lower()
        if re.fullmatch(r"[a-z0-9_]{4,32}", name):
            names.append(name)
        else:
            bad.append(t)
    known = await db.users_existing(ids) if ids else set()
    by_name = await db.users_by_usernames(names) if names else {}
    found = [i for i in ids if i in known] + [by_name[n] for n in names if n in by_name]
    unknown = [str(i) for i in ids if i not in known] + ["@" + n for n in names if n not in by_name] + bad
    return list(dict.fromkeys(found)), unknown


async def audience_count(kind: str, days: int, users_raw: str) -> tuple[int, list[str]]:
    ids, unknown = (await resolve_accounts(users_raw)) if kind == "users" else ([], [])
    return await db.ad_audience_count(kind, days, ids), unknown


async def recipients(ad) -> list[str]:
    users: list[int] = []
    if ad["to_bot"]:
        ids = (await resolve_accounts(ad["audience_users"]))[0] if ad["audience"] == "users" else []
        users = await db.ad_audience(ad["audience"], ad["audience_days"], ids)
    return [str(u) for u in users] + list(ad["targets"])


# --- yuborish ---
def _cid(chat_id: str):
    return int(chat_id) if re.fullmatch(r"-?\d+", chat_id) else chat_id


async def _media(ad):
    """Telegramdagi file_id (avval yuklangan bo'lsa) yoki fayl baytlari; media yo'q bo'lsa None."""
    if not ad["has_media"]:
        return None
    row = await db.ad_file(ad["id"])
    if row["file_id"]:
        return row["file_id"]
    return BufferedInputFile(row["media"], filename=row["media_name"] or "file")


async def _remember(ad, msg) -> str | None:
    mt = ad["media_type"]
    if mt == "photo" and msg.photo:
        file_id = msg.photo[-1].file_id
    else:
        obj = getattr(msg, mt, None) or msg.document
        file_id = obj.file_id if obj else None
    if file_id:
        await db.ad_set_file_id(ad["id"], ad["media_ver"], file_id)
    return file_id


async def _send(bot: Bot, chat_id, ad, media, kb):
    opts = {"reply_markup": kb, "disable_notification": ad["silent"], "protect_content": ad["protect"]}
    caption = ad["text"] or None
    mt = ad["media_type"] if media is not None else None
    if mt == "photo":
        return await bot.send_photo(chat_id, media, caption=caption, **opts)
    if mt == "video":
        return await bot.send_video(chat_id, media, caption=caption, supports_streaming=True, **opts)
    if mt == "animation":
        return await bot.send_animation(chat_id, media, caption=caption, **opts)
    if mt == "document":
        return await bot.send_document(chat_id, media, caption=caption, **opts)
    preview = LinkPreviewOptions(is_disabled=True) if ad["no_preview"] else None
    return await bot.send_message(chat_id, ad["text"], link_preview_options=preview, **opts)


async def _pin(bot: Bot, chat_id: str, message_id: int):
    try:
        await bot.pin_chat_message(_cid(chat_id), message_id, disable_notification=True)
    except Exception as e:
        logging.info("ads pin %s: %s", chat_id, e)


async def send_test(bot: Bot, ad_id: int, chat_id: int) -> str | None:
    """Sinov: bitta akkauntga (bosish sanalmaydi, qadash va o'chirish qo'llanmaydi). None — yuborildi."""
    ad = await db.ad_get(ad_id)
    media = await _media(ad)
    try:
        msg = await _send(bot, chat_id, ad, media, markup(ad_id, ad["buttons"], track=False))
    except TelegramForbiddenError:
        return "blocked"
    except Exception as e:
        logging.warning("ads test %s: %s", chat_id, e)
        return str(e)
    if isinstance(media, BufferedInputFile):
        await _remember(ad, msg)
    return None


# --- navbat va ishchi ---
_worker: asyncio.Task | None = None
_current: int | None = None
_halt: set[int] = set()       # pauza yoki to'xtatish so'ralgan reklamalar
_deleting: set[int] = set()
_errors: dict[int, str] = {}
_tasks: set[asyncio.Task] = set()
_wake = asyncio.Event()


def current() -> int | None:
    """Ayni paytda yuborilayotgan reklama (qolgan «sending» lar navbatda)."""
    return _current


def error(ad_id: int) -> str | None:
    return _errors.get(ad_id)


def deleting(ad_id: int) -> bool:
    return ad_id in _deleting


def poke():
    _wake.set()


async def start(ad_id: int) -> int:
    """Qabul qiluvchilarni navbatga yozib, yuborishni boshlaydi. 0 — hech kim yo'q yoki holat mos emas."""
    ad = await db.ad_get(ad_id)
    if not ad or ad["status"] not in ("draft", "scheduled"):
        return 0
    rec = await recipients(ad)
    if not rec or not await db.ad_queue(ad_id, rec):
        return 0
    _halt.discard(ad_id)
    _errors.pop(ad_id, None)
    poke()
    return len(rec)


async def pause(ad_id: int):
    ad = await db.ad_get(ad_id)
    if ad and ad["status"] == "sending":
        _halt.add(ad_id)
        await db.ad_set_status(ad_id, "paused")


async def resume(ad_id: int):
    ad = await db.ad_get(ad_id)
    if ad and ad["status"] == "paused":
        _halt.discard(ad_id)
        _errors.pop(ad_id, None)
        await db.ad_set_status(ad_id, "sending")
        poke()


async def stop(ad_id: int):
    ad = await db.ad_get(ad_id)
    if ad and ad["status"] in ("sending", "paused"):
        _halt.add(ad_id)
        await db.ad_skip_rest(ad_id)
        await db.ad_finish(ad_id, "stopped")


async def _run(bot: Bot, ad_id: int):
    global _current
    _current = ad_id
    try:
        ad = await db.ad_get(ad_id)
        kb = markup(ad_id, ad["buttons"], track=True)
        media = await _media(ad)
        streak = 0  # ketma-ket kutilmagan xatolar
        while ad_id not in _halt:
            batch = await db.ad_next_batch(ad_id, 100)
            if not batch:
                if (await db.ad_get(ad_id))["status"] == "sending":
                    await db.ad_finish(ad_id, "done")
                return
            for chat_id in batch:
                if ad_id in _halt:
                    return
                try:
                    msg = await _call(lambda: _send(bot, _cid(chat_id), ad, media, kb))
                    if isinstance(media, BufferedInputFile):
                        media = await _remember(ad, msg) or media
                    await db.ad_mark(ad_id, chat_id, "sent", msg.message_id)
                    streak = 0
                    if ad["pin"]:
                        await _pin(bot, chat_id, msg.message_id)
                except TelegramForbiddenError:
                    await db.ad_mark(ad_id, chat_id, "blocked")
                except TelegramBadRequest as e:
                    await db.ad_mark(ad_id, chat_id, "failed")
                    if "chat not found" not in str(e).lower():
                        streak += 1
                        logging.warning("ads send %s: %s", chat_id, e)
                except Exception:
                    logging.exception("ads send")
                    await db.ad_mark(ad_id, chat_id, "failed")
                    streak += 1
                if streak >= 25:
                    _errors[ad_id] = "Ketma-ket xatolar sabab pauza qilindi — matn, media va tugmalarni tekshiring."
                    _halt.add(ad_id)
                    await db.ad_set_status(ad_id, "paused")
                    return
                await asyncio.sleep(SEND_GAP)
    except Exception:
        logging.exception("ads worker")
    finally:
        _current = None
        poke()


def delete_all(bot: Bot, ad_id: int) -> bool:
    """Yetkazilgan xabarlarni hamma chatdan o'chiradi (Telegram faqat 48 soat ichida ruxsat beradi)."""
    if ad_id in _deleting:
        return False
    _deleting.add(ad_id)
    task = asyncio.create_task(_delete_all(bot, ad_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


async def _delete_all(bot: Bot, ad_id: int):
    try:
        await stop(ad_id)
        for chat_id, mid in await db.ad_sent_messages(ad_id):
            try:
                await _call(lambda: bot.delete_message(_cid(chat_id), mid))
                await db.ad_mark(ad_id, chat_id, "deleted")
            except Exception as e:
                logging.info("ads delete %s: %s", chat_id, e)
            await asyncio.sleep(SEND_GAP)
        await db.ad_set_status(ad_id, "deleted")
    except Exception:
        logging.exception("ads delete_all")
    finally:
        _deleting.discard(ad_id)


async def _auto_delete(bot: Bot):
    for ad_id, chat_id, mid in await db.ad_due_deletes(200):
        try:
            await bot.delete_message(_cid(chat_id), mid)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return
        except Exception as e:
            logging.info("ads auto-delete %s: %s", chat_id, e)
        # muvaffaqiyatsiz bo'lsa ham qayta urinilmaydi (masalan 48 soat o'tgan)
        await db.ad_mark(ad_id, chat_id, "deleted")
        await asyncio.sleep(SEND_GAP)


async def scheduler(bot: Bot):
    """Har 15 soniyada: vaqti kelganlarni boshlaydi, navbatdagini yuboradi, avto-o'chirishni bajaradi."""
    global _worker
    while True:
        try:
            for ad_id in await db.ad_due_scheduled():
                if not await start(ad_id):
                    _errors[ad_id] = "Yuborish vaqti keldi, lekin qabul qiluvchi topilmadi."
                    await db.ad_finish(ad_id, "stopped")
            if _worker is None or _worker.done():
                waiting = [i for i in await db.ad_sending_ids() if i not in _halt]
                if waiting:
                    _worker = asyncio.create_task(_run(bot, waiting[0]))
            await _auto_delete(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("ads scheduler")
        try:
            await asyncio.wait_for(_wake.wait(), 15)
        except asyncio.TimeoutError:
            pass
        _wake.clear()


def progress(ad, counts: dict[str, int]) -> dict:
    """Jarayon: jami, qolgan, foiz, tezlik (daqiqasiga) va taxminiy qolgan vaqt (daqiqa)."""
    total = sum(counts.values())
    queued = counts.get("queued", 0)
    done = total - queued
    rate = eta = None
    if ad["started_at"] and done:
        elapsed = (datetime.now(timezone.utc) - ad["started_at"]).total_seconds()
        if elapsed > 0:
            rate = done / elapsed * 60
            eta = queued / rate if rate else None
    return {"total": total, "queued": queued, "done": done, "pct": done / total * 100 if total else 0,
            "rate": rate, "eta": eta}
