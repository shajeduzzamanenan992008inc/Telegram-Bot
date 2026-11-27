"""TELEGRAM BOT - starter bot (aiogram 3.x)
Creator & Builder: Shajeduzzaman Enan

Run:  pip install -r requirements.txt  ->  copy .env.example to .env (add BOT_TOKEN)  ->  python bot.py
It sends the menu header images from ../13-menu-cards and the welcome video from ../10-video.
"""
import asyncio
import logging
import os
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)


def load_env(path: Path) -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_env(Path(__file__).with_name(".env"))

PACK = Path(__file__).resolve().parent.parent
CARDS = PACK / "13-menu-cards"
WELCOME_VIDEO = PACK / "10-video" / "welcome-animation-1280x640.mp4"
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/your_support")
CREDIT = "👨‍💻 <b>Creator &amp; Builder:</b> Shajeduzzaman Enan"

router = Router()

SECTIONS = {
    "menu": ("menu", "🧭 <b>Main menu</b>\nChoose an option below."),
    "profile": ("profile", "👤 <b>Your profile</b>\nName: {name}\nID: <code>{uid}</code>"),
    "premium": ("premium", "👑 <b>Unlock Premium</b>\n⚡ Faster responses\n🔓 Every feature\n🎧 Priority support"),
    "stats": ("stats", "📊 <b>Statistics</b>\nConnect your database to show live numbers here."),
    "settings": ("settings", "⚙️ <b>Settings</b>\nPick your preferences."),
    "help": ("help", "📖 <b>Help</b>\n/start  /menu  /profile  /premium  /about"),
}


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [b(text="⚡ Menu", callback_data="menu"), b(text="👤 Profile", callback_data="profile")],
        [b(text="👑 Premium", callback_data="premium"), b(text="📊 Stats", callback_data="stats")],
        [b(text="⚙️ Settings", callback_data="settings"), b(text="🎧 Support", url=SUPPORT_URL)],
    ])


async def send_card(m: Message, card: str, caption: str) -> None:
    path = CARDS / f"card-{card}.jpg"
    if path.exists():
        await m.answer_photo(FSInputFile(path), caption=caption, reply_markup=main_menu())
    else:
        await m.answer(caption, reply_markup=main_menu())


@router.message(CommandStart())
async def start(m: Message) -> None:
    caption = (f"⚡ <b>Welcome, {escape(m.from_user.full_name)}!</b>\n"
               "Lightning-fast replies. Smart tools. Zero downtime.\n\n" + CREDIT)
    if WELCOME_VIDEO.exists():
        await m.answer_animation(FSInputFile(WELCOME_VIDEO), caption=caption, reply_markup=main_menu())
    else:
        await send_card(m, "welcome", caption)


@router.message(Command("menu", "help", "profile", "premium"))
async def command_section(m: Message) -> None:
    key = m.text.lstrip("/").split("@")[0].split()[0]
    card, text = SECTIONS[key]
    await send_card(m, card, text.format(name=escape(m.from_user.full_name), uid=m.from_user.id))


@router.message(Command("about"))
async def about(m: Message) -> None:
    await send_card(m, "about", "ℹ️ <b>TELEGRAM BOT</b>\nLightning-fast Telegram assistant.\n\n" + CREDIT)


@router.callback_query(F.data.in_(set(SECTIONS)))
async def section(call: CallbackQuery) -> None:
    card, text = SECTIONS[call.data]
    await send_card(call.message, card, text.format(name=escape(call.from_user.full_name), uid=call.from_user.id))
    await call.answer()


async def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN in .env first")
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
