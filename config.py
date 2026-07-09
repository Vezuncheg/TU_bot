"""Конфигурация — все настройки в одном месте. Значения берутся из .env"""
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@teamuniverse")   # канал для проверки подписки
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/teamuniverse")

# user_id получателей карточек (число). Можно указать id рабочей группы (отрицательное).
DENIS_CHAT_ID = int(os.getenv("DENIS_CHAT_ID", "0"))    # кастинг
ARTEM_CHAT_ID = int(os.getenv("ARTEM_CHAT_ID", "0"))    # франшиза
WORK_CHAT_ID = int(os.getenv("WORK_CHAT_ID", "0"))      # /help, названия агентств, дайджест
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

DB_PATH = os.getenv("DB_PATH", "tu_bot.sqlite3")   # локальный SQLite для теста

# Supabase/Postgres: если задан — бот работает на нём (рабочий режим).
# Брать в Supabase: Settings → Database → Connection string → Session pooler (URI).
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Google Sheets (опционально): путь к JSON сервис-аккаунта и id таблицы.
# Если не заданы — бот работает только на SQLite, ничего не ломается.
GSHEETS_CREDS = os.getenv("GSHEETS_CREDS", "")
GSHEETS_SPREADSHEET_ID = os.getenv("GSHEETS_SPREADSHEET_ID", "")

# Файлы магнитов (положить рядом или указать путь)
ANTISCAM_PDF = os.getenv("ANTISCAM_PDF", "assets/antiscam_checklist.pdf")
GUIDE30_PDF = os.getenv("GUIDE30_PDF", "assets/guide30.pdf")

FOLLOWUP_CHECK_SECONDS = 600   # период проверки отложенных сообщений
