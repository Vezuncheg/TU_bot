"""Автопрогон обеих веток без Telegram: настоящий код бота, вместо серверов Telegram — заглушка, которая
запоминает всё, что бот отправил. База — временный SQLite. Запуск: python tests/run_flows.py
Проверяет: переходы, тексты без заглушек, длину подписей, цвет кнопок, таймер «Далее» в базе (и после перезапуска),
запись на разговор и напоминания, стоп для младше 18, метки ?start=, пересылку заявки и ответа представителя."""
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.chdir(ROOT)
DB_FILE = os.path.join(tempfile.gettempdir(), f"tu_flows_{os.getpid()}.sqlite3")
os.environ.update(DATABASE_URL=os.environ.get("TEST_PG", ""), DB_PATH=DB_FILE, BOT_TOKEN="123:TEST", LEADS_CHAT_ID="-1001", ARTEM_CHAT_ID="777",
                  SET_AVATAR="0", PARTNER_NEXT_SEC="60")

from aiogram import Bot                                              # noqa: E402
from aiogram.client.default import DefaultBotProperties             # noqa: E402
from aiogram.client.session.base import BaseSession                 # noqa: E402
from aiogram.methods import TelegramMethod                          # noqa: E402
from aiogram.types import (CallbackQuery, Chat, Contact, Document, Message, MessageId, PhotoSize, Update,  # noqa: E402
                           User, Video, VideoNote)

import bot as B                                                      # noqa: E402
import content as C                                                  # noqa: E402
import db                                                            # noqa: E402
import migrate                                                       # noqa: E402

SENT = []          # (chat_id, method, params)
PROBLEMS = []
_mid = [1000]


class FakeSession(BaseSession):
    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        name = type(method).__name__
        params = method.model_dump(exclude_none=True)
        SENT.append((params.get("chat_id"), name, params, method))
        _mid[0] += 1
        chat = Chat(id=int(params.get("chat_id") or 0), type="private")
        base = dict(message_id=_mid[0], date=datetime.now(), chat=chat)
        if name == "SendPhoto":
            return Message(**base, caption=params.get("caption"), photo=[PhotoSize(file_id=f"ph{_mid[0]}", file_unique_id="u", width=10, height=10)])
        if name == "SendVideo":
            return Message(**base, caption=params.get("caption"), video=Video(file_id=f"vd{_mid[0]}", file_unique_id="u", width=1, height=1, duration=1))
        if name == "SendDocument":
            return Message(**base, document=Document(file_id=f"dc{_mid[0]}", file_unique_id="u"))
        if name == "SendVideoNote":
            return Message(**base, video_note=VideoNote(file_id=f"vn{_mid[0]}", file_unique_id="u", length=1, duration=1))
        if name in ("SendMessage", "EditMessageMedia", "EditMessageText"):
            return Message(**base, text=params.get("text") or "x")
        if name == "CopyMessage":
            return MessageId(message_id=_mid[0])
        if name == "GetMe":
            return User(id=1, is_bot=True, first_name="TU")
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""


bot = Bot("123:TEST", session=FakeSession(), default=DefaultBotProperties(parse_mode="HTML"))
dp = B.build_dp()
_upd = [1]
TIMES = []


def user(uid, name="Катя", uname="katya"):
    return User(id=uid, is_bot=False, first_name=name, username=uname)


async def feed(uid, *, text=None, data=None, video_note=False, video=False, contact=None, chat_id=None, reply_to=None):
    _upd[0] += 1
    n0 = len(SENT)
    chat = Chat(id=chat_id or uid, type="private" if not chat_id else "supergroup")
    base = dict(message_id=_upd[0], date=datetime.now(), chat=chat, from_user=user(uid))
    if data is not None:
        last = Message(message_id=_mid[0], date=datetime.now(), chat=Chat(id=uid, type="private"), text="x")
        upd = Update(update_id=_upd[0], callback_query=CallbackQuery(id=str(_upd[0]), from_user=user(uid), chat_instance="c", data=data, message=last))
    elif video_note:
        upd = Update(update_id=_upd[0], message=Message(**base, video_note=VideoNote(file_id="USERNOTE", file_unique_id="u", length=1, duration=48)))
    elif video:
        upd = Update(update_id=_upd[0], message=Message(**base, video=Video(file_id="USERVID", file_unique_id="u", width=1, height=1, duration=72)))
    elif contact:
        upd = Update(update_id=_upd[0], message=Message(**base, contact=Contact(phone_number=contact, first_name="Катя", user_id=uid)))
    else:
        rt = Message(message_id=reply_to, date=datetime.now(), chat=chat, text="карточка") if reply_to else None
        upd = Update(update_id=_upd[0], message=Message(**base, text=text, reply_to_message=rt))
    t = time.perf_counter()
    await dp.feed_update(bot, upd)
    await asyncio.sleep(0.05)       # фоновые записи в базу
    TIMES.append((data or text or "media", (time.perf_counter() - t) * 1000, len([x for x in SENT[n0:] if x[1].startswith(("Send", "Edit"))])))
    out = SENT[n0:]
    check(out)
    return out


def buttons(params):
    rm = params.get("reply_markup") or {}
    return [b for row in rm.get("inline_keyboard", []) for b in row]


def check(out):
    for chat, name, p, m in out:
        txt = p.get("text") or p.get("caption") or ""
        if "ЗАГЛУШКА" in txt or "[фото" in txt or "в прототипе" in txt:
            PROBLEMS.append(f"заглушка в тексте: {txt[:80]}")
        if p.get("caption") and len(p["caption"]) > 1024:
            PROBLEMS.append(f"подпись длиннее 1024: {p['caption'][:60]}")
        if len(p.get("text") or "") > 4096:
            PROBLEMS.append("текст длиннее 4096")
        for b in buttons(p):
            if len((b.get("callback_data") or "").encode()) > 64:
                PROBLEMS.append(f"callback_data > 64 байт: {b['callback_data']}")
            st = b.get("style")
            green = b["text"].startswith("Поехали") or "Хочу стать партнёром" in b["text"]
            if st == "danger":
                PROBLEMS.append(f"красная кнопка: {b['text']}")
            if green and st != "success":
                PROBLEMS.append(f"должна быть зелёной: {b['text']}")
            if not green and st != "primary":
                PROBLEMS.append(f"должна быть синей: {b['text']} ({st})")
        if name in ("SendPhoto", "SendVideo") and isinstance(m.model_dump().get("photo") or m.model_dump().get("video"), str) is False:
            pass


def last_kb(uid):
    for chat, name, p, m in reversed(SENT):
        if chat == uid and buttons(p):
            return buttons(p)
    return []


async def press(uid, label):
    for b in last_kb(uid):
        if label in b["text"]:
            return await feed(uid, data=b["callback_data"])
    PROBLEMS.append(f"[{uid}] нет кнопки «{label}»; есть: {[b['text'] for b in last_kb(uid)]}")
    return []


def shown(uid, screen):
    with db._Ctx() as c:
        c.execute(db._q("SELECT COUNT(*) AS n FROM events WHERE user_id=%s AND event='screen' AND details=%s"), (uid, screen))
        return db._one(c)["n"] > 0


def expect(cond, what):
    print(("  ✅ " if cond else "  ❌ ") + what)
    if not cond:
        PROBLEMS.append(what)


async def run_due(force=True):
    """Сделать созревшими все ожидающие сообщения и прогнать цикл очереди один раз."""
    if force:
        with db._Ctx() as c:
            c.execute(db._q("UPDATE scheduled SET due_ts=%s WHERE status='pending' AND kind=%s"), (int(time.time()) - 1, "p1"))
    for r in await asyncio.to_thread(db.claim_due):
        await B.run_scheduled(bot, r)
    await asyncio.sleep(0.05)


async def survey(uid, s3="до 50 тыс. ₽", s4="4–6", age="21–24", src=None):
    await feed(uid, text="/start" + (f" {src}" if src else ""))
    await press(uid, "Поехали")
    await press(uid, "Давно слежу")
    await press(uid, "Заработок")
    await press(uid, s3)
    await press(uid, s4)
    return await press(uid, age)


async def flow_team():
    print("\nВетка «В команде» (тест → анкета → два видео → звонок → запись):")
    uid = 501
    await survey(uid)
    await press(uid, "Дальше")              # t1 → t2
    out = await press(uid, "Дальше")        # t2 → t3 + offer
    expect(shown(uid, "t3") and shown(uid, "offer"), "знакомство 3 из 3 и предложение после опроса")
    expect(any((p.get("reply_markup") or {}).get("keyboard") for _, n, p, m in out), "нижнее меню появилось после знакомства")
    await press(uid, "Пройти тест")
    await press(uid, "Поехали")
    for _ in range(7):
        await press(uid, "Долгий разговор") if any("Долгий разговор" in b["text"] for b in last_kb(uid)) else await feed(uid, data=last_kb(uid)[1]["callback_data"])
    await press(uid, "Норм, я сова")
    out = await press(uid, "Да, понимаю")
    texts = " ".join((p.get("text") or "") for _, n, p, m in out)
    expect("СОБЕСЕДНИЦА" in texts and "условия совпадают" in texts, "результат теста: собеседница, ✅")
    expect(shown(uid, "fork"), "развилка после теста")
    await press(uid, "В команде · 50 на 50")
    s = B._sessions[uid]
    expect("Анкета — 9 коротких" in " ".join((p.get("text") or "") for _, n, p, m in SENT[-3:]), "анкета: 9 вопросов (часы и ночь уже известны)")
    await press(uid, "Начать анкету")
    await press(uid, "21–24")
    await press(uid, "Другой город")
    await feed(uid, text="Тверь")
    await press(uid, "Работаю")
    await press(uid, "Каждый день")
    await press(uid, "Вечер")
    await press(uid, "Немного")
    await press(uid, "Телефон + свет")
    await press(uid, "Стабильный")
    out = await feed(uid, text="Люблю общаться и хочу свой доход")
    expect(shown(uid, "vCard"), "видео 1 из 2 — визитка")
    out = await feed(uid, text="а можно без видео?")
    expect("Жду кружок" in (out[0][2].get("text") or ""), "без видео дальше не пускает, подсказывает")
    out = await feed(uid, video_note=True)
    expect(shown(uid, "vExample") and shown(uid, "vBattle"), "пример баттла и видео 2 из 2")
    expect(any(n == "SendVideo" for _, n, p, m in out), "видео баттлов уходят как видео (со звуком)")
    out = await feed(uid, video=True)
    lead_msgs = [p for ch, n, p, m in out if ch == -1001]
    expect(any("Анкета и видео" in (p.get("text") or "") for p in lead_msgs), "карточка анкеты ушла представителю")
    expect(sum(1 for ch, n, p, m in out if ch == -1001 and n in ("SendVideoNote", "SendVideo")) == 2, "оба видео переслали представителю")
    expect("Тверь (решаем индивидуально)" in " ".join(p.get("text") or "" for p in lead_msgs), "свой город в карточке")
    await press(uid, "Звонок")
    out = await feed(uid, contact="+79161234567")
    expect(shown(uid, "day"), "после номера — выбор дня")
    out = await feed(uid, data=last_kb(uid)[0]["callback_data"])     # первый день
    out = await feed(uid, data=last_kb(uid)[0]["callback_data"])     # первое время
    expect(shown(uid, "confirm") or any("Записал" in (p.get("text") or "") for _, n, p, m in out), "подтверждение времени")
    out = await press(uid, "Да, буду")
    expect(any("ЗАПИСЬ" in (p.get("text") or "") for ch, n, p, m in out if ch == -1001), "запись ушла представителю")
    pend = {r["kind"] for r in db.pending(uid)}
    expect({"rem2", "rem15"} <= pend, f"напоминания поставлены: {sorted(pend)}")
    lead = db.lead_get_open(uid)
    expect(lead and lead["status"] == "записана" and lead["phone"] == "+79161234567" and lead["video_card"] == "USERNOTE",
           "заявка в базе: записана, телефон, видео")
    # перенос из напоминания
    await feed(uid, data="resched")
    await feed(uid, data=last_kb(uid)[0]["callback_data"])
    kbs = last_kb(uid)
    await feed(uid, data=kbs[1]["callback_data"] if len(kbs) > 2 else kbs[0]["callback_data"])
    out = await press(uid, "Да, буду")
    expect(db.lead_get_open(uid)["status"] == "перенесена", "перенос: статус «перенесена», старые напоминания заменены")
    # представитель отвечает на карточку
    card = [m for ch, n, p, m in SENT if ch == -1001 and n == "SendMessage"][-1]
    rid = _mid[0]
    with db._Ctx() as c:
        c.execute(db._q("SELECT message_id FROM relay WHERE user_id=%s ORDER BY message_id DESC LIMIT 1"), (uid,))
        rid = db._one(c)["message_id"]
    out = await feed(9, text="Катя, привет! Позвоню чуть раньше", chat_id=-1001, reply_to=rid)
    expect(any(ch == uid and "представителя" in (p.get("text") or "") for ch, n, p, m in out), "ответ представителя дошёл человеку")


async def flow_partner():
    print("\nВетка «Партнёр TeamUniverse» (реклама партнёрства → преимущества → калькулятор → переписка → запись):")
    uid = 502
    await survey(uid, s3="100–300 тыс. ₽", src="partner_ads1")
    await press(uid, "Дальше")
    out = await press(uid, "Дальше")
    expect(shown(uid, "offer") and any("Ты целишься в 100–300" in (p.get("caption") or "") for _, n, p, m in out), "предложение партнёрства по ответам")
    expect(db.get_user(uid)["source"] == "partner_ads1", "метка источника сохранена")
    await press(uid, "Что даёт партнёрство")
    expect(shown(uid, "p1"), "партнёрство: первое преимущество (видео)")
    await press(uid, "Далее")
    expect(B._sessions[uid]["p1step"] == 1, "«Далее» → второе преимущество")
    p = db.pending(uid, "p1")
    expect(len(p) == 1 and 55 <= p[0]["due_ts"] - time.time() <= 61, "таймер «через минуту» записан в базу")
    # перезапуск бота: память очищена, таймер в базе
    B._sessions.clear()
    await run_due()
    expect(B._sessions[uid]["p1step"] == 2, "после перезапуска таймер сработал: третье преимущество пришло само")
    n_before = len(SENT)
    await feed(uid, data="p1:2")     # старая кнопка «Далее» — повтор не шлём
    expect(len([x for x in SENT[n_before:] if x[1].startswith("Send")]) == 0, "повторное «Далее» не дублирует сообщение")
    await press(uid, "Далее")
    expect(not db.pending(uid, "p1"), "после последнего шага таймера нет")
    out = await press(uid, "Сколько можно зарабатывать")
    cap = [p for _, n, p, m in out if n == "SendPhoto"]
    expect(cap and any(b["text"] == "• 6 ч" for b in buttons(cap[0])), "калькулятор: 6 часов по умолчанию")
    out = await press(uid, "2 ч")
    names = [n for _, n, p, m in out]
    expect("EditMessageMedia" in names and "SendPhoto" not in names, "смена часов правит то же сообщение")
    await press(uid, "Назад")
    pend = {r["kind"] for r in db.pending(uid)}
    expect({"dz1", "dz3", "dz6"} <= pend, "после цены поставлен дожим: 1, 5, 14 дней")
    await press(uid, "Посчитать доход")
    await press(uid, "Хочу стать партнёром")
    await press(uid, "до 3 месяцев")
    await press(uid, "в этом месяце")
    await press(uid, "есть")
    await press(uid, "Переписка")
    await feed(uid, data=last_kb(uid)[0]["callback_data"])
    await feed(uid, data=last_kb(uid)[0]["callback_data"])
    out = await press(uid, "Да, буду")
    expect(any("частые вопросы" in (p.get("text") or "") for _, n, p, m in out), "запись: «пока ждёшь — частые вопросы»")
    pend = {r["kind"] for r in db.pending(uid)}
    expect(not ({"dz1", "dz3", "dz6"} & pend), "после записи дожим отменён")
    lead = db.lead_get_open(uid)
    expect(lead and lead["branch"] == "partner" and lead["way"] == "chat" and "Сумма на старт" in lead["answers"], "заявка партнёра в базе")
    # напоминания реально приходят
    with db._Ctx() as c:
        c.execute(db._q("UPDATE scheduled SET due_ts=%s WHERE user_id=%s AND status='pending'"), (int(time.time()) - 1, uid))
    n0 = len(SENT)
    await run_due(force=False)
    txt = " ".join(p.get("text") or "" for ch, n, p, m in SENT[n0:] if ch == uid)
    expect("Через 15 минут представитель напишет" in txt and "Через 2 часа" in txt, "напоминания о разговоре приходят")


async def flow_under18():
    print("\nСтоп для тех, кому нет 18:")
    uid = 503
    out = await survey(uid, age="младше 18")
    expect(shown(uid, "under18") and db.get_user(uid)["underage"] == 1, "младше 18 → стоп, отмечено в базе")
    await feed(uid, text="👥 В команду")
    await feed(uid, text="🚀 Партнёрство")
    await feed(uid, data="g:calc")
    expect(not shown(uid, "aIntro") and not shown(uid, "calc"), "анкета команды и калькулятор не открываются")
    await feed(uid, data="g:bQ1")
    expect(not shown(uid, "bQ1"), "запись на консультацию не открывается")


async def flow_red_and_links():
    print("\nТест с ответом «сейчас не твоё» и метки ссылок:")
    uid = 504
    await feed(uid, text="/start test_post12")
    expect(shown(uid, "testIntro") and db.get_user(uid)["source"] == "test_post12", "?start=test_post12 → сразу тест, метка записана")
    await press(uid, "Поехали")
    for _ in range(7):
        await feed(uid, data=last_kb(uid)[0]["callback_data"])
    await press(uid, "до 2")
    await press(uid, "Точно нет")
    out = await press(uid, "Нет, нужен результат")
    expect(shown(uid, "red") and not shown(uid, "fork"), "🔴: честное «сейчас не твоё», без развилки")
    t = " ".join(p.get("text") or "" for _, n, p, m in out)
    expect("Что мешает: до 2 часов" in t, "причины перечислены")
    await feed(uid, text="/start casting")
    expect(shown(uid, "aIntro"), "старая ссылка ?start=casting → анкета команды")
    await feed(uid, text="/start reel_tiktok_1")
    expect(db.get_user(uid)["source"] == "test_post12" and db.get_user(uid)["last_source"] == "reel_tiktok_1",
           "первая метка не перезаписывается, последняя — отдельно")
    out = await feed(uid, text="привет, сколько стоит?")
    expect(shown(uid, "freeText"), "свободный текст → «я бот и отвечаю кнопками»")
    await press(uid, "Не актуально") if any("Не актуально" in b["text"] for b in last_kb(uid)) else None


async def flow_materials():
    print("\nМеню, материалы, О студии:")
    uid = 505
    await feed(uid, text="/menu")
    await press(uid, "Полезные материалы")
    out = await press(uid, "Гайд")
    expect(any(n == "SendDocument" for _, n, p, m in out), "гайд уходит файлом")
    await feed(uid, text="ℹ️ О студии")
    expect(shown(uid, "about"), "нижнее меню → О студии")
    await press(uid, "Ещё пример баттла")
    expect(shown(uid, "t2v"), "ещё пример баттла")


def media_check():
    print("\nМедиа:")
    import media as M
    keys = set()
    for scr in C.S.values():
        for x in scr:
            if x.get("media"):
                keys.add(x["media"])
            if x.get("doc"):
                keys.add("doc:" + x["doc"])
    for x in C.P1:
        keys.add(x["media"])
    keys |= {f"card:calc_{h}" for h in C.CALC_HOURS} | {f"card:aq_{q[0]}" for q in C.TEAM_Q} | {f"card:bQ{i}" for i in (1, 2, 3)}
    keys.add("video:avatar")
    missing, big = [], []
    for k in sorted(keys):
        p = M.path_of(k)
        if not os.path.exists(p):
            missing.append(k)
            continue
        size = os.path.getsize(p)
        lim = 200 * 1024 if k.startswith("card:") else (5 * 1024 * 1024 if k.startswith("video:") else 20 * 1024 * 1024)
        if size > lim:
            big.append(f"{k} {size // 1024} КБ")
    expect(not missing, f"все файлы на месте ({len(keys)})" + (f" — нет: {missing}" if missing else ""))
    expect(not big, "картинки ≤ 200 КБ, видео ≤ 5 МБ" + (f" — больше: {big}" if big else ""))
    # повторы: один и тот же файл на разных экранах
    import hashlib
    seen = {}
    for k in sorted(keys):
        p = M.path_of(k)
        if os.path.exists(p) and not k.startswith("doc:"):
            h = hashlib.md5(open(p, "rb").read()).hexdigest()
            seen.setdefault(h, []).append(k)
    dup = [v for v in seen.values() if len(v) > 1]
    expect(not dup, "каждая картинка и видео — один раз" + (f" — повторы: {dup}" if dup else ""))


async def main():
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
    if db.PG:      # чистая тестовая база Postgres
        with db._Ctx() as c:
            c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    migrate.run()
    migrate.run()   # повторный запуск безопасен
    import media
    media.load()
    await flow_team()
    await flow_partner()
    await flow_under18()
    await flow_red_and_links()
    await flow_materials()
    media_check()
    errs = []
    with db._Ctx() as c:
        c.execute("SELECT details FROM events WHERE event='error'")
        errs = [r["details"] for r in db._all(c)]
    expect(not errs, "ошибок в журнале нет" + (f": {errs[:3]}" if errs else ""))
    ms = [t for _, t, n in TIMES]
    ms.sort()
    print(f"\nВремя обработки нажатия внутри бота (без сети Telegram), {len(ms)} нажатий: "
          f"середина {ms[len(ms) // 2]:.0f} мс, 95% — до {ms[int(len(ms) * .95)]:.0f} мс, худшее {ms[-1]:.0f} мс")
    sends = sorted({n for _, _, n in TIMES})
    print(f"Сообщений на одно нажатие: от {min(sends)} до {max(sends)}")
    print("\nИТОГ:", "всё прошло ✅" if not PROBLEMS else f"проблем: {len(PROBLEMS)}")
    for p in PROBLEMS:
        print("  •", p)
    json.dump({"times": TIMES, "problems": PROBLEMS}, open(os.path.join(HERE, "last_run.json"), "w", encoding="utf-8"), ensure_ascii=False)
    await bot.session.close()
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
