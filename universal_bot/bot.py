"""
Universal media bot (Telegram, aiogram 3):
  - havola bo'yicha video / MP3 yuklash (yt-dlp)
  - ovoz, audio, video -> matn (faster-whisper, bepul, serverning o'zida)
  - matn -> o'zbekcha xulosa (Claude API, ixtiyoriy)
  - video siqish, videodan MP3
  - kunlik bepul limit + Telegram Stars orqali Premium
  - ixtiyoriy majburiy kanalga obuna
"""
import asyncio
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile,
                           InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, LabeledPrice, Message,
                           PreCheckoutQuery, ReplyKeyboardMarkup)
from dotenv import load_dotenv
from yt_dlp import YoutubeDL

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")  # tiny/base/small/medium
MAX_MB = int(os.getenv("MAX_MB", "50"))              # Telegram yuklash limiti
MAX_MIN = int(os.getenv("MAX_MIN", "60"))            # matnga o'girish uchun maks. davomiylik
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "2"))
FREE_DAILY = int(os.getenv("FREE_DAILY", "5"))
PREMIUM_STARS = int(os.getenv("PREMIUM_STARS", "150"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "")  # masalan: @mening_kanalim
DB_PATH = os.getenv("DB_PATH", "bot.db")

URL_RE = re.compile(r"https?://\S+")
LABELS = {"video": "🎬 Video", "mp3": "🎵 MP3", "text": "📝 Matn",
          "sum": "🧠 Xulosa", "small": "🗜 Siqish"}
BTN_ME, BTN_PREMIUM, BTN_HELP = "📊 Limitim", "⭐ Premium", "ℹ️ Yordam"

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
sem = asyncio.Semaphore(MAX_PARALLEL)
whisper_sem = asyncio.Semaphore(1)  # transkripsiya CPU'ni ko'p yeydi
JOBS: dict[str, dict] = {}


class UserError(Exception):
    pass


# ---------------------------------------------------------------- baza / limit
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, "
           "premium_until INTEGER DEFAULT 0, day TEXT DEFAULT '', used INTEGER DEFAULT 0)")
db.commit()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def use_quota(uid: int) -> bool:
    db.execute("INSERT OR IGNORE INTO users(id) VALUES (?)", (uid,))
    premium_until, day, used = db.execute(
        "SELECT premium_until, day, used FROM users WHERE id=?", (uid,)).fetchone()
    if premium_until > time.time():
        return True
    if day != _today():
        used = 0
    if used >= FREE_DAILY:
        return False
    db.execute("UPDATE users SET day=?, used=? WHERE id=?", (_today(), used + 1, uid))
    db.commit()
    return True


def refund_quota(uid: int):
    db.execute("UPDATE users SET used = MAX(used-1, 0) WHERE id=? AND day=?", (uid, _today()))
    db.commit()


def add_premium(uid: int):
    db.execute("INSERT OR IGNORE INTO users(id) VALUES (?)", (uid,))
    old = db.execute("SELECT premium_until FROM users WHERE id=?", (uid,)).fetchone()[0]
    new = max(int(time.time()), old) + PREMIUM_DAYS * 86400
    db.execute("UPDATE users SET premium_until=? WHERE id=?", (new, uid))
    db.commit()
    return new


def status_text(uid: int) -> str:
    row = db.execute("SELECT premium_until, day, used FROM users WHERE id=?", (uid,)).fetchone()
    if row and row[0] > time.time():
        until = datetime.fromtimestamp(row[0]).strftime("%d.%m.%Y")
        return f"⭐ Premium faol: {until} gacha, cheksiz."
    used = row[2] if row and row[1] == _today() else 0
    return f"Bugun ishlatildi: {used}/{FREE_DAILY}. Cheksiz uchun /premium"


# ------------------------------------------------------------------ yordamchilar
async def check_sub(bot: Bot, uid: int) -> bool:
    if not REQUIRED_CHANNEL:
        return True
    try:
        m = await bot.get_chat_member(REQUIRED_CHANNEL, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return True  # bot kanalda admin bo'lmasa foydalanuvchini to'smaymiz


async def gate(bot: Bot, chat_id: int, uid: int) -> bool:
    """Obuna va limitni tekshiradi; o'tsa limitdan 1 ta ishlatadi."""
    if not await check_sub(bot, uid):
        await bot.send_message(
            chat_id, f"Botdan foydalanish uchun avval kanalga obuna bo'ling: "
                     f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}\nKeyin qaytadan yuboring.")
        return False
    if not use_quota(uid):
        buy = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text=f"⭐ Premium olish ({PREMIUM_STARS} ⭐)", callback_data="buy")]])
        await bot.send_message(
            chat_id, f"Bugungi bepul limit ({FREE_DAILY}) tugadi.\n"
                     f"Cheksiz foydalanish: {PREMIUM_STARS} ⭐ / {PREMIUM_DAYS} kun.",
            reply_markup=buy)
        return False
    return True


def make_job(job: dict) -> str:
    token = uuid.uuid4().hex[:10]
    JOBS[token] = job
    if len(JOBS) > 2000:
        JOBS.pop(next(iter(JOBS)))
    return token


def keyboard(token: str, rows: list[list[str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LABELS[a], callback_data=f"act:{a}:{token}") for a in row]
        for row in rows])


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ME), KeyboardButton(text=BTN_PREMIUM)],
                  [KeyboardButton(text=BTN_HELP)]],
        resize_keyboard=True, input_field_placeholder="Havola yoki fayl yuboring...")


def check_size(p: Path):
    mb = p.stat().st_size / 1048576
    if mb > MAX_MB:
        raise UserError(f"Natija juda katta ({mb:.0f} MB), limit {MAX_MB} MB.")


def safe_name(title: str, default: str) -> str:
    return re.sub(r"[^\w\- ]", "", title).strip()[:60] or default


async def send_long(bot: Bot, chat_id: int, text: str, prefix: str = ""):
    if len(prefix + text) <= 4000:
        await bot.send_message(chat_id, prefix + text)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "matn.txt"
            p.write_text(text, encoding="utf-8")
            await bot.send_document(chat_id, FSInputFile(p), caption=prefix.strip() or None)


# ------------------------------------------------------------ og'ir ishlar (sync)
def ytdlp_download(url: str, out_dir: str, audio_only: bool, max_minutes):
    fmt = "bestaudio/best" if audio_only else (
        f"b[filesize<{MAX_MB}M]/b[filesize_approx<{MAX_MB}M]/b[height<=480]/b[height<=360]/b")
    opts = {"outtmpl": f"{out_dir}/%(id)s.%(ext)s", "format": fmt,
            "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
            "no_warnings": True, "socket_timeout": 30, "retries": 3}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if max_minutes and (info.get("duration") or 0) > max_minutes * 60:
            raise UserError(f"Video juda uzun (maksimum {max_minutes} daqiqa).")
        done = ydl.process_ie_result(info, download=True)
        try:
            path = Path(done["requested_downloads"][0]["filepath"])
        except Exception:
            path = Path(ydl.prepare_filename(done))
        if not path.exists():
            path = path.with_suffix(".mp4")
        return path, (done.get("title") or "")


def ffmpeg(*args: str):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


_whisper = None


def transcribe(path: Path) -> str:
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = _whisper.transcribe(str(path), vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


async def summarize(text: str) -> str:
    if not ANTHROPIC_KEY:
        raise UserError("Xulosa funksiyasi sozlanmagan (ANTHROPIC_API_KEY yo'q).")
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=ANTHROPIC_KEY)
    r = await client.messages.create(
        model=CLAUDE_MODEL, max_tokens=1200,
        messages=[{"role": "user", "content":
                   "Quyidagi matnning o'zbek tilida qisqa xulosasini yoz: 5-8 ta asosiy fikr, "
                   "aniq va sodda. Matn boshqa tilda bo'lsa ham xulosa o'zbekcha bo'lsin.\n\n"
                   + text[:60000]}])
    return r.content[0].text


# ------------------------------------------------------------------- asosiy ish
async def process(bot: Bot, chat_id: int, action: str, job: dict):
    loop = asyncio.get_running_loop()
    with tempfile.TemporaryDirectory() as tmp:
        title = ""
        if job["type"] == "url":
            audio_only = action in ("mp3", "text", "sum")
            limit = MAX_MIN if action in ("text", "sum") else None
            path, title = await loop.run_in_executor(
                None, ytdlp_download, job["url"], tmp, audio_only, limit)
        else:
            path = Path(tmp) / "input.bin"
            await bot.download(job["file_id"], destination=path)

        if action == "video":
            check_size(path)
            try:
                await bot.send_video(chat_id, FSInputFile(path), supports_streaming=True)
            except TelegramBadRequest:
                await bot.send_document(chat_id, FSInputFile(path))

        elif action == "mp3":
            out = Path(tmp) / "audio.mp3"
            await loop.run_in_executor(None, lambda: ffmpeg("-i", str(path), "-vn", "-b:a", "128k", str(out)))
            check_size(out)
            name = safe_name(title, "audio")
            await bot.send_audio(chat_id, FSInputFile(out, filename=f"{name}.mp3"), title=title or None)

        elif action == "small":
            out = Path(tmp) / "small.mp4"
            await loop.run_in_executor(None, lambda: ffmpeg(
                "-i", str(path), "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "30",
                "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", str(out)))
            check_size(out)
            await bot.send_video(chat_id, FSInputFile(out), supports_streaming=True)

        elif action in ("text", "sum"):
            async with whisper_sem:
                text = await loop.run_in_executor(None, transcribe, path)
            if not text:
                raise UserError("Nutq topilmadi.")
            if action == "text":
                await send_long(bot, chat_id, text, "📝 ")
            else:
                summary = await summarize(text)
                await send_long(bot, chat_id, summary, "🧠 Xulosa:\n\n")
                if len(text) > 300:
                    await send_long(bot, chat_id, text)  # to'liq matn ham kerak bo'lsa


async def launch(bot: Bot, chat_id: int, uid: int, action: str, job: dict, status: Message):
    try:
        async with sem:
            await process(bot, chat_id, action, job)
        await status.delete()
    except UserError as e:
        refund_quota(uid)
        await status.edit_text(f"❌ {e}")
    except TelegramBadRequest as e:
        refund_quota(uid)
        if "too big" in str(e).lower():
            await status.edit_text("❌ Fayl 20 MB dan katta, Telegram bot uchun yuklashga ruxsat bermaydi.")
        else:
            logging.exception("telegram error")
            await status.edit_text("❌ Telegram xatosi. Qaytadan urinib ko'ring.")
    except Exception:
        refund_quota(uid)
        logging.exception("job failed")
        await status.edit_text("❌ Bajarib bo'lmadi. Havola noto'g'ri, video yopiq (private) "
                               "yoki sayt qo'llab-quvvatlanmaydi.")


# ------------------------------------------------------------------ buyruqlar
@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Salom! 👋 Men universal media botman:\n\n"
        "🔗 Havola yuboring (YouTube, Instagram, TikTok, Facebook, X va boshqalar) — "
        "video yoki MP3 yuklayman, matn va xulosa chiqaraman\n"
        "🎤 Ovozli xabar yuboring — matnga o'giraman\n"
        "🎬 Video/audio fayl yuboring — MP3, siqish, matn, xulosa\n\n"
        f"{status_text(m.from_user.id)}", reply_markup=main_menu())


@dp.message(Command("help"))
@dp.message(F.text == BTN_HELP)
async def help_cmd(m: Message):
    await m.answer("Havola yoki fayl yuboring, keyin kerakli tugmani bosing.\n"
                   f"Yuklash limiti {MAX_MB} MB, Telegram'dan keladigan fayllar 20 MB gacha.\n"
                   "/me — limitim, /premium — cheksiz tarif")


@dp.message(Command("me"))
@dp.message(F.text == BTN_ME)
async def me(m: Message):
    await m.answer(status_text(m.from_user.id))


async def send_premium_invoice(bot: Bot, chat_id: int):
    await bot.send_invoice(
        chat_id=chat_id, title="Premium obuna",
        description=f"{PREMIUM_DAYS} kun cheksiz foydalanish",
        payload="premium", provider_token="", currency="XTR",
        prices=[LabeledPrice(label="Premium", amount=PREMIUM_STARS)])


@dp.message(Command("premium"))
@dp.message(F.text == BTN_PREMIUM)
async def premium(m: Message, bot: Bot):
    await send_premium_invoice(bot, m.chat.id)


@dp.callback_query(F.data == "buy")
async def buy_cb(cb: CallbackQuery, bot: Bot):
    await cb.answer()
    await send_premium_invoice(bot, cb.message.chat.id)


@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(m: Message):
    until = add_premium(m.from_user.id)
    await m.answer(f"✅ To'lov qabul qilindi! Premium {datetime.fromtimestamp(until):%d.%m.%Y} gacha.")


@dp.message(Command("stats"))
async def stats(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
    total = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    prem = db.execute("SELECT COUNT(*) FROM users WHERE premium_until>?", (time.time(),)).fetchone()[0]
    await m.answer(f"Foydalanuvchilar: {total}\nPremium: {prem}")


# ---------------------------------------------------------- kontent qabul qilish
@dp.message(F.voice | F.video_note)
async def on_voice(m: Message, bot: Bot):
    if not await gate(bot, m.chat.id, m.from_user.id):
        return
    file_id = (m.voice or m.video_note).file_id
    status = await m.answer("⏳ Matnga o'girilmoqda...")
    await launch(bot, m.chat.id, m.from_user.id, "text", {"type": "file", "file_id": file_id}, status)


@dp.message(F.video | F.audio)
async def on_media(m: Message):
    file_id = (m.video or m.audio).file_id
    token = make_job({"type": "file", "file_id": file_id})
    rows = [["mp3", "small"], ["text", "sum"]] if m.video else [["text", "sum"]]
    await m.answer("Nima qilay?", reply_markup=keyboard(token, rows))


@dp.message(F.text.regexp(URL_RE))
async def on_link(m: Message):
    url = URL_RE.search(m.text).group(0)
    token = make_job({"type": "url", "url": url})
    await m.answer("Nima qilay?", reply_markup=keyboard(token, [["video", "mp3"], ["text", "sum"]]))


@dp.callback_query(F.data.startswith("act:"))
async def on_action(cb: CallbackQuery, bot: Bot):
    _, action, token = cb.data.split(":")
    job = JOBS.get(token)
    if not job:
        await cb.answer("Eskirgan so'rov. Havolani qaytadan yuboring.", show_alert=True)
        return
    await cb.answer()
    uid = cb.from_user.id
    if not await gate(bot, cb.message.chat.id, uid):
        return
    status = await cb.message.edit_text("⏳ Ishlanmoqda...")
    await launch(bot, cb.message.chat.id, uid, action, job, status)


@dp.message()
async def other(m: Message):
    await m.answer("Havola, ovozli xabar yoki video/audio fayl yuboring. Yordam: /help")


async def main():
    if not shutil.which("ffmpeg"):
        logging.warning("ffmpeg topilmadi! MP3, siqish va ba'zi yuklashlar ishlamaydi.")
    bot = Bot(TOKEN)
    await bot.set_my_commands([
        BotCommand(command="start", description="Boshlash"),
        BotCommand(command="me", description="Limitim"),
        BotCommand(command="premium", description="Cheksiz tarif"),
        BotCommand(command="help", description="Yordam"),
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
