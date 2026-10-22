"""Отправка картинок и видео: каждый файл грузится в Telegram один раз, дальше — по file_id из базы."""
import asyncio
import json
import logging
import os

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

import db

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "media")
_cache = {}          # key → file_id
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


def all_keys():
    out = []
    for sub, kind, ext in (("cards", "card", ".jpg"), ("video", "video", ".mp4")):
        d = os.path.join(MEDIA, sub)
        if os.path.isdir(d):
            out += [f"{kind}:{f[:-len(ext)]}" for f in sorted(os.listdir(d)) if f.endswith(ext)]
    return out


def _remember(key, file_id):
    _cache[key] = file_id
    asyncio.get_running_loop().run_in_executor(None, db.media_set, key, file_id)


async def send(bot: Bot, chat_id, key, caption=None, reply_markup=None):
    """Отправить медиа по ключу. Возвращает Message."""
    kind = key.split(":", 1)[0]
    fid = _cache.get(key)
    for attempt in (0, 1):
        src = fid if (fid and attempt == 0) else FSInputFile(path_of(key))
        try:
            if kind == "card":
                m = await bot.send_photo(chat_id, src, caption=caption or None, reply_markup=reply_markup)
                new_id = m.photo[-1].file_id if m.photo else None
            elif kind == "video":
                meta = _manifest.get(key, {})
                m = await bot.send_video(chat_id, src, caption=caption or None, reply_markup=reply_markup,
                                         width=meta.get("w"), height=meta.get("h"), duration=meta.get("dur"),
                                         supports_streaming=True)
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
    fid = _cache.get(key)
    return InputMediaPhoto(media=fid or FSInputFile(path_of(key)))


def remember_from(key, message):
    if message and getattr(message, "photo", None):
        fid = message.photo[-1].file_id
        if fid != _cache.get(key):
            _remember(key, fid)


async def warmup(bot: Bot, chat_id):
    """Один раз загрузить все медиа в служебный чат и запомнить file_id."""
    n = 0
    for key in all_keys():
        if key in _cache:
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
