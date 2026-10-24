import asyncio
import contextvars
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlparse

from aiogram.enums import ChatMemberStatus
from aiohttp import web

import db
from config import POLL_DAYS, TZ_OFFSET
from services import auth, channels, results, subscription

e = html.escape
TZ = timezone(timedelta(hours=TZ_OFFSET))

# --- sessiya: "admin_id.muddat.imzo" cookie; parol o'zgarsa, o'sha adminning sessiyalari bekor bo'ladi ---
COOKIE = "admin_session"
SESSION_TTL = 7 * 86400
SHORT_TTL = 12 * 3600
_secret = b""
# login urinishlari: ip -> muvaffaqiyatsiz urinish vaqtlari
_fails: dict[str, list[float]] = {}
MAX_FAILS, FAIL_WINDOW = 5, 600
PUBLIC = {"/login"}
# joriy so'rovdagi admin: (id, username, password_hash, is_super)
current_admin: contextvars.ContextVar = contextvars.ContextVar("current_admin", default=None)


def _sign(admin, exp: int) -> str:
    msg = f"{admin[0]}.{exp}.{admin[2]}".encode()
    return f"{admin[0]}.{exp}.{hmac.new(_secret, msg, hashlib.sha256).hexdigest()}"


async def _session_admin(request: web.Request):
    parts = request.cookies.get(COOKIE, "").split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[1]) < time.time():
        return None
    admin = await db.admin_by_id(int(parts[0]))
    if not admin or not hmac.compare_digest(".".join(parts), _sign(admin, int(parts[1]))):
        return None
    return admin


def _set_session(resp, admin, remember: bool):
    ttl = SESSION_TTL if remember else SHORT_TTL
    resp.set_cookie(COOKIE, _sign(admin, int(time.time()) + ttl), max_age=ttl if remember else None,
                    httponly=True, samesite="Strict", path="/")


@web.middleware
async def guard(request: web.Request, handler):
    # CSRF: POST faqat o'z saytimizdan (cookie ham SameSite=Strict)
    if request.method == "POST":
        origin = request.headers.get("Origin") or request.headers.get("Referer", "")
        if origin and urlparse(origin).netloc != request.host:
            return web.Response(status=403, text="Origin mos emas")
    if request.path in PUBLIC:
        return await handler(request)
    admin = await _session_admin(request)
    if admin:
        request["admin"] = admin
        current_admin.set(admin)
        return await handler(request)
    if request.method == "GET" and not request.path.startswith("/api/"):
        raise web.HTTPSeeOther("/login?next=" + quote(request.path_qs))
    return web.json_response({"error": "login kerak"}, status=401)


def _require_super(request: web.Request):
    if not request["admin"][3]:
        raise web.HTTPSeeOther("/?m=forbidden")


async def _security_headers(request, response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store")


# --- formatlash ---
def num(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def dt(x) -> str:
    return x.astimezone(TZ).strftime("%d.%m.%Y %H:%M") if x else "—"


def time_left(ends_at) -> str | None:
    s = (ends_at - datetime.now(timezone.utc)).total_seconds()
    if s <= 0:
        return None
    d, h, m = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
    if d:
        return f"{d} kun {h} soat"
    return f"{h} soat {m} daqiqa" if h else f"{m} daqiqa"


def _today_start() -> datetime:
    return datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)


# --- ko'rinish ---
ICONS = {
    "brand": '<path d="m9 11 3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "dash": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
            '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41'
           'M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "trash": '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "eye": '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "back": '<path d="m12 19-7-7 7-7M19 12H5"/>',
    "chev": '<path d="m6 9 6 6 6-6"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
            '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1'
              'c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "up": '<path d="m18 15-6-6-6 6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6'
             'l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/>',
}


def icon(name: str, cls: str = "") -> str:
    return f'<svg class="i {cls}" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


_DARK = """
  color-scheme:dark;--bg:#0e0f11;--surface:#17181b;--surface-2:#202125;--text:#f0efec;--text-2:#c3c2b7;
  --muted:#8f8e88;--border:rgba(255,255,255,.09);--accent:#3987e5;--accent-hi:#5598e7;
  --accent-soft:rgba(57,135,229,.16);--accent-ink:#fff;--danger:#e66767;--danger-soft:rgba(230,103,103,.14);
  --good:#0ca30c;--good-soft:rgba(12,163,12,.16);--warn-soft:rgba(250,178,25,.12);--warn-ink:#fab219;
  --track:#26272b;--grid:#2c2c2a;--axis:#383835;--series:#3987e5;--series-hi:#5598e7;
  --wash:rgba(57,135,229,.08);--shadow:none;
"""

STYLE = """
:root{
  color-scheme:light;--bg:#f5f6f8;--surface:#fff;--surface-2:#f0f2f5;--text:#0f1419;--text-2:#4b5563;
  --muted:#6b7280;--border:rgba(15,20,25,.1);--accent:#2a78d6;--accent-hi:#1c5cab;--accent-soft:#e6effb;
  --accent-ink:#fff;--danger:#c62f2f;--danger-soft:#fdeaea;--good:#0a7d0a;--good-soft:#e5f4e5;
  --warn-soft:#fff4dc;--warn-ink:#8a5a00;--track:#eceef2;--grid:#e6e6e1;--axis:#c3c2b7;
  --series:#2a78d6;--series-hi:#1c5cab;--wash:rgba(42,120,214,.06);
  --shadow:0 1px 2px rgba(16,24,40,.04),0 2px 8px rgba(16,24,40,.04);
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){""" + _DARK + """}}
:root[data-theme=dark]{""" + _DARK + """}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
h1{font-size:22px;line-height:1.25;margin:0;letter-spacing:-.01em} h2{font-size:15px;margin:0;font-weight:650}
.wrap{max-width:1120px;margin:0 auto;padding:0 16px}
main.wrap{padding-top:24px;padding-bottom:56px}
.i{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.muted{color:var(--muted)} .small{font-size:12.5px}

.top{position:sticky;top:0;z-index:10;background:var(--surface);background:color-mix(in srgb,var(--surface) 82%,transparent);backdrop-filter:saturate(1.6) blur(12px);-webkit-backdrop-filter:saturate(1.6) blur(12px);border-bottom:1px solid var(--border)}
.top-in{display:flex;align-items:center;gap:20px;height:58px}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;color:var(--text);font-size:15px;white-space:nowrap}
.brand:hover{text-decoration:none}
.brand .i{width:22px;height:22px;color:var(--accent)}
.nav{display:flex;gap:4px;flex:1}
.nav a{display:flex;align-items:center;gap:7px;padding:7px 12px;border-radius:9px;color:var(--text-2);font-weight:550;white-space:nowrap}
.nav a:hover{background:var(--surface-2);color:var(--text);text-decoration:none}
.nav a.on{background:var(--accent-soft);color:var(--accent)}
.top-act{display:flex;gap:2px}
.top-act form{margin:0}
@media (max-width:720px){
  .top-in{flex-wrap:wrap;height:auto;padding-top:10px;gap:6px}
  .brand{flex:1}
  .nav{order:3;flex-basis:100%;overflow-x:auto;padding-bottom:8px;scrollbar-width:none}
}
.t-sun{display:none}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .t-sun{display:block}:root:not([data-theme=light]) .t-moon{display:none}}
:root[data-theme=dark] .t-sun{display:block} :root[data-theme=dark] .t-moon{display:none}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:36px;padding:0 14px;border-radius:9px;border:1px solid transparent;font:inherit;font-weight:600;cursor:pointer;white-space:nowrap;text-decoration:none!important;transition:background .15s,border-color .15s,color .15s}
.btn.primary{background:var(--accent);color:var(--accent-ink)} .btn.primary:hover{background:var(--accent-hi)}
.btn.ghost{background:transparent;color:var(--text-2)} .btn.ghost:hover{background:var(--surface-2);color:var(--text)}
.btn.outline{background:var(--surface);color:var(--text);border-color:var(--border)} .btn.outline:hover{background:var(--surface-2)}
.btn.danger{background:var(--danger-soft);color:var(--danger)} .btn.danger:hover{background:var(--danger);color:#fff}
.btn.icon-only{width:36px;padding:0} .btn.sm{height:30px;padding:0 10px;font-size:13px} .btn.icon-only.sm{width:30px;padding:0}
.btn.block{width:100%;height:40px}
.btn[aria-disabled=true]{opacity:.45;pointer-events:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}

input[type=text],input[type=search],input[type=number],input[type=password],textarea{
  font-family:inherit;font-size:14px;font-weight:400;color:var(--text);background:var(--surface);
  border:1px solid var(--border);border-radius:9px;height:38px;padding:0 12px;width:100%;transition:border-color .15s,box-shadow .15s}
textarea{height:auto;padding:10px 12px;resize:vertical;line-height:1.5}
input:focus,textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.field{display:flex;flex-direction:column;gap:6px;font-weight:550;font-size:13px;color:var(--text-2)}
.field .muted{font-weight:400}
.check{display:flex;gap:8px;align-items:center;color:var(--text-2);font-size:13px;cursor:pointer}
.check input{width:16px;height:16px;accent-color:var(--accent);margin:0}
.stack{display:flex;flex-direction:column;gap:14px}
.grid-form{display:grid;grid-template-columns:1fr 150px;gap:14px}
.grid-form .full{grid-column:1/-1}
@media (max-width:560px){.grid-form{grid-template-columns:1fr}}
form{margin:0}

.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px;box-shadow:var(--shadow);min-width:0}
.card-h{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:16px}
.section{margin-top:16px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:16px;margin-top:16px}

.ph{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:20px}
.ph-title{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.ph-meta{display:flex;flex-wrap:wrap;gap:4px 16px;margin-top:8px;color:var(--muted);font-size:13px}
.ph-meta b{color:var(--text-2);font-weight:600}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.back{display:inline-flex;align-items:center;gap:5px;color:var(--muted);font-size:13px;margin-bottom:10px}
.back:hover{color:var(--text);text-decoration:none}

.pill{display:inline-flex;align-items:center;gap:6px;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.pill.on{background:var(--good-soft);color:var(--good)} .pill.off{background:var(--surface-2);color:var(--muted)}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(210px,100%),1fr));gap:16px;margin-bottom:16px}
.tile{padding:16px 18px}
.tile .k{color:var(--muted);font-size:13px;font-weight:550}
.tile .v{font-size:28px;font-weight:700;letter-spacing:-.02em;margin-top:2px;line-height:1.25;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tile .v.txt{font-size:18px;letter-spacing:-.01em;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;min-height:35px;display:-webkit-box}
.tile .s{color:var(--muted);font-size:12.5px;margin-top:2px}

.res{display:flex;align-items:center;gap:14px;padding:12px 0;border-top:1px solid var(--border)}
.res:first-child{border-top:0;padding-top:2px}
.rank{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;background:var(--surface-2);color:var(--text-2);font-weight:650;font-size:13px;flex:none}
.res.lead .rank{background:var(--accent);color:var(--accent-ink)}
.res-main{flex:1;min-width:0}
.res-top{display:flex;justify-content:space-between;gap:12px;margin-bottom:7px}
.res-name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.res-num{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
.res-num b{color:var(--text);font-weight:650}
@media (max-width:560px){.res-top{flex-direction:column;gap:2px}.res-name{white-space:normal}}
.track{height:8px;background:var(--track);border-radius:99px;overflow:hidden}
.track i{display:block;height:100%;background:var(--series);border-radius:99px;transition:width .6s ease}
.add{display:flex;gap:8px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)}

.chart{margin-top:4px}
.plot{position:relative;height:180px;margin-left:34px;border-bottom:1px solid var(--axis)}
.gl{position:absolute;left:0;right:0;border-top:1px solid var(--grid)}
.gl span{position:absolute;right:100%;margin-right:8px;transform:translateY(-50%);font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.cols{position:absolute;inset:0;display:grid;gap:2px;align-items:end}
.col{height:100%;display:flex;align-items:flex-end;justify-content:center;border-radius:4px 4px 0 0;outline-offset:0}
.col:hover,.col:focus-visible{background:var(--wash)}
.col i{display:block;width:100%;max-width:44px;background:var(--series);border-radius:4px 4px 0 0}
.col:hover i,.col:focus-visible i{background:var(--series-hi)}
.xl{display:grid;gap:2px;margin:6px 0 0 34px;font-size:11px;color:var(--muted);text-align:center;font-variant-numeric:tabular-nums}
.xl span{white-space:nowrap;overflow:visible;min-width:0}
details.tbl{margin-top:14px}
details.tbl summary{color:var(--accent);cursor:pointer;font-size:13px;font-weight:550;width:max-content}
details.tbl table{margin-top:8px}

details.card>summary{cursor:pointer;font-weight:650;font-size:15px;list-style:none;display:flex;align-items:center;gap:8px}
details.card>summary::-webkit-details-marker{display:none}
details.card[open]>summary{margin-bottom:18px}
summary .chev{margin-left:auto;color:var(--muted);transition:transform .2s}
details[open] .chev{transform:rotate(180deg)}

.table-wrap{overflow-x:auto;margin:0 -20px;padding:0 20px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;font-weight:600;color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;padding:10px 12px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
th:first-child,td:first-child{padding-left:0} th:last-child,td:last-child{padding-right:0}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--wash)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12.5px;color:var(--text-2)}
.nowrap{white-space:nowrap}
a.row-link{color:var(--text);font-weight:600}

.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.search{position:relative;flex:1;min-width:min(220px,100%)}
.search .i{position:absolute;left:12px;top:11px;color:var(--muted)}
.search input{padding-left:36px}
.seg{display:inline-flex;background:var(--surface-2);border-radius:10px;padding:3px;max-width:100%;overflow-x:auto}
.seg a{padding:5px 12px;border-radius:8px;color:var(--text-2);font-weight:550;font-size:13px;white-space:nowrap}
.seg a:hover{text-decoration:none;color:var(--text)}
.seg a.on{background:var(--surface);color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.pager{display:flex;justify-content:space-between;align-items:center;margin-top:16px;color:var(--muted);font-size:13px;gap:8px;flex-wrap:wrap}

.empty{text-align:center;padding:36px 16px;color:var(--muted)}
.empty .i{width:36px;height:36px;color:var(--axis);margin-bottom:6px;stroke-width:1.5}
.alert{padding:10px 12px;border-radius:9px;font-size:13px;font-weight:500}
.alert.danger{background:var(--danger-soft);color:var(--danger)}
.alert.warn{background:var(--warn-soft);color:var(--warn-ink)}

.login{min-height:100vh;display:grid;place-items:center;padding:24px 16px}
.login-card{width:100%;max-width:380px;padding:32px 28px}
.login-logo{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:18px}
.login-logo .i{width:26px;height:26px}
.login-card h1{font-size:20px}
.login-card>p{margin:4px 0 22px}
.pw{position:relative}
.pw input{padding-right:42px}
.pw-eye{position:absolute;right:4px;top:4px;width:30px;height:30px;border:0;background:transparent;color:var(--muted);border-radius:7px;cursor:pointer;display:grid;place-items:center}
.pw-eye:hover{background:var(--surface-2);color:var(--text)}
.corner{position:fixed;top:12px;right:12px}

#tip{position:fixed;z-index:50;pointer-events:none;background:var(--text);color:var(--bg);padding:5px 10px;border-radius:8px;font-size:12px;font-weight:550;opacity:0;transform:translate(-50%,-100%);transition:opacity .1s;white-space:nowrap}
#tip.show{opacity:1}
#toast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,16px);background:var(--text);color:var(--bg);padding:10px 16px;border-radius:10px;font-weight:550;opacity:0;transition:opacity .25s,transform .25s;pointer-events:none;z-index:60;max-width:calc(100% - 32px)}
#toast.show{opacity:1;transform:translate(-50%,0)}
#toast.err{background:var(--danger);color:#fff}

.me{display:flex;align-items:center;gap:8px;padding:4px 10px 4px 4px;border-radius:999px;color:var(--text-2);font-weight:550;margin:0 4px}
.me:hover,.me.on{background:var(--surface-2);text-decoration:none;color:var(--text)}
.avatar{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:13px}
@media (max-width:1000px){.me-name{display:none}.me{padding:4px}}
select{font-family:inherit;font-size:14px;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:9px;height:38px;padding:0 10px;width:100%}
select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.field .hint{font-weight:400;color:var(--muted);font-size:12px}
.split{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:16px;align-items:start}
@media (max-width:900px){.split{grid-template-columns:1fr}}
.pill.bad{background:var(--danger-soft);color:var(--danger)} .pill.warn{background:var(--warn-soft);color:var(--warn-ink)}
.pill.accent{background:var(--accent-soft);color:var(--accent)} .pill.plain::before{display:none}
.ch{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--border)}
.ch:first-child{border-top:0;padding-top:0}
.ch.off .ch-main{opacity:.5}
.ch-ico{width:36px;height:36px;border-radius:10px;background:var(--surface-2);display:grid;place-items:center;font-size:18px;flex:none}
.ch-main{flex:1;min-width:0}
.ch-title{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ch-sub{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin-top:2px}
.ch-sub a{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px}
.ch-act{display:flex;gap:2px;align-items:center;flex:none}
.ch-act .btn.sm{font-weight:550}
@media (max-width:560px){.ch{flex-wrap:wrap}.ch-act{width:100%;justify-content:flex-end}}
.preview{background:var(--surface-2);border-radius:12px;padding:14px}
.bubble{background:var(--surface);border-radius:12px 12px 12px 4px;padding:10px 12px;font-size:13.5px;box-shadow:var(--shadow);margin-bottom:6px}
.tg-btn{display:block;text-align:center;background:var(--surface);border-radius:8px;padding:8px 10px;margin-top:4px;font-size:13px;font-weight:550;color:var(--accent);box-shadow:var(--shadow);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.inline-form{display:flex;gap:6px;align-items:center}
.inline-form input{height:30px;font-size:13px;width:170px}
"""

THEME_JS = "try{var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t}catch(e){}"

APP_JS = r"""
function toggleTheme(){var r=document.documentElement,d=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;r.dataset.theme=d?'light':'dark';try{localStorage.setItem('theme',r.dataset.theme)}catch(e){}}
function togglePw(){var p=document.getElementById('pw');p.type=p.type==='password'?'text':'password'}
(function(){
  document.addEventListener('submit',function(ev){var m=ev.target.dataset.confirm;if(m&&!confirm(m))ev.preventDefault()});
  var tip=document.getElementById('tip');
  function show(el){var r=el.getBoundingClientRect(),bar=el.querySelector('i'),top=bar?bar.getBoundingClientRect().top:r.top;
    tip.textContent=el.dataset.tip;tip.style.left=Math.min(Math.max(r.left+r.width/2,80),innerWidth-80)+'px';
    tip.style.top=Math.max(top-8,36)+'px';tip.classList.add('show')}
  function hide(){tip.classList.remove('show')}
  document.addEventListener('mouseover',function(ev){var el=ev.target.closest('[data-tip]');el?show(el):hide()});
  document.addEventListener('focusin',function(ev){var el=ev.target.closest('[data-tip]');el?show(el):hide()});
  addEventListener('scroll',hide,{passive:true});
  var MSG={created:"So'rovnoma yaratildi",added:"Nomzod qo'shildi",deleted:"Nomzod o'chirildi",
    published:"Post kanalga joylandi",closed:"So'rovnoma yakunlandi",nocand:"Avval kamida bitta nomzod qo'shing",
    forbidden:"Bu bo'lim faqat bosh admin uchun",
    ch_added:"Obuna qo'shildi",ch_saved:"Saqlandi",ch_deleted:"Obuna o'chirildi",
    badtg:"Telegram kanal @username yoki -100… ID ko'rinishida bo'lishi kerak",
    badurl:"Havola https:// bilan boshlanishi kerak",needlink:"Yopiq kanal (ID) uchun taklif havolasini kiriting",
    notitle:"Nomini kiriting",
    ad_added:"Admin qo'shildi",ad_saved:"Saqlandi",ad_deleted:"Admin o'chirildi",
    exists:"Bunday login band",baduser:"Login 3–32 belgi: lotin harflari, raqam, _ . -",
    shortpw:"Parol kamida 8 belgidan iborat bo'lsin",mismatch:"Yangi parollar bir xil emas",
    badpw:"Joriy parol noto'g'ri",lastsuper:"Kamida bitta bosh admin qolishi kerak",
    self:"O'zingizni o'chira olmaysiz yoki rolingizni o'zgartira olmaysiz",pw_changed:"Parol o'zgartirildi"};
  var ERR={badtg:1,badurl:1,needlink:1,notitle:1,exists:1,baduser:1,shortpw:1,mismatch:1,badpw:1,lastsuper:1,self:1,forbidden:1};
  var u=new URL(location.href),m=u.searchParams.get('m');
  if(m&&MSG[m]){var t=document.getElementById('toast');t.textContent=MSG[m];t.classList.toggle('err',!!ERR[m]);
    requestAnimationFrame(function(){t.classList.add('show')});setTimeout(function(){t.classList.remove('show')},2800);
    u.searchParams.delete('m');history.replaceState(null,'',u.pathname+u.search+u.hash)}
  if(location.hash==='#new'){var n=document.getElementById('new');if(n)n.open=true}
  var live=document.getElementById('live');
  if(live){
    var pid=live.dataset.poll;
    function set(id,v){var el=document.getElementById('v-'+id);if(el&&v!=null)el.textContent=v}
    function tick(){if(document.hidden)return;
      fetch('/api/live?poll='+pid,{credentials:'same-origin'}).then(function(r){
        if(r.status===401){location.href='/login';throw 0}return r.json()}).then(function(d){
        ['total','total_sub','today','leader','leader_sub','updated'].forEach(function(k){set(k,d[k])});
        document.getElementById('results').innerHTML=d.html}).catch(function(){})}
    setInterval(tick,10000);
    document.addEventListener('visibilitychange',function(){if(!document.hidden)tick()});
  }
  var ks=document.getElementById('kind');
  if(ks){
    var u=document.getElementById('ch-url'),ul=document.getElementById('ch-url-label');
    function upd(){var tg=ks.value==='telegram';
      document.querySelectorAll('[data-tg]').forEach(function(el){el.hidden=!tg});
      u.required=!tg;u.placeholder=tg?'https://t.me/+AbCdEf…':'https://…';
      ul.textContent=tg?'Taklif havolasi (faqat yopiq kanal uchun)':'Havola'}
    ks.addEventListener('change',upd);upd();
  }
})();
"""


def _topbar(active: str) -> str:
    admin = current_admin.get()
    links = [("/", "dash", "Boshqaruv"), ("/polls", "list", "So'rovnomalar"),
             ("/users", "users", "Foydalanuvchilar"), ("/channels", "link", "Obunalar")]
    if admin and admin[3]:
        links.append(("/admins", "shield", "Adminlar"))
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{icon(key)}<span>{label}</span></a>'
        for href, key, label in links
    )
    me = ""
    if admin:
        me = (f'<a href="/account" class="me{" on" if active == "account" else ""}" title="Profil va parol">'
              f'<span class=avatar>{e(admin[1][:1].upper())}</span><span class=me-name>{e(admin[1])}</span></a>')
    return (
        f'<header class=top><div class="wrap top-in">'
        f'<a href="/" class=brand>{icon("brand")}<span>Ovoz berish</span></a>'
        f'<nav class=nav>{nav}</nav><div class=top-act>{_theme_btn()}{me}'
        f'<form method=post action=/logout><button class="btn ghost icon-only" aria-label=Chiqish title=Chiqish>'
        f'{icon("logout")}</button></form></div></div></header>'
    )


def _theme_btn(cls: str = "") -> str:
    return (f'<button type=button class="btn ghost icon-only {cls}" onclick="toggleTheme()" '
            f'aria-label="Mavzuni almashtirish" title="Kunduzgi / tungi mavzu">'
            f'{icon("moon", "t-moon")}{icon("sun", "t-sun")}</button>')


def page(body: str, *, title: str = "Admin", active: str = "", status: int = 200, bare: bool = False):
    top = "" if bare else _topbar(active)
    main = f"<main class=login>{body}</main>" if bare else f"<main class=wrap>{body}</main>"
    return web.Response(
        text=f"<!doctype html><html lang=uz><head><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>{e(title)} · Ovoz berish</title><script>{THEME_JS}</script>"
             f"<style>{STYLE}</style></head><body>{top}{main}"
             f"<div id=tip role=tooltip></div><div id=toast role=status aria-live=polite></div>"
             f"<script>{APP_JS}</script></body></html>",
        content_type="text/html", status=status,
    )


def _empty(text: str, icon_name: str = "inbox") -> str:
    return f'<div class=empty>{icon(icon_name)}<div>{text}</div></div>'


def _tile(label: str, value: str, sub: str = "", key: str = "", text: bool = False) -> str:
    vid = f' id="v-{key}"' if key else ""
    sid = f' id="v-{key}_sub"' if key else ""
    return (f'<div class="card tile"><div class=k>{label}</div>'
            f'<div class="v{" txt" if text else ""}"{vid}>{e(value)}</div><div class=s{sid}>{e(sub)}</div></div>')


def _nice_top(mx: int) -> int:
    """Grafik o'qi uchun chiroyli yuqori qiymat."""
    if mx <= 1:
        return 1
    k = 10 ** math.floor(math.log10(mx))
    return next(m * k for m in (1, 2, 3, 4, 5, 6, 8, 10) if m * k >= mx)


def column_chart(points, every: int, label: str) -> str:
    """points: [(o'q_yorlig'i, maslahat, qiymat), ...] — bitta qatorli ustunli diagramma."""
    top = _nice_top(max((v for *_, v in points), default=0))
    n = len(points)
    cols = "".join(
        f'<div class=col tabindex=0 data-tip="{e(tip)}: {num(v)} ovoz">'
        f'<i style="height:{f"max(2px,{v / top * 100:.2f}%)" if v else "0"}"></i></div>'
        for _, tip, v in points
    )
    xl = "".join(f"<span>{e(lb) if i % every == 0 else ''}</span>" for i, (lb, _, _) in enumerate(points))
    ticks = [top, top // 2] if top >= 2 else [top]
    grid = "".join(f'<div class=gl style="bottom:{t / top * 100:.0f}%"><span>{num(t)}</span></div>' for t in ticks)
    tmpl = f"grid-template-columns:repeat({n},minmax(0,1fr))"
    return (f'<div class=chart role=img aria-label="{e(label)}"><div class=plot>{grid}<span class=gl style="bottom:0">'
            f'<span>0</span></span><div class=cols style="{tmpl}">{cols}</div></div>'
            f'<div class=xl style="{tmpl}">{xl}</div></div>')


def _chart_table(points, head: str) -> str:
    rows = "".join(f"<tr><td>{e(tip)}</td><td class=num>{num(v)}</td></tr>" for _, tip, v in points)
    return (f'<details class=tbl><summary>Jadval ko\'rinishi</summary><div class=table-wrap><table>'
            f'<thead><tr><th>{head}</th><th class=num>Ovozlar</th></tr></thead><tbody>{rows}</tbody></table></div></details>')


def _day_points(per_day: dict, created_at, ends_at, active: bool):
    today = datetime.now(TZ).date()
    start = created_at.astimezone(TZ).date()
    if per_day:
        start = min(start, min(per_day))
    if active:
        end = min(today, ends_at.astimezone(TZ).date()) if ends_at else today
    else:
        end = max(per_day) if per_day else start
    end = max(end, start)
    start = max(start, end - timedelta(days=119))
    pts, d = [], start
    while d <= end:
        pts.append((d.strftime("%d.%m"), d.strftime("%d.%m.%Y"), per_day.get(d, 0)))
        d += timedelta(days=1)
    return pts


def _results_html(rows, total: int, editable: bool) -> str:
    if not rows:
        return _empty("Hali nomzod yo'q" + (" — pastdan qo'shing." if editable else "."), "users")
    out = []
    for i, (cid, name, n) in enumerate(rows):
        pct = n / total * 100 if total else 0
        delete = ""
        if editable:
            delete = (f'<form method=post action=/candidate/delete '
                      f'data-confirm="«{e(name)}» o\'chirilsinmi? Unga berilgan ovozlar ham o\'chadi.">'
                      f'<input type=hidden name=id value={cid}>'
                      f'<button class="btn ghost icon-only sm" aria-label="O\'chirish" title="O\'chirish">'
                      f'{icon("trash")}</button></form>')
        out.append(
            f'<div class="res{" lead" if i == 0 and n else ""}"><div class=rank>{i + 1}</div><div class=res-main>'
            f'<div class=res-top><span class=res-name>{e(name)}</span>'
            f'<span class=res-num><b>{pct:.1f}%</b> · {num(n)} ovoz</span></div>'
            f'<div class=track><i style="width:{pct:.2f}%"></i></div></div>{delete}</div>'
        )
    return "".join(out)


async def _live_data(poll) -> dict:
    pid, active = poll[0], poll[4]
    rows = await db.results(pid)
    total = sum(n for *_, n in rows)
    today = await db.votes_since(pid, _today_start())
    lead = rows[0] if total else None
    return {
        "total": num(total),
        "total_sub": f"bugun +{num(today)}",
        "today": num(today),
        "leader": lead[1] if lead else "—",
        "leader_sub": f"{lead[2] / total * 100:.1f}% · {num(lead[2])} ovoz" if lead else "Hali ovoz yo'q",
        "updated": f"yangilandi {datetime.now(TZ):%H:%M:%S}" if active else "yakuniy natijalar",
        "html": _results_html(rows, total, active),
    }


def _new_poll_form(has_active: bool) -> str:
    warn = ('<div class="alert warn full">Hozirgi faol so\'rovnoma yopiladi va kanal postida '
            'yakuniy natija chiqadi.</div>') if has_active else ""
    return (
        '<form method=post action=/poll/new class=grid-form>'
        '<label class=field>Sarlavha / savol<input type=text name=title required maxlength=200 '
        'placeholder="Masalan: Yilning eng yaxshi ..."></label>'
        f'<label class=field>Davomiyligi (kun)<input type=number name=days value={POLL_DAYS} min=1 max=90></label>'
        '<label class="field full">Nomzodlar <span class=muted>ixtiyoriy, har qatorda bittadan</span>'
        '<textarea name=candidates rows=4 placeholder="1-nomzod&#10;2-nomzod&#10;3-nomzod"></textarea></label>'
        f'{warn}<div class=full><button class="btn primary">{icon("plus")}So\'rovnoma yaratish</button></div></form>'
    )


async def poll_view(poll, *, home: bool) -> str:
    pid, title, message_id, ends_at, active, created_at = poll
    d = await _live_data(poll)
    users_total, users_new, _ = await db.users_stats(_today_start())
    per_day = dict(await db.votes_per_day(pid, TZ_OFFSET))
    per_hour = dict(await db.votes_per_hour(pid, TZ_OFFSET))

    meta = [f"Boshlangan <b>{dt(created_at)}</b>"]
    if ends_at:
        left = time_left(ends_at) if active else None
        meta.append(f"Tugash <b>{dt(ends_at)}</b>" + (f" · {left} qoldi" if left else ""))
    if active:
        meta.append("Kanal posti: " + ("<b>joylangan</b>" if message_id else "<b>hali joylanmagan</b>"))
    status = '<span class="pill on">Faol</span>' if active else '<span class="pill off">Yakunlangan</span>'

    actions = []
    if active:
        actions.append(f'<form method=post action=/publish><button class="btn primary">{icon("send")}'
                       f'{"Postni qayta joylash" if message_id else "Kanalga joylash"}</button></form>')
        actions.append(f'<form method=post action=/close data-confirm="So\'rovnoma yakunlansinmi? '
                       f'Kanal postida yakuniy natija chiqadi."><button class="btn danger">{icon("stop")}'
                       f'Yakunlash</button></form>')
    actions.append(f'<a class="btn outline" href="/export.csv?poll={pid}">{icon("download")}CSV</a>')

    back = "" if home else f'<a class=back href="/polls">{icon("back")}So\'rovnomalar</a>'
    head = (f'{back}<div class=ph><div><div class=ph-title><h1>{e(title)}</h1>{status}</div>'
            f'<div class=ph-meta>{"".join(f"<span>{m}</span>" for m in meta)}</div></div>'
            f'<div class=actions>{"".join(actions)}</div></div>')

    tiles = ('<div class=tiles>'
             + _tile("Jami ovoz", d["total"], d["total_sub"], "total")
             + _tile("Bugungi ovozlar", d["today"], "soat 00:00 dan beri", "today")
             + _tile("Bot foydalanuvchilari", num(users_total), f"bugun +{num(users_new)}")
             + _tile("Peshqadam", d["leader"], d["leader_sub"], "leader", text=True)
             + '</div>')

    add = ""
    if active:
        add = (f'<form method=post action=/candidate/add class=add>'
               f'<input type=text name=name placeholder="Yangi nomzod ismi" required maxlength=64 aria-label="Nomzod ismi">'
               f'<button class="btn primary">{icon("plus")}Qo\'shish</button></form>')
    results_card = (f'<section class=card><div class=card-h><h2>Natijalar</h2>'
                    f'<span class="muted small" id=v-updated>{d["updated"]}</span></div>'
                    f'<div id=results>{d["html"]}</div>{add}</section>')

    day_pts = _day_points(per_day, created_at, ends_at, active)
    hour_pts = [(f"{h:02d}", f"{h:02d}:00–{h:02d}:59", per_hour.get(h, 0)) for h in range(24)]
    charts = (
        '<div class=grid2>'
        f'<section class=card><div class=card-h><h2>Kunlik ovozlar</h2>'
        f'<span class="muted small">{len(day_pts)} kun</span></div>'
        f'{column_chart(day_pts, max(1, math.ceil(len(day_pts) / 8)), "Kunlik ovozlar")}'
        f'{_chart_table(day_pts, "Sana")}</section>'
        f'<section class=card><div class=card-h><h2>Soatlik faollik</h2>'
        f'<span class="muted small">kun soatlari bo\'yicha</span></div>'
        f'{column_chart(hour_pts, 3, "Soatlik faollik")}{_chart_table(hour_pts, "Soat")}</section>'
        '</div>'
    )

    new = ""
    if home:
        new = (f'<details class="card section" id=new{"" if active else " open"}>'
               f'<summary>{icon("plus")}Yangi so\'rovnoma{icon("chev", "chev")}</summary>'
               f'{_new_poll_form(active)}</details>')
    live = f'<div id=live data-poll="{pid}" hidden></div>' if active else ""
    return live + head + tiles + results_card + charts + new


# --- sahifalar ---
def _safe_next(n: str) -> str:
    return n if n.startswith("/") and not n.startswith("//") and not n.startswith("/login") else "/"


def _login_page(next_: str, error: str = "", user: str = "", status: int = 200):
    err = f'<div class="alert danger" role=alert>{e(error)}</div>' if error else ""
    body = (
        f'<div class=corner>{_theme_btn()}</div>'
        f'<div class="card login-card"><div class=login-logo>{icon("brand")}</div>'
        f'<h1>Admin panelga kirish</h1><p class=muted>Ovoz berish botini boshqarish</p>'
        f'<form method=post action=/login class=stack>{err}'
        f'<input type=hidden name=next value="{e(next_)}">'
        f'<label class=field>Login<input type=text name=username autocomplete=username required '
        f'{"" if user else "autofocus "}value="{e(user)}"></label>'
        f'<label class=field>Parol<span class=pw><input type=password name=password id=pw '
        f'autocomplete=current-password required{" autofocus" if user else ""}>'
        f'<button type=button class=pw-eye onclick="togglePw()" aria-label="Parolni ko\'rsatish" '
        f'title="Parolni ko\'rsatish">{icon("eye")}</button></span></label>'
        f'<label class=check><input type=checkbox name=remember checked>Meni eslab qol (7 kun)</label>'
        f'<button class="btn primary block">Kirish</button></form></div>'
    )
    return page(body, title="Kirish", bare=True, status=status)


async def login_page(request: web.Request):
    if await _session_admin(request):
        raise web.HTTPSeeOther("/")
    return _login_page(_safe_next(request.query.get("next", "/")))


def _client_ip(request: web.Request) -> str:
    # proxy (nginx/Coolify) ortida haqiqiy IP sarlavhada keladi; sarlavhaga faqat lokal proxy'dan ishonamiz
    remote = request.remote or "?"
    try:
        trusted = ipaddress.ip_address(remote).is_private
    except ValueError:
        trusted = False
    if trusted:
        fwd = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[-1]
        if fwd.strip():
            return fwd.strip()
    return remote


async def login_post(request: web.Request):
    data = await request.post()
    next_ = _safe_next(data.get("next", "/"))
    user = (data.get("username") or "")[:64]
    ip = _client_ip(request)
    now = time.time()
    fails = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    if len(fails) >= MAX_FAILS:
        wait = math.ceil((FAIL_WINDOW - (now - fails[0])) / 60)
        return _login_page(next_, f"Juda ko'p urinish. {wait} daqiqadan so'ng qayta urinib ko'ring.", user, 429)
    admin = await auth.check_login(user, (data.get("password") or "")[:256])
    if not admin:
        fails.append(now)
        _fails[ip] = fails
        await asyncio.sleep(1)
        return _login_page(next_, "Login yoki parol noto'g'ri.", user, 401)
    _fails.pop(ip, None)
    await db.touch_admin_login(admin[0])
    resp = web.HTTPSeeOther(next_)
    _set_session(resp, admin, bool(data.get("remember")))
    raise resp


async def logout(request: web.Request):
    resp = web.HTTPSeeOther("/login")
    resp.del_cookie(COOKIE, path="/")
    raise resp


async def dashboard(request: web.Request):
    last = await db.last_poll()
    if not last:
        body = (f'<div class=ph><div><h1>Xush kelibsiz</h1><div class=ph-meta>Birinchi so\'rovnomani yarating — '
                f'nomzodlarni hozir yoki keyinroq qo\'shishingiz mumkin.</div></div></div>'
                f'<section class=card id=new>{_new_poll_form(False)}</section>')
        return page(body, title="Boshqaruv", active="dash")
    poll = await db.get_poll(last[0])
    return page(await poll_view(poll, home=True), title="Boshqaruv", active="dash")


async def poll_page(request: web.Request):
    poll = await db.get_poll(int(request.match_info["id"]))
    if not poll:
        raise web.HTTPNotFound()
    return page(await poll_view(poll, home=False), title=poll[1], active="polls")


async def polls_page(request: web.Request):
    rows = await db.polls_overview()
    on, off = '<span class="pill on">Faol</span>', '<span class="pill off">Yakunlangan</span>'
    if rows:
        trs = "".join(
            f'<tr><td><a class=row-link href="/poll/{pid}">{e(title)}</a><div class="muted small">#{pid}</div></td>'
            f'<td>{on if active else off}</td>'
            f'<td class=nowrap>{dt(created)}<div class="muted small">→ {dt(ends)}</div></td>'
            f'<td class=num>{num(total)}</td><td>{e(winner) if winner and total else "<span class=muted>—</span>"}</td></tr>'
            for pid, title, active, created, ends, total, winner in rows
        )
        content = (f'<div class=table-wrap><table><thead><tr><th>So\'rovnoma</th><th>Holat</th><th>Muddat</th>'
                   f'<th class=num>Ovozlar</th><th>G\'olib / peshqadam</th></tr></thead><tbody>{trs}</tbody></table></div>')
    else:
        content = _empty("Hali so'rovnoma yo'q.", "list")
    body = (f'<div class=ph><div><h1>So\'rovnomalar</h1><div class=ph-meta>Barcha o\'tgan va joriy so\'rovnomalar '
            f'· {len(rows)} ta</div></div><div class=actions><a class="btn primary" href="/#new">{icon("plus")}'
            f'Yangi so\'rovnoma</a></div></div><section class=card>{content}</section>')
    return page(body, title="So'rovnomalar", active="polls")


USER_FILTERS = {"all": "Hammasi", "voted": "Ovoz berganlar", "not": "Ovoz bermaganlar"}
PER_PAGE = 50


async def users_page(request: web.Request):
    q = request.query.get("q", "").strip()[:64]
    f = request.query.get("f", "all")
    if f not in USER_FILTERS:
        f = "all"
    try:
        p = max(1, int(request.query.get("p", "1")))
    except ValueError:
        p = 1
    last = await db.last_poll()
    pid = last[0] if last else None
    rows, count = await db.users_list(pid, q, f, PER_PAGE, (p - 1) * PER_PAGE)
    total, new_today, social = await db.users_stats(_today_start())
    voted = await db.votes_since(pid) if pid else 0

    def url(**kw) -> str:
        params = {"q": q, "f": f, "p": p, **kw}
        params = {k: v for k, v in params.items() if v and (k, v) not in (("f", "all"), ("p", 1))}
        return "/users" + ("?" + urlencode(params) if params else "")

    tiles = ('<div class=tiles>'
             + _tile("Jami foydalanuvchilar", num(total), "botga /start bosganlar")
             + _tile("Bugun qo'shilgan", num(new_today), "soat 00:00 dan beri")
             + _tile("Ovoz berganlar", num(voted),
                     f"{voted / total * 100:.0f}% · oxirgi so'rovnomada" if total else "oxirgi so'rovnomada")
             + _tile("Ijtimoiy tarmoq tasdig'i", num(social), "Facebook / X «obuna bo'ldim»")
             + '</div>')

    seg = "".join(f'<a href="{e(url(f=k, p=1))}" class="{"on" if k == f else ""}">{v}</a>'
                  for k, v in USER_FILTERS.items())
    toolbar = (f'<form class=toolbar method=get action=/users>'
               f'<label class=search>{icon("search")}<input type=search name=q value="{e(q)}" '
               f'placeholder="Username yoki ID bo\'yicha qidirish" aria-label="Qidirish"></label>'
               f'<input type=hidden name=f value="{f}"><nav class=seg aria-label="Filtr">{seg}</nav>'
               f'<a class="btn outline" href="/users.csv">{icon("download")}CSV</a></form>')

    if rows:
        trs = []
        for uid, username, soc, created, cand, voted_at in rows:
            who = (f'<a href="https://t.me/{e(username)}" target=_blank rel=noopener>@{e(username)}</a>'
                   if username else '<span class=muted>username yo\'q</span>')
            vote = (f'{e(cand)}<div class="muted small">{dt(voted_at)}</div>'
                    if cand else '<span class=muted>—</span>')
            soc_html = '<span class="pill on">Ha</span>' if soc else '<span class=muted>—</span>'
            trs.append(f'<tr><td>{who}</td><td class=mono>{uid}</td><td class=nowrap>{dt(created)}</td>'
                       f'<td>{soc_html}</td><td>{vote}</td></tr>')
        first, last_n = (p - 1) * PER_PAGE + 1, (p - 1) * PER_PAGE + len(rows)
        prev = (f'<a class="btn outline sm" href="{e(url(p=p - 1))}">← Oldingi</a>' if p > 1
                else '<span class="btn outline sm" aria-disabled=true>← Oldingi</span>')
        nxt = (f'<a class="btn outline sm" href="{e(url(p=p + 1))}">Keyingi →</a>' if last_n < count
               else '<span class="btn outline sm" aria-disabled=true>Keyingi →</span>')
        poll_note = f"«{e(last[1])}» bo'yicha" if last else ""
        content = (f'<div class=table-wrap><table><thead><tr><th>Foydalanuvchi</th><th>Telegram ID</th>'
                   f'<th>Qo\'shilgan</th><th>Ijtimoiy</th><th>Ovozi <span style="text-transform:none;'
                   f'letter-spacing:0;font-weight:400">{poll_note}</span></th></tr></thead>'
                   f'<tbody>{"".join(trs)}</tbody></table></div>'
                   f'<div class=pager><span>{num(first)}–{num(last_n)} / {num(count)}</span>'
                   f'<div class=actions>{prev}{nxt}</div></div>')
    else:
        content = _empty("Hech narsa topilmadi." if q or f != "all" else "Hali foydalanuvchi yo'q.", "users")

    body = (f'<div class=ph><div><h1>Foydalanuvchilar</h1><div class=ph-meta>Botga kirgan barcha '
            f'foydalanuvchilar</div></div></div>{tiles}<section class=card>{toolbar}{content}</section>')
    return page(body, title="Foydalanuvchilar", active="users")


# --- amallar ---
def _back(m: str):
    raise web.HTTPSeeOther(f"/?m={m}")


async def new_poll(request: web.Request):
    data = await request.post()
    title = (data.get("title") or "").strip()[:200]
    try:
        days = max(1, min(90, int(data.get("days", POLL_DAYS))))
    except ValueError:
        days = POLL_DAYS
    if not title:
        raise web.HTTPSeeOther("/#new")
    active = await db.active_poll()
    if active:
        try:
            await results.close_and_publish_final(request.app["bot"], active)
        except Exception:
            logging.exception("eski so'rovnomani yopishda xato")
    pid = await db.create_poll(title, datetime.now(timezone.utc) + timedelta(days=days))
    names = []
    for line in (data.get("candidates") or "").splitlines():
        name = line.strip()[:64]
        if name and name not in names:
            names.append(name)
    for name in names[:50]:
        await db.add_candidate(pid, name)
    _back("created")


async def add_candidate(request: web.Request):
    data = await request.post()
    name = (data.get("name") or "").strip()[:64]
    poll = await db.active_poll()
    if poll and name:
        await db.add_candidate(poll[0], name)
    _back("added")


async def delete_candidate(request: web.Request):
    data = await request.post()
    poll = await db.active_poll()
    try:
        cid = int(data.get("id", ""))
    except ValueError:
        cid = None
    if poll and cid in dict(await db.candidates(poll[0])):
        await db.delete_candidate(cid)
    _back("deleted")


async def publish(request: web.Request):
    poll = await db.active_poll()
    if not poll:
        raise web.HTTPSeeOther("/")
    if not await db.candidates(poll[0]):
        _back("nocand")
    try:
        await results.publish(request.app["bot"], poll[0], poll[1])
    except Exception as ex:
        body = (f'<div class=ph><div><h1>Kanalga joylab bo\'lmadi</h1></div></div><section class="card stack">'
                f'<div class="alert danger">{e(str(ex))}</div>'
                f'<p class=muted style="margin:0">Bot natijalar kanalida admin ekanini va .env dagi '
                f'<b>RESULTS_CHANNEL</b> to\'g\'ri kanal ekanini tekshiring.</p>'
                f'<div><a class="btn outline" href="/">{icon("back")}Orqaga</a></div></section>')
        return page(body, title="Xatolik", active="dash", status=502)
    _back("published")


async def close(request: web.Request):
    poll = await db.active_poll()
    if poll:
        await results.close_and_publish_final(request.app["bot"], poll)
    _back("closed")


async def api_live(request: web.Request):
    try:
        poll = await db.get_poll(int(request.query.get("poll", "")))
    except ValueError:
        raise web.HTTPBadRequest()
    if not poll:
        raise web.HTTPNotFound()
    return web.json_response(await _live_data(poll))


def _csv(name: str, header, rows) -> web.Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return web.Response(
        body=buf.getvalue().encode("utf-8-sig"),
        content_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}"},
    )


async def export(request: web.Request):
    try:
        poll = await db.get_poll(int(request.query["poll"]))
    except (KeyError, ValueError):
        last = await db.last_poll()
        poll = await db.get_poll(last[0]) if last else None
    rows = []
    if poll:
        rows = [(uid, un, cand, dt(t)) for uid, un, cand, t in await db.export_votes(poll[0])]
    return _csv(f"votes-{poll[0] if poll else 0}.csv", ["user_id", "username", "candidate", "time"], rows)


async def export_users(request: web.Request):
    last = await db.last_poll()
    rows, _ = await db.users_list(last[0] if last else None, limit=10_000_000)
    return _csv("users.csv", ["user_id", "username", "social_confirmed", "joined", "voted_for", "voted_at"],
                [(uid, un, int(soc), dt(c), cand or "", dt(vt) if vt else "") for uid, un, soc, c, cand, vt in rows])


# --- obunalar ---
def _to(path: str, m: str):
    raise web.HTTPSeeOther(f"{path}?m={m}")


def _channels_changed():
    channels.invalidate()
    subscription.reset()


async def _tg_status(bot, target: str):
    """True — bot kanalda admin, False — admin emas, None — kanal topilmadi / bot a'zo emas."""
    try:
        me = await bot.me()
        member = await asyncio.wait_for(bot.get_chat_member(target, me.id), 5)
    except Exception:
        return None
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


def _icon_btn(action: str, name: str, label: str, disabled=False, confirm="", extra="") -> str:
    if disabled:
        return f'<span class="btn ghost icon-only sm" aria-disabled=true>{icon(name)}</span>'
    conf = f' data-confirm="{e(confirm)}"' if confirm else ""
    return (f'<form method=post action="{action}"{conf}>{extra}<button class="btn ghost icon-only sm" '
            f'aria-label="{e(label)}" title="{e(label)}">{icon(name)}</button></form>')


async def channels_page(request: web.Request):
    rows = await db.channels()
    tg = [r for r in rows if r[1] == "telegram"]
    statuses = dict(zip((r[0] for r in tg),
                        await asyncio.gather(*(_tg_status(request.app["bot"], r[3]) for r in tg))))
    items = []
    for i, (cid, kind, title, target, url, _, active) in enumerate(rows):
        link = channels.button_url(kind, target, url)
        kind_name = channels.KINDS.get(kind, channels.KINDS["other"])[1]
        if kind == "telegram":
            st = statuses[cid]
            badge = ('<span class="pill on">Bot admin</span>' if st else
                     '<span class="pill bad">Bot admin emas</span>' if st is False else
                     '<span class="pill bad">Kanal topilmadi</span>')
            sub = f'<span>{kind_name}</span><a href="{e(link)}" target=_blank rel=noopener>{e(target)}</a>{badge}'
        else:
            sub = (f'<span>{kind_name}</span><a href="{e(link)}" target=_blank rel=noopener>{e(link)}</a>'
                   f'<span class="pill off">ishonch asosida</span>')
        toggle = (f'<form method=post action="/channels/{cid}/toggle">'
                  f'<button class="btn {"outline" if active else "primary"} sm" '
                  f'title="{"Vaqtincha o&#x27;chirish" if active else "Qayta yoqish"}">'
                  f'{"Yoqilgan" if active else "Yoqish"}</button></form>')
        items.append(
            f'<div class="ch{"" if active else " off"}"><div class=ch-ico>{channels.emoji(kind)}</div>'
            f'<div class=ch-main><div class=ch-title>{e(title)}</div><div class=ch-sub>{sub}</div></div>'
            f'<div class=ch-act>'
            + _icon_btn(f"/channels/{cid}/move", "up", "Yuqoriga", i == 0, extra="<input type=hidden name=step value=-1>")
            + _icon_btn(f"/channels/{cid}/move", "down", "Pastga", i == len(rows) - 1,
                        extra="<input type=hidden name=step value=1>")
            + toggle
            + _icon_btn(f"/channels/{cid}/delete", "trash", "O'chirish", confirm=f"«{title}» o'chirilsinmi?")
            + '</div></div>'
        )
    listing = "".join(items) or _empty("Hali obuna qo'shilmagan — foydalanuvchilar hech qayerga obuna "
                                       "bo'lmasdan ovoz bera oladi.", "link")

    options = "".join(f'<option value="{k}">{v[0]} {v[1]}</option>' for k, v in channels.KINDS.items())
    form = (
        '<form method=post action=/channels/add class=stack>'
        f'<label class=field>Turi<select name=kind id=kind>{options}</select></label>'
        '<label class=field data-tg>Kanal <span class=hint>@username yoki -100… ID. '
        'Botni shu kanalga <b>admin</b> qiling — obuna shu orqali tekshiriladi.</span>'
        '<input type=text name=target placeholder="@kanal_nomi" maxlength=64></label>'
        '<label class=field><span id=ch-url-label>Havola</span>'
        '<input type=text name=url id=ch-url inputmode=url maxlength=300 placeholder="https://…"></label>'
        '<label class=field>Tugma nomi <span class=hint>ixtiyoriy — bo\'sh qolsa avtomatik</span>'
        '<input type=text name=title maxlength=48 placeholder="Masalan: Rasmiy kanal"></label>'
        f'<div><button class="btn primary">{icon("plus")}Qo\'shish</button></div></form>'
    )

    act = [r for r in rows if r[6]]
    poll = await db.active_poll()
    btns = "".join(f'<span class=tg-btn>{channels.emoji(k)} {e(t)}</span>' for _, k, t, *_ in act)
    if any(r[1] != "telegram" for r in act):
        btns += '<span class=tg-btn>☑️ Ijtimoiy tarmoqlarga obuna bo\'ldim</span>'
    btns += '<span class=tg-btn>✅ Tekshirish</span>'
    preview = (f'<div class=preview><div class=bubble>🗳 <b>{e(poll[1]) if poll else "So&#x27;rovnoma"}</b><br><br>'
               f'Ovoz berish uchun quyidagi sahifalarimizga obuna bo\'ling, so\'ng «Tekshirish»ni bosing.</div>'
               f'{btns}</div>')

    body = (
        f'<div class=ph><div><h1>Obunalar</h1><div class=ph-meta>Ovoz berishdan oldin foydalanuvchi obuna '
        f'bo\'lishi kerak bo\'lgan kanal va sahifalar</div></div></div>'
        f'<div class=split><div class=stack>'
        f'<section class=card><div class=card-h><h2>Ro\'yxat</h2><span class="muted small">{len(rows)} ta</span></div>'
        f'{listing}</section>'
        f'<section class=card><p class="muted small" style="margin:0">📢 <b>Telegram kanallar</b> — bot obunani '
        f'o\'zi aniq tekshiradi (bot kanalda admin bo\'lishi shart). Kanaldan chiqib ketgan foydalanuvchining '
        f'ovozi bekor bo\'ladi.<br>🔗 <b>Boshqa tarmoqlar</b> (Facebook, X, Instagram…) — tekshirishning iloji yo\'q, '
        f'foydalanuvchi «obuna bo\'ldim» tugmasini bosadi. Yangi havola qo\'shilsa, hamma qayta tasdiqlaydi.</p></section>'
        f'</div><div class=stack>'
        f'<section class=card><div class=card-h><h2>Yangi obuna</h2></div>{form}</section>'
        f'<section class=card><div class=card-h><h2>Botda ko\'rinishi</h2></div>{preview}</section>'
        f'</div></div>'
    )
    return page(body, title="Obunalar", active="link")


async def channel_add(request: web.Request):
    data = await request.post()
    kind = data.get("kind", "")
    if kind not in channels.KINDS:
        kind = "other"
    title = (data.get("title") or "").strip()[:48]
    target = (data.get("target") or "").strip()
    url = (data.get("url") or "").strip()[:300]
    if url and not channels.URL.match(url):
        _to("/channels", "badurl")
    if kind == "telegram":
        m = re.match(r"^(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{3,31})/?$", target)
        if m:
            target = "@" + m.group(1)
        elif target and target[0] not in "@-":
            target = "@" + target
        if not channels.TG_TARGET.match(target):
            _to("/channels", "badtg")
        if target.startswith("-") and not url:
            _to("/channels", "needlink")
        if not title:
            try:
                title = (await request.app["bot"].get_chat(target)).title[:48]
            except Exception:
                title = target
    else:
        if not url:
            _to("/channels", "badurl")
        target = ""
        title = title or channels.KINDS[kind][1]
    await db.add_channel(kind, title, target, url)
    _channels_changed()
    _to("/channels", "ch_added")


async def channel_toggle(request: web.Request):
    cid = int(request.match_info["id"])
    row = next((r for r in await db.channels() if r[0] == cid), None)
    if row:
        await db.set_channel_active(cid, not row[6])
        _channels_changed()
    _to("/channels", "ch_saved")


async def channel_move(request: web.Request):
    data = await request.post()
    await db.move_channel(int(request.match_info["id"]), -1 if data.get("step") == "-1" else 1)
    _channels_changed()
    raise web.HTTPSeeOther("/channels")


async def channel_delete(request: web.Request):
    await db.delete_channel(int(request.match_info["id"]))
    _channels_changed()
    _to("/channels", "ch_deleted")


# --- adminlar ---
USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


def _role_pill(is_super: bool) -> str:
    return ('<span class="pill accent plain">Bosh admin</span>' if is_super
            else '<span class="pill off plain">Admin</span>')


async def admins_page(request: web.Request):
    _require_super(request)
    me = request["admin"]
    trs = []
    for aid, username, is_super, created, last_login in await db.admins():
        self_ = aid == me[0]
        you = ' <span class="muted small">(siz)</span>' if self_ else ""
        name = (f'<div style="display:flex;align-items:center;gap:10px"><span class=avatar>{e(username[:1].upper())}'
                f'</span><b>{e(username)}</b>{you}</div>')
        actions = [
            f'<details><summary class="btn ghost sm">{icon("key")}Parol</summary>'
            f'<form method=post action="/admins/{aid}/password" class=inline-form style="margin-top:6px">'
            f'<input type=password name=password minlength=8 required placeholder="Yangi parol" '
            f'autocomplete=new-password aria-label="Yangi parol"><button class="btn primary sm">Saqlash</button>'
            f'</form></details>'
        ]
        if not self_:
            actions.insert(0, f'<form method=post action="/admins/{aid}/role"><button class="btn ghost sm">'
                              f'{"Oddiy admin qilish" if is_super else "Bosh admin qilish"}</button></form>')
            actions.append(_icon_btn(f"/admins/{aid}/delete", "trash", "O'chirish",
                                     confirm=f"«{username}» adminlikdan o'chirilsinmi?"))
        trs.append(f'<tr><td>{name}</td><td>{_role_pill(is_super)}</td><td class=nowrap>{dt(created)}</td>'
                   f'<td class=nowrap>{dt(last_login)}</td>'
                   f'<td><div class=actions style="justify-content:flex-end;align-items:flex-start;flex-wrap:nowrap">'
                   f'{"".join(actions)}</div></td></tr>')
    table = (f'<div class=table-wrap><table><thead><tr><th>Login</th><th>Rol</th><th>Qo\'shilgan</th>'
             f'<th>Oxirgi kirish</th><th></th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>')
    form = (
        '<form method=post action=/admins/add class=stack>'
        '<label class=field>Login <span class=hint>3–32 belgi: lotin harflari, raqam, _ . -</span>'
        '<input type=text name=username required maxlength=32 autocomplete=off></label>'
        '<label class=field>Parol <span class=hint>kamida 8 belgi</span>'
        '<input type=password name=password required minlength=8 autocomplete=new-password></label>'
        '<label class=field>Rol<select name=role><option value=admin>Admin</option>'
        '<option value=super>Bosh admin</option></select></label>'
        f'<div><button class="btn primary">{icon("plus")}Admin qo\'shish</button></div></form>'
    )
    roles = ('<p class="muted small" style="margin:0"><b>Bosh admin</b> — hamma narsa, shu jumladan adminlarni '
             'qo\'shish, o\'chirish va ularning parolini almashtirish.<br><b>Admin</b> — so\'rovnoma, nomzodlar, '
             'obunalar va statistika; adminlar bo\'limini ko\'rmaydi.</p>')
    body = (f'<div class=ph><div><h1>Adminlar</h1><div class=ph-meta>Panelga kira oladigan foydalanuvchilar</div>'
            f'</div></div><div class=split><section class=card>{table}</section><div class=stack>'
            f'<section class=card><div class=card-h><h2>Yangi admin</h2></div>{form}</section>'
            f'<section class=card>{roles}</section></div></div>')
    return page(body, title="Adminlar", active="shield")


async def admin_add(request: web.Request):
    _require_super(request)
    data = await request.post()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not USERNAME.match(username):
        _to("/admins", "baduser")
    if len(password) < auth.MIN_PASSWORD:
        _to("/admins", "shortpw")
    if not await db.add_admin(username, await auth.hash_password(password[:256]), data.get("role") == "super"):
        _to("/admins", "exists")
    _to("/admins", "ad_added")


async def admin_role(request: web.Request):
    _require_super(request)
    target = await db.admin_by_id(int(request.match_info["id"]))
    if not target or target[0] == request["admin"][0]:
        _to("/admins", "self")
    if target[3] and await db.admins_count(super_only=True) <= 1:
        _to("/admins", "lastsuper")
    await db.set_admin_role(target[0], not target[3])
    _to("/admins", "ad_saved")


async def admin_password(request: web.Request):
    _require_super(request)
    data = await request.post()
    password = data.get("password") or ""
    if len(password) < auth.MIN_PASSWORD:
        _to("/admins", "shortpw")
    aid = int(request.match_info["id"])
    await db.set_admin_password(aid, await auth.hash_password(password[:256]))
    resp = web.HTTPSeeOther("/admins?m=pw_changed")
    if aid == request["admin"][0]:  # o'z parolini o'zgartirsa — sessiyani yangilaymiz
        _set_session(resp, await db.admin_by_id(aid), True)
    raise resp


async def admin_delete(request: web.Request):
    _require_super(request)
    target = await db.admin_by_id(int(request.match_info["id"]))
    if not target or target[0] == request["admin"][0]:
        _to("/admins", "self")
    if target[3] and await db.admins_count(super_only=True) <= 1:
        _to("/admins", "lastsuper")
    await db.delete_admin(target[0])
    _to("/admins", "ad_deleted")


async def account_page(request: web.Request):
    me = request["admin"]
    body = (
        f'<div class=ph><div><h1>Profil</h1><div class=ph-meta>'
        f'<span>Login: <b>{e(me[1])}</b></span>{_role_pill(me[3])}</div></div></div>'
        f'<section class=card style="max-width:480px"><div class=card-h><h2>Parolni o\'zgartirish</h2></div>'
        f'<form method=post action=/account/password class=stack>'
        f'<label class=field>Joriy parol<input type=password name=current required autocomplete=current-password></label>'
        f'<label class=field>Yangi parol <span class=hint>kamida 8 belgi</span>'
        f'<input type=password name=password required minlength=8 autocomplete=new-password></label>'
        f'<label class=field>Yangi parol (takror)<input type=password name=password2 required minlength=8 '
        f'autocomplete=new-password></label>'
        f'<div><button class="btn primary">{icon("key")}Saqlash</button></div></form></section>'
    )
    return page(body, title="Profil", active="account")


async def account_password(request: web.Request):
    me = request["admin"]
    data = await request.post()
    password = data.get("password") or ""
    if not await auth.verify_password((data.get("current") or "")[:256], me[2]):
        _to("/account", "badpw")
    if len(password) < auth.MIN_PASSWORD:
        _to("/account", "shortpw")
    if password != data.get("password2"):
        _to("/account", "mismatch")
    await db.set_admin_password(me[0], await auth.hash_password(password[:256]))
    resp = web.HTTPSeeOther("/account?m=pw_changed")
    _set_session(resp, await db.admin_by_id(me[0]), True)
    raise resp


async def _load_secret(app):
    global _secret
    _secret = await auth.session_secret()


def make_app(bot) -> web.Application:
    app = web.Application(middlewares=[guard])
    app["bot"] = bot
    app.on_response_prepare.append(_security_headers)
    app.on_startup.append(_load_secret)
    app.add_routes([
        web.get("/login", login_page),
        web.post("/login", login_post),
        web.post("/logout", logout),
        web.get("/", dashboard),
        web.get("/polls", polls_page),
        web.get(r"/poll/{id:\d+}", poll_page),
        web.get("/users", users_page),
        web.post("/poll/new", new_poll),
        web.post("/candidate/add", add_candidate),
        web.post("/candidate/delete", delete_candidate),
        web.post("/publish", publish),
        web.post("/close", close),
        web.get("/api/live", api_live),
        web.get("/export.csv", export),
        web.get("/users.csv", export_users),
        web.get("/channels", channels_page),
        web.post("/channels/add", channel_add),
        web.post(r"/channels/{id:\d+}/toggle", channel_toggle),
        web.post(r"/channels/{id:\d+}/move", channel_move),
        web.post(r"/channels/{id:\d+}/delete", channel_delete),
        web.get("/admins", admins_page),
        web.post("/admins/add", admin_add),
        web.post(r"/admins/{id:\d+}/role", admin_role),
        web.post(r"/admins/{id:\d+}/password", admin_password),
        web.post(r"/admins/{id:\d+}/delete", admin_delete),
        web.get("/account", account_page),
        web.post("/account/password", account_password),
    ])
    return app
