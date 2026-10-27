"""Отправка картинок и видео: каждый файл грузится в Telegram один раз, дальше — по file_id из базы."""
import asyncio
import hashlib
import json
import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import db

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "media")
_cache = {}          # key@отпечаток файла → file_id (заменили файл — отпечаток другой, грузим заново)
_fp = {}             # key → отпечаток содержимого
_manifest = {}       # key → {w, h, dur} для видео


def load():
    global _manifest
    try:
        _cache.update(db.media_all())
    except Exception as e:
        logging.warning(f"media cache: {e}")
    p = os.path.join(MEDIA, "manifest.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            _manifest = json.load(f)


def path_of(key):
    kind, name = key.split(":", 1)
    if kind == "card":
        return os.path.join(MEDIA, "cards", name + ".jpg")
    if kind == "video":
        return os.path.join(MEDIA, "video", name + ".mp4")
    if kind == "doc":
        return os.path.join(MEDIA, name)
    raise ValueError(key)


def resolve(key):
    """card:имя → video:имя, если на этом экране картинку заменили видео (23.10: баттлы ведущих вместо стока)."""
    kind, name = key.split(":", 1)
    if kind == "card" and not os.path.exists(path_of(key)) and os.path.exists(path_of("video:" + name)):
        return "video:" + name
    return key


def all_keys():
    out = []
    for sub, kind, ext in (("cards", "card", ".jpg"), ("video", "video", ".mp4")):
        d = os.path.join(MEDIA, sub)
        if os.path.isdir(d):
            out += [f"{kind}:{f[:-len(ext)]}" for f in sorted(os.listdir(d)) if f.endswith(ext)]
    return out


def _ck(key):
    """Ключ кэша: имя + отпечаток содержимого файла."""
    if key not in _fp:
        try:
            with open(path_of(key), "rb") as f:
                _fp[key] = hashlib.sha1(f.read()).hexdigest()[:10]
        except OSError:
            _fp[key] = ""
    return f"{key}@{_fp[key]}"


def _get(key):
    return _cache.get(_ck(key))


def _remember(key, file_id):
    ck = _ck(key)
    _cache[ck] = file_id
    asyncio.get_running_loop().run_in_executor(None, db.media_set, ck, file_id)


async def send(bot: Bot, chat_id, key, caption=None, reply_markup=None):
    """Отправить медиа по ключу. Возвращает Message.
    Подпись — над картинкой или видео (show_caption_above_media, правило 26.10): человек сначала видит текст."""
    above = True if caption else None
    key = resolve(key)
    kind = key.split(":", 1)[0]
    fid = _get(key)
    for attempt in (0, 1):
        src = fid if (fid and attempt == 0) else FSInputFile(path_of(key))
        if not isinstance(src, str):
            # 27.10: файл грузится в Telegram впервые — это долго; пусть человек видит «отправляет фото/видео…»
            act = {"card": "upload_photo", "video": "upload_video"}.get(kind, "upload_document")
            try:
                await bot.send_chat_action(chat_id, act)
            except Exception:
                pass
        try:
            if kind == "card":
                m = await bot.send_photo(chat_id, src, caption=caption or None, reply_markup=reply_markup,
                                         show_caption_above_media=above)
                new_id = m.photo[-1].file_id if m.photo else None
            elif kind == "video":
                meta = _manifest.get(key, {})
                m = await bot.send_video(chat_id, src, caption=caption or None, reply_markup=reply_markup,
                                         width=meta.get("w"), height=meta.get("h"), duration=meta.get("dur"),
                                         supports_streaming=True, show_caption_above_media=above)
                new_id = m.video.file_id if m.video else None
            else:
                m = await bot.send_document(chat_id, src, caption=caption or None, reply_markup=reply_markup)
                new_id = m.document.file_id if m.document else None
            if new_id and new_id != fid:
                _remember(key, new_id)
            return m
        except TelegramBadRequest as e:
            # file_id устарел или не подходит — грузим файл заново
            if fid and attempt == 0 and ("file" in str(e).lower() or "wrong" in str(e).lower()):
                logging.warning(f"media {key}: file_id не подошёл, загружаю заново ({e})")
                fid = None
                continue
            raise


def input_media(key):
    """Для правки сообщения (калькулятор): InputMediaPhoto по file_id или файлу."""
    from aiogram.types import InputMediaPhoto
    fid = _get(key)
    return InputMediaPhoto(media=fid or FSInputFile(path_of(key)), show_caption_above_media=True)


def remember_from(key, message):
    if message and getattr(message, "photo", None):
        fid = message.photo[-1].file_id
        if fid != _get(key):
            _remember(key, fid)


async def warmup(bot: Bot, chat_id):
    """Один раз загрузить все медиа в служебный чат и запомнить file_id."""
    n = 0
    for key in all_keys():
        if _get(key):
            continue
        try:
            m = await send(bot, chat_id, key)
            n += 1
            try:
                await bot.delete_message(chat_id, m.message_id)
            except Exception:
                pass
        except Exception as e:
            logging.warning(f"warmup {key}: {e}")
    logging.info(f"warmup: загружено {n} файлов")
