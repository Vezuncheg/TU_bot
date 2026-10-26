"""Бот TeamUniverse v2 (стиль Б «Живой эфир», 22.10.2026) — точка входа и все сценарии.
Схема воронки и тексты — прототип v9 (Materials/Бот/Прототип_бот_и_дашборд.html), тексты — content.py.
Запуск: python bot.py (переменные — config.py)."""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           InputProfilePhotoAnimated, KeyboardButton, Message, ReplyKeyboardMarkup)

import config
import content as C
import db
import media
import migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tu")
router = Router()


# =====================================================================
# помощники
# =====================================================================
def bg(fn, *a, **kw):
    """Запись в базу в фоне: человеку отвечаем сразу, база — потом."""
    async def run():
        try:
            await asyncio.to_thread(fn, *a, **kw)
        except Exception as e:
            log.warning(f"db {fn.__name__}: {e}")
    return asyncio.create_task(run())


def ev(uid, event, details=""):
    bg(db.log_event, uid, event, details)


# Сколько знаков помещается в кнопку на телефоне шириной 375 pt, если в ряду 2 или 3 кнопки (длиннее — Telegram режет «…»)
BTN_FIT = {1: 34, 2: 17, 3: 11, 4: 7}


def split_rows(rows):
    """Ряд, где хоть одна кнопка не помещается целиком, раскладываем по одной кнопке в ряд (правка 26.10)."""
    out = []
    for row in rows:
        n = len(row)
        if n > 1 and any(len(b[0]) > BTN_FIT.get(n, 7) for b in row):
            out += [[b] for b in row]
        else:
            out.append(row)
    return out


def kb(rows):
    if not rows:
        return None
    out = []
    for row in split_rows(rows):
        r = []
        for b in row:
            text, action = b[0], b[1]
            style = b[2] if len(b) > 2 else "primary"
            if action.startswith("http"):
                r.append(InlineKeyboardButton(text=text, url=action, style=style))
            else:
                r.append(InlineKeyboardButton(text=text, callback_data=action[:64], style=style))
        out.append(r)
    return InlineKeyboardMarkup(inline_keyboard=out)


def reply_menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t, style="primary") for t in row] for row in C.REPLY_MENU],
        resize_keyboard=True, is_persistent=True)


def phone_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=C.PHONE_SHARE, request_contact=True, style="primary")],
                                         [KeyboardButton(text=C.PHONE_MANUAL, style="primary")]],
                               resize_keyboard=True, one_time_keyboard=True)


def msk(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), config.MSK)


WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def dd(d):
    return f"{WD[d.weekday()]} {d:%d.%m}"


def hm(d):
    return f"{d:%H:%M}"


def rub(n):
    return f"{round(n):,}".replace(",", " ") + " ₽"


def mask_phone(p):
    digits = re.sub(r"\D", "", p or "")
    if len(digits) < 10:
        return p or ""
    return f"+7 9•• ••• {digits[-4:-2]}-{digits[-2:]}"


# =====================================================================
# сессия человека: где он в воронке и что ответил. Хранится в базе (users.session) — переживает перезапуск
# =====================================================================
_sessions = {}
_locks = {}


def lock(uid):
    if uid not in _locks:
        _locks[uid] = asyncio.Lock()
    return _locks[uid]


def new_session(src="organic"):
    return {"src": src, "screen": None, "ans": {}, "types": {"E": 0, "S": 0, "T": 0, "D": 0}, "flags": [], "cond": {},
            "cond_lbl": {}, "test_q": None, "aq": None, "p1step": -1, "calc_h": None, "br": None, "expect": None}


async def get_sess(uid):
    s = _sessions.get(uid)
    if s is None:
        s = await asyncio.to_thread(db.load_session, uid) or new_session()
        _sessions[uid] = s
    return s


async def save_sess(uid, s):
    _sessions[uid] = s
    try:
        await asyncio.to_thread(db.save_session, uid, s)
    except Exception as e:
        log.warning(f"save session {uid}: {e}")


async def user_flags(uid, s=None):
    """Флаги «младше 18» и «не писать»: из сессии, а если их там нет — один раз из базы."""
    if s is not None and "u18" in s:
        return s["u18"], s.get("nw", False)
    u = await asyncio.to_thread(db.get_user, uid) or {}
    f = bool(u.get("underage")), bool(u.get("no_warmup"))
    if s is not None:
        s["u18"], s["nw"] = f
    return f


# =====================================================================
# отправка экранов
# =====================================================================
async def send_msgs(bot, uid, msgs, fmt=None, last_kb=None):
    """Отправить список сообщений экрана. fmt — подстановки в тексты. Возвращает последнее Message."""
    fmt = fmt or {}
    m = None
    for i, x in enumerate(msgs):
        text = x.get("text") or ""
        if fmt and text:
            text = text.format(**fmt)
        rows = x.get("kb")
        if last_kb is not None and i == len(msgs) - 1:
            rows = last_kb
        markup = kb(rows) if rows else (reply_menu() if x.get("reply_menu") else None)
        if x.get("media"):
            m = await media.send(bot, uid, x["media"], caption=text or None, reply_markup=markup)
        elif x.get("doc"):
            m = await media.send(bot, uid, "doc:" + x["doc"], caption=text or None, reply_markup=markup)
        else:
            m = await bot.send_message(uid, text, reply_markup=markup)
    return m


async def show(bot, uid, s, screen, **kw):
    """Показать экран. Логика экранов — как в прототипе v9."""
    underage, no_warm = await user_flags(uid, s) if screen in GUARDED else (False, False)
    if underage and screen in GUARDED:
        screen = "under18"
    s["screen"] = screen
    ev(uid, "screen", screen)
    fn = SPECIAL.get(screen)
    if fn:
        return await fn(bot, uid, s, **kw)
    return await send_msgs(bot, uid, C.S[screen])


# экраны, которые не показываем тем, кому нет 18
GUARDED = {"aIntro", "bQ1", "bQ2", "bQ3", "way", "phone", "chatWay", "day", "p3", "calc", "vCard"}


async def sc_under18(bot, uid, s, **kw):
    s["u18"] = s["nw"] = True
    bg(db.set_user, uid, underage=1, no_warmup=1)
    bg(db.cancel, uid)
    ev(uid, "underage")
    return await send_msgs(bot, uid, C.S["under18"])


async def sc_red(bot, uid, s, **kw):
    reasons = "; ".join(C.TEST_FLAGS[f] for f in s["flags"]) or "время и вечерние эфиры"
    return await send_msgs(bot, uid, C.S["red"], fmt={"reasons": reasons})


async def sc_offer(bot, uid, s, **kw):
    s["menu_shown"] = True
    want = s["ans"].get("Желаемый доход")
    big = want in ("100–300 тыс. ₽", "больше 300 тыс. ₽")
    pro = str(s.get("src", "")).startswith("partner") or big or s.get("why") in ("Уже стримлю и хочу больше", "Ищу своё дело")
    if pro:
        lead = C.OFFER_BIG.format(want=want) if big else C.OFFER_PRO
        return await send_msgs(bot, uid, C.S["offerPro"], fmt={"lead": lead})
    return await send_msgs(bot, uid, C.S["offer"])


def team_list(s):
    """Вопросы анкеты: пропускаем то, что человек уже ответил в опросе или тесте (как в прототипе)."""
    out = []
    for q in C.TEAM_Q:
        if q[0] == "hours" and s["cond_lbl"].get("hours"):
            continue
        if q[0] == "nights" and s["cond_lbl"].get("nights"):
            continue
        out.append(q)
    return out


async def sc_aIntro(bot, uid, s, **kw):
    s["br"] = "team"
    bg(db.set_user, uid, branch="team")
    if s["cond_lbl"].get("nights"):
        s["ans"]["Вечер и ночь"] = s["cond_lbl"]["nights"] + " (из теста)"
    return await send_msgs(bot, uid, C.S["aIntro"], fmt={"n": len(team_list(s))})


async def ask_team(bot, uid, s, i):
    L = team_list(s)
    if i >= len(L):
        return await show(bot, uid, s, "vCard")
    key, label, q, opts = L[i]
    s["aq"] = i
    s["screen"] = f"aq:{key}"
    ev(uid, "screen", f"aq:{key}")
    text = f"<i>Вопрос {i + 1} из {len(L)}</i>\n{q}"
    if not opts:
        s["expect"] = "team_free"
        return await media.send(bot, uid, f"card:aq_{key}", caption=text)
    rows = []
    for j in range(0, len(opts), 2):
        rows.append([(o, f"aq:{i}:{j + k}") for k, o in enumerate(opts[j:j + 2])])
    return await media.send(bot, uid, f"card:aq_{key}", caption=text, reply_markup=kb(rows))


async def sc_vCard(bot, uid, s, **kw):
    s["expect"] = "video_card"
    return await send_msgs(bot, uid, C.S["vCard"])


async def sc_aDone(bot, uid, s, **kw):
    m = await send_msgs(bot, uid, C.S["aDone"])
    s["screen"] = "way"
    ev(uid, "screen", "way")
    asyncio.create_task(save_lead(bot, uid, json.loads(json.dumps(s)), status="анкета", notify=True))
    return m


async def sc_p1(bot, uid, s, **kw):
    s["br"] = "partner"
    bg(db.set_user, uid, branch="partner")
    s["p1step"] = -1
    return await p1_step(bot, uid, s, 0)


async def p1_step(bot, uid, s, i):
    if s.get("p1step", -1) >= i:
        return None                          # этот шаг уже ушёл — повторное нажатие игнорируем
    s["p1step"] = i
    s["screen"] = "p1"
    ev(uid, "partner_step", str(i + 1))
    x = C.P1[i]
    rows = x.get("kb") or [[("Далее →", f"p1:{i + 1}")]]
    m = await send_msgs(bot, uid, [x], last_kb=rows)
    if i < len(C.P1) - 1:
        bg(db.reschedule, uid, "p1", time.time() + config.PARTNER_NEXT_SEC, {"step": i + 1})
    else:
        bg(db.cancel, uid, ["p1"])
    return m


async def sc_p3(bot, uid, s, **kw):
    first = not s.get("saw_price")
    s["saw_price"] = True
    m = await send_msgs(bot, uid, C.S["p3"])
    if first:
        _, no_warm = await user_flags(uid, s)
        if not no_warm:
            t = time.time()
            for kind, days in (("dz1", 1), ("dz3", 5), ("dz6", 14)):
                bg(db.schedule, uid, kind, t + days * 86400)
    return m


def calc_rows(h):
    return [[((("• " if x == h else "") + f"{x} ч"), f"calc:{x}") for x in C.CALC_HOURS],
            [("🚀 Хочу стать партнёром", "g:bQ1", "success")], [("◀ Назад", "g:p3")]]


async def sc_calc(bot, uid, s, **kw):
    h = s.get("calc_h") or C.CALC_DEFAULT
    s["calc_h"] = h
    s["ans"]["Калькулятор"] = f"{h} ч в день"
    ev(uid, "calc", str(h))
    m = await media.send(bot, uid, f"card:calc_{h}", caption=C.CALC_TEXT, reply_markup=kb(calc_rows(h)))
    s["calc_msg"] = m.message_id if m else None
    return m


async def sc_bQ(bot, uid, s, n):
    s["br"] = s.get("br") or "partner"
    rows = []
    labels = C.BQ[n][1]
    if n == 0:
        rows = [[(labels[j], f"bq:0:{j}") for j in range(3)]]
    elif n == 1:
        rows = [[(labels[j], f"bq:1:{j}")] for j in range(3)]
    else:
        rows = [[(labels[0], "bq:2:0"), (labels[1], "bq:2:1")], [(labels[2], "bq:2:2")]]
    texts = {0: "<i>3 вопроса перед консультацией</i>\n<b>Опыт эфиров?</b>\nЧтобы на консультации не тратить время на анкету.", 1: "", 2: ""}  # bQ1 — видео (В-09), вопрос в подписи
    return await media.send(bot, uid, f"card:bQ{n + 1}", caption=texts[n] or None, reply_markup=kb(rows))


async def sc_phone(bot, uid, s, **kw):
    s["way"] = "call"
    s["expect"] = "phone"
    return await bot.send_message(uid, C.S["phone"][0]["text"], reply_markup=phone_kb())


async def sc_chatWay(bot, uid, s, **kw):
    s["way"] = "chat"
    s["screen"] = "day"
    ev(uid, "screen", "day")
    return await sc_day(bot, uid, s, prefix=C.S["chatWay"][0]["text"])


# ---------------- запись на разговор ----------------
def free_slots(day_off, taken=None):
    """Свободные окна дня: рабочие часы минус уже занятые записи (как в прототипе; Google Календарь — позже)."""
    now = msk()
    base = (now + timedelta(days=day_off)).replace(hour=0, minute=0, second=0, microsecond=0)
    t0, t1 = int(base.timestamp()), int((base + timedelta(days=1)).timestamp())
    if taken is None:
        try:
            taken = db.booked_slots(t0, t1)
        except Exception:
            taken = []
    out = []
    t = config.SLOT_FROM_H * 60
    while t + config.SLOT_LEN_MIN <= config.SLOT_TO_H * 60:
        st = base + timedelta(minutes=t)
        ok = st > now + timedelta(minutes=config.SLOT_MIN_AHEAD_MIN)
        if ok and not any(abs(x - st.timestamp()) < config.SLOT_LEN_MIN * 60 for x in taken):
            out.append(st)
        t += config.SLOT_STEP_MIN
    return out


def win_word(n):
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} окно"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} окна"
    return f"{n} окон"


async def sc_day(bot, uid, s, prefix="", **kw):
    s["expect"] = None
    def _days():
        t0 = int(msk().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        try:
            taken = db.booked_slots(t0, t0 + 3 * 86400)
        except Exception:
            taken = []
        return [(i, free_slots(i, taken)) for i in range(3)]
    days = await asyncio.to_thread(_days)
    now = msk()
    rows = []
    for i, f in days:
        if not f:
            continue
        d = now + timedelta(days=i)
        pre = "Сегодня, " if i == 0 else ("Завтра, " if i == 1 else "")
        rows.append([(f"{pre}{dd(d)} · {win_word(len(f))}", f"day:{i}")])
    rows.append([("Другой день", "daymore")])
    return await bot.send_message(uid, (prefix + "\n\n" if prefix else "") + C.DAY_TEXT, reply_markup=kb(rows))


async def sc_time(bot, uid, s, day_off=0, **kw):
    f = await asyncio.to_thread(free_slots, day_off)
    d = msk() + timedelta(days=day_off)
    if not f:
        return await bot.send_message(uid, C.NO_SLOTS, reply_markup=kb([[("◀ Другой день", "g:day")]]))
    rows = [[(hm(x), f"slot:{int(x.timestamp())}") for x in f[k:k + 3]] for k in range(0, len(f), 3)]
    rows.append([("◀ Другой день", "g:day")])
    return await bot.send_message(uid, C.TIME_TEXT.format(day=dd(d)), reply_markup=kb(rows))


async def sc_confirm(bot, uid, s, ts=0, **kw):
    d = msk(ts)
    way = "звонок" if s.get("way") == "call" else "переписка в Telegram"
    s["slot"] = ts
    return await bot.send_message(uid, C.CONFIRM_TEXT.format(day=dd(d), time=hm(d), way=way),
                                  reply_markup=kb([[(f"✅ Да, буду в {hm(d)}", f"ok:{ts}")], [("Выбрать другое время", "g:day")]]))


async def sc_done(bot, uid, s, ts=0, **kw):
    # время могли занять, пока человек думал
    taken = await asyncio.to_thread(db.booked_slots, ts - config.SLOT_LEN_MIN * 60 + 1, ts + config.SLOT_LEN_MIN * 60)
    lead = await asyncio.to_thread(db.lead_get_open, uid)
    mine = lead and lead.get("slot_ts") == ts
    if taken and not mine:
        s["screen"] = "day"
        ev(uid, "screen", "day")
        return await sc_day(bot, uid, s, prefix=C.SLOT_TAKEN)
    resched = bool(lead and lead.get("slot_ts") and lead.get("status") in ("записана", "перенесена"))
    s["slot"] = ts
    t, rows = C.DONE_TEAM if s.get("br") == "team" else C.DONE_PARTNER
    x = C.S["done"][0]
    m = await send_msgs(bot, uid, [dict(media=x["media"], text=x["text"] + "\n\n" + t, kb=rows)])
    # напоминания: за сутки, за 2 часа, за 15 минут; старые — отменить; дожим партнёрства — больше не нужен
    await asyncio.to_thread(db.cancel, uid, ["rem24", "rem2", "rem15", "dz1", "dz3", "dz6", "dzTip", "month"])
    now = time.time()
    for kind, before in (("rem24", 86400), ("rem2", 7200), ("rem15", 900)):
        if ts - before > now + 60:
            bg(db.schedule, uid, kind, ts - before, {"slot": ts})
    if s.get("br") == "team" and ts - now > 3 * 3600:
        bg(db.schedule, uid, "dzTip", now + 3600, {"slot": ts})
    ev(uid, "booked", f"{s.get('br')} {msk(ts):%d.%m %H:%M}")
    await save_lead(bot, uid, s, status="перенесена" if resched else "записана", notify=True, slot=ts)
    return m


async def sc_notActual(bot, uid, s, **kw):
    s["nw"] = True
    bg(db.set_user, uid, no_warmup=1)
    bg(db.cancel, uid)
    ev(uid, "not_actual")
    return await send_msgs(bot, uid, C.S["notActual"])


async def sc_testIntro(bot, uid, s, **kw):
    return await send_msgs(bot, uid, C.S["testIntro"])


# ---------------- материалы: сначала подписка на канал (решение Артёма 23.10) ----------------
_sub_warned = False


async def is_subscribed(bot, uid):
    """True / False; None — проверить нельзя (бот не админ канала, канал не найден): тогда материал отдаём."""
    try:
        m = await bot.get_chat_member(config.SUB_CHANNEL, uid)
    except Exception as e:
        global _sub_warned
        log.warning(f"проверка подписки: {e}")
        if not _sub_warned and config.ARTEM_CHAT_ID:
            _sub_warned = True
            try:
                await bot.send_message(config.ARTEM_CHAT_ID, f"⚠️ Бот не может проверить подписку на {config.SUB_CHANNEL}: {e}\n"
                                                             "Материалы пока отдаются без проверки. Сделайте бота администратором канала.")
            except Exception:
                pass
        return None
    st = getattr(m, "status", "")
    return st in ("member", "administrator", "creator") or (st == "restricted" and bool(getattr(m, "is_member", False)))


async def sc_material(bot, uid, s, name, **kw):
    ok = await is_subscribed(bot, uid)
    if ok is False:
        again = s.get("sub_wait") == name
        s["sub_wait"] = name
        ev(uid, "sub_need", name)
        return await send_msgs(bot, uid, [dict(text=C.SUB_AGAIN if again else C.SUB_NEED,
                                               kb=[[("📢 Подписаться на канал", config.CHANNEL_URL)],
                                                   [("✅ Подписка есть — прислать", f"g:{name}")]])])
    if s.pop("sub_wait", None) == name and ok:
        ev(uid, "subscribed", name)
    ev(uid, "material", name)
    return await send_msgs(bot, uid, C.S[name])


SPECIAL = {"under18": sc_under18, "red": sc_red, "offer": sc_offer, "aIntro": sc_aIntro, "vCard": sc_vCard,
           "aDone": sc_aDone, "p1": sc_p1, "p3": sc_p3, "calc": sc_calc, "phone": sc_phone, "chatWay": sc_chatWay,
           "day": sc_day, "time": sc_time, "confirm": sc_confirm, "done": sc_done, "notActual": sc_notActual,
           "testIntro": sc_testIntro,
           "bQ1": lambda b, u, s, **k: sc_bQ(b, u, s, 0), "bQ2": lambda b, u, s, **k: sc_bQ(b, u, s, 1),
           "bQ3": lambda b, u, s, **k: sc_bQ(b, u, s, 2),
           "guide": lambda b, u, s, **k: sc_material(b, u, s, "guide"),
           "check": lambda b, u, s, **k: sc_material(b, u, s, "check")}


# ---------------- тест ----------------
async def ask_test(bot, uid, s, i):
    nT, total = len(C.TEST_TYPE), len(C.TEST_TYPE) + len(C.TEST_COND)
    # часы уже известны из опроса — вопрос пропускаем
    if i == nT and s["cond_lbl"].get("hours"):
        val, flag = C.S4_TO_TEST.get(s["cond_lbl"]["hours"], ("", False))
        s["cond"]["hours"] = val
        if flag and "hours" not in s["flags"]:
            s["flags"].append("hours")
        i += 1
    s["test_q"] = i
    s["screen"] = f"test:{i + 1}"
    if i < nT:
        q, opts = C.TEST_TYPE[i]
        rows = [[(o, f"tq:{i}:{j}")] for j, (o, _) in enumerate(opts)]
    elif i < total:
        q, opts, _ = C.TEST_COND[i - nT]
        rows = [[(o[0], f"tq:{i}:{j}")] for j, o in enumerate(opts)]
    else:
        return await test_result(bot, uid, s)
    ev(uid, "screen", f"test:{i + 1}")
    return await bot.send_message(uid, f"<i>{i + 1}/{total}</i>\n{q}", reply_markup=kb(rows))


async def test_result(bot, uid, s):
    s["test_done"] = True
    s["screen"] = "result"
    ev(uid, "screen", "result")
    order = ["E", "D", "S", "T"]
    t = order[0]
    for k in order:
        if s["types"][k] > s["types"][t]:
            t = k
    s["type"] = t
    v = "green" if not s["flags"] else ("yellow" if len(s["flags"]) == 1 else "red")
    s["verdict"] = v
    bg(db.set_user, uid, test_type=t, test_verdict=v, test_flags=",".join(s["flags"]),
       test_hours=s["cond"].get("hours", ""), test_nights=s["cond"].get("nights", ""))
    bg(db.cancel, uid, ["dzTest"])
    ev(uid, f"test_verdict_{v}", t)
    title, body = C.TEST_RES[t]
    head = f"Твой типаж: <b>{title}</b>\n\n{body}\n\n"
    if v == "red":              # типаж, а по «Дальше» — честное «сейчас не твоё» с причинами (вместе не помещаются на экран)
        return await bot.send_message(uid, head.rstrip(), reply_markup=kb([[("Дальше →", "g:red")]]))
    verdict = C.VERDICT_GREEN if v == "green" else C.VERDICT_YELLOW.format(flag=C.TEST_FLAGS[s["flags"][0]])
    return await bot.send_message(uid, head + verdict, reply_markup=kb([[("Дальше →", "g:fork")]]))


# ---------------- заявка → представителю ----------------
def lead_card(u, s, lead_status, slot=None):
    name = (u.get("first_name") or "").strip() or "—"
    uname = f"@{u['username']}" if u.get("username") else f"<a href=\"tg://user?id={u['user_id']}\">профиль</a>"
    br = "👥 В команде" if s.get("br") == "team" else "🚀 Партнёр"
    head = {"анкета": "📝 Анкета и видео — ещё не выбрала время", "записана": "📅 ЗАПИСЬ на разговор",
            "перенесена": "🔁 ПЕРЕНОС разговора"}.get(lead_status, lead_status)
    lines = [f"{head} · {br}", f"{name} · {uname} · id {u['user_id']}", f"Источник: {u.get('source') or '—'}"]
    if slot:
        d = msk(slot)
        lines.append(f"Когда: <b>{dd(d)} {hm(d)}</b> (МСК) · " + ("📞 звонок" if s.get("way") == "call" else "💬 переписка в Telegram"))
    if s.get("phone"):
        lines.append(f"Телефон: {s['phone']}")
    ans = dict(s.get("ans") or {})
    for k in ("why_s1", "main_s2"):
        ans.pop(k, None)
    if s.get("type"):
        ans["Типаж по тесту"] = C.TEST_RES[s["type"]][0] + {"green": " · ✅", "yellow": " · ⚠️", "red": " · ❌"}.get(s.get("verdict"), "")
    if ans:
        lines.append("")
        lines += [f"• {k}: {v}" for k, v in ans.items()]
    lines += ["", C.LEAD_FORWARD_HINT]
    return "\n".join(lines)


async def save_lead(bot, uid, s, status, notify=False, slot=None):
    u = await asyncio.to_thread(db.get_user, uid) or {"user_id": uid}
    fields = dict(branch=s.get("br"), answers=s.get("ans") or {}, way=s.get("way"), phone=s.get("phone"), status=status,
                  source=u.get("source"), username=u.get("username"), name=u.get("first_name"))
    if slot:
        fields["slot_ts"] = slot
    if s.get("video_card"):
        fields["video_card"] = s["video_card"]["id"]
    if s.get("video_battle"):
        fields["video_battle"] = s["video_battle"]["id"]
    await asyncio.to_thread(db.lead_save, uid, **fields)
    ev(uid, "lead", f"{s.get('br')} {status}")
    if not notify or not config.LEADS_CHAT_ID:
        return
    try:
        m = await bot.send_message(config.LEADS_CHAT_ID, lead_card(u, s, status, slot))
        bg(db.relay_set, config.LEADS_CHAT_ID, m.message_id, uid)
        if status == "анкета":
            for key in ("video_card", "video_battle"):
                v = s.get(key)
                if not v:
                    continue
                if v.get("note"):
                    vm = await bot.send_video_note(config.LEADS_CHAT_ID, v["id"], reply_to_message_id=m.message_id)
                else:
                    vm = await bot.send_video(config.LEADS_CHAT_ID, v["id"], reply_to_message_id=m.message_id,
                                              caption="Видео-визитка" if key == "video_card" else "Видео баттла")
                bg(db.relay_set, config.LEADS_CHAT_ID, vm.message_id, uid)
    except Exception as e:
        log.warning(f"lead notify: {e}")


async def forward_if_lead(bot, uid, text):
    """Человек с заявкой написал боту — переслать представителю."""
    try:
        if await asyncio.to_thread(db.lead_get_open, uid):
            await to_team(bot, uid, text)
    except Exception as e:
        log.warning(f"forward_if_lead: {e}")


async def to_team(bot, uid, text):
    """Сообщение представителю (вопрос, просьба познакомить) с возможностью ответить через бота."""
    if not config.LEADS_CHAT_ID:
        return
    u = await asyncio.to_thread(db.get_user, uid) or {"user_id": uid}
    who = f"@{u['username']}" if u.get("username") else f"<a href=\"tg://user?id={uid}\">{u.get('first_name') or 'профиль'}</a>"
    try:
        m = await bot.send_message(config.LEADS_CHAT_ID, f"{text}\nОт: {who} · id {uid}\n\n{C.LEAD_FORWARD_HINT}")
        bg(db.relay_set, config.LEADS_CHAT_ID, m.message_id, uid)
    except Exception as e:
        log.warning(f"to_team: {e}")


# =====================================================================
# обработчики
# =====================================================================
# старые метки (бот до v2): test, casting, antiscam, guide30, franchise — ведут туда же по смыслу.
# franchise → «Партнёр TeamUniverse» (решение Артёма 23.10)
ROUTES_EXACT = {"test": "testIntro", "casting": "aIntro", "team": "aIntro", "franchise": "p1", "partner": "p1",
                "paket": "p1", "guide30": "guide", "guide": "guide", "antiscam": "check", "check": "check",
                "menu": "menu", "about": "about", "calc": "calc"}


def route_for(src):
    if src in ROUTES_EXACT:
        return ROUTES_EXACT[src]
    if src.startswith("test_") or src.startswith("test-"):
        return "testIntro"          # ?start=test_post12 — тест с меткой поста
    return "start"                  # остальные метки (partner_ads1, reel_*, channel…) — обычный вход, метка сохраняется


@router.message(F.chat.type == "private", CommandStart())
async def h_start(msg: Message, command: CommandObject):
    uid = msg.from_user.id
    src = re.sub(r"[^\w\-]", "", (command.args or "organic").strip().lower())[:64] or "organic"
    async with lock(uid):
        up = asyncio.create_task(asyncio.to_thread(db.upsert_user, uid, msg.from_user.username, msg.from_user.first_name, src))
        s = new_session(src)
        scr = route_for(src)
        if scr in GUARDED:
            u, is_new = await up
            s["u18"], s["nw"] = bool(u.get("underage")), False
        await show(msg.bot, uid, s, scr)
        u, is_new = await up
        ev(uid, "start", src)
        if is_new:
            ev(uid, "new_user", src)
        s["u18"], s["nw"] = bool(u.get("underage")), False
        if u.get("no_warmup"):
            bg(db.set_user, uid, no_warmup=0)       # сам нажал /start — снова можно писать
        await save_sess(uid, s)


@router.message(F.chat.type == "private", Command("menu"))
async def h_menu(msg: Message):
    uid = msg.from_user.id
    async with lock(uid):
        s = await get_sess(uid)
        s["expect"] = None
        await show(msg.bot, uid, s, "menu")
        await save_sess(uid, s)


@router.message(Command("stats"))
async def h_stats(msg: Message, command: CommandObject):
    if msg.from_user.id not in config.ADMIN_IDS:
        return
    days = int(command.args) if (command.args or "").isdigit() else 7
    await msg.answer(await asyncio.to_thread(stats_text, days))


STEPS = [("start", "пришли (/start)"), ("screen:s1", "опрос начали"), ("screen:s5", "опрос: вопрос 5"),
         ("screen:t1", "знакомство"), ("screen:t3", "сколько получается"), ("screen:testIntro", "тест: вход"),
         ("screen:test:1", "тест начали"), ("screen:result", "тест прошли"), ("screen:fork", "развилка"),
         ("screen:aIntro", "команда: о формате"), ("screen:vCard", "команда: анкета готова"),
         ("lead_team_анкета", "команда: видео прислали"), ("screen:p1", "партнёр: что даёт"), ("screen:p3", "партнёр: цена"),
         ("screen:calc", "калькулятор"), ("screen:bQ1", "перед консультацией"), ("screen:way", "способ связи"),
         ("booked", "записались на разговор")]


def stats_text(days=7):
    f = db.funnel(days)
    # события screen/lead хранятся с деталями — посчитаем по деталям отдельно
    with db._Ctx() as c:
        c.execute(db._q("SELECT event, details, COUNT(DISTINCT user_id) AS n FROM events WHERE ts>%s AND event IN ('screen','lead') "
                        "GROUP BY event, details"), (db.now() - days * 86400,))
        for r in db._all(c):
            key = f"screen:{r['details']}" if r["event"] == "screen" else "lead_" + str(r["details"]).replace(" ", "_")
            f[key] = r["n"]
    lines = [f"📊 Воронка за {days} дн. (разных людей):"]
    prev = None
    for key, name in STEPS:
        n = f.get(key, 0)
        drop = f" (−{round((1 - n / prev) * 100)}%)" if prev else ""
        lines.append(f"{n:>4} · {name}{drop if prev and n <= prev else ''}")
        prev = n or prev
    src = db.sources(days)
    if src:
        lines += ["", "Откуда новые:"] + [f"{n:>4} · {s}" for s, n in src[:10]]
    stop = db.last_screens(days)
    if stop:
        lines += ["", "Где сейчас остановились:"] + [f"{n:>4} · {s}" for s, n in stop[:8]]
    return "\n".join(lines)


class Latency(BaseMiddleware):
    """Время от прихода нажатия до последнего отправленного сообщения — в журнал и в логи Railway."""
    async def __call__(self, handler, event, data):
        t = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            ms = int((time.perf_counter() - t) * 1000)
            uid = getattr(getattr(event, "from_user", None), "id", None)
            what = getattr(event, "data", None) or (getattr(event, "text", None) or "")[:20]
            log.info(f"lat {ms} ms · {uid} · {what}")
            if uid:
                ev(uid, "lat", f"{ms} {what}")


@router.callback_query()
async def h_cb(cb: CallbackQuery):
    uid = cb.from_user.id
    data = cb.data or ""
    try:
        await cb.answer()                       # сразу убрать «часики» на кнопке
    except Exception:
        pass
    async with lock(uid):
        s = await get_sess(uid)
        ev(uid, "btn", data)
        bot = cb.bot
        a, _, arg = data.partition(":")
        try:
            await handle_action(bot, uid, s, a, arg, cb)
        except TelegramForbiddenError:
            bg(db.set_user, uid, no_warmup=1)
        await save_sess(uid, s)


async def handle_action(bot, uid, s, a, arg, cb=None):
    if a == "g":
        if arg in C.S or arg in SPECIAL:
            if arg != "vExample":            # пример баттла смотрят, пока бот ждёт видео 2 из 2
                s["expect"] = None
            return await show(bot, uid, s, arg)
        return
    if a in ("s1", "s2", "s3", "s4", "s5"):
        i = int(arg)
        if a == "s1":
            s["why"] = C.S1[i]; ev(uid, "answer", f"привело: {C.S1[i]}"); nxt = "s2"
        elif a == "s2":
            s["main"] = C.S2[i]; ev(uid, "answer", f"главное: {C.S2[i]}"); nxt = "s3"
        elif a == "s3":
            s["ans"]["Желаемый доход"] = C.S3[i]; ev(uid, "answer", f"доход: {C.S3[i]}"); nxt = "s4"
        elif a == "s4":
            h = list(C.S4)[i]
            s["cond_lbl"]["hours"] = h; s["hours_day"] = C.S4[h]; s["ans"]["Часов в день"] = h
            ev(uid, "answer", f"часов в день: {h}"); nxt = "s5"
        else:
            s["ans"]["Возраст"] = C.AGES[i]; ev(uid, "answer", f"возраст: {C.AGES[i]}"); nxt = "t1"
        return await show(bot, uid, s, nxt)
    if a == "u18":
        s["ans"]["Возраст"] = "младше 18"
        return await show(bot, uid, s, "under18")
    if a == "tgo":
        s.update(types={"E": 0, "S": 0, "T": 0, "D": 0}, flags=[], cond={}, test_done=False)
        s["cond_lbl"] = {k: v for k, v in s["cond_lbl"].items() if k == "hours"}
        ev(uid, "test_started")
        bg(db.reschedule, uid, "dzTest", time.time() + 86400)
        return await ask_test(bot, uid, s, 0)
    if a == "tq":
        i, j = map(int, arg.split(":"))
        if s.get("test_q") != i:
            return                                   # старая кнопка
        nT = len(C.TEST_TYPE)
        if i < nT:
            s["types"][C.TEST_TYPE[i][1][j][1]] += 1
        else:
            q, opts, key = C.TEST_COND[i - nT]
            lbl, val, flag = opts[j]
            s["cond"][key] = val
            s["cond_lbl"][key] = lbl
            if flag and key not in s["flags"]:
                s["flags"].append(key)
        return await ask_test(bot, uid, s, i + 1)
    if a == "aq0":
        s["br"] = "team"
        return await ask_team(bot, uid, s, 0)
    if a == "aq":
        i, j = map(int, arg.split(":"))
        if s.get("aq") != i or s.get("expect") == "team_free":
            return
        L = team_list(s)
        key, label, q, opts = L[i]
        o = opts[j]
        if key == "age" and o == "младше 18":
            return await show(bot, uid, s, "under18")
        if key == "city" and o == "Другой город":
            s["expect"] = "city_other"
            return await bot.send_message(uid, C.CITY_OTHER)
        s["ans"][label] = o
        ev(uid, "answer", f"{label}: {o}")
        return await ask_team(bot, uid, s, i + 1)
    if a == "p1":
        return await p1_step(bot, uid, s, int(arg))
    if a == "calc":
        h = int(arg)
        if h not in C.CALC_HOURS:
            return
        s["calc_h"] = h
        s["ans"]["Калькулятор"] = f"{h} ч в день"
        ev(uid, "calc", str(h))
        if cb and cb.message:
            try:
                m = await bot.edit_message_media(chat_id=uid, message_id=cb.message.message_id,
                                                 media=media.input_media(f"card:calc_{h}").model_copy(update={"caption": C.CALC_TEXT}),
                                                 reply_markup=kb(calc_rows(h)))
                if isinstance(m, Message):
                    media.remember_from(f"card:calc_{h}", m)
                return m
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                log.warning(f"calc edit: {e}")
        return await sc_calc(bot, uid, s)
    if a == "bq":
        n, j = map(int, arg.split(":"))
        name, labels, values = C.BQ[n]
        s["ans"][name] = values[j]
        ev(uid, "answer", f"{name}: {values[j]}")
        if n < 2:
            return await show(bot, uid, s, f"bQ{n + 2}")
        m = await show(bot, uid, s, "way")
        asyncio.create_task(save_lead(bot, uid, json.loads(json.dumps(s)), status="анкета"))
        return m
    if a == "day":
        s["screen"] = "time"
        return await sc_time(bot, uid, s, day_off=int(arg))
    if a == "daymore":
        now = msk()
        rows = [[(dd(now + timedelta(days=i)), f"day:{i}") for i in (3, 4)]]
        return await bot.send_message(uid, C.DAY_MORE, reply_markup=kb(rows))
    if a == "slot":
        s["screen"] = "confirm"
        return await sc_confirm(bot, uid, s, ts=int(arg))
    if a == "ok":
        s["screen"] = "done"
        return await sc_done(bot, uid, s, ts=int(arg))
    if a == "remok":
        return await bot.send_message(uid, C.REM_OK.get(arg, "👍"))
    if a == "resched":
        ev(uid, "reschedule")
        return await show(bot, uid, s, "day")
    if a == "month":
        await asyncio.to_thread(db.cancel, uid, ["dz1", "dz3", "dz6", "month"])
        bg(db.schedule, uid, "month", time.time() + 30 * 86400)
        return await bot.send_message(uid, C.MONTH_OK)
    if a == "meetpartner":
        await to_team(bot, uid, "🤝 Просит разговор с ведущей-партнёром (из дожима)")
        return await bot.send_message(uid, C.MEETPARTNER_OK)


@router.message(F.chat.id == config.LEADS_CHAT_ID, F.reply_to_message)
async def h_relay(msg: Message):
    """Представитель ответил на карточку заявки — пересылаем человеку."""
    uid = await asyncio.to_thread(db.relay_get, msg.chat.id, msg.reply_to_message.message_id)
    if not uid:
        return
    try:
        if msg.text:
            await msg.bot.send_message(uid, C.FROM_TEAM.format(text=msg.html_text))
        else:
            await msg.bot.copy_message(uid, msg.chat.id, msg.message_id)
        await msg.reply(C.RELAY_SENT)
        ev(uid, "team_reply")
    except Exception as e:
        await msg.reply(C.RELAY_FAIL.format(err=e))


@router.message(F.chat.type == "private", F.contact)
async def h_contact(msg: Message):
    uid = msg.from_user.id
    async with lock(uid):
        s = await get_sess(uid)
        if s.get("expect") != "phone":
            return
        await got_phone(msg.bot, uid, s, msg.contact.phone_number)
        await save_sess(uid, s)


async def got_phone(bot, uid, s, phone):
    s["phone"] = phone
    s["expect"] = None
    bg(db.set_user, uid, phone=phone)
    ev(uid, "phone")
    await bot.send_message(uid, "📱 Номер записал.", reply_markup=reply_menu())
    return await show(bot, uid, s, "day")


@router.message(F.chat.type == "private", F.video | F.video_note)
async def h_video(msg: Message):
    uid = msg.from_user.id
    async with lock(uid):
        s = await get_sess(uid)
        v = msg.video_note or msg.video
        exp = s.get("expect")
        if exp not in ("video_card", "video_battle"):
            lead = await asyncio.to_thread(db.lead_get_open, uid)
            if lead:
                await to_team(msg.bot, uid, "🎥 Прислала видео вне анкеты (ниже)")
                await msg.forward(config.LEADS_CHAT_ID) if config.LEADS_CHAT_ID else None
            else:
                await show(msg.bot, uid, s, "freeText")
            await save_sess(uid, s)
            return
        if getattr(v, "duration", 0) and v.duration > 180:
            await msg.answer(C.VIDEO_TOO_LONG)
            return
        item = {"id": v.file_id, "note": bool(msg.video_note), "dur": getattr(v, "duration", 0)}
        if exp == "video_card":
            s["video_card"] = item
            s["ans"]["Визитка"] = ("кружок" if item["note"] else "видео") + f" {item['dur'] // 60}:{item['dur'] % 60:02d}"
            ev(uid, "video", "card")
            s["expect"] = "video_battle"
            await show(msg.bot, uid, s, "vBattle")
            s["expect"] = "video_battle"
        else:
            s["video_battle"] = item
            s["ans"]["Баттл"] = ("кружок" if item["note"] else "видео") + f" {item['dur'] // 60}:{item['dur'] % 60:02d}"
            ev(uid, "video", "battle")
            s["expect"] = None
            await show(msg.bot, uid, s, "aDone")
        await save_sess(uid, s)


@router.message(F.chat.type == "private", F.text)
async def h_text(msg: Message):
    uid = msg.from_user.id
    text = msg.text.strip()
    async with lock(uid):
        s = await get_sess(uid)
        bot = msg.bot
        exp = s.get("expect")
        if text in C.REPLY_ROUTES and exp not in ("phone",):
            s["expect"] = None
            ev(uid, "menu_btn", text)
            await show(bot, uid, s, C.REPLY_ROUTES[text])
        elif exp == "phone":
            if text == C.PHONE_MANUAL:
                await bot.send_message(uid, C.PHONE_ASK)
            elif len(re.sub(r"\D", "", text)) >= 10:
                await got_phone(bot, uid, s, text[:32])
            else:
                await bot.send_message(uid, C.PHONE_BAD)
        elif exp == "city_other":
            s["ans"]["Город"] = text[:100] + " (решаем индивидуально)"
            s["expect"] = None
            ev(uid, "answer", "Город: другой")
            await ask_team(bot, uid, s, (s.get("aq") or 0) + 1)
        elif exp == "team_free":
            L = team_list(s)
            i = s.get("aq") or 0
            if i < len(L):
                s["ans"][L[i][1]] = text[:500]
            s["expect"] = None
            ev(uid, "answer", f"{L[i][1] if i < len(L) else '?'}: текст")
            await ask_team(bot, uid, s, i + 1)
        elif exp == "video_card":
            await bot.send_message(uid, C.VIDEO_WAIT_CARD)
        elif exp == "video_battle":
            await bot.send_message(uid, C.VIDEO_WAIT_BATTLE)
        else:
            ev(uid, "free_text", text[:100])
            await show(bot, uid, s, "freeText")
            asyncio.create_task(forward_if_lead(bot, uid, f"💬 Написала боту:\n«{text[:1000]}»"))
        await save_sess(uid, s)


# =====================================================================
# отложенные сообщения: «Далее» в партнёрстве, напоминания, дожим
# =====================================================================
async def run_scheduled(bot, row):
    uid, kind, p = row["user_id"], row["kind"], row.get("payload") or {}
    async with lock(uid):
        s = await get_sess(uid)
        underage, no_warm = await user_flags(uid, s)
        if underage:
            return
        if kind == "p1":
            step = int(p.get("step", 0))
            if s.get("screen") == "p1" and s.get("p1step") == step - 1:
                await p1_step(bot, uid, s, step)
                ev(uid, "partner_auto", str(step + 1))
        elif kind in ("rem24", "rem2", "rem15"):
            lead = await asyncio.to_thread(db.lead_get_open, uid)
            if not lead or lead.get("slot_ts") != p.get("slot") or lead.get("status") not in ("записана", "перенесена"):
                return
            d = msk(p["slot"])
            if kind == "rem24":
                who = C.WHO_CALL if lead.get("way") == "call" else C.WHO_CHAT
                await bot.send_message(uid, C.REM24.format(time=hm(d), who=who),
                                       reply_markup=kb([[("👍 Буду", "remok:rem24"), ("Перенести", "resched")]]))
            elif kind == "rem2":
                await bot.send_message(uid, C.REM2, reply_markup=kb([[("👍 Буду", "remok:rem2"), ("Перенести", "resched")]]))
            else:
                t = C.REM15_CALL.format(phone=mask_phone(lead.get("phone"))) if lead.get("way") == "call" else C.REM15_CHAT
                await bot.send_message(uid, t, reply_markup=kb([[("Перенести", "resched")]]))
            ev(uid, "reminder", kind)
        elif kind in ("dz1", "dz3", "dz6", "month"):
            if no_warm:
                return
            lead = await asyncio.to_thread(db.lead_get_open, uid)
            if lead and lead.get("status") in ("записана", "перенесена"):
                return
            if kind == "month":
                await show(bot, uid, s, "p3")
            else:
                await send_msgs(bot, uid, C.S[kind])
            ev(uid, "warmup_sent", kind)
        elif kind == "dzTest":
            if no_warm or s.get("test_done"):
                return
            await send_msgs(bot, uid, C.S["dzTest"])
            ev(uid, "warmup_sent", kind)
        elif kind == "dzTip":
            if no_warm:
                return
            tip = C.TEST_TIPS.get(s.get("type") or "S")
            tip = C.DZ_TIP_PREFIX + tip.split(" ", 1)[1]          # «🎁 3 приёма…» → «🎁 Пока ждёшь встречи — 3 приёма…»
            await bot.send_message(uid, tip + C.DZ_TIP_TAIL)
            ev(uid, "warmup_sent", kind)
        await save_sess(uid, s)


async def scheduler(bot):
    while True:
        try:
            rows = await asyncio.to_thread(db.claim_due)
            for r in rows:
                try:
                    await run_scheduled(bot, r)
                except TelegramForbiddenError:
                    bg(db.set_user, r["user_id"], no_warmup=1)      # человек заблокировал бота
                except Exception as e:
                    log.warning(f"scheduled {r.get('kind')} {r.get('user_id')}: {e}")
        except Exception as e:
            log.warning(f"scheduler: {e}")
        await asyncio.sleep(config.SCHEDULER_TICK)


# =====================================================================
# запуск
# =====================================================================
async def set_avatar(bot):
    if not config.SET_AVATAR:
        return
    try:
        if await asyncio.to_thread(db.kv_get, "avatar_v2"):
            return
        await bot.set_my_profile_photo(photo=InputProfilePhotoAnimated(
            animation=FSInputFile(media.path_of("video:avatar")), main_frame_timestamp=0.0))
        await asyncio.to_thread(db.kv_set, "avatar_v2", str(int(time.time())))
        log.info("аватарка бота поставлена")
    except Exception as e:
        log.warning(f"аватарка: {e}")


def build_dp():
    dp = Dispatcher()
    router.callback_query.outer_middleware(Latency())
    router.message.outer_middleware(Latency())
    dp.include_router(router)

    @dp.errors()
    async def on_error(event):
        log.exception(f"ошибка: {event.exception}")
        try:
            upd = event.update
            uid = (upd.message or upd.callback_query).from_user.id if (upd.message or upd.callback_query) else 0
            await asyncio.to_thread(db.log_event, uid, "error", repr(event.exception)[:300])
        except Exception:
            pass
        return True
    return dp


async def main():
    await asyncio.to_thread(migrate.run)
    media.load()
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = build_dp()
    await bot.set_my_commands([BotCommand(command="start", description="🚀 Начать заново"),
                               BotCommand(command="menu", description="☰ Меню")])
    asyncio.create_task(set_avatar(bot))
    if config.MEDIA_WARMUP_CHAT_ID:
        asyncio.create_task(media.warmup(bot, config.MEDIA_WARMUP_CHAT_ID))
    asyncio.create_task(scheduler(bot))
    log.info("TeamUniverse bot started (v2)")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
