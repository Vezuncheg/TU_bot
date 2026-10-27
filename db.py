"""База: Supabase/Postgres (DATABASE_URL задан) или локальный SQLite (для проверки на своём компьютере).
Все функции синхронные — бот вызывает их через asyncio.to_thread, чтобы не тормозить ответы."""
import json
import threading
import time

import config

PG = bool(config.DATABASE_URL)
_lock = threading.Lock()
_SIM = float(__import__("os").getenv("SIM_DB_MS", "0")) / 1000      # только для проверки: имитация задержки сети до базы

if PG:
    import psycopg2
    import psycopg2.extras
    from psycopg2.pool import ThreadedConnectionPool

    _pool = None
    _pool_lock = threading.Lock()
    # 27.10: раньше пул был (1, 8) — psycopg2 держит открытыми только minconn соединений, остальные закрывает.
    # Каждое нажатие пишет в базу 4–5 раз параллельно → новое подключение к Supabase на каждую запись (≈0,5–1 с),
    # а больше 8 записей сразу — ошибка «pool exhausted» и экран не приходит. Теперь держим все соединения открытыми,
    # а при нехватке ждём свободное.
    _POOL_N = max(2, int(__import__("os").getenv("DB_POOL", "6")))
    _slots = threading.BoundedSemaphore(_POOL_N)
    _IDLE_MAX = 240          # соединение без дела дольше 4 минут переоткрываем: пулер Supabase может его уже закрыть
    _last_use = {}           # id(conn) → время последнего использования

    def _get_pool():
        global _pool
        if _pool is None:
            with _pool_lock:
                if _pool is None:
                    _pool = ThreadedConnectionPool(1, _POOL_N, dsn=config.DATABASE_URL, keepalives=1,
                                                   keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
                    _pool.minconn = _POOL_N      # открываем по мере надобности, но обратно в пул кладём все
        return _pool

    class _Ctx:
        def __enter__(self):
            if _SIM:
                time.sleep(_SIM)
            _slots.acquire()
            try:
                pool = _get_pool()
                conn = pool.getconn()
                if conn.closed or time.time() - _last_use.get(id(conn), time.time()) > _IDLE_MAX:
                    _last_use.pop(id(conn), None)
                    pool.putconn(conn, close=True)
                    conn = pool.getconn()
                self.conn = conn
                self.conn.autocommit = True
                self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            except Exception:
                _slots.release()
                raise
            return self.cur

        def __exit__(self, et, ev, tb):
            try:
                self.cur.close()
            finally:
                bad = bool(et and isinstance(ev, (psycopg2.OperationalError, psycopg2.InterfaceError)))
                _last_use[id(self.conn)] = time.time()
                if bad:
                    _last_use.pop(id(self.conn), None)
                # соединение с ошибкой не возвращаем в пул целым — закрываем
                try:
                    _get_pool().putconn(self.conn, close=bad)
                finally:
                    _slots.release()

    def _q(sql):
        return sql
else:
    import sqlite3

    _sconn = None

    def _get_conn():
        global _sconn
        if _sconn is None:
            _sconn = sqlite3.connect(config.DB_PATH, check_same_thread=False, isolation_level=None)
            _sconn.row_factory = sqlite3.Row
            _sconn.execute("PRAGMA journal_mode=WAL")
        return _sconn

    class _Ctx:
        def __enter__(self):
            if _SIM:
                time.sleep(_SIM)
            _lock.acquire()
            self.cur = _get_conn().cursor()
            return self.cur

        def __exit__(self, *a):
            try:
                self.cur.close()
            finally:
                _lock.release()

    def _q(sql):
        return sql.replace("%s", "?")


def _one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def _all(cur):
    return [dict(r) for r in cur.fetchall()]


def now():
    return int(time.time())


# ---------------- люди ----------------
def get_user(uid):
    with _Ctx() as c:
        c.execute(_q("SELECT * FROM users WHERE user_id=%s"), (uid,))
        return _one(c)


def upsert_user(uid, username, first_name, source):
    """Первая метка (source) не перезаписывается; последняя — в last_source. Возвращает (user, новый_ли)."""
    t = now()
    with _Ctx() as c:
        c.execute(_q("SELECT * FROM users WHERE user_id=%s"), (uid,))
        u = _one(c)
        if u:
            c.execute(_q("UPDATE users SET username=%s, first_name=%s, last_seen=%s, last_source=%s WHERE user_id=%s"),
                      (username, first_name, t, source, uid))
            u.update(username=username, first_name=first_name, last_seen=t, last_source=source)
            return u, False
        c.execute(_q("INSERT INTO users(user_id,username,first_name,created_at,source,last_source,last_seen) "
                     "VALUES(%s,%s,%s,%s,%s,%s,%s)"), (uid, username, first_name, t, source, source, t))
        c.execute(_q("SELECT * FROM users WHERE user_id=%s"), (uid,))
        return _one(c), True


def set_user(uid, **kw):
    if not kw:
        return
    kw["updated_at"] = now()
    cols = ", ".join(f"{k}=%s" for k in kw)
    with _Ctx() as c:
        c.execute(_q(f"UPDATE users SET {cols} WHERE user_id=%s"), (*kw.values(), uid))


def save_session(uid, session):
    set_user(uid, session=json.dumps(session, ensure_ascii=False))


def load_session(uid):
    u = get_user(uid)
    if not u or not u.get("session"):
        return None
    try:
        return json.loads(u["session"])
    except Exception:
        return None


# ---------------- журнал ----------------
def log_event(uid, event, details=""):
    with _Ctx() as c:
        c.execute(_q("INSERT INTO events(ts,user_id,event,details) VALUES(%s,%s,%s,%s)"),
                  (now(), uid, event, str(details)[:500]))


# ---------------- медиа ----------------
def media_get(key):
    with _Ctx() as c:
        c.execute(_q("SELECT file_id FROM media_cache WHERE key=%s"), (key,))
        r = _one(c)
        return r["file_id"] if r else None


def media_set(key, file_id):
    with _Ctx() as c:
        if PG:
            c.execute("INSERT INTO media_cache(key,file_id,updated_ts) VALUES(%s,%s,%s) "
                      "ON CONFLICT(key) DO UPDATE SET file_id=EXCLUDED.file_id, updated_ts=EXCLUDED.updated_ts",
                      (key, file_id, now()))
        else:
            c.execute("INSERT OR REPLACE INTO media_cache(key,file_id,updated_ts) VALUES(?,?,?)", (key, file_id, now()))


def media_all():
    with _Ctx() as c:
        c.execute("SELECT key, file_id FROM media_cache")
        return {r["key"]: r["file_id"] for r in _all(c)}


# ---------------- очередь отложенных сообщений ----------------
def schedule(uid, kind, due_ts, payload=None):
    with _Ctx() as c:
        c.execute(_q("INSERT INTO scheduled(user_id,kind,payload,due_ts,status,created_ts) VALUES(%s,%s,%s,%s,'pending',%s)"),
                  (uid, kind, json.dumps(payload or {}, ensure_ascii=False), int(due_ts), now()))


def reschedule(uid, kind, due_ts, payload=None):
    """Заменить ожидающее сообщение вида kind новым — отмена и запись одной операцией, по порядку
    (раньше шли двумя фоновыми задачами, и отмена иногда стирала только что записанный таймер)."""
    with _Ctx() as c:
        c.execute(_q("UPDATE scheduled SET status='cancelled' WHERE user_id=%s AND status='pending' AND kind=%s"), (uid, kind))
        c.execute(_q("INSERT INTO scheduled(user_id,kind,payload,due_ts,status,created_ts) VALUES(%s,%s,%s,%s,'pending',%s)"),
                  (uid, kind, json.dumps(payload or {}, ensure_ascii=False), int(due_ts), now()))


def cancel(uid, kinds=None, prefix=None):
    """Отменить ожидающие сообщения человека: все, по списку видов или по началу названия."""
    sql = "UPDATE scheduled SET status='cancelled' WHERE user_id=%s AND status='pending'"
    args = [uid]
    if kinds:
        sql += " AND kind IN (" + ",".join(["%s"] * len(kinds)) + ")"
        args += list(kinds)
    if prefix:
        sql += " AND kind LIKE %s"
        args.append(prefix + "%")
    with _Ctx() as c:
        c.execute(_q(sql), args)
        return c.rowcount


def claim_due(limit=50):
    """Забрать созревшие сообщения: помечаются 'sent' в той же операции — двойной отправки не будет."""
    t = now()
    with _Ctx() as c:
        if PG:
            c.execute("UPDATE scheduled SET status='sent' WHERE id IN (SELECT id FROM scheduled WHERE status='pending' "
                      "AND due_ts<=%s ORDER BY due_ts LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING *", (t, limit))
        else:
            c.execute("UPDATE scheduled SET status='sent' WHERE id IN (SELECT id FROM scheduled WHERE status='pending' "
                      "AND due_ts<=? ORDER BY due_ts LIMIT ?) RETURNING *", (t, limit))
        rows = _all(c)
    for r in rows:
        try:
            r["payload"] = json.loads(r.get("payload") or "{}")
        except Exception:
            r["payload"] = {}
    return rows


def pending(uid, kind=None):
    sql = "SELECT * FROM scheduled WHERE user_id=%s AND status='pending'"
    args = [uid]
    if kind:
        sql += " AND kind=%s"
        args.append(kind)
    with _Ctx() as c:
        c.execute(_q(sql), args)
        return _all(c)


# ---------------- заявки ----------------
def lead_get_open(uid):
    with _Ctx() as c:
        c.execute(_q("SELECT * FROM leads WHERE user_id=%s AND status NOT IN ('не актуально') ORDER BY id DESC LIMIT 1"), (uid,))
        return _one(c)


def lead_save(uid, **kw):
    """Создать или обновить открытую заявку человека."""
    kw["updated_ts"] = now()
    if "answers" in kw and not isinstance(kw["answers"], str):
        kw["answers"] = json.dumps(kw["answers"], ensure_ascii=False)
    cur = lead_get_open(uid)
    with _Ctx() as c:
        if cur:
            cols = ", ".join(f"{k}=%s" for k in kw)
            c.execute(_q(f"UPDATE leads SET {cols} WHERE id=%s"), (*kw.values(), cur["id"]))
            lid = cur["id"]
        else:
            kw.setdefault("ts", now())
            kw["user_id"] = uid
            cols = ",".join(kw)
            ph = ",".join(["%s"] * len(kw))
            if PG:
                c.execute(f"INSERT INTO leads({cols}) VALUES({ph}) RETURNING id", tuple(kw.values()))
                lid = _one(c)["id"]
            else:
                c.execute(f"INSERT INTO leads({cols}) VALUES({ph.replace('%s', '?')})", tuple(kw.values()))
                lid = c.lastrowid
    return lid


def booked_slots(t_from, t_to):
    with _Ctx() as c:
        c.execute(_q("SELECT slot_ts FROM leads WHERE slot_ts IS NOT NULL AND slot_ts>=%s AND slot_ts<%s "
                     "AND status IN ('записана','перенесена')"), (t_from, t_to))
        return [r["slot_ts"] for r in _all(c)]


# ---------------- ответ представителя через бота ----------------
def relay_set(chat_id, message_id, uid):
    with _Ctx() as c:
        if PG:
            c.execute("INSERT INTO relay(chat_id,message_id,user_id) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                      (chat_id, message_id, uid))
        else:
            c.execute("INSERT OR IGNORE INTO relay(chat_id,message_id,user_id) VALUES(?,?,?)", (chat_id, message_id, uid))


def relay_get(chat_id, message_id):
    with _Ctx() as c:
        c.execute(_q("SELECT user_id FROM relay WHERE chat_id=%s AND message_id=%s"), (chat_id, message_id))
        r = _one(c)
        return r["user_id"] if r else None


# ---------------- флаги ----------------
def kv_get(k):
    with _Ctx() as c:
        c.execute(_q("SELECT v FROM kv WHERE k=%s"), (k,))
        r = _one(c)
        return r["v"] if r else None


def kv_set(k, v):
    with _Ctx() as c:
        if PG:
            c.execute("INSERT INTO kv(k,v) VALUES(%s,%s) ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v", (k, v))
        else:
            c.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))


# ---------------- статистика ----------------
def funnel(days=7, source=None):
    """Сколько разных людей дошли до каждого шага за период."""
    t = now() - days * 86400
    sql = ("SELECT e.event AS event, COUNT(DISTINCT e.user_id) AS n FROM events e "
           + ("JOIN users u ON u.user_id=e.user_id " if source else "")
           + "WHERE e.ts>%s " + ("AND u.source=%s " if source else "") + "GROUP BY e.event")
    args = (t, source) if source else (t,)
    with _Ctx() as c:
        c.execute(_q(sql), args)
        return {r["event"]: r["n"] for r in _all(c)}


def sources(days=7):
    t = now() - days * 86400
    with _Ctx() as c:
        c.execute(_q("SELECT COALESCE(source,'—') AS s, COUNT(*) AS n FROM users WHERE created_at>%s GROUP BY source ORDER BY n DESC"), (t,))
        return [(r["s"], r["n"]) for r in _all(c)]


def leads_count(days=7):
    t = now() - days * 86400
    with _Ctx() as c:
        c.execute(_q("SELECT branch, status, COUNT(*) AS n FROM leads WHERE ts>%s GROUP BY branch, status"), (t,))
        return _all(c)


def last_screens(days=7):
    """Где люди остановились: последний экран из сессии тех, кто приходил за период."""
    t = now() - days * 86400
    with _Ctx() as c:
        c.execute(_q("SELECT session FROM users WHERE last_seen>%s AND session IS NOT NULL"), (t,))
        out = {}
        for r in _all(c):
            try:
                s = json.loads(r["session"]).get("screen") or "—"
            except Exception:
                s = "—"
            out[s] = out.get(s, 0) + 1
        return sorted(out.items(), key=lambda x: -x[1])
