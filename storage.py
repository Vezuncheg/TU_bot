"""Хранилище с двумя бэкендами:
- DATABASE_URL задан (Supabase/Postgres) → рабочий режим, данные переживают редеплои
- DATABASE_URL пуст → локальный SQLite для теста на своей машине
Интерфейс функций одинаковый, bot.py о бэкенде не знает.
"""
import json, time, threading
import config

_lock = threading.Lock()
PG = bool(config.DATABASE_URL)

if PG:
    import psycopg2
    import psycopg2.extras
    from psycopg2.pool import SimpleConnectionPool
    _pool = SimpleConnectionPool(1, 5, dsn=config.DATABASE_URL)

    class _Ctx:
        def __enter__(self):
            self.conn = _pool.getconn()
            self.conn.autocommit = True
            self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return self.cur
        def __exit__(self, *a):
            self.cur.close(); _pool.putconn(self.conn)

    def _q(sql):  # плейсхолдеры уже %s
        return sql
else:
    import sqlite3
    def _conn():
        c = sqlite3.connect(config.DB_PATH)
        c.row_factory = sqlite3.Row
        return c

    class _Ctx:
        def __enter__(self):
            self.conn = _conn()
            self.cur = self.conn.cursor()
            return self.cur
        def __exit__(self, *a):
            self.conn.commit(); self.conn.close()

    def _q(sql):
        return sql.replace("%s", "?")

def _fetchone(cur):
    r = cur.fetchone()
    return dict(r) if r else None

def _fetchall(cur):
    return [dict(r) for r in cur.fetchall()]

SERIAL = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
BIG = "BIGINT" if PG else "INTEGER"

def init_db():
    with _lock, _Ctx() as c:
        for sql in f"""
        CREATE TABLE IF NOT EXISTS users(
            user_id {BIG} PRIMARY KEY, username TEXT, first_name TEXT,
            created_at {BIG}, source TEXT, subscribed INTEGER DEFAULT 0,
            test_type TEXT, test_verdict TEXT, test_flags TEXT,
            test_hours TEXT, test_nights TEXT,
            magnets TEXT DEFAULT '[]', underage INTEGER DEFAULT 0,
            no_warmup INTEGER DEFAULT 0, last_seen {BIG});
        CREATE TABLE IF NOT EXISTS casting(
            id {SERIAL}, ts {BIG}, user_id {BIG},
            username TEXT, name TEXT, age INTEGER, city TEXT, exp TEXT,
            hours TEXT, nights TEXT, video_file_id TEXT, contact TEXT,
            score TEXT, status TEXT DEFAULT 'новая');
        CREATE TABLE IF NOT EXISTS franchise(
            id {SERIAL}, ts {BIG}, user_id {BIG},
            username TEXT, name TEXT, city TEXT, biz_exp TEXT, budget TEXT,
            timing TEXT, familiar TEXT, contact TEXT,
            score TEXT, status TEXT DEFAULT 'новая');
        CREATE TABLE IF NOT EXISTS events(
            id {SERIAL}, ts {BIG}, user_id {BIG}, event TEXT, details TEXT);
        CREATE TABLE IF NOT EXISTS followups(
            id {SERIAL}, user_id {BIG}, due_ts {BIG}, kind TEXT,
            payload TEXT, sent INTEGER DEFAULT 0);
        """.split(";"):
            if sql.strip():
                c.execute(sql)

def upsert_user(user_id, username, first_name, source):
    now = int(time.time())
    with _lock, _Ctx() as c:
        c.execute(_q("SELECT user_id FROM users WHERE user_id=%s"), (user_id,))
        if _fetchone(c):
            c.execute(_q("UPDATE users SET username=%s, first_name=%s, last_seen=%s WHERE user_id=%s"),
                      (username, first_name, now, user_id))
        else:
            c.execute(_q("INSERT INTO users(user_id,username,first_name,created_at,source,last_seen) "
                         "VALUES(%s,%s,%s,%s,%s,%s)"), (user_id, username, first_name, now, source, now))

def get_user(user_id):
    with _Ctx() as c:
        c.execute(_q("SELECT * FROM users WHERE user_id=%s"), (user_id,))
        return _fetchone(c)

def set_user(user_id, **kw):
    if not kw: return
    with _lock, _Ctx() as c:
        cols = ", ".join(f"{k}=%s" for k in kw)
        c.execute(_q(f"UPDATE users SET {cols} WHERE user_id=%s"), (*kw.values(), user_id))

def add_magnet(user_id, name):
    u = get_user(user_id) or {}
    mags = json.loads(u.get("magnets") or "[]")
    if name not in mags:
        mags.append(name)
        set_user(user_id, magnets=json.dumps(mags, ensure_ascii=False))

def log_event(user_id, event, details=""):
    with _lock, _Ctx() as c:
        c.execute(_q("INSERT INTO events(ts,user_id,event,details) VALUES(%s,%s,%s,%s)"),
                  (int(time.time()), user_id, event, details))

def recent_application(table, user_id, days=30):
    assert table in ("casting", "franchise")
    with _Ctx() as c:
        c.execute(_q(f"SELECT * FROM {table} WHERE user_id=%s AND ts>%s ORDER BY ts DESC LIMIT 1"),
                  (user_id, int(time.time()) - days*86400))
        return _fetchone(c)

def save_casting(**kw):
    with _lock, _Ctx() as c:
        c.execute(_q("INSERT INTO casting(ts,user_id,username,name,age,city,exp,hours,nights,"
                     "video_file_id,contact,score) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                  (int(time.time()), kw["user_id"], kw["username"], kw["name"], kw["age"],
                   kw["city"], kw["exp"], kw["hours"], kw["nights"],
                   kw.get("video_file_id"), kw["contact"], kw["score"]))

def attach_casting_video(user_id, file_id):
    with _lock, _Ctx() as c:
        c.execute(_q("UPDATE casting SET video_file_id=%s, score='hot' WHERE user_id=%s AND id="
                     "(SELECT id FROM casting WHERE user_id=%s ORDER BY ts DESC LIMIT 1)"),
                  (file_id, user_id, user_id))

def save_franchise(**kw):
    with _lock, _Ctx() as c:
        c.execute(_q("INSERT INTO franchise(ts,user_id,username,name,city,biz_exp,budget,timing,"
                     "familiar,contact,score) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"),
                  (int(time.time()), kw["user_id"], kw["username"], kw["name"], kw["city"],
                   kw["biz_exp"], kw["budget"], kw["timing"], kw["familiar"],
                   kw["contact"], kw["score"]))

def schedule(user_id, delay_seconds, kind, payload=""):
    with _lock, _Ctx() as c:
        c.execute(_q("INSERT INTO followups(user_id,due_ts,kind,payload) VALUES(%s,%s,%s,%s)"),
                  (user_id, int(time.time()) + delay_seconds, kind, payload))

def cancel_followups(user_id, kind_prefix=""):
    with _lock, _Ctx() as c:
        c.execute(_q("UPDATE followups SET sent=1 WHERE user_id=%s AND sent=0 AND kind LIKE %s"),
                  (user_id, kind_prefix + "%"))

def due_followups():
    with _Ctx() as c:
        c.execute(_q("SELECT * FROM followups WHERE sent=0 AND due_ts<=%s"), (int(time.time()),))
        return _fetchall(c)

def mark_sent(fid):
    with _lock, _Ctx() as c:
        c.execute(_q("UPDATE followups SET sent=1 WHERE id=%s"), (fid,))

def stats(days=7):
    with _Ctx() as c:
        c.execute(_q("SELECT event, COUNT(*) AS n FROM events WHERE ts>%s GROUP BY event ORDER BY n DESC"),
                  (int(time.time()) - days*86400,))
        return {r["event"]: r["n"] for r in _fetchall(c)}

# ---------- Google Sheets: опциональное зеркало (без изменений) ----------
_gs = None
def _sheet():
    global _gs
    if _gs is not None: return _gs
    if not (config.GSHEETS_CREDS and config.GSHEETS_SPREADSHEET_ID):
        _gs = False; return False
    try:
        import gspread
        gc = gspread.service_account(filename=config.GSHEETS_CREDS)
        _gs = gc.open_by_key(config.GSHEETS_SPREADSHEET_ID)
    except Exception:
        _gs = False
    return _gs

def mirror(sheet_name, row):
    sh = _sheet()
    if not sh: return
    try:
        try:
            ws = sh.worksheet(sheet_name)
        except Exception:
            ws = sh.add_worksheet(sheet_name, rows=1000, cols=20)
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception:
        pass
