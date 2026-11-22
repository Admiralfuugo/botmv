"""Admin panel: Reklama bo'limi — bot foydalanuvchilariga, aniq akkauntlarga va kanal/guruhlarga reklama.

web.py dagi make_app() oxirida setup(app) chaqiriladi: yo'nalishlar va rejalashtiruvchi fon vazifasi ulanadi.
"""
import asyncio
import html
import mimetypes
from datetime import datetime, timezone

from aiohttp import web

import db
import web as panel
import web_premium as wp
from services import ads, targets

e, icon, num, dt = panel.e, panel.icon, panel.num, panel.dt

STATUS = {
    "draft": ("off", "Qoralama"), "scheduled": ("warn", "Rejalashtirilgan"), "sending": ("accent", "Yuborilmoqda"),
    "paused": ("warn", "Pauza"), "done": ("on", "Yuborildi"), "stopped": ("off", "To'xtatildi"),
    "deleted": ("off", "O'chirildi"),
}
EDITABLE = ("draft", "scheduled")

STYLE = """<style>
.ad-grid{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;align-items:start;
  grid-template-areas:"content pv" "who pv" "set pv" "test pv" "act pv" "manage pv"}
.ad-form{display:contents}
.a-content{grid-area:content}.a-who{grid-area:who}.a-set{grid-area:set}.a-test{grid-area:test}
.a-act{grid-area:act}.a-manage{grid-area:manage}
.a-pv{grid-area:pv;align-self:stretch;min-width:0}
.ad-pv{position:sticky;top:76px}
@media (max-width:900px){.ad-grid{grid-template-columns:minmax(0,1fr);
  grid-template-areas:"content" "pv" "who" "set" "test" "act" "manage"}.ad-pv{position:static}}
#ad-text{min-height:340px;font-size:15px;line-height:1.6}
.ad .preview{padding:16px}
.ad .bubble{white-space:pre-wrap;word-break:break-word;font-size:15px;line-height:1.5;min-height:56px}
.ad .bubble img,.ad .bubble video{width:100%;max-height:420px;object-fit:cover;border-radius:8px;display:block;margin-bottom:8px}
.ad .bubble blockquote{margin:4px 0;padding:2px 0 2px 10px;border-left:3px solid var(--accent);color:var(--text-2)}
.ad .bubble code,.ad .bubble pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;background:var(--surface-2);border-radius:4px;padding:0 4px}
.ad .bubble pre{display:block;padding:8px;margin:4px 0;white-space:pre-wrap}
.ad .spoiler{background:var(--text-2);color:transparent;border-radius:4px;transition:color .2s,background .2s}
.ad .spoiler:hover{color:inherit;background:var(--surface-2)}
.ad .doc{display:flex;gap:8px;align-items:center;padding:8px 10px;background:var(--surface-2);border-radius:8px;margin-bottom:8px;font-weight:550}
.pv-row{display:flex;gap:4px}
.pv-row .tg-btn{flex:1;min-width:0}
.tb{display:flex;gap:4px;flex-wrap:wrap}
.tb button{height:32px;min-width:36px;padding:0 10px;border:1px solid var(--border);background:var(--surface);color:var(--text-2);border-radius:7px;cursor:pointer;font:inherit;font-size:13.5px}
.tb button:hover{background:var(--surface-2);color:var(--text)}
.cnt{font-size:12px;color:var(--muted);text-align:right;font-weight:400}
.cnt.over{color:var(--danger);font-weight:650}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr));gap:10px}
.radio{display:flex;gap:16px;flex-wrap:wrap;font-weight:400}
.radio label{display:flex;gap:6px;align-items:center;cursor:pointer;color:var(--text-2);font-size:13.5px}
.ad input[type=datetime-local]{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--border);border-radius:9px;height:38px;padding:0 10px;max-width:260px}
.ad input[type=file]{font:inherit;font-size:13px;color:var(--text-2)}
.bar{height:10px;background:var(--track);border-radius:99px;overflow:hidden;margin:6px 0}
.bar i{display:block;height:100%;background:var(--series);border-radius:99px;transition:width .6s ease}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none}
</style>"""

EDITOR_JS = r"""<script>(function(){
var f=document.getElementById('adform');if(!f)return;
function $(id){return document.getElementById(id)}
function on(el,evs,fn){if(el)evs.split(' ').forEach(function(ev){el.addEventListener(ev,fn)})}
function safe(fn){try{fn()}catch(err){if(window.console)console.error(err)}}
var ta=$('ad-text'),cnt=$('ad-count'),pvT=$('pv-text'),bt=$('ad-btns'),pvB=$('pv-btns'),btErr=$('ad-btns-err'),
  file=$('ad-file'),pvM=$('pv-media'),rm=$('ad-rm'),send=$('ad-send'),at=$('ad-at');
var hadMedia=f.dataset.media==='1',origMedia=pvM?pvM.innerHTML:'';
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function fmt(s){var h=esc(s);
  h=h.replace(/&lt;(\/?)(b|strong|i|em|u|ins|s|strike|del|code|pre)&gt;/g,'<$1$2>');
  h=h.replace(/&lt;blockquote(?: expandable)?&gt;/g,'<blockquote>').replace(/&lt;\/blockquote&gt;/g,'</blockquote>');
  h=h.replace(/&lt;tg-spoiler&gt;/g,'<span class=spoiler>').replace(/&lt;\/tg-spoiler&gt;/g,'</span>');
  h=h.replace(/&lt;a href=&quot;((?:https?|tg):\/\/(?:(?!&quot;)\S)+)&quot;&gt;/g,'<a href="$1" target=_blank rel=noopener>').replace(/&lt;\/a&gt;/g,'</a>');
  return h}
function hasMedia(){return (file&&file.files.length>0)||(hadMedia&&!(rm&&rm.checked))}
function upd(){if(!ta||!pvT)return;var v=ta.value;
  try{pvT.innerHTML=v.trim()?fmt(v):'<span class=muted>Matn shu yerda ko\'rinadi</span>'}catch(err){pvT.textContent=v}
  safe(function(){var lim=hasMedia()?1024:4096,d=document.createElement('div');d.innerHTML=fmt(v);var n=d.textContent.length;
    cnt.textContent=n+' / '+lim;cnt.classList.toggle('over',n>lim)})}
on(ta,'input keyup change paste cut',function(){setTimeout(upd,0)});
upd();
var BTN=/^(.+?)\s*[-–—]\s*((?:https?|tg):\/\/\S+)$/i;
function btns(){var rows=[],bad=0;bt.value.split('\n').forEach(function(line,i){line=line.trim();if(!line)return;
  rows.push(line.split('|').map(function(p){var m=p.trim().match(BTN);if(!m){bad=bad||i+1;return null}return m[1]}))});
  pvB.innerHTML=rows.map(function(r){return '<div class=pv-row>'+r.map(function(t){return t===null?
    '<span class=tg-btn style="color:var(--danger)">xato</span>':'<span class=tg-btn>'+esc(t)+'</span>'}).join('')+'</div>'}).join('');
  btErr.textContent=bad?(bad+'-qatorda xato: «Matn - https://…» ko\'rinishida yozing'):''}
on(bt,'input keyup change paste',function(){setTimeout(function(){safe(btns)},0)});
safe(btns);
safe(function(){f.querySelectorAll('[data-wrap]').forEach(function(b){b.addEventListener('click',function(){
  var t=b.dataset.wrap,a=ta.selectionStart,z=ta.selectionEnd,sel=ta.value.slice(a,z),o='<'+t+'>',c='</'+t+'>';
  if(t==='a'){var u=prompt('Havola manzili','https://');if(!u)return;o='<a href="'+u.replace(/"/g,'%22')+'">';c='</a>';if(!sel)sel=u}
  ta.setRangeText(o+sel+c,a,z,'end');ta.setSelectionRange(a+o.length,a+o.length+sel.length);ta.focus();upd()})})});
on(file,'change',function(){var x=file.files[0];
  if(x&&x.size>11.8*1024*1024){alert('Fayl 12 MB dan katta. Kichikroq fayl tanlang.');file.value='';x=null}
  if(!x){pvM.innerHTML=rm&&rm.checked?'':origMedia}else{var u=URL.createObjectURL(x);
    pvM.innerHTML=x.type.indexOf('image/')===0?'<img src="'+u+'" alt="">':x.type.indexOf('video/')===0?
      '<video src="'+u+'" controls muted playsinline></video>':'<div class=doc>📎 '+esc(x.name)+'</div>'}
  upd()});
on(rm,'change',function(){if(!file.files.length)pvM.innerHTML=rm.checked?'':origMedia;upd()});
function later(){var r=f.querySelector('input[name=when]:checked');return !!r&&r.value==='later'}
function when(){if(!at||!send)return;at.hidden=!later();at.required=later();send.textContent=later()?'🗓 Rejalashtirish':'🚀 Yuborish'}
f.querySelectorAll('input[name=when]').forEach(function(r){r.addEventListener('change',when)});
safe(when);
on(send,'click',function(ev){var tot=$('aud-total'),m=later()?'Reklama belgilangan vaqtda yuborilsinmi?':
  'Reklama hozir yuborilsinmi? '+(tot?tot.textContent:'');if(!confirm(m))ev.preventDefault()});
})();</script>"""

LIVE_JS = """<script>(function(){var el=document.getElementById('ad-live');
if(!el||el.dataset.running!=='1')return;
var t=setInterval(function(){fetch('/api/ads/'+el.dataset.id+'/progress',{credentials:'same-origin'})
.then(function(r){return r.json()}).then(function(d){el.innerHTML=d.html;
if(!d.running){clearInterval(t);setTimeout(function(){location.reload()},1500)}}).catch(function(){})},3000)})();</script>"""


def _to(path: str, m: str):
    raise web.HTTPSeeOther(f"{path}?m={m}")


def _pill(status: str, ad_id: int) -> str:
    if status == "sending" and ads.current() != ad_id:
        return '<span class="pill warn">Navbatda</span>'
    cls, label = STATUS.get(status, ("off", status))
    return f'<span class="pill {cls}">{e(label)}</span>'


def _audience_label(kind: str, days: int) -> str:
    return f"So'nggi {days} kunda qo'shilganlar" if kind == "new" else ads.AUDIENCES.get(kind, kind)


def _who(to_bot: bool, kind: str, days: int, n_targets: int) -> str:
    parts = []
    if to_bot:
        parts.append(_audience_label(kind, days))
    if n_targets:
        parts.append(f"{n_targets} kanal/guruh")
    return " + ".join(parts) or "—"


def _media_html(ad) -> str:
    if not ad["has_media"]:
        return ""
    src = f'/ads/media/{ad["id"]}-{ad["media_ver"]}'
    kind = ad["media_type"]
    if kind in ("photo", "animation"):
        return f'<img src="{src}" alt="">'
    if kind == "video":
        return f'<video src="{src}" controls muted playsinline preload=metadata></video>'
    return f'<div class=doc>📎 {e(ad["media_name"] or "fayl")}</div>'


def _buttons_html(raw: str) -> str:
    try:
        rows = ads.parse_buttons(raw)
    except ValueError:
        return ""
    return "".join('<div class=pv-row>' + "".join(f'<span class=tg-btn>{e(t)}</span>' for t, _ in row) + '</div>'
                   for row in rows)


def _preview(ad, sticky: bool = True) -> str:
    text = ad["text"] or '<span class=muted>Matn shu yerda ko\'rinadi</span>'
    return (f'<section class="card{" ad-pv" if sticky else ""}"><div class=card-h><h2>Ko\'rinishi</h2>'
            f'<span class="muted small">Telegramda shunday chiqadi</span></div><div class=preview><div class=bubble>'
            f'<div id=pv-media>{_media_html(ad)}</div><div id=pv-text>{text}</div></div>'
            f'<div id=pv-btns>{_buttons_html(ad["buttons"])}</div></div></section>')


# --- sahifalar ---
async def ads_page(request: web.Request):
    rows = await db.ad_list()
    track = ads.tracking()
    if rows:
        trs = []
        for r in rows:
            when = (f'🗓 {dt(r["scheduled_at"])}' if r["status"] == "scheduled" else
                    dt(r["started_at"]) if r["started_at"] else dt(r["created_at"]))
            ctr = f'{r["clicks"] / r["sent"] * 100:.1f}%' if track and r["sent"] else "—"
            clicks = num(r["clicks"]) if track else "—"
            trs.append(
                f'<tr><td><a class=row-link href="/ads/{r["id"]}">{e(r["title"])}</a>'
                f'<div class="muted small">#{r["id"]} · {e(r["created_by"])}</div></td>'
                f'<td>{_pill(r["status"], r["id"])}</td>'
                f'<td>{e(_who(r["to_bot"], r["audience"], r["audience_days"], r["n_targets"]))}</td>'
                f'<td class=num>{num(r["sent"])} / {num(r["total"])}</td><td class=num>{clicks}</td>'
                f'<td class=num>{ctr}</td><td class=nowrap>{when}</td></tr>'
            )
        content = (f'<div class=table-wrap><table><thead><tr><th>Reklama</th><th>Holat</th><th>Kimga</th>'
                   f'<th class=num>Yetkazildi</th><th class=num>Bosishlar</th><th class=num>CTR</th><th>Vaqt</th>'
                   f'</tr></thead><tbody>{"".join(trs)}</tbody></table></div>')
    else:
        content = panel._empty("Hali reklama yo'q. «Yangi reklama» tugmasini bosing.", "inbox")
    note = "" if track else ('<div class="alert warn" style="margin-bottom:16px">Bosish statistikasi uchun serverda '
                             '<b>PUBLIC_URL</b> sozlanishi kerak. Hozir tugmalar to\'g\'ridan havolaga ochiladi.</div>')
    body = (f'{STYLE}<div class=ad><div class=ph><div><h1>Reklama</h1><div class=ph-meta>Bot foydalanuvchilari, '
            f'aniq akkauntlar va kanal/guruhlarga reklama yuborish</div></div><div class=actions>'
            f'<form method=post action=/ads/new><button class="btn primary">{icon("plus")}Yangi reklama</button>'
            f'</form></div></div>{note}<section class=card>{content}</section></div>')
    return panel.page(body, title="Reklama", active="megaphone")


async def ad_page(request: web.Request):
    ad = await db.ad_get(int(request.match_info["id"]))
    if not ad:
        raise web.HTTPNotFound()
    body = await (_editor(request, ad) if ad["status"] in EDITABLE else _report(ad))
    return panel.page(body, title=ad["title"], active="megaphone")


async def _editor(request: web.Request, ad) -> str:
    aid = ad["id"]
    items = await targets.available(request.app["bot"])
    test_chat = await db.get_setting(f"ads_test:{request['admin'][0]}") or ""
    count, unknown = await ads.audience_count(ad["audience"], ad["audience_days"], ad["audience_users"])
    scheduled = ad["scheduled_at"]

    tools = "".join(f'<button type=button data-wrap="{t}" title="{e(label)}">{sym}</button>' for t, sym, label in (
        ("b", "<b>B</b>", "Qalin"), ("i", "<i>I</i>", "Kursiv"), ("u", "<u>U</u>", "Tagiga chizilgan"),
        ("s", "<s>S</s>", "Ustidan chizilgan"), ("tg-spoiler", "▒", "Spoiler (yashirin matn)"), ("a", "🔗", "Havola"),
        ("code", "&lt;/&gt;", "Kod"), ("blockquote", "❝", "Iqtibos")))
    current = ""
    if ad["has_media"]:
        kind = ads.MEDIA_KINDS.get(ad["media_type"], "Fayl")
        current = (f'<div class="muted small">Hozirgi: {kind} — {e(ad["media_name"] or "")}</div>'
                   f'<label class=check><input type=checkbox name=remove_media id=ad-rm>Mediani olib tashlash</label>')
    del_opts = '<option value="">O\'chirilmaydi</option>' + "".join(
        f'<option value={h}{" selected" if ad["delete_after"] == h else ""}>{h} soatdan keyin</option>'
        for h in ads.DELETE_HOURS)

    def chk(name, label):
        return f'<label class=check><input type=checkbox name={name}{" checked" if ad[name] else ""}>{label}</label>'

    at_value = wp._local_input(scheduled) if scheduled else ""
    # matn bazada Telegram HTML ko'rinishida (&amp; …): muharrirda odatdagi belgilar bilan ko'rsatiladi
    text_value = e(html.unescape(ad["text"]))
    audience = wp.audience_block(api="/api/ads/audience", kind=ad["audience"], days=ad["audience_days"],
                                 users_raw=ad["audience_users"], count=count, unknown=unknown, to_bot=ad["to_bot"])
    test_button = '<button class="btn primary" name=action value=test>🧪 Menga yuborish</button>'
    form = (
        f'<form method=post action="/ads/{aid}/save" enctype=multipart/form-data id=adform class=ad-form '
        f'data-media="{1 if ad["has_media"] else 0}">'
        # Enter bosilganda «Saqlash» ishlashi uchun birinchi (ko'rinmas) tugma
        f'<button name=action value=save class=sr-only tabindex=-1 aria-hidden=true></button>'
        f'<section class="card a-content"><div class=card-h><h2>Kontent</h2></div><div class=stack>'
        f'<label class=field>Nomi <span class=hint>faqat panelda ko\'rinadi</span>'
        f'<input type=text name=title required maxlength=120 value="{e(ad["title"])}"></label>'
        f'<div class=field><div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-end">'
        f'<span>Matn</span><span class=cnt id=ad-count></span></div><div class=tb>{tools}</div>'
        f'<textarea name=text id=ad-text rows=14 maxlength=8000 aria-label="Reklama matni" '
        f'placeholder="Reklama matni. Belgilab, yuqoridagi tugmalar bilan formatlang.">{text_value}</textarea></div>'
        f'<div class=field>Media <span class=hint>rasm, video, GIF yoki fayl — 12 MB gacha</span>{current}'
        f'<input type=file name=media id=ad-file></div>'
        f'<label class=field>Tugmalar <span class=hint>har qator alohida; bir qatorda bir nechta tugma — «|» bilan</span>'
        f'<textarea name=buttons id=ad-btns rows=3 maxlength=3000 placeholder="Batafsil - https://example.com&#10;'
        f'Kanal - https://t.me/kanal | Sayt - https://site.uz">{e(ad["buttons"])}</textarea>'
        f'<span class=hint id=ad-btns-err style="color:var(--danger)"></span></label></div></section>'

        f'<section class="card a-who"><div class=card-h><h2>Kimga</h2><span class="muted small" id=aud-total></span>'
        f'</div><div class=stack>{audience}'
        f'<div><div class="muted small" style="margin-bottom:2px">Kanal va guruhlar</div>'
        f'{wp.target_checks(items, ad["targets"])}</div></div></section>'

        f'<section class="card a-set"><div class=card-h><h2>Sozlamalar</h2></div><div class=stack><div class=opts>'
        + chk("silent", "🔕 Ovozsiz yuborish") + chk("no_preview", "🔗 Havola ko'rinishini o'chirish")
        + chk("protect", "🛡 Nusxalash va forwardni taqiqlash") + chk("pin", "📌 Chat tepasiga qadash")
        + f'</div><label class=field style="max-width:260px">Avto-o\'chirish<select name=delete_after>{del_opts}'
        f'</select></label><div class=field>Yuborish vaqti <span class=hint>Toshkent vaqti</span><div class=radio>'
        f'<label><input type=radio name=when value=now{"" if scheduled else " checked"}>Hozir</label>'
        f'<label><input type=radio name=when value=later{" checked" if scheduled else ""}>Rejalashtirish</label></div>'
        f'<input type=datetime-local name=scheduled_at id=ad-at value="{at_value}"></div></div></section>'

        f'<section class="card a-test"><div class=card-h><h2>🧪 Sinov uchun yuborish</h2>'
        f'<span class="muted small">avval saqlanadi, keyin yuboriladi</span></div><div class=stack>'
        f'{wp.test_box(test_chat, test_button)}</div></section>'

        f'<div class="actions a-act"><button class="btn outline" name=action value=save>Saqlash</button>'
        f'<button class="btn primary" name=action value=send id=ad-send>🚀 Yuborish</button></div></form>'
    )
    extra = []
    if ad["status"] == "scheduled":
        extra.append(f'<form method=post action="/ads/{aid}/unschedule"><button class="btn outline">'
                     f'Rejalashtirishni bekor qilish</button></form>')
    extra.append(f'<form method=post action="/ads/{aid}/remove" data-confirm="{e("Reklama butunlay o‘chirilsinmi?")}">'
                 f'<button class="btn danger">{icon("trash")}O\'chirish</button></form>')
    meta = [f'Yaratgan: <b>{e(ad["created_by"])}</b> · {dt(ad["created_at"])}']
    if scheduled and ad["status"] == "scheduled":
        meta.append(f"Yuboriladi: <b>{dt(scheduled)}</b>")
    head = (f'<a class=back href="/ads">{icon("back")}Reklama</a><div class=ph><div><div class=ph-title>'
            f'<h1>{e(ad["title"])}</h1>{_pill(ad["status"], aid)}</div><div class=ph-meta>'
            f'{"".join(f"<span>{m}</span>" for m in meta)}</div></div><div class=actions>{"".join(extra)}</div></div>')
    manage = f'<section class="card a-manage">{wp.target_manage(items, f"/ads/{aid}")}</section>'
    return (f'{wp.STYLE}{STYLE}<div class=ad>{head}<div class=ad-grid>{form}{manage}'
            f'<div class=a-pv>{_preview(ad)}</div></div></div>{wp.AUDIENCE_JS}{EDITOR_JS}')


async def _live_html(ad) -> str:
    counts = await db.ad_counts(ad["id"])
    clicks = await db.ad_clicks(ad["id"])
    p = ads.progress(ad, counts)
    sent = counts.get("sent", 0) + counts.get("deleted", 0)
    track = ads.tracking()
    n_clicks = sum(clicks.values())
    ctr = f"{n_clicks / sent * 100:.1f}%" if track and sent else "—"
    tiles = ('<div class=tiles>'
             + panel._tile("Jami", num(p["total"]), f"qolgan {num(p['queued'])}" if p["queued"] else "qabul qiluvchi")
             + panel._tile("Yetkazildi", num(sent), f"{sent / p['total'] * 100:.0f}%" if p["total"] else "")
             + panel._tile("Botni bloklagan", num(counts.get("blocked", 0)), "xabar bormadi")
             + panel._tile("Xato", num(counts.get("failed", 0)),
                           f"o'tkazildi: {num(counts.get('skipped', 0))}" if counts.get("skipped") else "")
             + panel._tile("Bosishlar", num(n_clicks) if track else "—", "" if track else "PUBLIC_URL sozlanmagan")
             + panel._tile("CTR", ctr, "bosishlar / yetkazilgan")
             + '</div>')
    bar = ""
    if p["queued"] and ad["status"] in ("sending", "paused"):
        if ad["status"] == "paused":
            speed = "⏸ Pauza"
        elif ads.current() != ad["id"]:
            speed = "Navbatda — oldingi reklama tugashini kutmoqda"
        else:
            speed = (f"tezlik ~{num(p['rate'])} / daqiqa · qolgan ~{num(max(1, p['eta']))} daqiqa"
                     if p["rate"] else "boshlanmoqda…")
        bar = (f'<section class="card section"><div class=card-h><h2>Jarayon</h2>'
               f'<span class="muted small">{num(p["done"])} / {num(p["total"])} · {p["pct"]:.0f}%</span></div>'
               f'<div class=bar><i style="width:{p["pct"]:.1f}%"></i></div>'
               f'<div class="muted small">{speed}</div></section>')
    if counts.get("deleted"):
        bar += (f'<div class="muted small" style="margin-top:10px">🗑 O\'chirilgan xabarlar: '
                f'{num(counts["deleted"])}</div>')
    return tiles + bar


async def _report(ad) -> str:
    aid, status = ad["id"], ad["status"]
    actions = []
    if status == "sending":
        actions.append(f'<form method=post action="/ads/{aid}/pause"><button class="btn outline">⏸ Pauza</button></form>')
    if status == "paused":
        actions.append(f'<form method=post action="/ads/{aid}/resume"><button class="btn primary">▶️ Davom ettirish'
                       f'</button></form>')
    if status in ("sending", "paused"):
        actions.append(f'<form method=post action="/ads/{aid}/stop" data-confirm="{e("Yuborish to‘xtatilsinmi? Qolganlarga yuborilmaydi.")}">'
                       f'<button class="btn outline">⏹ To\'xtatish</button></form>')
    last = await db.ad_last_sent_at(aid)
    recent = last and (datetime.now(timezone.utc) - last).total_seconds() < 47 * 3600
    if status != "deleted" and recent and not ads.deleting(aid):
        actions.append(f'<form method=post action="/ads/{aid}/delete_all" data-confirm="{e("Reklama barcha chatlardan o‘chirilsinmi?")}">'
                       f'<button class="btn danger">{icon("trash")}Hammadan o\'chirish</button></form>')
    actions.append(f'<form method=post action="/ads/{aid}/copy"><button class="btn outline">📄 Nusxa olish</button></form>')

    meta = [f'Yaratgan: <b>{e(ad["created_by"])}</b>',
            f'Kimga: <b>{e(_who(ad["to_bot"], ad["audience"], ad["audience_days"], len(ad["targets"])))}</b>']
    if ad["started_at"]:
        meta.append(f'Boshlangan <b>{dt(ad["started_at"])}</b>')
    if ad["finished_at"]:
        meta.append(f'Tugagan <b>{dt(ad["finished_at"])}</b>')
    if ad["delete_after"]:
        meta.append(f'Avto-o\'chirish: <b>{ad["delete_after"]} soat</b>')
    head = (f'<a class=back href="/ads">{icon("back")}Reklama</a><div class=ph><div><div class=ph-title>'
            f'<h1>{e(ad["title"])}</h1>{_pill(status, aid)}</div><div class=ph-meta>'
            f'{"".join(f"<span>{m}</span>" for m in meta)}</div></div><div class=actions>{"".join(actions)}</div></div>')
    alerts = ""
    if ads.error(aid):
        alerts += f'<div class="alert danger" style="margin-bottom:16px">{e(ads.error(aid))}</div>'
    if ads.deleting(aid):
        alerts += '<div class="alert warn" style="margin-bottom:16px">🗑 Xabarlar chatlardan o\'chirilmoqda…</div>'
    running = 1 if status == "sending" else 0
    live = f'<div id=ad-live data-id="{aid}" data-running="{running}">{await _live_html(ad)}</div>'

    clicks = await db.ad_clicks(aid)
    buttons = ads.button_list(ad["buttons"])
    stats = ""
    if buttons:
        track = ads.tracking()
        trs = "".join(
            f'<tr><td>{e(text)}</td><td><a href="{e(url)}" target=_blank rel=noopener>{e(url)}</a></td>'
            f'<td class=num>{num(clicks.get(i, 0)) if track else "—"}</td></tr>'
            for i, (text, url) in enumerate(buttons)
        )
        stats = (f'<section class="card section"><div class=card-h><h2>Tugmalar</h2></div><div class=table-wrap>'
                 f'<table><thead><tr><th>Tugma</th><th>Havola</th><th class=num>Bosishlar</th></tr></thead>'
                 f'<tbody>{trs}</tbody></table></div></section>')
    return (f'{wp.STYLE}{STYLE}<div class=ad>{head}{alerts}{live}{stats}'
            f'<div class=section style="max-width:520px">{_preview(ad, sticky=False)}</div></div>{LIVE_JS}')


# --- amallar ---
async def ad_new(request: web.Request):
    aid = await db.ad_create("Yangi reklama", request["admin"][1])
    raise web.HTTPSeeOther(f"/ads/{aid}")


async def _load(request: web.Request):
    aid = int(request.match_info["id"])
    ad = await db.ad_get(aid)
    if not ad:
        raise web.HTTPNotFound()
    return aid, ad, f"/ads/{aid}"


async def ad_save(request: web.Request):
    aid, ad, back = await _load(request)
    if ad["status"] not in EDITABLE:
        _to(back, "ad_locked")
    data = await request.post()
    bot = request.app["bot"]
    action = data.get("action") or "save"
    text = ads.clean(data.get("text") or "")
    buttons = (data.get("buttons") or "").strip()[:3000]
    try:
        ads.parse_buttons(buttons)
    except ValueError:
        _to(back, "ad_badbtn")
    audience = data.get("audience") if data.get("audience") in ads.AUDIENCES else "all"
    try:
        days = max(1, min(365, int(data.get("audience_days") or 7)))
    except ValueError:
        days = 7
    try:
        delete_after = int(data.get("delete_after") or 0)
    except ValueError:
        delete_after = 0
    items = await targets.available(bot)

    upload = data.get("media")
    raw = upload.file.read() if getattr(upload, "file", None) else b""
    remove = bool(data.get("remove_media"))
    kind = name = None
    if raw:
        name = (getattr(upload, "filename", "") or "fayl")[:120]
        kind = ads.media_kind(name, getattr(upload, "content_type", ""))
        if kind == "photo":
            try:
                raw = await asyncio.to_thread(panel._to_jpeg, raw)
            except Exception:
                _to(back, "badimg")
            name = name.rsplit(".", 1)[0] + ".jpg"
    has_media = bool(raw) or (ad["has_media"] and not remove)
    if ads.visible_len(text) > (ads.CAPTION_LIMIT if has_media else ads.TEXT_LIMIT):
        _to(back, "ad_toolong")

    await db.ad_update(
        aid, title=(data.get("title") or "").strip()[:120] or "Reklama", text=text, buttons=buttons,
        to_bot=bool(data.get("to_bot")), audience=audience, audience_days=days,
        audience_users=(data.get("audience_users") or "").strip()[:5000],
        targets=targets.pick(items, data.getall("t", [])),
        silent=bool(data.get("silent")), no_preview=bool(data.get("no_preview")), protect=bool(data.get("protect")),
        pin=bool(data.get("pin")), delete_after=delete_after if delete_after in ads.DELETE_HOURS else None,
    )
    if raw:
        await db.ad_set_media(aid, raw, kind, name)
    elif remove and ad["has_media"]:
        await db.ad_set_media(aid, None, None, None)

    later = data.get("when") == "later"
    at = wp._parse_local(data.get("scheduled_at")) if later else None
    await db.ad_set_scheduled_at(aid, at)
    if ad["status"] == "scheduled" and not (later and at and at > datetime.now(timezone.utc)):
        await db.ad_set_status(aid, "draft")

    if action == "test":
        chat = (data.get("test_chat") or "").strip()[:64]
        await db.set_setting(f"ads_test:{request['admin'][0]}", chat)
        found, _ = await ads.resolve_accounts(chat)
        if not found:
            _to(back, "ad_testbad")
        _to(back, "ad_testsent" if await ads.send_test(bot, aid, found[0]) is None else "ad_testfail")
    if action == "send":
        if not text and not has_media:
            _to(back, "ad_empty")
        if later:
            if not at or at <= datetime.now(timezone.utc):
                _to(back, "badtime")
            ad = await db.ad_get(aid)
            if not await ads.recipients(ad):
                _to(back, "ad_noone")
            await db.ad_schedule(aid, at)
            _to(back, "ad_scheduled")
        if not await ads.start(aid):
            _to(back, "ad_noone")
        _to(back, "ad_started")
    _to(back, "ad_saved")


async def ad_unschedule(request: web.Request):
    aid, ad, back = await _load(request)
    if ad["status"] == "scheduled":
        await db.ad_set_status(aid, "draft")
    _to(back, "ad_unscheduled")


async def ad_remove(request: web.Request):
    aid, ad, back = await _load(request)
    if ad["status"] not in EDITABLE:
        _to(back, "ad_locked")
    await db.ad_delete(aid)
    _to("/ads", "ad_removed")


async def ad_pause(request: web.Request):
    aid, _, back = await _load(request)
    await ads.pause(aid)
    _to(back, "ad_paused")


async def ad_resume(request: web.Request):
    aid, _, back = await _load(request)
    await ads.resume(aid)
    _to(back, "ad_resumed")


async def ad_stop(request: web.Request):
    aid, _, back = await _load(request)
    await ads.stop(aid)
    _to(back, "ad_stopped")


async def ad_delete_all(request: web.Request):
    aid, _, back = await _load(request)
    ads.delete_all(request.app["bot"], aid)
    _to(back, "ad_deleting")


async def ad_copy(request: web.Request):
    aid, _, _ = await _load(request)
    new_id = await db.ad_copy(aid, request["admin"][1])
    _to(f"/ads/{new_id}", "ad_copied")


async def ad_media(request: web.Request):
    row = await db.ad_get_media(int(request.match_info["id"]), int(request.match_info["ver"]))
    if not row:
        raise web.HTTPNotFound()
    kind = row["media_type"]
    ctype = ("image/jpeg" if kind == "photo" else "image/gif" if kind == "animation"
             else mimetypes.guess_type(row["media_name"] or "")[0] or "application/octet-stream")
    return web.Response(body=row["media"], content_type=ctype)


async def api_audience(request: web.Request):
    data = await request.post()
    kind = data.get("audience") if data.get("audience") in ads.AUDIENCES else "all"
    try:
        days = max(1, min(365, int(data.get("days") or 7)))
    except ValueError:
        days = 7
    count, unknown = await ads.audience_count(kind, days, (data.get("users") or "")[:5000])
    return web.json_response({"count": count, "unknown": unknown[:20]})


async def api_progress(request: web.Request):
    ad = await db.ad_get(int(request.match_info["id"]))
    if not ad:
        raise web.HTTPNotFound()
    return web.json_response({"html": await _live_html(ad), "running": ad["status"] == "sending"})


async def click(request: web.Request):
    """Reklama tugmasi: bosishni sanab, asl havolaga yo'naltiradi (login talab qilinmaydi)."""
    aid, idx = int(request.match_info["id"]), int(request.match_info["idx"])
    ad = await db.ad_get(aid)
    url = ads.button_url(ad["buttons"], idx) if ad else None
    if not url:
        raise web.HTTPNotFound()
    await db.ad_click(aid, idx)
    raise web.HTTPFound(url)


async def _start_scheduler(app: web.Application):
    app["ads_scheduler"] = asyncio.create_task(ads.scheduler(app["bot"]))


async def _stop_scheduler(app: web.Application):
    task = app.get("ads_scheduler")
    if task:
        task.cancel()


def setup(app: web.Application):
    app.add_routes([
        web.get("/ads", ads_page),
        web.post("/ads/new", ad_new),
        web.get(r"/ads/{id:\d+}", ad_page),
        web.post(r"/ads/{id:\d+}/save", ad_save),
        web.post(r"/ads/{id:\d+}/unschedule", ad_unschedule),
        web.post(r"/ads/{id:\d+}/remove", ad_remove),
        web.post(r"/ads/{id:\d+}/pause", ad_pause),
        web.post(r"/ads/{id:\d+}/resume", ad_resume),
        web.post(r"/ads/{id:\d+}/stop", ad_stop),
        web.post(r"/ads/{id:\d+}/delete_all", ad_delete_all),
        web.post(r"/ads/{id:\d+}/copy", ad_copy),
        web.get(r"/ads/media/{id:\d+}-{ver:\d+}", ad_media),
        web.post("/api/ads/audience", api_audience),
        web.get(r"/api/ads/{id:\d+}/progress", api_progress),
        web.get(r"/r/{id:\d+}/{idx:\d+}", click),
    ])
    app.on_startup.append(_start_scheduler)
    app.on_cleanup.append(_stop_scheduler)
