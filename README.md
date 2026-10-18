# Telegram botlar

| Papka | Nima qiladi |
|---|---|
| `universal_bot/` | Universal bot: havoladan video/MP3, ovoz va videodan matn, AI xulosa, siqish, kunlik limit + Telegram Stars Premium |
| `video_taker_bot/` | Oddiy bot: havola yuborilsa video yuklab beradi |

Har bir bot alohida Docker xizmati sifatida ishlaydi. **Har bir botga o'z tokeni kerak**
(bitta tokenni ikki joyda ishlatib bo'lmaydi).

## Mahalliy ishga tushirish
```
cd universal_bot
cp .env.example .env      # tokenni yozing
pip install -r requirements.txt
python bot.py             # ffmpeg o'rnatilgan bo'lishi kerak
```

## Railway'ga joylash
1. Bu reponi GitHub'ga yuklang (private tavsiya etiladi). `.env` fayl repoga tushmaydi.
2. railway.com → **New Project → Deploy from GitHub repo** → shu reponi tanlang.
3. Xizmat sozlamalari → **Source → Root Directory**: `/universal_bot`
   (ikkinchi bot uchun yangi xizmat qo'shing, Root Directory: `/video_taker_bot`).
4. **Variables** bo'limiga `.env.example` dagi qiymatlarni kiriting (`BOT_TOKEN` majburiy).
5. Faqat universal bot uchun: **Volume** qo'shing, mount path: `/data`
   (limit/premium bazasi va Whisper modeli shu yerda saqlanadi, volume bo'lmasa har deploy'da yo'qoladi).
6. Deploy tugagach **Logs** da xato yo'qligini tekshiring va botga `/start` yuboring.

Eslatmalar:
- Replika soni 1 bo'lsin (polling bot bir nechta nusxada ishlamaydi).
- Railway'da `WHISPER_MODEL=base` yoki `tiny` bilan boshlang: kamroq RAM va protsessor ketadi.
- Railway sozlamalar faylini (`railway.toml`) Root Directory'ga ergashtirmasligi mumkin; kerak bo'lsa
  Settings → Config-as-code da yo'lni `/universal_bot/railway.toml` deb ko'rsating. Dockerfile baribir avtomatik topiladi.
