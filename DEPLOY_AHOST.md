# botmv — ahost.uz ga joylash yo'riqnomasi

> Narxlar ahost.uz saytidan 2026-10-23 holatida olingan. Buyurtma berishdan oldin qayta tekshiring.

## 0. Loyiha nimadan iborat (nima uchun aynan VDS kerak)

- `bot.py` — **bitta jarayon**: aiogram bot (polling, 24/7 ishlaydi) + aiohttp admin panel (`WEB_PORT=8080`) + natijalarni yangilovchi fon vazifa.
- Ma'lumotlar bazasi — **PostgreSQL 16**.
- Docker tayyor: `Dockerfile` va `docker-compose.yml`.

**Xulosa:** ahost.uz'ning oddiy (cPanel) hostingi (NANO/MICRO/MINI/SMART) bu bot uchun **to'g'ri kelmaydi**:
u yerda doimiy ishlaydigan jarayon (polling), Docker va PostgreSQL yo'q. Faqat **VDS (virtual server)** olish kerak.

---

## 1. Narxlar tahlili (VDS CLOUD, TAS-IX, Toshkent)

| Tarif | CPU | RAM | SSD | Narx / oy | Bot uchun baho |
|---|---|---|---|---|---|
| VDS CLOUD 10 | 1 | 1 GB | 10 GB | 125 000 so'm | ⚠️ Minimal. Ishlaydi, lekin Docker + Postgres bilan RAM tor, disk tez to'ladi |
| **VDS CLOUD 20** | 1 | 1 GB | 20 GB | **140 000 so'm** | ✅ **Boshlash uchun tavsiya** (+ 1 GB swap) |
| **VDS CLOUD 50** | 1 | 2 GB | 50 GB | **200 000 so'm** | ✅✅ **Eng optimal** — ko'p ovoz beruvchi, katta kanal bo'lsa |
| VDS CLOUD 100 | 2 | 2 GB | 100 GB | 320 000 so'm | Ortiqcha (bir nechta bot bo'lsa) |
| VDS CLOUD 200 | 4 | 4 GB | 200 GB | 590 000 so'm | Bu loyiha uchun keraksiz |

Barcha tariflarda: 100 Mbit/s, 1 IP bepul, trafik cheklanmagan (UNLIM 100 — bepul).

**Qo'shimcha resurslar:** +1 GB RAM — 15 000 so'm/oy, +10 GB SSD — 15 000 so'm/oy, +1 CPU — 45 000 so'm/oy.
> 💡 CLOUD 20 + 1 GB RAM = 155 000 so'm — CLOUD 50 dan arzon, lekin disk 20 GB. Bot bazasi kichik bo'lgani uchun bu ham yaxshi variant.

**Qo'shimcha xarajatlar:**
| Narsa | Narx |
|---|---|
| Domen `.uz` (admin panel uchun, ixtiyoriy) | ~30–40 ming so'm/yil (aksiyada `.asia` 25 000 so'm) |
| SSL sertifikat | **0 so'm** — Let's Encrypt (bepul) ishlatamiz |
| ahost mutaxassisi sozlab bersa | 200 000 so'm/soat (o'zingiz qilsangiz — bepul) |
| Bepul: OS o'rnatish va tarmoq sozlash (bir marta) | 0 so'm |

**Oylik byudjet (taxminiy):**
- Minimal: **140 000 so'm/oy** (~1,7 mln/yil) + domen.
- Tavsiya: **200 000 so'm/oy** (~2,4 mln/yil) + domen.

**Taqqoslash uchun:** chet el VPS (Hetzner, Contabo) ~4–6 $/oy, lekin to'lov xorijiy karta bilan; ahost.uz — so'mda, Click/Payme/bank orqali, yuridik shaxsga hujjatlar beriladi, Toshkentda (o'zbek foydalanuvchilarga admin panel tez ochiladi). Telegram API serverlari xorijda bo'lgani uchun bot tezligi ikkala variantda deyarli bir xil.

---

## 2. Buyurtma berish

1. https://www.ahost.uz/vds → **VDS CLOUD 20** yoki **50** → «ЗАКАЗАТЬ».
2. OS sifatida **Ubuntu 24.04 LTS** ni tanlang (yoki tiketda so'rang — bepul o'rnatib berishadi).
3. To'lov qiling. Pochtangizga **IP manzil, root parol** keladi.
4. (Ixtiyoriy) Domen oling va DNS'da `A` yozuv qo'shing: `admin.domeningiz.uz → server IP`.

---

## 3. Serverni tayyorlash

Kompyuteringizdan (PowerShell) ulaning:

```bash
ssh root@SERVER_IP
```

Yangilash, xavfsizlik va Docker:

```bash
apt update && apt upgrade -y
apt install -y ufw fail2ban git
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
curl -fsSL https://get.docker.com | sh
```

1 GB RAM'li tarifda swap qo'shing (Postgres + Python uchun zaxira):

```bash
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

Root bilan ishlamaslik uchun alohida foydalanuvchi:

```bash
adduser deploy && usermod -aG sudo,docker deploy
```

---

## 4. Loyihani serverga yuklash

**Variant A — GitHub orqali (tavsiya):** loyihani **private** repoga yuklang (`.env` va `.venv` repoga tushmasin!), keyin serverda:

```bash
su - deploy
git clone https://github.com/SIZNING/botmv.git && cd botmv
```

**Variant B — to'g'ridan-to'g'ri nusxalash** (kompyuteringizdagi PowerShell'dan, `.venv`siz):

```bash
scp -r E:\10.22.2026\botmv\bot.py E:\10.22.2026\botmv\web.py E:\10.22.2026\botmv\db.py E:\10.22.2026\botmv\config.py E:\10.22.2026\botmv\handlers E:\10.22.2026\botmv\services E:\10.22.2026\botmv\requirements.txt E:\10.22.2026\botmv\Dockerfile E:\10.22.2026\botmv\docker-compose.yml E:\10.22.2026\botmv\.dockerignore E:\10.22.2026\botmv\.env.example deploy@SERVER_IP:~/botmv/
```

---

## 5. `.env` va ishga tushirish

```bash
cd ~/botmv
cp .env.example .env
nano .env
```

To'ldiring: `BOT_TOKEN`, `RESULTS_CHANNEL`, `ADMIN_USER`, **kuchli** `ADMIN_PASSWORD` (16+ belgi).
`DATABASE_URL` ni o'zgartirmang — compose o'zi beradi.

`docker-compose.yml` dagi Postgres paroli `votes` — serverda uni almashtiring (`POSTGRES_PASSWORD` va `DATABASE_URL` ikkalasida bir xil).

```bash
docker compose up -d --build
docker compose logs -f bot
```

Logda `Admin panel: http://localhost:8080` chiqsa — bot ishladi. Telegram'da `/start` bosib tekshiring.

> ⚠️ Bot polling'da ishlaydi: **bir token bilan faqat bitta joyda** ishlashi kerak. Serverda ishga tushirgach, kompyuteringizdagi `ishga.ps1` ni to'xtating, aks holda `Conflict: terminated by other getUpdates` xatosi chiqadi.

`restart: unless-stopped` tufayli server qayta yuklansa ham bot o'zi ko'tariladi.

---

## 6. Admin panelni domen + HTTPS bilan ochish

Compose admin panelni faqat `127.0.0.1:8080` da ochadi (bu to'g'ri — tashqaridan to'g'ridan-to'g'ri kirib bo'lmaydi). Tashqariga Nginx orqali HTTPS bilan chiqaramiz:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo nano /etc/nginx/sites-available/botmv
```

```nginx
server {
    server_name admin.domeningiz.uz;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/botmv /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d admin.domeningiz.uz
```

Endi panel: `https://admin.domeningiz.uz`. Sertifikat avtomatik yangilanadi.

Domen olmasangiz: panelga SSH tunnel orqali kiring (eng xavfsiz, bepul):
```bash
ssh -L 8080:127.0.0.1:8080 deploy@SERVER_IP
```
so'ng brauzerda `http://localhost:8080`.

---

## 7. Zaxira nusxa (backup) — majburiy

Har kuni 03:00 da bazani saqlash (7 kunlik):

```bash
mkdir -p ~/backups
crontab -e
```
```
0 3 * * * cd ~/botmv && docker compose exec -T db pg_dump -U votes votes | gzip > ~/backups/votes_$(date +\%F).sql.gz && find ~/backups -mtime +7 -delete
```

Haftada bir marta backup'ni kompyuteringizga ham yuklab oling (`scp`) — server buzilsa ham ma'lumot qoladi.

Tiklash:
```bash
gunzip -c votes_SANA.sql.gz | docker compose exec -T db psql -U votes votes
```

---

## 8. Yangilash (kod o'zgarganda)

```bash
cd ~/botmv && git pull && docker compose up -d --build
```

---

## 9. Sifatli ishlashi uchun takliflar

**Barqarorlik**
- `UPDATE_INTERVAL=15` — kanalda postni tez-tez tahrirlash Telegram limitiga (flood) olib kelishi mumkin; ovozlar ko'p bo'lsa 30–60 soniya qiling.
- Log hajmi diskni to'ldirmasligi uchun `docker-compose.yml` dagi `bot` servisiga qo'shing:
  ```yaml
  logging:
    driver: json-file
    options: { max-size: "10m", max-file: "3" }
  ```
- Monitoring: **UptimeRobot** (bepul) bilan `https://admin.domeningiz.uz/login` ni kuzating — server tushsa Telegram/email'ga xabar keladi.

**Xavfsizlik**
- SSH'ga parol o'rniga **kalit** bilan kiring, `PasswordAuthentication no` qiling.
- `.env` faylini hech qayerga (GitHub, Telegram) yubormang.
- Postgres porti tashqariga ochilmagan (compose'da `ports` yo'q) — shunday qoldiring.
- ⚠️ **Kodda tuzatish kerak:** admin panel login urinishlarini `request.remote` (IP) bo'yicha cheklaydi ([web.py:676](web.py:676)). Nginx ortida hamma so'rovlar `127.0.0.1` dan keladi, ya'ni bitta odam parol terishda xato qilsa — **hamma adminlar bloklanadi**. Nginx'ni qo'shishdan oldin `X-Real-IP` sarlavhasini o'qiydigan qilib tuzatish kerak.

**Tezlik**
- 1 GB RAM'da muammo bo'lsa (`docker stats` da RAM 90%+) — +1 GB RAM qo'shing (15 000 so'm/oy).
- Ovoz beruvchilar 10 000+ bo'lsa — CLOUD 50 ga o'ting.

---

## 10. Tez-tez uchraydigan muammolar

| Belgi | Sabab va yechim |
|---|---|
| `Conflict: terminated by other getUpdates` | Bot boshqa joyda ham ishlayapti — kompyuterdagisini o'chiring |
| `Unauthorized` | `BOT_TOKEN` noto'g'ri |
| Kanalga post chiqmaydi | Bot kanalga **admin** qilinmagan / post yozish huquqi yo'q |
| Admin panel ochilmaydi | `docker compose ps`, `sudo nginx -t`, `ufw status` tekshiring |
| Disk to'ldi | `docker system prune -f`, log cheklovini qo'shing |

**ahost.uz yordami:** +998 78 113-44-54, info@ahost.uz, tiket: https://clients.ahost.uz (24/7).
