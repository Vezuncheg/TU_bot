"""Настройки бота. Значения — из переменных окружения (Railway → Variables) или файла .env.
Имена прежних переменных сохранены: новый бот запускается на том же наборе, что и старый."""
import os
from datetime import timedelta, timezone
from dotenv import load_dotenv

load_dotenv()


def _int(name, default=0):
    try:
        return int(os.getenv(name, str(default)) or default)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")          # Supabase (Session pooler). Пусто — локальный SQLite
DB_PATH = os.getenv("DB_PATH", "tu_bot.sqlite3")

# Куда приходят заявки и вопросы. WORK_CHAT_ID — рабочая группа (если задана), иначе — лично Артёму.
ARTEM_CHAT_ID = _int("ARTEM_CHAT_ID")
WORK_CHAT_ID = _int("WORK_CHAT_ID")
LEADS_CHAT_ID = _int("LEADS_CHAT_ID") or WORK_CHAT_ID or ARTEM_CHAT_ID
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
if ARTEM_CHAT_ID > 0:
    ADMIN_IDS.add(ARTEM_CHAT_ID)

CHANNEL_URL = "https://t.me/teamuniverse_live"      # новый канал (решение 19.10); старая переменная CHANNEL_URL не используется
# Канал для проверки подписки перед материалами (бот должен быть его администратором).
# Старая переменная CHANNEL_ID указывала на прежний канал — не используется.
SUB_CHANNEL = os.getenv("SUB_CHANNEL_ID", "@teamuniverse_live")

# Аватарка бота: ставится один раз при первом запуске новой версии (флаг в таблице kv)
SET_AVATAR = os.getenv("SET_AVATAR", "1") == "1"

# Время — по Москве (без перехода на летнее время)
MSK = timezone(timedelta(hours=3))

# Запись на разговор: рабочие часы, длина окна, шаг, минимум до начала (как в прототипе)
SLOT_FROM_H = _int("SLOT_FROM_H", 11)
SLOT_TO_H = _int("SLOT_TO_H", 21)
SLOT_LEN_MIN = _int("SLOT_LEN_MIN", 30)
SLOT_STEP_MIN = _int("SLOT_STEP_MIN", 90)
SLOT_MIN_AHEAD_MIN = _int("SLOT_MIN_AHEAD_MIN", 60)

# «Далее» в партнёрстве: через сколько секунд следующее преимущество уходит само
PARTNER_NEXT_SEC = _int("PARTNER_NEXT_SEC", 60)

# Цикл отложенных сообщений (секунды)
SCHEDULER_TICK = _int("SCHEDULER_TICK", 5)

# Канал/чат для предзагрузки медиа (необязательно): бот один раз отправляет туда все файлы и запоминает file_id,
# чтобы первый человек не ждал загрузки. Пусто — файлы загружаются при первом показе.
MEDIA_WARMUP_CHAT_ID = _int("MEDIA_WARMUP_CHAT_ID")
