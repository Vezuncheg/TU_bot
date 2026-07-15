"""TeamUniverse Bot — точка входа и все хендлеры (aiogram 3).
Сценарии: M (меню), A (антискам), G (гайд), T (тест), C (кастинг), F (франшиза).
Запуск: заполнить .env → pip install -r requirements.txt → python bot.py
"""
import asyncio, logging, datetime
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, InlineKeyboardMarkup,
                           InlineKeyboardButton, FSInputFile)
from aiogram.exceptions import TelegramBadRequest

import config, storage as db, texts as T

logging.basicConfig(level=logging.INFO)
router = Router()

# ---------------- helpers ----------------
def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) if not d.startswith("http")
         else InlineKeyboardButton(text=t, url=d) for t, d in row] for row in rows])

async def is_subscribed(bot: Bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(config.CHANNEL_ID, user_id)
        return m.status in ("member", "administrator", "creator")
    except TelegramBadRequest:
        return False

async def send_doc(msg: Message, path: str, fallback: str):
    try:
        await msg.answer_document(FSInputFile(path))
    except Exception:
        await msg.answer(f"[Файл {fallback} не найден на сервере — положите его по пути из config.py]")

async def notify(bot: Bot, chat_id: int, text: str, video: str = None):
    if not chat_id: return
    try:
        await bot.send_message(chat_id, text)
        if video:
            await bot.send_video_note(chat_id, video)
    except Exception as e:
        logging.warning(f"notify failed: {e}")

# ---------------- FSM ----------------
class Test(StatesGroup):
    q = State()

class Casting(StatesGroup):
    age = State(); city = State(); exp = State(); hours = State()
    nights = State(); video = State(); contact = State()

class Franchise(StatesGroup):
    city = State(); biz = State(); budget = State(); timing = State()
    familiar = State(); contact = State()

# ---------------- /start и меню ----------------
MENU_KB = kb([
    [("🎬 Хочу стримить — кастинг", "m_stream")],
    [("🧭 Тест: подхожу ли я для заработка", "go_test")],
    [("📕 Полезные материалы", "m_materials")],
    [("🤝 Партнёрство и франшиза", "m_franchise")],
    [("📢 Наш канал с реальными цифрами", config.CHANNEL_URL)],
])

@router.message(CommandStart(deep_link=True))
async def start_deeplink(msg: Message, command: CommandObject, state: FSMContext):
    src = (command.args or "organic").strip().lower()
    db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name, src)
    db.log_event(msg.from_user.id, f"start_{src}")
    routes = {"test": run_test_intro, "antiscam": run_antiscam, "guide30": run_guide,
              "casting": run_casting_intro, "franchise": run_franchise_intro}
    handler = routes.get(src)
    if handler:
        await handler(msg, state)
    else:
        await msg.answer(T.MENU_MAIN, reply_markup=MENU_KB)

@router.message(CommandStart())
async def start_plain(msg: Message):
    db.upsert_user(msg.from_user.id, msg.from_user.username, msg.from_user.first_name, "organic")
    db.log_event(msg.from_user.id, "start_organic")
    await msg.answer(T.MENU_MAIN, reply_markup=MENU_KB)

@router.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(T.MENU_MAIN, reply_markup=MENU_KB)

@router.message(Command("help"))
async def cmd_help(msg: Message, state: FSMContext):
    await state.set_state(None)
    await state.update_data(help_mode=True)
    await msg.answer(T.HELP_TEXT)

@router.message(Command("materials"))
async def cmd_materials(msg: Message):
    await show_materials(msg)

@router.message(Command("casting"))
async def cmd_casting(msg: Message, state: FSMContext):
    await run_casting_intro(msg, state)

# --- прослойки пользы сценария M (правка 2) ---
@router.callback_query(F.data == "m_stream")
async def m_stream(cb: CallbackQuery):
    db.log_event(cb.from_user.id, "menu_stream")
    await cb.message.answer(T.M2_STREAM, reply_markup=kb([
        [("🧭 Проверить, подхожу ли я — тест 3 мин", "go_test")],
        [("🎬 Сразу на кастинг", "go_casting")]]))
    await cb.answer()

@router.callback_query(F.data == "m_franchise")
async def m_franchise(cb: CallbackQuery):
    db.log_event(cb.from_user.id, "menu_franchise")
    await cb.message.answer(T.M3_FRANCHISE, reply_markup=kb([
        [("📝 Заполнить анкету", "go_franchise")],
        [("📚 Сначала изучу материалы", "fr_later")]]))
    await cb.answer()

@router.callback_query(F.data == "fr_later")
async def fr_later(cb: CallbackQuery):
    db.log_event(cb.from_user.id, "franchise_interest")
    db.schedule(cb.from_user.id, 3*86400, "fr_m3_reminder")
    await cb.message.answer("Хорошо! Материалы выше, напомню через пару дней. 🤝")
    await cb.answer()

@router.callback_query(F.data == "m_materials")
async def m_materials(cb: CallbackQuery):
    await show_materials(cb.message); await cb.answer()

async def show_materials(msg: Message):
    await msg.answer("Что прислать?", reply_markup=kb([
        [("🛡 Чек-лист: как не попасть на скам", "go_antiscam")],
        [("📕 Гайд: первые 30 дней ведущие", "go_guide")]]))

# ---------------- Сценарии A и G: магниты ----------------
async def run_antiscam(msg: Message, state: FSMContext = None):
    await msg.answer(T.ANTISCAM_HELLO)
    await deliver_magnet(msg, "antiscam")

async def run_guide(msg: Message, state: FSMContext = None):
    await msg.answer(T.GUIDE_HELLO)
    await deliver_magnet(msg, "guide30")

async def deliver_magnet(msg: Message, which: str):
    uid = msg.chat.id
    if not await is_subscribed(msg.bot, uid):
        await msg.answer(T.NEED_SUB, reply_markup=kb([
            [("📢 Подписаться на канал", config.CHANNEL_URL)],
            [("✅ Готово, проверяй", f"chk_{which}")]]))
        return
    db.set_user(uid, subscribed=1)
    if which == "antiscam":
        await send_doc(msg, config.ANTISCAM_PDF, "чек-листа")
        await msg.answer(T.ANTISCAM_DELIVERED, reply_markup=kb([
            [("🧭 Пройти тест", "go_test")], [("Не сейчас", "noop")]]))
        db.schedule(uid, 86400, "antiscam_24h")
    else:
        await send_doc(msg, config.GUIDE30_PDF, "гайда")
        await msg.answer(T.GUIDE_DELIVERED, reply_markup=kb([
            [("🎬 Подать заявку на кастинг", "go_casting")], [("Сначала дочитаю", "noop")]]))
        db.schedule(uid, 2*86400, "guide_48h")
    db.add_magnet(uid, which)
    db.log_event(uid, f"magnet_{which}")

@router.callback_query(F.data.startswith("chk_"))
async def recheck_sub(cb: CallbackQuery):
    which = cb.data.split("_", 1)[1]
    if await is_subscribed(cb.bot, cb.from_user.id):
        db.set_user(cb.from_user.id, subscribed=1)
        db.log_event(cb.from_user.id, "subscribed")
        await deliver_magnet(cb.message, which)
    else:
        await cb.answer("Пока не вижу подписки 🙈 Подпишитесь и нажмите ещё раз", show_alert=True)
        return
    await cb.answer()

# ---------------- Сценарий T: тест «Подходишь ли ты» ----------------
async def run_test_intro(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(T.TEST_INTRO, reply_markup=kb([[("Поехали →", "t_go")]]))

@router.callback_query(F.data == "go_test")
async def cb_test(cb: CallbackQuery, state: FSMContext):
    await run_test_intro(cb.message, state); await cb.answer()

@router.callback_query(F.data == "t_go")
async def t_go(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Test.q)
    await state.update_data(qn=0, types={"E":0,"S":0,"T":0,"D":0}, flags=[], cond={})
    db.log_event(cb.from_user.id, "test_started")
    await ask_test_q(cb.message, 0)
    await cb.answer()

async def ask_test_q(msg: Message, qn: int):
    if qn < 7:
        text, opts = T.TEST_TYPE_QUESTIONS[qn]
        buttons = [[(o, f"ta_{qn}_{letter}")] for o, letter in opts]
    else:
        text, opts, key = T.TEST_COND_QUESTIONS[qn - 7]
        buttons = [[(o, f"tc_{qn}_{val}_{1 if flag else 0}")] for o, val, flag in opts]
    await msg.answer(text, reply_markup=kb(buttons))

@router.callback_query(Test.q, F.data.startswith(("ta_", "tc_")))
async def test_answer(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    qn = data["qn"]
    parts = cb.data.split("_")
    if parts[0] == "ta":
        data["types"][parts[2]] += 1
    else:
        _, _, val, flag = parts[0], parts[1], parts[2], parts[3]
        key = T.TEST_COND_QUESTIONS[qn - 7][2]
        data["cond"][key] = val
        if flag == "1":
            data["flags"].append(key)
    qn += 1
    data["qn"] = qn
    await state.update_data(data)
    if qn < 10:
        await ask_test_q(cb.message, qn)
    else:
        await finish_test(cb.message, cb.from_user.id, state)
    await cb.answer()

async def finish_test(msg: Message, uid: int, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    types, flags, cond = data["types"], data["flags"], data["cond"]
    # типаж: максимум, ничья по приоритету Э > Д > С > Т
    order = ["E", "D", "S", "T"]
    ttype = max(order, key=lambda k: (types[k], -order.index(k)))
    verdict = "green" if len(flags) == 0 else ("yellow" if len(flags) == 1 else "red")
    db.set_user(uid, test_type=ttype, test_verdict=verdict,
                test_flags=",".join(flags),
                test_hours=cond.get("hours",""), test_nights=cond.get("nights",""))
    db.log_event(uid, f"test_verdict_{verdict}", ttype)
    title, body = T.TYPE_RESULTS[ttype]
    await msg.answer(f"Твой типаж: {title}\n\n{body}")
    if verdict == "green":
        await msg.answer(T.VERDICT_GREEN, reply_markup=kb([
            [("🎬 На кастинг (часть ответов уже перенесла)", "go_casting")],
            [("📕 Сначала гайд 30 дней", "go_guide")]]))
    elif verdict == "yellow":
        await msg.answer(T.VERDICT_YELLOW.format(flag=T.FLAG_EXPLAIN[flags[0]]),
                         reply_markup=kb([[("📕 Гайд: первые 30 дней", "go_guide")],
                                          [("🎬 Всё равно на кастинг", "go_casting")]]))
    else:
        expl = "; ".join(T.FLAG_EXPLAIN[f] for f in flags)
        await msg.answer(T.VERDICT_RED.format(flags=expl),
                         reply_markup=kb([[("📕 Забрать гайд — понять, как всё устроено", "go_guide")]]))
    # польза-сюрприз (G11)
    await msg.answer(T.TYPE_TIPS[ttype])

# ---------------- Сценарий C: кастинг ----------------
@router.callback_query(F.data == "go_casting")
async def cb_casting(cb: CallbackQuery, state: FSMContext):
    await run_casting_intro(cb.message, state, cb.from_user.id); await cb.answer()

async def run_casting_intro(msg: Message, state: FSMContext, uid: int = None):
    uid = uid or msg.chat.id
    u = db.get_user(uid) or {}
    if u.get("underage"):
        await msg.answer(T.UNDERAGE_STOP); return
    prev = db.recent_application("casting", uid)
    if prev:
        dt = datetime.datetime.fromtimestamp(prev["ts"]).strftime("%d.%m")
        await msg.answer(T.DUPLICATE_APP.format(date=dt, status=prev["status"])); return
    await state.clear()
    await msg.answer(T.CASTING_INTRO, reply_markup=kb([
        [("Начать ✅", "c_go")], [("Сначала расскажите про условия", "c_terms")]]))

@router.callback_query(F.data == "c_terms")
async def c_terms(cb: CallbackQuery):
    await cb.message.answer(T.CASTING_TERMS, reply_markup=kb([[("Начать ✅", "c_go")]]))
    await cb.answer()

@router.callback_query(F.data == "c_go")
async def c_go(cb: CallbackQuery, state: FSMContext):
    db.log_event(cb.from_user.id, "casting_started")
    u = db.get_user(cb.from_user.id) or {}
    prefill = bool(u.get("test_hours") and u.get("test_nights"))
    await state.update_data(prefill=prefill)
    if prefill:
        await cb.message.answer(T.PREFILL_NOTE)
    await state.set_state(Casting.age)
    await cb.message.answer(T.Q_AGE)
    await cb.answer()

@router.message(Casting.age)
async def c_age(msg: Message, state: FSMContext):
    try:
        age = int(msg.text.strip())
        assert 10 <= age <= 80
    except Exception:
        await msg.answer(T.AGE_RETRY); return
    if age < 18:
        db.set_user(msg.from_user.id, underage=1, no_warmup=1)
        db.cancel_followups(msg.from_user.id)
        db.log_event(msg.from_user.id, "casting_underage")
        await state.clear()
        await msg.answer(T.UNDERAGE_STOP)
        return
    await state.update_data(age=age)
    await state.set_state(Casting.city)
    await msg.answer(T.Q_CITY)

@router.message(Casting.city)
async def c_city(msg: Message, state: FSMContext):
    await state.update_data(city=msg.text.strip()[:100])
    await state.set_state(Casting.exp)
    await msg.answer(T.Q_EXP, reply_markup=kb([
        [("Веду соцсети активно", "ce_active")], [("Аккаунт есть, но тихий", "ce_quiet")],
        [("С полного нуля", "ce_zero")]]))

@router.callback_query(Casting.exp, F.data.startswith("ce_"))
async def c_exp(cb: CallbackQuery, state: FSMContext):
    m = {"ce_active": "веду активно", "ce_quiet": "аккаунт тихий", "ce_zero": "с нуля"}
    await state.update_data(exp=m[cb.data])
    data = await state.get_data()
    if data.get("prefill"):
        u = db.get_user(cb.from_user.id)
        await state.update_data(hours=u["test_hours"], nights=u["test_nights"])
        await state.set_state(Casting.video)
        await cb.message.answer(T.Q_VIDEO, reply_markup=kb([[("Запишу позже", "cv_later")]]))
    else:
        await state.set_state(Casting.hours)
        await cb.message.answer(T.Q_HOURS, reply_markup=kb([
            [("до 10", "ch_lt10")], [("10–20", "ch_mid")], [("20+", "ch_hi")]]))
    await cb.answer()

@router.callback_query(Casting.hours, F.data.startswith("ch_"))
async def c_hours(cb: CallbackQuery, state: FSMContext):
    m = {"ch_lt10": "lt10", "ch_mid": "10-20", "ch_hi": "20+"}
    await state.update_data(hours=m[cb.data])
    await state.set_state(Casting.nights)
    await cb.message.answer(T.Q_NIGHTS, reply_markup=kb([
        [("Норм, я сова 🦉", "cn_ok")], [("Надо понять, как устроено", "cn_maybe")],
        [("Точно нет", "cn_no")]]))
    await cb.answer()

@router.callback_query(Casting.nights, F.data.startswith("cn_"))
async def c_nights(cb: CallbackQuery, state: FSMContext):
    m = {"cn_ok": "ok", "cn_maybe": "maybe", "cn_no": "no"}
    await state.update_data(nights=m[cb.data])
    await state.set_state(Casting.video)
    await cb.message.answer(T.Q_VIDEO, reply_markup=kb([[("Запишу позже", "cv_later")]]))
    await cb.answer()

@router.message(Casting.video, F.video_note | F.video)
async def c_video(msg: Message, state: FSMContext):
    media = msg.video_note or msg.video
    if getattr(media, "duration", 0) > 180 or getattr(media, "file_size", 0) > 20*1024*1024:
        await msg.answer(T.VIDEO_TOO_BIG); return
    await state.update_data(video_file_id=media.file_id, video_is_note=bool(msg.video_note))
    await state.set_state(Casting.contact)
    await msg.answer(T.Q_CONTACT)

@router.callback_query(Casting.video, F.data == "cv_later")
async def c_video_later(cb: CallbackQuery, state: FSMContext):
    await state.update_data(video_file_id=None)
    await state.set_state(Casting.contact)
    await cb.message.answer(T.Q_CONTACT)
    await cb.answer()

@router.message(Casting.contact)
async def c_contact(msg: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    uid, u = msg.from_user.id, msg.from_user
    hours, nights, video = data.get("hours"), data.get("nights"), data.get("video_file_id")
    cold = hours == "lt10" or nights == "no"
    score = "cold" if cold else ("hot" if video else "warm")
    db.save_casting(user_id=uid, username=u.username, name=u.first_name, age=data["age"],
                    city=data["city"], exp=data["exp"], hours=hours, nights=nights,
                    video_file_id=video, contact=msg.text.strip()[:100], score=score)
    db.mirror("Casting", [datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
                          u.first_name or "", f"@{u.username}" if u.username else str(uid),
                          data["age"], data["city"], data["exp"], hours, nights,
                          "есть" if video else "нет", msg.text.strip()[:100], score])
    db.log_event(uid, f"casting_scored_{score}")
    if score == "hot":
        await msg.answer(T.CASTING_DONE_HOT)
        await send_doc(msg, config.GUIDE30_PDF, "гайда")
        card = (f"🔥 КАСТИНГ | {u.first_name}, {data['age']}, {data['city']}\n"
                f"Опыт: {data['exp']} · Часы: {hours} · Ночные: {nights}\n"
                f"Источник: {(db.get_user(uid) or {}).get('source')} · "
                f"Профиль: @{u.username or uid} · Контакт: {msg.text.strip()[:100]}")
        await notify(msg.bot, config.DENIS_CHAT_ID, card,
                     video if data.get("video_is_note") else None)
    elif score == "warm":
        await msg.answer(T.CASTING_DONE_WARM)
        await send_doc(msg, config.GUIDE30_PDF, "гайда")
        db.schedule(uid, 86400, "casting_video_24h")
    else:
        reason = ("при менее чем 10 часах в неделю" if hours == "lt10"
                  else "без вечерних эфиров под зарубежный прайм")
        await msg.answer(T.CASTING_DONE_COLD.format(reason=reason))
        await send_doc(msg, config.GUIDE30_PDF, "гайда")

@router.message(F.video_note | F.video)
async def stray_video(msg: Message):
    """Визитка, присланная после анкеты (warm → hot)."""
    prev = db.recent_application("casting", msg.from_user.id)
    if prev and not prev["video_file_id"]:
        media = msg.video_note or msg.video
        db.attach_casting_video(msg.from_user.id, media.file_id)
        db.cancel_followups(msg.from_user.id, "casting_video")
        db.log_event(msg.from_user.id, "casting_video_attached")
        await msg.answer("Приложила к заявке! Теперь она уходит на разбор в первую очередь 🎬")
        card = (f"🔥 КАСТИНГ (довисла визитка) | {prev['name']}, {prev['age']}, {prev['city']}\n"
                f"Часы: {prev['hours']} · Ночные: {prev['nights']} · Контакт: {prev['contact']}")
        await notify(msg.bot, config.DENIS_CHAT_ID, card,
                     media.file_id if msg.video_note else None)

# ---------------- Сценарий F: франшиза ----------------
@router.callback_query(F.data == "go_franchise")
async def cb_fr(cb: CallbackQuery, state: FSMContext):
    await run_franchise_intro(cb.message, state, cb.from_user.id); await cb.answer()

async def run_franchise_intro(msg: Message, state: FSMContext, uid: int = None):
    uid = uid or msg.chat.id
    prev = db.recent_application("franchise", uid)
    if prev:
        dt = datetime.datetime.fromtimestamp(prev["ts"]).strftime("%d.%m")
        await msg.answer(T.DUPLICATE_APP.format(date=dt, status=prev["status"])); return
    await state.clear()
    await msg.answer(T.FR_INTRO, reply_markup=kb([[("Поехали →", "f_go")]]))

@router.callback_query(F.data == "f_go")
async def f_go(cb: CallbackQuery, state: FSMContext):
    db.log_event(cb.from_user.id, "franchise_started")
    await state.set_state(Franchise.city)
    await cb.message.answer(T.FR_CITY)
    await cb.answer()

@router.message(Franchise.city)
async def f_city(msg: Message, state: FSMContext):
    await state.update_data(city=msg.text.strip()[:100])
    await state.set_state(Franchise.biz)
    await msg.answer(T.FR_EXP, reply_markup=kb([
        [("Действующий бизнес", "fb_now")], [("Был опыт", "fb_past")],
        [("Опыта нет, есть команда/партнёр", "fb_team")], [("Опыта нет", "fb_none")]]))

@router.callback_query(Franchise.biz, F.data.startswith("fb_"))
async def f_biz(cb: CallbackQuery, state: FSMContext):
    m = {"fb_now": "действующий бизнес", "fb_past": "был опыт",
         "fb_team": "нет, есть команда", "fb_none": "нет"}
    await state.update_data(biz=m[cb.data])
    await state.set_state(Franchise.budget)
    # [ЗАГЛУШКА: вилки от реальных цен тарифов]
    await cb.message.answer(T.FR_BUDGET, reply_markup=kb([
        [("до 300 тыс.", "fu_a")], [("300–700 тыс.", "fu_b")],
        [("700 тыс. и выше", "fu_c")], [("Пока изучаю модель", "fu_x")]]))
    await cb.answer()

@router.callback_query(Franchise.budget, F.data.startswith("fu_"))
async def f_budget(cb: CallbackQuery, state: FSMContext):
    m = {"fu_a": "до 300К", "fu_b": "300-700К", "fu_c": "700К+", "fu_x": "изучаю"}
    await state.update_data(budget=m[cb.data])
    await state.set_state(Franchise.timing)
    await cb.message.answer(T.FR_TIMING, reply_markup=kb([
        [("В течение месяца", "ft_1")], [("2–3 месяца", "ft_3")],
        [("Полгода и позже", "ft_6")], [("Просто интересуюсь", "ft_x")]]))
    await cb.answer()

@router.callback_query(Franchise.timing, F.data.startswith("ft_"))
async def f_timing(cb: CallbackQuery, state: FSMContext):
    m = {"ft_1": "месяц", "ft_3": "2-3 мес", "ft_6": "полгода+", "ft_x": "интересуюсь"}
    await state.update_data(timing=m[cb.data])
    await state.set_state(Franchise.familiar)
    await cb.message.answer(T.FR_FAMILIAR, reply_markup=kb([
        [("Работаю в этой нише", "ff_pro")], [("Смотрю как зритель", "ff_view")],
        [("Узнал(а) из вашего контента", "ff_new")]]))
    await cb.answer()

@router.callback_query(Franchise.familiar, F.data.startswith("ff_"))
async def f_familiar(cb: CallbackQuery, state: FSMContext):
    m = {"ff_pro": "в нише", "ff_view": "зритель", "ff_new": "из контента"}
    await state.update_data(familiar=m[cb.data])
    await state.set_state(Franchise.contact)
    await cb.message.answer(T.FR_CONTACT)
    await cb.answer()

@router.message(Franchise.contact)
async def f_contact(msg: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    uid, u = msg.from_user.id, msg.from_user
    hot = d["budget"] != "изучаю" and d["timing"] in ("месяц", "2-3 мес") and d["biz"] != "нет"
    cold = d["timing"] == "интересуюсь" and d["budget"] == "изучаю"
    score = "hot" if hot else ("cold" if cold else "warm")
    db.save_franchise(user_id=uid, username=u.username, name=u.first_name, city=d["city"],
                      biz_exp=d["biz"], budget=d["budget"], timing=d["timing"],
                      familiar=d["familiar"], contact=msg.text.strip()[:100], score=score)
    db.mirror("Franchise", [datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
                            u.first_name or "", f"@{u.username}" if u.username else str(uid),
                            d["city"], d["biz"], d["budget"], d["timing"], d["familiar"],
                            msg.text.strip()[:100], score])
    db.log_event(uid, f"franchise_scored_{score}")
    if score == "hot":
        await msg.answer(T.FR_DONE_HOT)
        card = (f"🔥 ФРАНШИЗА | {u.first_name}, {d['city']}\n"
                f"Опыт: {d['biz']} · Бюджет: {d['budget']} · Срок: {d['timing']} · Ниша: {d['familiar']}\n"
                f"Контакт: {msg.text.strip()[:100]} · Профиль: @{u.username or uid} · "
                f"Источник: {(db.get_user(uid) or {}).get('source')}")
        await notify(msg.bot, config.ARTEM_CHAT_ID, card)
    elif score == "warm":
        await msg.answer(T.FR_DONE_WARM)
        await msg.answer(T.FR_WARM_1)
        db.schedule(uid, 5*86400, "fr_warm_2")
        db.schedule(uid, 10*86400, "fr_warm_3")
        await notify(msg.bot, config.ARTEM_CHAT_ID,
                     f"🌡 ФРАНШИЗА (тёплый) | {u.first_name}, {d['city']} · {d['budget']} · {d['timing']} — в прогреве")
    else:
        await msg.answer(T.FR_DONE_COLD)

# ---------------- Свободный текст, /help, названия команд ----------------
@router.callback_query(F.data == "noop")
async def noop(cb: CallbackQuery):
    await cb.answer("Ок 🙂")

@router.callback_query(F.data == "fwd_help")
async def fwd_help(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    text = data.get("pending_text", "")
    if text:
        u = cb.from_user
        await notify(cb.bot, config.WORK_CHAT_ID,
                     f"💬 Вопрос от @{u.username or u.id} ({u.first_name}):\n{text}")
        db.log_event(u.id, "help_request")
        await cb.message.answer(T.HELP_FORWARDED)
    await state.update_data(pending_text=None)
    await cb.answer()

@router.message(F.text)
async def free_text(msg: Message, state: FSMContext):
    """Вне сценариев: режим help или предложение переслать команде."""
    data = await state.get_data()
    if data.get("help_mode"):
        await state.update_data(help_mode=False)
        u = msg.from_user
        await notify(msg.bot, config.WORK_CHAT_ID,
                     f"💬 Вопрос от @{u.username or u.id} ({u.first_name}):\n{msg.text}")
        db.log_event(u.id, "help_request")
        await msg.answer(T.HELP_FORWARDED)
        return
    # эвристика «прислали название агентства» после чек-листа
    u = db.get_user(msg.from_user.id) or {}
    if "antiscam" in (u.get("magnets") or "") and len(msg.text) < 60:
        await notify(msg.bot, config.WORK_CHAT_ID,
                     f"🔍 Проверка команды от @{msg.from_user.username or msg.from_user.id}: {msg.text}")
        db.log_event(msg.from_user.id, "agency_check", msg.text[:100])
        await msg.answer(T.AGENCY_NAME_ACK)
        return
    await state.update_data(pending_text=msg.text[:500])
    await msg.answer(T.FREE_TEXT_FALLBACK, reply_markup=kb([
        [("Да, отправить", "fwd_help")], [("Меню", "noop_menu")]]))

@router.callback_query(F.data == "noop_menu")
async def noop_menu(cb: CallbackQuery):
    await cb.message.answer(T.MENU_MAIN, reply_markup=MENU_KB)
    await cb.answer()

# ---------------- go_antiscam / go_guide колбэки ----------------
@router.callback_query(F.data == "go_antiscam")
async def cb_antiscam(cb: CallbackQuery):
    await run_antiscam(cb.message); await cb.answer()

@router.callback_query(F.data == "go_guide")
async def cb_guide(cb: CallbackQuery):
    await run_guide(cb.message); await cb.answer()

# ---------------- Админ ----------------
@router.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id not in config.ADMIN_IDS: return
    s7 = db.stats(7)
    lines = "\n".join(f"{k}: {v}" for k, v in list(s7.items())[:25]) or "пусто"
    await msg.answer(f"📊 События за 7 дней:\n{lines}")

# ---------------- Отложенные сообщения ----------------
FOLLOWUP_TEXTS = {
    "antiscam_24h": (T.ANTISCAM_FOLLOWUP_24H,
                     [[("🎬 Узнать про кастинг", "go_casting")], [("Пока просто читаю канал", "noop")]]),
    "guide_48h": (T.GUIDE_FOLLOWUP_48H,
                  [[("🎬 Расскажи про кастинг", "go_casting")],
                   [("⏰ Напомни через неделю", "remind_week")], [("Не моё", "stop_warmup")]]),
    "guide_week": (T.GUIDE_REMIND_WEEK, [[("🎬 Кастинг", "go_casting")]]),
    "casting_video_24h": (T.VIDEO_REMIND_24H, None),
    "fr_m3_reminder": (T.M3_REMINDER, [[("📝 Заполнить анкету", "go_franchise")]]),
    "fr_warm_2": (T.FR_WARM_2, None),
    "fr_warm_3": (T.FR_WARM_3, [[("📝 Анкета партнёрства", "go_franchise")]]),
}

@router.callback_query(F.data == "remind_week")
async def remind_week(cb: CallbackQuery):
    db.schedule(cb.from_user.id, 7*86400, "guide_week")
    await cb.message.answer("Договорились, напомню через неделю ⏰")
    await cb.answer()

@router.callback_query(F.data == "stop_warmup")
async def stop_warmup(cb: CallbackQuery):
    db.set_user(cb.from_user.id, no_warmup=1)
    db.cancel_followups(cb.from_user.id)
    await cb.message.answer("Понял, больше не беспокою. Канал всегда открыт 🤝")
    await cb.answer()

async def followup_loop(bot: Bot):
    while True:
        try:
            for f in db.due_followups():
                db.mark_sent(f["id"])
                u = db.get_user(f["user_id"]) or {}
                if u.get("no_warmup") or u.get("underage"):
                    continue
                item = FOLLOWUP_TEXTS.get(f["kind"])
                if not item: continue
                text, buttons = item
                try:
                    await bot.send_message(f["user_id"], text,
                                           reply_markup=kb(buttons) if buttons else None)
                    db.log_event(f["user_id"], f"warmup_sent_{f['kind']}")
                except Exception:
                    db.set_user(f["user_id"], no_warmup=1)  # заблокировал бота
        except Exception as e:
            logging.warning(f"followup loop: {e}")
        await asyncio.sleep(config.FOLLOWUP_CHECK_SECONDS)

# ---------------- main ----------------
async def main():
    db.init_db()
    bot = Bot(config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(followup_loop(bot))
    logging.info("TeamUniverse bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
