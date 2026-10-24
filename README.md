# Ovoz berish boti

Telegram kanal(lar), Facebook va Twitter (X) obunachilari ovoz beradi; natijalar kanaldagi postda jonli yangilanadi. Admin — veb panel (`http://localhost:8080`).

## O'rnatish (Windows, Docker Desktop)
1. [Docker Desktop](https://www.docker.com/products/docker-desktop/) o'rnating va ishga tushiring.
2. @BotFather → `/newbot` → tokenni oling.
3. Botni **har bir majburiy kanalga** va natijalar kanaliga **admin** qiling (post yozish/tahrirlash huquqi bilan).
4. `.env.example` ni `.env` deb nusxalang va to'ldiring (`BOT_TOKEN`, `REQUIRED_CHANNELS`, `RESULTS_CHANNEL`, `ADMIN_PASSWORD`...).
5. Ishga tushiring:
   ```bash
   docker compose up -d --build
   ```
6. Brauzerda http://localhost:8080 oching (login: `ADMIN_USER`, parol: `ADMIN_PASSWORD`).

## Ishlatish
1. Panelda so'rovnoma yarating (sarlavha + necha kun, standart 15).
2. Nomzodlarni qo'shing.
3. **«Kanalga joylash»** — kanalda jonli natija posti paydo bo'ladi, har 15 s da yangilanadi.
4. Muddat tugagach so'rovnoma o'zi yopiladi va post yakuniy natija bilan yangilanadi. Qo'lda ham yakunlash mumkin.
5. «CSV eksport» — barcha ovozlar ro'yxati.

## Foydali buyruqlar
```bash
docker compose logs -f bot        # loglarni ko'rish
docker compose restart bot        # qayta yuklash
docker compose down               # to'xtatish (ma'lumotlar saqlanadi)
```
Zaxira nusxa (bazani faylga):
```bash
docker compose exec db pg_dump -U votes votes > backup.sql
```

## Muhim
- **Kompyuter 15 kun davomida yoniq va internetga ulangan bo'lishi kerak** (uyquga ketmasin: Windows → Quvvat sozlamalari → «Uyquga o'tmasin»). Aks holda bot ovoz qabul qilmaydi. 100 000 ovoz uchun ishonchliroq yo'l — arzon VPS ($5–10/oy); shu loyiha o'zgarishsiz o'sha yerda ham ishlaydi.
- Telegram obunasi `getChatMember` bilan aniq tekshiriladi; Facebook/X uchun bu mumkin emas, shuning uchun «obuna bo'ldim» tugmasi (ishonch asosida).
- Himoya: bitta Telegram akkaunt = 1 ovoz, captcha, tugmani tez bosishga cheklov, obunani 5 daqiqaga keshlash, kanaldan chiqqanning ovozi bekor bo'ladi.
- Admin panelni internetga ochmang (u faqat `127.0.0.1` ga bog'langan).
