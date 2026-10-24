# Ishga tushirish (qisqa)

Telegram ovoz berish boti (aiogram 3) + veb admin panel (aiohttp), baza — PostgreSQL 16. Docker ishlatilmaydi (WSL bu Windows build'da ishlamaydi).

## Bitta buyruq
```powershell
.\ishga.ps1
```
Bu skript: PostgreSQL'ni yoqadi (to'xtagan bo'lsa) → eski `bot.py` jarayonini to'xtatadi → botni ishga tushiradi.
Tayyor belgisi logda: `Run polling for bot @rekruter_bot`. Panel: http://localhost:8080

Claude uchun: skriptni **fonda** ishga tushir (`run_in_background`), ~7 s kutib, chiqish faylining oxirgi qatorlarini o'qi. Boshqa tekshiruv shart emas.

## Yo'llar
| Narsa | Joyi |
|---|---|
| Python muhit | `.venv\` (Python 3.13) |
| PostgreSQL | `C:\Users\fuugo\pgsql\bin\` (portativ, xizmat emas) |
| Baza ma'lumotlari / log | `C:\Users\fuugo\pgdata\` / `C:\Users\fuugo\pgsql\server.log` |
| Baza ulanishi | `postgresql://votes:votes@localhost:5432/votes` |
| Sozlamalar | `.env` (`BOT_TOKEN`, `RESULTS_CHANNEL`, `ADMIN_*`, `TZ_OFFSET`) |

## Qo'lda
```powershell
C:\Users\fuugo\pgsql\bin\pg_ctl.exe -D C:\Users\fuugo\pgdata -l C:\Users\fuugo\pgsql\server.log start    # baza
.\.venv\Scripts\python.exe bot.py                                                                          # bot
C:\Users\fuugo\pgsql\bin\pg_ctl.exe -D C:\Users\fuugo\pgdata stop                                         # bazani to'xtatish
```
`requirements.txt` o'zgarsa: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`

## Kod tuzilishi
- `bot.py` — kirish nuqtasi (baza, birinchi admin, polling, panel, natija yangilovchi)
- `web.py` — admin panel: login, boshqaruv, so'rovnomalar, foydalanuvchilar, obunalar, adminlar, profil
- `db.py` — barcha SQL (jadvallar `init()` da avtomatik yaratiladi/yangilanadi)
- `handlers/user.py` — bot: /start, obuna tekshiruvi, captcha, ovoz, /natija
- `services/` — `channels.py` (obunalar, bazadan), `auth.py` (admin parollari, sessiya kaliti), `subscription.py` (Telegram obuna tekshiruvi), `results.py` (kanal posti)

## Eslatmalar
- Obuna kanallari va adminlar **paneldan** boshqariladi (`.env` dagi `REQUIRED_CHANNELS`/`FB_URL`/... faqat birinchi marta ko'chirilgan).
- Bot har bir majburiy Telegram kanalda admin bo'lishi shart.
- Log faylini `pgdata` ichiga qo'yma: kompyuter to'satdan o'chgandan keyin baza 30+ s qotib qoladi.
- Testni haqiqiy bazada qilma — `votes_test` bazasini yarat, keyin o'chir.
