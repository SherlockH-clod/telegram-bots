import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
from dotenv import load_dotenv
from yt_dlp import YoutubeDL

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]

# Oddiy Bot API orqali yuklash limiti 50 MB. Local Bot API server bilan 2000 MB gacha oshirsa bo'ladi.
MAX_MB = int(os.getenv("MAX_MB", "50"))
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "3"))
URL_RE = re.compile(r"https?://\S+")

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
sem = asyncio.Semaphore(MAX_PARALLEL)


def download(url: str, out_dir: str) -> Path:
    opts = {
        "outtmpl": f"{out_dir}/%(id)s.%(ext)s",
        "format": (
            f"b[filesize<{MAX_MB}M]/b[filesize_approx<{MAX_MB}M]/"
            "b[height<=480]/b[height<=360]/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
        if not path.exists():
            path = path.with_suffix(".mp4")
        return path


@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "Salom! 👋\nMenga YouTube, Instagram, TikTok, Facebook, X (Twitter), "
        "Pinterest, Reddit va boshqa saytlardan video havolasini yuboring — "
        "men uni yuklab beraman."
    )


@dp.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer("Shunchaki video havolasini yuboring. Limit: "
                   f"{MAX_MB} MB. Katta videolar kichikroq sifatda yuklanadi.")


@dp.message(F.text.regexp(URL_RE))
async def handle_link(m: Message):
    url = URL_RE.search(m.text).group(0)
    status = await m.answer("⏳ Yuklanmoqda...")
    async with sem:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                loop = asyncio.get_running_loop()
                path = await loop.run_in_executor(None, download, url, tmp)
                size_mb = path.stat().st_size / 1024 / 1024
                if size_mb > MAX_MB:
                    await status.edit_text(
                        f"❌ Video juda katta ({size_mb:.0f} MB). Limit {MAX_MB} MB.")
                    return
                await status.edit_text("📤 Yuborilmoqda...")
                try:
                    await m.answer_video(FSInputFile(path), supports_streaming=True)
                except Exception:
                    await m.answer_document(FSInputFile(path))
                await status.delete()
            except Exception as e:
                logging.exception("download failed")
                await status.edit_text(
                    "❌ Yuklab bo'lmadi. Havola noto'g'ri, video yopiq (private) "
                    "yoki sayt qo'llab-quvvatlanmaydi.")


@dp.message()
async def other(m: Message):
    await m.answer("Iltimos, video havolasini (https://...) yuboring.")


async def main():
    bot = Bot(TOKEN)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
