"""Переадресатор для прежнего бота @Tik_Tok_team_bot (переезд 03.11.2026).
На любое сообщение отвечает «Мы переехали» и даёт кнопку в новый бот. Метка из ссылки (?start=test_post12 и др.)
переносится в новый бот — люди из старых постов попадают на тот же экран.
Запуск — отдельным сервисом Railway из этого же репозитория: Start Command `python redirect.py`,
переменная BOT_TOKEN = токен ПРЕЖНЕГО бота. База не нужна: DATABASE_URL этому сервису не задаём."""
import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
NEW_BOT = os.getenv("NEW_BOT", "TeamUniverse_app")
TEXT = ("Мы переехали 🪐\n\nТеперь TeamUniverse — здесь: @{bot}\n"
        "Нажми кнопку ниже и «Старт» — всё, что было, ждёт тебя там.")

dp = Dispatcher()


def kb(arg=""):
    url = f"https://t.me/{NEW_BOT}" + (f"?start={arg}" if arg else "")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Перейти в новый бот", url=url, style="success")]])


@dp.message(F.chat.type == "private", CommandStart())
async def on_start(msg: Message, command: CommandObject):
    arg = re.sub(r"[^\w\-]", "", (command.args or "").strip())[:64]
    await msg.answer(TEXT.format(bot=NEW_BOT), reply_markup=kb(arg))


@dp.message(F.chat.type == "private")
async def on_any(msg: Message):
    await msg.answer(TEXT.format(bot=NEW_BOT), reply_markup=kb())


@dp.callback_query()
async def on_button(cb: CallbackQuery):
    """Старые кнопки в чате прежнего бота."""
    await cb.answer()
    await cb.message.answer(TEXT.format(bot=NEW_BOT), reply_markup=kb())


async def main():
    bot = Bot(os.environ["BOT_TOKEN"])
    await bot.delete_my_commands()
    await bot.set_my_short_description(short_description=f"Мы переехали → @{NEW_BOT}")
    await bot.set_my_description(description=f"Мы переехали 🪐\nTeamUniverse теперь здесь: @{NEW_BOT}")
    logging.info(f"переадресатор запущен → @{NEW_BOT}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
