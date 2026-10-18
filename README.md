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

