"""Применить изменения базы (migrations/*.sql). Только добавляет таблицы и столбцы, старые данные не трогает.
Запуск вручную: python migrate.py  (бот при старте вызывает то же самое — повторный запуск безопасен)."""
import os
import re
import sys

import config
import db

HERE = os.path.dirname(os.path.abspath(__file__))

# Таблицы прежнего бота — на случай чистой базы (в боевой Supabase они уже есть, IF NOT EXISTS их не тронет)
BASE = """
CREATE TABLE IF NOT EXISTS users(
    user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT,
    created_at BIGINT, source TEXT, subscribed INTEGER DEFAULT 0,
    test_type TEXT, test_verdict TEXT, test_flags TEXT,
    test_hours TEXT, test_nights TEXT,
    magnets TEXT DEFAULT '[]', underage INTEGER DEFAULT 0,
    no_warmup INTEGER DEFAULT 0, last_seen BIGINT);
CREATE TABLE IF NOT EXISTS events(
    id SERIAL PRIMARY KEY, ts BIGINT, user_id BIGINT, event TEXT, details TEXT);
"""


def _statements(sql):
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def _sqlite(stmt, cur):
    """Перевод на диалект SQLite: SERIAL → AUTOINCREMENT, ADD COLUMN IF NOT EXISTS → проверка столбцов."""
    m = re.match(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+) (.+)", stmt, re.S)
    if m:
        table, col, typ = m.groups()
        cur.execute(f"PRAGMA table_info({table})")
        if col not in [r[1] for r in cur.fetchall()]:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        return
    stmt = stmt.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("BIGINT", "INTEGER")
    cur.execute(stmt)


def run(verbose=False):
    files = sorted(f for f in os.listdir(os.path.join(HERE, "migrations")) if f.endswith(".sql"))
    stmts = _statements(BASE)
    for f in files:
        with open(os.path.join(HERE, "migrations", f), encoding="utf-8") as fh:
            stmts += _statements(fh.read())
    with db._Ctx() as cur:
        for s in stmts:
            if db.PG:
                cur.execute(s)
            else:
                _sqlite(s, cur)
    if verbose:
        print(f"ok: {len(stmts)} команд, база: {'Postgres' if db.PG else 'SQLite ' + config.DB_PATH}")


if __name__ == "__main__":
    run(verbose=True)
    sys.exit(0)
