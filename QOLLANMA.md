# botmv — kundalik ish qo'llanmasi

Server: ahost.uz VDS `185.196.215.18` · Panel: Coolify (`http://185.196.215.18:8000`) · Kod: GitHub `Admiralfuugo/botmv` (private)

Loyiha papkasi (faqat shu yerda ishlang): `C:\Users\fuugo\Documents\GitHub\botmv\botmv`

---

## 1. Hammasi qanday bog'langan

```
Siz (kod o'zgartirasiz)
   │  GitHub Desktop → Commit → Push
   ▼
GitHub (Admiralfuugo/botmv)
   │  "kod yangilandi" xabari (webhook) — avtomatik
   ▼
Coolify (serverda) → yangi kodni yuklaydi → qayta quradi → botni qayta ishga tushiradi
   │
   ▼
Bot + PostgreSQL baza (serverda, 24/7)
```

**Eng muhim qoida:** kod (dastur) va ma'lumot (baza) — alohida narsalar.

| Narsa | Qayerda saqlanadi | Push qilganda nima bo'ladi |
|---|---|---|
| Kod (`.py`, `Dockerfile` …) | GitHub | Serverga yangisi tushadi |
| So'rovnomalar, ovozlar, adminlar, kanallar | Serverdagi PostgreSQL bazasi (`pgdata`) | **Hech narsa bo'lmaydi — saqlanib qoladi** |
| Token, parollar (`BOT_TOKEN` …) | Coolify → Environment Variables | O'zgarmaydi |

Ya'ni admin panelda qo'shgan so'rovnomangiz kod yangilanganda **o'chib ketmaydi**.

---

## 2. Domen ulanguncha admin panelga kirish (SSH tunnel)

**1-qadam.** Serverga ulaning va bot konteynerining ichki IP sini toping:

```bash
ssh root@185.196.215.18
```
```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' $(docker ps -qf name=bot-)
```
Masalan `10.0.2.4` chiqadi. Bu oynani `exit` bilan yopsangiz bo'ladi.

**2-qadam.** Kompyuteringizda yangi PowerShell oynasida (IP ni o'zingiznikiga almashtiring):

```bash
ssh -L 8080:10.0.2.4:8080 root@185.196.215.18
```
Parolni kiriting va **oynani ochiq qoldiring**.

**3-qadam.** Brauzerda: `http://localhost:8080` → login/parol.

⚠️ **IP o'zgaradi:** har push/deploy'dan keyin konteyner yangi IP olishi mumkin. Panel ochilmasa — 1-qadamni qaytaring.
⚠️ Kompyuteringizdagi lokal bot (`ishga.ps1`) shu payt ishlamasin — aks holda `localhost:8080` lokal panelni ochadi.

---

## 3. Kodni o'zgartirish va serverga chiqarish

### Oddiy tartib

1. **O'zgartiring** — kodni `Documents\GitHub\botmv\botmv` papkasida tahrirlang.
2. **(Ixtiyoriy) Lokal sinab ko'ring** — `.\ishga.ps1`.
   ⚠️ Bir token bilan bot faqat **bitta joyda** ishlaydi. Lokal ishga tushirsangiz, serverdagi bot `Conflict` xatosi beradi (Telegram'dan xabar olmay qoladi). Sinab bo'lgach, **lokal botni albatta to'xtating** (oynani yoping / Ctrl+C). Yaxshisi — sinov uchun @BotFather'dan **alohida test bot** oching va lokal `.env` ga uning tokenini yozing.
3. **GitHub Desktop** → Changes'da o'zgargan fayllarni tekshiring (`.env` bo'lmasin!) → Summary'ga izoh → **Commit to main** → **Push origin**.
4. **Kuting ~1–2 daqiqa.** Coolify o'zi deploy qiladi. Tekshirish: Coolify → loyiha → **Deployments** — eng yuqorida yangi qator, holati yashil (Finished).
5. Telegram'da botni tekshiring.

### Qaysi o'zgarish qanday chiqariladi

| Nima o'zgardi | Nima qilasiz |
|---|---|
| `.py` fayllar (bot mantiqi, admin panel) | Push — tamom |
| `requirements.txt` (yangi kutubxona) | Push — Coolify o'zi o'rnatadi |
| `docker-compose.yml`, `Dockerfile` | Push. Deploy logini kuzating — xato bo'lsa shu yerda chiqadi |
| Token, kanal, `UPDATE_INTERVAL` kabi sozlamalar | **GitHub emas!** Coolify → Environment Variables → o'zgartiring → **Redeploy** |
| Yangi sozlama qo'shildi (kodda yangi `os.getenv(...)`) | Coolify'ga o'sha o'zgaruvchini qo'shing **va** kodni push qiling |
| So'rovnoma, kanal, admin qo'shish | Faqat admin panel orqali — push kerak emas |

### Deploy yiqilsa

- Coolify → Deployments → qizil qator → logning **oxirgi qizil qatorlari** — xato sababi shu yerda.
- Qurish (build) bosqichida yiqilsa, **eski bot ishlashda davom etadi** — shoshilmang.
- Tezda orqaga qaytish: GitHub Desktop → **History** → oxirgi commit ustida o'ng tugma → **Revert changes in commit** → Push. Coolify avvalgi holatni qayta chiqaradi.

---

## 4. Dushanba: `admin.fuugo.uz` ni ulash

DNS'da `admin → 185.196.215.18` yozuvi allaqachon qo'shilgan. Domen faollashgach:

1. **Tekshiring** — kompyuteringizda:
   ```bash
   nslookup admin.fuugo.uz
   ```
   Javobda `185.196.215.18` chiqsa — tayyor. Chiqmasa — bir necha soat kuting.
2. Coolify → loyiha → bot sozlamalari → **Domains** (bot servisi uchun) maydoniga:
   ```
   https://admin.fuugo.uz:8080
   ```
   (`:8080` — bu "bot ichida panel 8080-portda ishlaydi" degani; tashqaridan manzil oddiy `https://admin.fuugo.uz` bo'ladi.)
3. **Save** → **Redeploy**.
4. 1–2 daqiqadan so'ng `https://admin.fuugo.uz` ochiladi, qulf belgisi (HTTPS) bilan. Sertifikatni Coolify o'zi oladi va yangilaydi.
5. Endi SSH tunnel kerak emas.

---

## 5. Hech qachon qilmang ⛔

- `.env` faylini GitHub'ga yuklamang (`.gitignore` buni to'sadi — Changes'da baribir tekshiring).
- Botni bir vaqtda ham kompyuterda, ham serverda ishga tushirmang.
- Coolify'da `POSTGRES_PASSWORD` ni **o'zgartirmang**. Baza birinchi ishga tushganda parolni eslab qolgan; o'zgartirsangiz bot bazaga ulana olmaydi.
- Coolify'da **Storages / Volumes** dagi `pgdata` ni o'chirmang — barcha ma'lumot shu yerda.
- Loyihani (resource) Coolify'dan o'chirmang — baza ham birga o'chishi mumkin.
- Root parol, `ADMIN_PASSWORD`, Coolify kalitlarini hech kimga yubormang.

---

## 6. Keyin qilinadigan ishlar

- [ ] Dushanba: `admin.fuugo.uz` (4-bo'lim)
- [ ] Bazaning kunlik backup'ini yoqish
- [ ] Bot yiqilsa Telegram'ga xabar (Coolify → Notifications)
- [ ] Coolify panelini ham domen + HTTPS orqali ochish (masalan `coolify.fuugo.uz`)
