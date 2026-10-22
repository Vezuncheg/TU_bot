-- Бот v2 (стиль Б, 22.10.2026). Только добавляет: ничего не удаляет и не переименовывает.
-- Повторный запуск безопасен (IF NOT EXISTS). Старый бот с этими изменениями работает как раньше.
-- Можно выполнить вручную: Supabase → SQL Editor → вставить файл → Run. Бот при старте применяет его сам (migrate.py).

-- Существующая таблица людей: новые столбцы
ALTER TABLE users ADD COLUMN IF NOT EXISTS session TEXT;          -- где человек в воронке и его ответы (JSON)
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_source TEXT;      -- последняя метка ?start= (первая — в source, не перезаписывается)
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS branch TEXT;           -- 'team' (в команде) / 'partner'
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at BIGINT;

-- Заявки и записи на разговор (обе ветки)
CREATE TABLE IF NOT EXISTS leads(
    id SERIAL PRIMARY KEY,
    ts BIGINT, updated_ts BIGINT,
    user_id BIGINT, username TEXT, name TEXT,
    branch TEXT,                 -- team / partner
    answers TEXT,                -- JSON: ответы опроса, теста, анкеты
    way TEXT,                    -- call / chat
    phone TEXT,
    slot_ts BIGINT,              -- время разговора (unix, UTC)
    status TEXT DEFAULT 'новая', -- новая / анкета / записана / перенесена / не актуально
    source TEXT,
    video_card TEXT,             -- file_id видео-визитки
    video_battle TEXT            -- file_id видео баттла
);
CREATE INDEX IF NOT EXISTS leads_user_idx ON leads(user_id);
CREATE INDEX IF NOT EXISTS leads_slot_idx ON leads(slot_ts);

-- Очередь отложенных сообщений: «Далее» в партнёрстве, напоминания о встрече, дожим. Переживает перезапуск.
CREATE TABLE IF NOT EXISTS scheduled(
    id SERIAL PRIMARY KEY,
    user_id BIGINT, kind TEXT, payload TEXT,
    due_ts BIGINT, status TEXT DEFAULT 'pending',   -- pending / sent / cancelled
    created_ts BIGINT
);
CREATE INDEX IF NOT EXISTS scheduled_due_idx ON scheduled(status, due_ts);
CREATE INDEX IF NOT EXISTS scheduled_user_idx ON scheduled(user_id, status);

-- file_id загруженных в Telegram файлов: каждый файл грузится один раз
CREATE TABLE IF NOT EXISTS media_cache(
    key TEXT PRIMARY KEY, file_id TEXT, updated_ts BIGINT
);

-- Ответ представителя человеку: ответить (reply) на карточку заявки в рабочем чате
CREATE TABLE IF NOT EXISTS relay(
    chat_id BIGINT, message_id BIGINT, user_id BIGINT,
    PRIMARY KEY(chat_id, message_id)
);

-- Служебные флаги (например, «аватарка уже поставлена»)
CREATE TABLE IF NOT EXISTS kv(
    k TEXT PRIMARY KEY, v TEXT
);

CREATE INDEX IF NOT EXISTS events_ts_idx ON events(ts);
CREATE INDEX IF NOT EXISTS events_user_idx ON events(user_id);
