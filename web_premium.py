"""Admin panel: Premium bo'limi (Telegram Premium konkursi).

web.py dagi make_app() oxirida setup(app) chaqiriladi; sahifa uslubi va yordamchilar web.py dan olinadi.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from aiohttp import web

import db
import web as panel
from config import RESULTS_CHANNEL
from services import ads, channels, premium, targets

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
.tgt{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:7px 0;border-top:1px solid var(--border)}
.tgt:first-child{border-top:0}
.tgt.off{opacity:.55;cursor:not-allowed}
.tgt-name{flex:1;min-width:160px;color:var(--text);font-weight:550}
.tgt-name .muted{font-weight:400}
.tgt-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.tgt-add>summary{list-style:none;width:max-content}
.tgt-add>summary::-webkit-details-marker{display:none}
.tgt-form{display:flex;gap:8px;flex-wrap:wrap}
.tgt-form input[type=text]{flex:1;min-width:180px}
.alert.good{background:var(--good-soft);color:var(--good)}
.gw [hidden],.ad [hidden]{display:none!important}
.aud{display:grid;grid-template-columns:1fr 130px;gap:10px}
@media (max-width:560px){.aud{grid-template-columns:1fr}}
.aud-count{font-weight:650;font-size:15px}
.test-row{display:flex;gap:8px;flex-wrap:wrap}
.test-row input[type=text]{flex:1;min-width:min(240px,100%);height:44px;font-size:15px}
.test-row .btn{height:44px;padding:0 18px}
.gw .bubble{font-size:14.5px}
</style>"""

# --- joylash joylari: Premium va Reklama sahifalari birga ishlatadi ---
_ROLE_PILL = {"admin": '<span class="pill on">Bot admin</span>', "member": '<span class="pill on">Bot a\'zo</span>'}
_STATE_PILL = {"noadmin": '<span class="pill bad">Bot admin emas</span>', "missing": '<span class="pill bad">Topilmadi</span>'}
_SOURCE = {"results": "natijalar kanali", "obuna": "Obunalar", "added": "qo'shilgan"}
# oxirgi joylash natijasi: giveaway_id -> [(nomi, xato yoki None)]; sahifada bir marta ko'rsatiladi
_last_result: dict[int, list] = {}


def target_checks(items: list[dict], checked=(), posted=(), name: str = "t") -> str:
    """Kanal/guruhlar ro'yxati checkbox ko'rinishida; bot yoza olmaydiganlari o'chiq."""
    if not items:
        return '<p class="muted small" style="margin:0">Hali kanal yoki guruh yo\'q — «Joy qo\'shish» orqali qo\'shing.</p>'
    checked = {c.lower() for c in checked}
    posted = {c.lower() for c in posted}
    out = []
    for i in items:
        key, ok = i["chat_id"].lower(), i["state"] == "ok"
        pill = _ROLE_PILL.get(i["role"]) if ok else _STATE_PILL.get(i["state"], "")
        done = ' <span class="pill accent plain">joylangan</span>' if key in posted else ""
        attrs = (" checked" if ok and key in checked else "") + ("" if ok else " disabled")
        out.append(
            f'<label class="check tgt{"" if ok else " off"}"><input type=checkbox name={name} '
            f'value="{e(i["chat_id"])}"{attrs}><span class=tgt-name>{"👥" if i["group"] else "📢"} {e(i["title"])} '
            f'<span class="muted small">{e(i["chat_id"])} · {_SOURCE.get(i["source"], "")}</span></span>{pill}{done}</label>'
        )
    return "<div>" + "".join(out) + "</div>"


def audience_block(*, api: str, kind: str = "all", days: int = 7, users_raw: str = "", count: int = 0,
                   unknown=(), to_bot: bool = True, note: str = "") -> str:
    """«Kimga»: bot foydalanuvchilari va auditoriya (Premium va Reklama birga ishlatadi). Soni api dan olinadi."""
    opts = "".join(f'<option value="{k}"{" selected" if k == kind else ""}>{e(v)}</option>'
                   for k, v in ads.AUDIENCES.items())
    unknown_note = e("Botda topilmadi, ularga yuborilmaydi: " + ", ".join(unknown)) if unknown else ""
    return (
        f'<div class=stack id=aud data-api="{e(api)}">'
        f'<label class=check><input type=checkbox name=to_bot id=aud-tobot{" checked" if to_bot else ""}>'
        f'🤖 Bot foydalanuvchilari{note}</label>'
        f'<div class=aud id=aud-box><label class=field>Auditoriya<select name=audience id=aud-kind>{opts}</select>'
        f'</label><label class=field id=aud-daysbox>Necha kun<input type=number name=audience_days id=aud-days '
        f'min=1 max=365 value={int(days)}></label></div>'
        f'<label class=field id=aud-usersbox>Aniq akkauntlar <span class=hint>har qatorda Telegram ID yoki @username; '
        f'faqat botga /start bosganlarga yuborish mumkin</span><textarea name=audience_users id=aud-users rows=3 '
        f'maxlength=5000 placeholder="123456789&#10;@username">{e(users_raw)}</textarea></label>'
        f'<div class=aud-count id=aud-count data-n={int(count)}>👥 {num(count)} kishi</div>'
        f'<div class="alert warn" id=aud-unknown{"" if unknown else " hidden"}>{unknown_note}</div></div>'
    )


AUDIENCE_JS = r"""<script>(function(){var box=document.getElementById('aud');if(!box)return;
function $(i){return document.getElementById(i)}
var tb=$('aud-tobot'),kind=$('aud-kind'),days=$('aud-days'),us=$('aud-users'),ac=$('aud-count'),unk=$('aud-unknown'),
  tot=$('aud-total'),form=box.closest('form')||document,timer,n=+ac.dataset.n||0;
function vis(){$('aud-box').hidden=!tb.checked;$('aud-daysbox').hidden=kind.value!=='new';
  $('aud-usersbox').hidden=!tb.checked||kind.value!=='users';ac.hidden=!tb.checked;if(!tb.checked)unk.hidden=true}
function total(){if(!tot)return;var k=form.querySelectorAll('input[name=t]:checked').length,m=tb.checked?n:0;
  tot.textContent='Jami: '+m.toLocaleString('ru')+' kishi'+(k?' + '+k+' kanal/guruh':'')}
function count(){clearTimeout(timer);timer=setTimeout(function(){if(!tb.checked){total();return}
  var d=new FormData();d.append('audience',kind.value);d.append('days',days.value);d.append('users',us.value);
  fetch(box.dataset.api,{method:'POST',body:d,credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){
    n=j.count;ac.textContent='👥 '+n.toLocaleString('ru')+' kishi';
    if(j.unknown&&j.unknown.length){unk.hidden=false;unk.textContent='Botda topilmadi, ularga yuborilmaydi: '+j.unknown.join(', ')}
    else{unk.hidden=true}total()}).catch(function(){})},350)}
[tb,kind,days].forEach(function(el){el.addEventListener('change',function(){vis();count()})});
us.addEventListener('input',count);days.addEventListener('input',count);
form.querySelectorAll('input[name=t]').forEach(function(c){c.addEventListener('change',total)});
vis();total();})();</script>"""


def test_box(value: str, button: str = "", note: str = "") -> str:
    """Sinov uchun akkaunt maydoni va tugmasi (formaning ichida yoki alohida)."""
    button = button or '<button class="btn primary">🧪 Menga yuborish</button>'
    return (f'<p class="muted small" style="margin:0">O\'zingizning Telegram ID yoki @username kiriting. U akkaunt botga '
            f'/start bosgan bo\'lishi kerak. Sinov statistikaga kirmaydi.{note}</p>'
            f'<div class=test-row><input type=text name=test_chat maxlength=64 value="{e(value)}" '
            f'placeholder="123456789 yoki @username" aria-label="Sinov akkaunti">{button}</div>')


def target_manage(items: list[dict], back: str) -> str:
    """«Joy qo'shish»: qo'lda qo'shilgan kanal/guruhlar ro'yxati va qo'shish formasi."""
    rows = "".join(
        f'<div class=tgt-row><span>{e(i["title"])} <span class="muted small">{e(i["chat_id"])}</span></span>'
        f'<form method=post action="/targets/{i["id"]}/delete" data-confirm="{e("«" + i["title"] + "» ro‘yxatdan olinsinmi?")}">'
        f'<input type=hidden name=back value="{e(back)}"><button class="btn ghost icon-only sm" aria-label="O\'chirish" '
        f'title="O\'chirish">{icon("trash")}</button></form></div>'
        for i in items if i["source"] == "added"
    )
    form = (f'<form method=post action=/targets/add class=tgt-form><input type=hidden name=back value="{e(back)}">'
            f'<input type=text name=chat_id required maxlength=80 placeholder="@kanal, -100… yoki t.me/…" '
            f'aria-label="Kanal yoki guruh"><button class="btn primary">{icon("plus")}Qo\'shish</button></form>')
    return (f'<details class=tgt-add><summary class="btn ghost sm">{icon("plus")}Joy qo\'shish</summary>'
            f'<div class=stack style="margin-top:10px"><p class="muted small" style="margin:0">Botni avval kanalga '
            f'<b>admin</b> qiling yoki guruhga qo\'shing. Nomi avtomatik olinadi.</p>{rows}{form}</div></details>')


# botga yuborish jarayonini sahifani yangilamasdan ko'rsatadi
PROGRESS_JS = """<script>(function(){var el=document.getElementById('pr-progress');
if(!el||el.dataset.running!=='1')return;
var t=setInterval(function(){fetch('/api/premium/progress?g='+el.dataset.g,{credentials:'same-origin'})
.then(function(r){return r.json()}).then(function(d){el.innerHTML=d.html;if(!d.running)clearInterval(t)})
.catch(function(){})},3000)})();</script>"""


# post matni yozilayotganda «Telegramda ko'rinishi» darhol yangilanadi
PREVIEW_JS = r"""<script>(function(){var d=document.getElementById('gw-desc'),pt=document.getElementById('gw-pv-text');
if(!d||!pt)return;var t=document.getElementById('gw-title'),b=document.getElementById('gw-btn'),pb=document.getElementById('gw-pv-btn');
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function upd(){var v=d.value.trim();pt.innerHTML=v?esc(v):'🎁 <b>'+esc(t.value.trim())+'</b>';
  if(pb)pb.textContent=b.value.trim()||'🚀 Boshlash'}
[d,t,b].forEach(function(el){if(el)['input','keyup','change','paste'].forEach(function(ev){
  el.addEventListener(ev,function(){setTimeout(upd,0)})})});})();</script>"""


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

    def idx(name: str) -> str:  # id faqat tahrirlash formasida (yangi konkurs formasi ham shu sahifada)
        return f" id={name}" if g else ""

    return (
        f'<label class=field>Sarlavha <span class=hint>panelda ko\'rinadi; post matni bo\'sh bo\'lsa postda ham</span>'
        f'<input type=text name=title{idx("gw-title")} required maxlength=200 value="{e(title)}" '
        f'placeholder="Masalan: Telegram Premium konkursi"></label>'
        f'<label class=field>Tugash vaqti <span class=hint>Toshkent vaqti</span>'
        f'<input type=datetime-local name=ends_at required value="{ends}"></label>'
        f'<label class="field full">Post matni <span class=hint>rasm bo\'lsa — 1024 belgigacha</span>'
        f'<textarea name=description{idx("gw-desc")} rows=9 maxlength={1024 if has_img else 3500} '
        f'placeholder="Telegram Premium yutib oling! Shartlar: ...">{e(desc)}</textarea></label>{img}'
        f'<label class=field>{img_label} <span class=hint>JPG, PNG yoki WEBP, 10 MB gacha</span>'
        f'<input type=file name=image accept="image/jpeg,image/png,image/webp"></label>'
        f'<label class=field>Tugma matni<input type=text name=button_text{idx("gw-btn")} maxlength=40 '
        f'value="{e(button)}"></label>'
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


def _result_html(gid: int) -> str:
    res = _last_result.pop(gid, None)
    if not res:
        return ""
    ok = sum(1 for _, err in res if not err)
    errors = "".join(f"<div>{e(title)}: {e(err)}</div>" for title, err in res if err)
    html_ = f'<div class="alert {"warn" if errors else "good"}" style="margin-top:12px">✅ {ok} joyga joylandi'
    if errors:
        html_ += f", {len(res) - ok} ta xato:{errors}"
    return html_ + "</div>"


async def _publish_card(bot, g) -> str:
    gid = g["id"]
    unsent = await db.gw_audience_unsent_count(gid, "all")
    items = await targets.available(bot)
    posted = [c for c, _ in await premium.posts(g)]
    # birinchi joylashda natijalar kanali oldindan belgilanadi
    checked = [] if posted else [RESULTS_CHANNEL]
    running = 1 if premium.sending(gid) else 0
    confirm = e("Post tanlangan joylarga yuborilsinmi? Avval joylangan joyga yana bitta yangi post chiqadi.")
    audience = audience_block(api=f"/api/premium/audience?g={gid}", count=unsent, to_bot=bool(unsent),
                              note=" <span class=muted>— avval olganlarga qayta yuborilmaydi</span>")
    return (
        f'<section class=card><div class=card-h><h2>Kimga joylash</h2>'
        f'<span class="muted small" id=aud-total></span></div>'
        f'<form method=post action="/premium/{gid}/publish" class=stack data-confirm="{confirm}">{audience}'
        f'<div><div class="muted small" style="margin-bottom:2px">Kanal va guruhlar</div>'
        f'{target_checks(items, checked, posted)}</div>'
        f'<div><button class="btn primary">{icon("send")}Joylash</button></div></form>'
        f'{_result_html(gid)}'
        f'<div id=pr-progress class="muted small" style="margin-top:14px" data-g="{gid}" data-running="{running}">'
        f'{await _progress_html(gid)}</div>'
        f'<div style="margin-top:12px">{target_manage(items, f"/premium/{gid}")}</div></section>'
    )


def _test_card(gid: int, value: str) -> str:
    return (f'<section class=card><div class=card-h><h2>🧪 Sinov uchun yuborish</h2>'
            f'<span class="muted small">saqlangan post botdagi ko\'rinishda keladi</span></div>'
            f'<form method=post action="/premium/{gid}/test" class=stack>'
            f'{test_box(value, note=" «🚀 Boshlash»ni bosib, konkursni o‘zingiz sinab ko‘rishingiz mumkin.")}'
            f'</form></section>')


async def _preview(g) -> str:
    img = f'<img src="/premium/image/{g["id"]}-{g["image_ver"]}.jpg" alt="">' if g["has_image"] else ""
    button = e(g["button_text"] or premium.DEFAULT_BUTTON)
    post = (f'<div class=preview><div class=bubble>{img}<span id=gw-pv-text>{premium.post_text(g)}</span></div>'
            f'<span class=tg-btn id=gw-pv-btn>{button}</span></div>')
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
    n_posts = len(await premium.posts(g))
    meta = [f"Boshlangan <b>{dt(g['created_at'])}</b>",
            f"Tugash <b>{dt(g['ends_at'])}</b>" + (f" · {left} qoldi" if left else ""),
            "Joylangan: " + (f"<b>{n_posts} joyda</b>" if n_posts else "<b>hali joylanmagan</b>")]
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
        test_chat = await db.get_setting(f"ads_test:{request['admin'][0]}") or ""
        main = (f'<div class="split section"><div class=stack>{post_card}{await _publish_card(bot, g)}'
                f'{_test_card(gid, test_chat)}</div><div class=stack>{await _preview(g)}</div></div>')
    else:
        main = f'<div class=section>{await _preview(g)}</div>'

    new = (f'<details class="card section" id=new><summary>{icon("plus")}Yangi konkurs{icon("chev", "chev")}'
           f'</summary>{_new_form(bool(await db.gw_active()))}</details>')
    return (STYLE + '<div class=gw>' + head + tiles + winners + main
            + await _participants(request, g, stats) + await _history(gid) + new + '</div>'
            + PROGRESS_JS + PREVIEW_JS + AUDIENCE_JS)


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
    bot = request.app["bot"]
    items = await targets.available(bot)
    dest = targets.pick(items, data.getall("t", []))
    to_bot = bool(data.get("to_bot"))
    if not (dest or to_bot):
        _to(back, "pr_nowhere")
    users = []
    if to_bot:
        kind, days, ids = await _audience_args(data)
        users = await db.gw_audience_unsent(gid, kind, days, ids)
        if not users and not dest:
            _to(back, "pr_nousers")
    if dest:
        titles = {i["chat_id"]: i["title"] for i in items}
        _last_result[gid] = [(titles.get(c, c), err) for c, err in await premium.publish_to(bot, gid, dest)]
    if users and not await premium.broadcast(bot, gid, users):
        _to(back, "pr_running")
    _to(back, "pr_published")


async def _audience_args(data):
    """Formadagi auditoriya: (turi, N kun, aniq akkauntlar ID lari)."""
    kind = data.get("audience") if data.get("audience") in ads.AUDIENCES else "all"
    try:
        days = max(1, min(365, int(data.get("audience_days") or data.get("days") or 7)))
    except ValueError:
        days = 7
    ids = []
    if kind == "users":
        ids, _ = await ads.resolve_accounts((data.get("audience_users") or data.get("users") or "")[:5000])
    return kind, days, ids


async def gw_test(request: web.Request):
    gid, g, back = await _editable(request)
    data = await request.post()
    chat = (data.get("test_chat") or "").strip()[:64]
    await db.set_setting(f"ads_test:{request['admin'][0]}", chat)
    found, _ = await ads.resolve_accounts(chat)
    if not found:
        _to(back, "ad_testbad")
    try:
        await premium.send_post(request.app["bot"], found[0], g, record=False)
        ok = True
    except Exception as ex:
        logging.warning("premium sinov %s: %s", found[0], ex)
        ok = False
    _to(back, "pr_testsent" if ok else "pr_testfail")


async def api_audience(request: web.Request):
    try:
        gid = int(request.query.get("g", ""))
    except ValueError:
        raise web.HTTPBadRequest()
    data = await request.post()
    kind, days, ids = await _audience_args(data)
    unknown = []
    if kind == "users":
        _, unknown = await ads.resolve_accounts((data.get("users") or "")[:5000])
    count = await db.gw_audience_unsent_count(gid, kind, days, ids)
    return web.json_response({"count": count, "unknown": unknown[:20]})


def _safe_back(value) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"/(premium|ads)(/\d+)?", value) else "/premium"


async def target_add(request: web.Request):
    data = await request.post()
    back = _safe_back(data.get("back"))
    chat_id = targets.normalize(data.get("chat_id"))
    if not chat_id:
        _to(back, "tg_bad")
    info = await targets.check(request.app["bot"], chat_id, fresh=True)
    if info["state"] == "missing":
        _to(back, "tg_missing")
    await db.target_add(chat_id, (info["title"] or chat_id)[:64])
    _to(back, "tg_added" if info["state"] == "ok" else "tg_noadmin")


async def target_delete(request: web.Request):
    data = await request.post()
    await db.target_delete(int(request.match_info["id"]))
    _to(_safe_back(data.get("back")), "tg_deleted")


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
        web.post(r"/premium/{id:\d+}/test", gw_test),
        web.post("/api/premium/audience", api_audience),
        web.post("/targets/add", target_add),
        web.post(r"/targets/{id:\d+}/delete", target_delete),
    ])
