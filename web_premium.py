"""Admin panel: Premium bo'limi (Telegram Premium konkursi).

web.py dagi make_app() oxirida setup(app) chaqiriladi; sahifa uslubi va yordamchilar web.py dan olinadi.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from aiohttp import web

import db
import web as panel
from services import channels, premium

e, icon, num, dt = panel.e, panel.icon, panel.num, panel.dt

FILTERS = {"all": "Hammasi", "joined": "Ishtirokchilar", "started": "Obuna bo'lmaganlar", "left": "Chiqib ketganlar"}
STATUS_TEXT = {"joined": "ishtirokchi", "started": "obuna bo'lmagan", "left": "chiqib ketgan"}
PER_PAGE = 50

STYLE = """<style>
.gw-form{display:grid;grid-template-columns:1fr 230px;gap:14px}
.gw-form .full{grid-column:1/-1}
@media (max-width:560px){.gw-form{grid-template-columns:1fr}}
.gw input[type=datetime-local]{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:9px;height:38px;padding:0 10px;width:100%}
.gw input[type=datetime-local]:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.gw .bubble{white-space:pre-wrap;word-break:break-word}
.gw .bubble img{width:100%;border-radius:8px;display:block;margin-bottom:8px}
.gw .seg a .muted{font-weight:400}
.win{display:flex;gap:14px;align-items:flex-start;padding:14px 0;border-top:1px solid var(--border)}
.win:first-child{border-top:0;padding-top:0}
.win-num{font-size:20px;font-weight:700;color:var(--accent);min-width:84px;font-variant-numeric:tabular-nums}
.win-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:8px}
.win-meta{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px;color:var(--muted)}
.win-meta b{color:var(--text)}
@media (max-width:560px){.win{flex-direction:column;gap:6px}}
</style>"""

# botga yuborish jarayonini sahifani yangilamasdan ko'rsatadi
PROGRESS_JS = """<script>(function(){var el=document.getElementById('pr-progress');
if(!el||el.dataset.running!=='1')return;
var t=setInterval(function(){fetch('/api/premium/progress?g='+el.dataset.g,{credentials:'same-origin'})
.then(function(r){return r.json()}).then(function(d){el.innerHTML=d.html;if(!d.running)clearInterval(t)})
.catch(function(){})},3000)})();</script>"""


def _to(path: str, m: str):
    raise web.HTTPSeeOther(f"{path}?m={m}")


def _parse_local(value) -> datetime | None:
    try:
        local = datetime.strptime(value or "", "%Y-%m-%dT%H:%M").replace(tzinfo=panel.TZ)
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _local_input(x: datetime) -> str:
    return x.astimezone(panel.TZ).strftime("%Y-%m-%dT%H:%M")


def _status_pill(status: str, is_winner: bool) -> str:
    if is_winner:
        return '<span class="pill accent plain">🏆 G\'olib</span>'
    return {
        "joined": '<span class="pill on">Ishtirokchi</span>',
        "started": '<span class="pill warn">Obuna bo\'lmagan</span>',
        "left": '<span class="pill bad">Chiqib ketgan</span>',
    }.get(status, e(status))


def _user_link(username) -> str:
    if not username:
        return '<span class=muted>—</span>'
    return f'<a href="https://t.me/{e(username)}" target=_blank rel=noopener>@{e(username)}</a>'


# --- formalar ---
def _fields(g=None) -> str:
    """Yangi konkurs va tahrirlash formasining umumiy maydonlari."""
    title = g["title"] if g else ""
    desc = g["description"] if g else ""
    button = g["button_text"] if g else premium.DEFAULT_BUTTON
    ends = _local_input(g["ends_at"] if g else datetime.now(timezone.utc) + timedelta(days=7))
    has_img = bool(g and g["has_image"])
    img = ""
    if has_img:
        img = (f'<div class=full><img src="/premium/image/{g["id"]}-{g["image_ver"]}.jpg" alt="Post rasmi" '
               f'style="max-width:320px;width:100%;border-radius:12px;display:block"></div>'
               f'<label class="check full"><input type=checkbox name=remove_image>Rasmni o\'chirish '
               f'<span class=muted>— allaqachon yuborilgan postlarda rasm qoladi, faqat matni yangilanadi</span></label>')
    img_label = "Rasmni almashtirish" if has_img else "Rasm"
    return (
        f'<label class=field>Sarlavha <span class=hint>panelda ko\'rinadi; post matni bo\'sh bo\'lsa postda ham</span>'
        f'<input type=text name=title required maxlength=200 value="{e(title)}" '
        f'placeholder="Masalan: Telegram Premium konkursi"></label>'
        f'<label class=field>Tugash vaqti <span class=hint>Toshkent vaqti</span>'
        f'<input type=datetime-local name=ends_at required value="{ends}"></label>'
        f'<label class="field full">Post matni <span class=hint>rasm bo\'lsa — 1024 belgigacha</span>'
        f'<textarea name=description rows=7 maxlength={1024 if has_img else 3500} '
        f'placeholder="Telegram Premium yutib oling! Shartlar: ...">{e(desc)}</textarea></label>{img}'
        f'<label class=field>{img_label} <span class=hint>JPG, PNG yoki WEBP, 10 MB gacha</span>'
        f'<input type=file name=image accept="image/jpeg,image/png,image/webp"></label>'
        f'<label class=field>Tugma matni<input type=text name=button_text maxlength=40 value="{e(button)}"></label>'
    )


def _new_form(has_active: bool) -> str:
    warn = ('<div class="alert warn full">Hozirgi faol konkurs yopiladi. Eski postdagi «Boshlash» tugmasi '
            'yangi konkursga olib boradi.</div>') if has_active else ""
    return (f'<form method=post action=/premium/new enctype=multipart/form-data class=gw-form>{_fields()}{warn}'
            f'<div class=full><button class="btn primary">{icon("plus")}Konkurs yaratish</button></div></form>')


# --- sahifa bloklari ---
async def _progress_html(gid: int) -> str:
    job = premium.last_job(gid)
    if job and job["running"]:
        return (f"⏳ Botga yuborilmoqda: <b>{num(job['done'])}</b> / {num(job['total'])} · "
                f"bloklagan {num(job['blocked'])}")
    delivered, failed = await db.gw_message_stats(gid)
    html_ = f"Botda yetkazilgan: <b>{num(delivered)}</b> · yuborib bo'lmagan {num(failed)}"
    if job and job["error"]:
        html_ += f'<div class="alert danger" style="margin-top:8px">{e(job["error"])}</div>'
    return html_


async def _publish_card(g) -> str:
    gid = g["id"]
    unsent = await db.gw_unsent_count(gid)
    posted = bool(g["message_id"])
    ch_note = "allaqachon joylangan, yana bitta yangi post chiqadi" if posted else "natijalar kanaliga"
    running = 1 if premium.sending(gid) else 0
    confirm = e("Post tanlangan joylarga yuborilsinmi?")
    return (
        f'<section class=card><div class=card-h><h2>Joylash</h2>'
        f'<span class="muted small">qayerga yuborishni tanlang</span></div>'
        f'<form method=post action="/premium/{gid}/publish" class=stack data-confirm="{confirm}">'
        f'<label class=check><input type=checkbox name=channel{"" if posted else " checked"}>'
        f'📢 Kanal <span class=muted>— {ch_note}</span></label>'
        f'<label class=check><input type=checkbox name=bot{" checked" if unsent else ""}>'
        f'🤖 Bot foydalanuvchilari <span class=muted>— yangi {num(unsent)} kishiga</span></label>'
        f'<div><button class="btn primary">{icon("send")}Joylash</button></div></form>'
        f'<div id=pr-progress class="muted small" style="margin-top:14px" data-g="{gid}" data-running="{running}">'
        f'{await _progress_html(gid)}</div></section>'
    )


async def _preview(g) -> str:
    img = f'<img src="/premium/image/{g["id"]}-{g["image_ver"]}.jpg" alt="">' if g["has_image"] else ""
    button = e(g["button_text"] or premium.DEFAULT_BUTTON)
    post = (f'<div class=preview><div class=bubble>{img}{premium.post_text(g)}</div>'
            f'<span class=tg-btn>{button}</span></div>')
    btns = "".join(f'<span class=tg-btn>{channels.emoji(k)} {e(t)}</span>' for _, k, t, *_ in await channels.active())
    invite = (f'<div class=preview><div class=bubble>{premium.INVITE_TEXT}</div>{btns}'
              f'<span class=tg-btn>✅ Tasdiqlash</span></div>')
    return (f'<section class=card><div class=card-h><h2>Telegramda ko\'rinishi</h2></div>'
            f'<div class="muted small" style="margin-bottom:6px">Post</div>{post}'
            f'<div class="muted small" style="margin:14px 0 6px">«Boshlash» bosilgach</div>{invite}</section>')


async def _winners_card(g, stats) -> str:
    gid = g["id"]
    winners = await db.gw_winners(gid)
    items = []
    for w in winners:
        sent = f' <span class="pill on">Yuborildi {dt(w["notified_at"])}</span>' if w["notified_at"] else ""
        gone = ' <span class="pill bad">Chiqib ketgan</span>' if w["status"] == "left" else ""
        phone = e(w["phone"]) if w["phone"] else "—"
        items.append(
            f'<div class=win><div class=win-num>#{w["number"]}</div><div class=win-main>'
            f'<div><b>{e(w["full_name"]) or "—"}</b>{sent}{gone}</div>'
            f'<div class=win-meta><span>{_user_link(w["username"])}</span>'
            f'<span>ID <b class=mono>{w["user_id"]}</b></span><span>Telefon <b>{phone}</b></span>'
            f'<span>Aniqlangan {dt(w["won_at"])}</span>'
            f'<a href="tg://user?id={w["user_id"]}">Telegramda ochish</a></div>'
            f'<form method=post action="/premium/{gid}/notify" class=stack>'
            f'<input type=hidden name=user_id value={w["user_id"]}>'
            f'<textarea name=text rows=4 maxlength=3500 aria-label="Tabrik matni">'
            f'{e(premium.win_text(w["number"]))}</textarea>'
            f'<div><button class="btn primary sm">{icon("send")}Bot orqali yuborish</button></div></form>'
            f'</div></div>'
        )
    eligible = stats["joined"] - sum(1 for w in winners if w["status"] == "joined")
    label = "🎲 Yana bir g'olib aniqlash" if winners else "🎲 G'olibni aniqlash"
    off = "" if eligible > 0 else " disabled aria-disabled=true"
    confirm = e("Tasodifiy g'olib aniqlansinmi?")
    listing = "".join(items) or _empty_winners()
    return (
        f'<section class=card id=win><div class=card-h><h2>🏆 G\'olibni aniqlash</h2>'
        f'<span class="muted small">{num(max(eligible, 0))} ishtirokchi orasidan · '
        f'tanlashda kanal obunasi jonli tekshiriladi</span></div>'
        f'{listing}<form method=post action="/premium/{gid}/draw" data-confirm="{confirm}" '
        f'style="margin-top:16px"><button class="btn primary"{off}>{label}</button></form></section>'
    )


def _empty_winners() -> str:
    return '<p class=muted style="margin:0">Hali g\'olib aniqlanmagan.</p>'


async def _participants(request: web.Request, g, stats) -> str:
    gid = g["id"]
    q = request.query.get("q", "").strip()[:64]
    f = request.query.get("f", "all")
    if f not in FILTERS:
        f = "all"
    try:
        p = max(1, int(request.query.get("p", "1")))
    except ValueError:
        p = 1
    rows, count = await db.gw_entries(gid, q, f, PER_PAGE, (p - 1) * PER_PAGE)
    base = request.path

    def url(**kw) -> str:
        params = {"q": q, "f": f, "p": p, **kw}
        params = {k: v for k, v in params.items() if v and (k, v) not in (("f", "all"), ("p", 1))}
        return base + ("?" + urlencode(params) if params else "") + "#people"

    counts = {"all": stats["started"], "joined": stats["joined"], "started": stats["pending"], "left": stats["left_n"]}
    seg = "".join(
        f'<a href="{e(url(f=k, p=1))}" class="{"on" if k == f else ""}">{v} <span class=muted>{num(counts[k])}</span></a>'
        for k, v in FILTERS.items()
    )
    toolbar = (f'<form class=toolbar method=get action="{e(base)}">'
               f'<label class=search>{icon("search")}<input type=search name=q value="{e(q)}" '
               f'placeholder="Ism, username, ID, telefon yoki #raqam" aria-label="Qidirish"></label>'
               f'<input type=hidden name=f value="{f}"><nav class=seg aria-label="Filtr">{seg}</nav>'
               f'<a class="btn outline" href="/premium/{gid}/export.csv">{icon("download")}CSV</a></form>')
    if rows:
        trs = []
        for r in rows:
            number = f"#{r['number']}" if r["number"] else '<span class=muted>—</span>'
            name = e(r["full_name"]) if r["full_name"] else '<span class=muted>—</span>'
            phone = e(r["phone"]) if r["phone"] else '<span class=muted>—</span>'
            trs.append(f'<tr><td class="mono nowrap">{number}</td><td>{name}</td><td>{_user_link(r["username"])}</td>'
                       f'<td class=mono>{r["user_id"]}</td><td class=nowrap>{phone}</td>'
                       f'<td>{_status_pill(r["status"], r["is_winner"])}</td><td class=nowrap>{dt(r["at"])}</td></tr>')
        first, last_n = (p - 1) * PER_PAGE + 1, (p - 1) * PER_PAGE + len(rows)
        prev = (f'<a class="btn outline sm" href="{e(url(p=p - 1))}">← Oldingi</a>' if p > 1
                else '<span class="btn outline sm" aria-disabled=true>← Oldingi</span>')
        nxt = (f'<a class="btn outline sm" href="{e(url(p=p + 1))}">Keyingi →</a>' if last_n < count
               else '<span class="btn outline sm" aria-disabled=true>Keyingi →</span>')
        content = (f'<div class=table-wrap><table><thead><tr><th>№</th><th>Ism</th><th>Username</th>'
                   f'<th>Telegram ID</th><th>Telefon</th><th>Holat</th><th>Vaqt</th></tr></thead>'
                   f'<tbody>{"".join(trs)}</tbody></table></div>'
                   f'<div class=pager><span>{num(first)}–{num(last_n)} / {num(count)}</span>'
                   f'<div class=actions>{prev}{nxt}</div></div>')
    else:
        content = panel._empty("Hech narsa topilmadi." if q or f != "all" else "Hali hech kim qatnashmagan.", "users")
    return (f'<section class="card section" id=people><div class=card-h><h2>Ishtirokchilar</h2>'
            f'<span class="muted small">«Obuna bo\'lmagan» — Boshlashni bosgan, lekin tasdiqlamagan</span></div>'
            f'{toolbar}{content}</section>')


async def _history(current_id: int) -> str:
    rows = await db.gw_list()
    if len(rows) < 2:
        return ""
    on, off = '<span class="pill on">Faol</span>', '<span class="pill off">Yopilgan</span>'
    here = ' <span class="muted small">· ochiq</span>'
    trs = "".join(
        f'<tr><td><a class=row-link href="/premium/{r["id"]}">{e(r["title"])}</a>'
        f'{here if r["id"] == current_id else ""}'
        f'<div class="muted small">#{r["id"]}</div></td><td>{on if r["is_active"] else off}</td>'
        f'<td class=nowrap>{dt(r["created_at"])}<div class="muted small">→ {dt(r["ends_at"])}</div></td>'
        f'<td class=num>{num(r["joined"])}</td></tr>'
        for r in rows
    )
    return (f'<section class="card section"><div class=card-h><h2>Barcha konkurslar</h2>'
            f'<span class="muted small">{len(rows)} ta</span></div><div class=table-wrap><table><thead><tr>'
            f'<th>Konkurs</th><th>Holat</th><th>Muddat</th><th class=num>Ishtirokchilar</th></tr></thead>'
            f'<tbody>{trs}</tbody></table></div></section>')


async def _view(request: web.Request, g) -> str:
    bot = request.app["bot"]
    me = await bot.me()
    gid, active, ended = g["id"], g["is_active"], premium.is_ended(g)
    stats = await db.gw_stats(gid, panel._today_start())

    if active and not ended:
        status = '<span class="pill on">Faol</span>'
    elif ended:
        status = '<span class="pill off">Muddat tugagan</span>'
    else:
        status = '<span class="pill off">Yopilgan</span>'
    left = None if ended else panel.time_left(g["ends_at"])
    meta = [f"Boshlangan <b>{dt(g['created_at'])}</b>",
            f"Tugash <b>{dt(g['ends_at'])}</b>" + (f" · {left} qoldi" if left else ""),
            "Kanal posti: " + ("<b>joylangan</b>" if g["message_id"] else "<b>joylanmagan</b>")]
    actions = []
    if active:
        actions.append(f'<a class="btn outline" href="{e(premium.deep_link(me.username))}" target=_blank '
                       f'rel=noopener data-tip="Konkursni botda o\'zingiz sinab ko\'ring">{icon("send")}Botda ochish</a>')
    if active and not ended:
        confirm = e("Konkurs muddati hozir tugatilsinmi? Yangi ishtirokchilar qabul qilinmaydi.")
        actions.append(f'<form method=post action="/premium/{gid}/end" data-confirm="{confirm}">'
                       f'<button class="btn danger">{icon("stop")}Hozir yakunlash</button></form>')
    actions.append(f'<a class="btn outline" href="/premium/{gid}/export.csv">{icon("download")}CSV</a>')
    back = "" if active else f'<a class=back href="/premium">{icon("back")}Premium</a>'
    head = (f'{back}<div class=ph><div><div class=ph-title><h1>{e(g["title"])}</h1>{status}</div>'
            f'<div class=ph-meta>{"".join(f"<span>{m}</span>" for m in meta)}</div></div>'
            f'<div class=actions>{"".join(actions)}</div></div>')

    joined_all = stats["joined"] + stats["left_n"]
    tiles = ('<div class=tiles>'
             + panel._tile("Boshlaganlar", num(stats["started"]), "«Boshlash»ni bosganlar")
             + panel._tile("Ishtirokchilar", num(stats["joined"]), f"chiqib ketgan: {num(stats['left_n'])}")
             + panel._tile("Bugun qo'shilgan", num(stats["today"]), "soat 00:00 dan beri")
             + panel._tile("Telefon ulashganlar", num(stats["phones"]),
                           f"{stats['phones'] / joined_all * 100:.0f}% ishtirokchilardan" if joined_all else "ixtiyoriy")
             + '</div>')

    winners = await _winners_card(g, stats) if ended else ""
    if active:
        post_card = (f'<section class=card><div class=card-h><h2>Post</h2>'
                     f'<span class="muted small">saqlansa kanal va botdagi nusxalar ham yangilanadi</span></div>'
                     f'<form method=post action="/premium/{gid}/save" enctype=multipart/form-data class=gw-form>'
                     f'{_fields(g)}<div class=full><button class="btn primary">Saqlash</button></div></form></section>')
        main = (f'<div class="split section"><div class=stack>{post_card}{await _publish_card(g)}</div>'
                f'<div class=stack>{await _preview(g)}</div></div>')
    else:
        main = f'<div class=section>{await _preview(g)}</div>'

    new = (f'<details class="card section" id=new><summary>{icon("plus")}Yangi konkurs{icon("chev", "chev")}'
           f'</summary>{_new_form(bool(await db.gw_active()))}</details>')
    return (STYLE + '<div class=gw>' + head + tiles + winners + main
            + await _participants(request, g, stats) + await _history(gid) + new + '</div>' + PROGRESS_JS)


# --- sahifalar ---
async def premium_page(request: web.Request):
    gid = request.match_info.get("id")
    g = await db.gw_get(int(gid)) if gid else await db.gw_last()
    if gid and not g:
        raise web.HTTPNotFound()
    if not g:
        body = (STYLE + '<div class=gw><div class=ph><div><h1>Premium</h1><div class=ph-meta>Telegram Premium '
                'konkursi: foydalanuvchi obuna bo\'lib, ishtirok raqamini oladi. Birinchi konkursni yarating.'
                '</div></div></div><section class=card id=new><div class=card-h><h2>Yangi konkurs</h2></div>'
                f'{_new_form(False)}</section></div>')
        return panel.page(body, title="Premium", active="gift")
    return panel.page(await _view(request, g), title="Premium", active="gift")


# --- amallar ---
async def _read_form(request: web.Request):
    data = await request.post()
    title = (data.get("title") or "").strip()[:200]
    desc = (data.get("description") or "").strip()[:3500]
    button = (data.get("button_text") or "").strip()[:40] or premium.DEFAULT_BUTTON
    ends = _parse_local(data.get("ends_at"))
    upload = data.get("image")
    raw = upload.file.read() if getattr(upload, "file", None) else b""
    return title, desc, button, ends, raw, bool(data.get("remove_image"))


async def _jpeg(raw: bytes, back: str) -> bytes:
    try:
        return await asyncio.to_thread(panel._to_jpeg, raw)
    except Exception:
        _to(back, "badimg")


async def _editable(request: web.Request):
    """(id, konkurs, qaytish manzili); konkurs yopilgan bo'lsa — xabar bilan qaytariladi."""
    gid = int(request.match_info["id"])
    back = f"/premium/{gid}"
    g = await db.gw_get(gid)
    if not g:
        raise web.HTTPNotFound()
    if not g["is_active"]:
        _to(back, "pr_closed")
    return gid, g, back


async def gw_new(request: web.Request):
    title, desc, button, ends, raw, _ = await _read_form(request)
    if not title:
        _to("/premium", "notitle")
    if not ends or ends <= datetime.now(timezone.utc):
        _to("/premium", "badtime")
    if raw and len(desc) > 1024:
        _to("/premium", "toolong")
    jpeg = await _jpeg(raw, "/premium") if raw else None
    gid = await db.gw_create(title, desc, button, ends)
    if jpeg:
        await db.gw_set_image(gid, jpeg)
    _to(f"/premium/{gid}", "pr_created")


async def gw_save(request: web.Request):
    gid, g, back = await _editable(request)
    title, desc, button, ends, raw, remove = await _read_form(request)
    if not title:
        _to(back, "notitle")
    if not ends:
        _to(back, "badtime")
    if (raw or (g["has_image"] and not remove)) and len(desc) > 1024:
        _to(back, "toolong")
    jpeg = await _jpeg(raw, back) if raw else None
    await db.gw_update(gid, title, desc, button, ends)
    if jpeg or (remove and g["has_image"]):
        await db.gw_set_image(gid, jpeg)
    premium.refresh(request.app["bot"], gid, media=bool(jpeg))
    _to(back, "pr_saved")


async def gw_publish(request: web.Request):
    gid, g, back = await _editable(request)
    data = await request.post()
    to_channel, to_bot = bool(data.get("channel")), bool(data.get("bot"))
    if not (to_channel or to_bot):
        _to(back, "pr_nowhere")
    bot = request.app["bot"]
    if to_channel:
        try:
            await premium.publish_channel(bot, gid)
        except Exception as ex:
            logging.exception("premium kanalga joylash")
            body = (f'<div class=ph><div><h1>Kanalga joylab bo\'lmadi</h1></div></div><section class="card stack">'
                    f'<div class="alert danger">{e(str(ex))}</div>'
                    f'<p class=muted style="margin:0">Bot natijalar kanalida admin ekanini va .env dagi '
                    f'<b>RESULTS_CHANNEL</b> to\'g\'ri kanal ekanini tekshiring.</p>'
                    f'<div><a class="btn outline" href="{back}">{icon("back")}Orqaga</a></div></section>')
            return panel.page(body, title="Xatolik", active="gift", status=502)
    if to_bot and not await premium.broadcast(bot, gid):
        _to(back, "pr_running")
    _to(back, "pr_published")


async def gw_end(request: web.Request):
    gid, g, back = await _editable(request)
    if not premium.is_ended(g):
        await db.gw_set_ends(gid, datetime.now(timezone.utc))
    _to(back, "pr_ended")


async def gw_draw(request: web.Request):
    gid = int(request.match_info["id"])
    back = f"/premium/{gid}"
    g = await db.gw_get(gid)
    if not g:
        raise web.HTTPNotFound()
    if not premium.is_ended(g):
        _to(back, "pr_notended")
    _, err = await premium.draw(request.app["bot"], gid)
    _to(back, {"nobody": "pr_nobody", "tgerr": "pr_tgerr"}.get(err, "pr_winner"))


async def gw_notify(request: web.Request):
    gid = int(request.match_info["id"])
    back = f"/premium/{gid}"
    data = await request.post()
    text = (data.get("text") or "").strip()[:3500]
    try:
        uid = int(data.get("user_id", ""))
    except ValueError:
        uid = None
    if not text:
        _to(back, "pr_notext")
    entry = await db.gw_entry(gid, uid) if uid else None
    if not entry or not entry["is_winner"]:
        _to(back, "pr_senderr")
    err = await premium.notify(request.app["bot"], gid, uid, text)
    _to(back, {"blocked": "pr_blocked", "error": "pr_senderr"}.get(err, "pr_sent"))


async def gw_export(request: web.Request):
    gid = int(request.match_info["id"])
    rows, _ = await db.gw_entries(gid, limit=10_000_000)
    return panel._csv(
        f"premium-{gid}.csv",
        ["number", "full_name", "username", "user_id", "phone", "status", "winner", "time"],
        [(r["number"] or "", r["full_name"], r["username"] or "", r["user_id"], r["phone"] or "",
          STATUS_TEXT.get(r["status"], r["status"]), "ha" if r["is_winner"] else "", dt(r["at"])) for r in rows],
    )


async def gw_image(request: web.Request):
    data = await db.gw_get_image(int(request.match_info["id"]), int(request.match_info["ver"]))
    if not data:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/jpeg")


async def api_progress(request: web.Request):
    try:
        gid = int(request.query.get("g", ""))
    except ValueError:
        raise web.HTTPBadRequest()
    return web.json_response({"html": await _progress_html(gid), "running": bool(premium.sending(gid))})


def setup(app: web.Application):
    app.add_routes([
        web.get("/premium", premium_page),
        web.get(r"/premium/{id:\d+}", premium_page),
        web.post("/premium/new", gw_new),
        web.post(r"/premium/{id:\d+}/save", gw_save),
        web.post(r"/premium/{id:\d+}/publish", gw_publish),
        web.post(r"/premium/{id:\d+}/end", gw_end),
        web.post(r"/premium/{id:\d+}/draw", gw_draw),
        web.post(r"/premium/{id:\d+}/notify", gw_notify),
        web.get(r"/premium/{id:\d+}/export.csv", gw_export),
        web.get(r"/premium/image/{id:\d+}-{ver:\d+}.jpg", gw_image),
        web.get("/api/premium/progress", api_progress),
    ])
