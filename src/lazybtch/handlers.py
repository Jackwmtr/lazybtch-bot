"""Telegram handlers: commands, audio pipeline, group policy."""
from __future__ import annotations

import hashlib
import re
from typing import Callable

from loguru import logger

from .crypto import KeyVault, mask_key
from .groq import (
    FileTooLarge,
    GroqAuthError,
    GroqBadRequest,
    GroqClient,
    GroqError,
    GroqRateLimitError,
    GroqTimeoutError,
    download_telegram_file,
)
from .store import Store

LANG_RE = re.compile(r"^[a-z]{2}([_-][a-z]{2,4})?$", re.IGNORECASE)
KEY_RE = re.compile(r"^gsk_\w{10,}$")
MAX_REPLY_LEN = 4096

START_TEXT = """\
🎙 LazyBTch. Перешли мне голосовое — верну текст.

Для начала привяжи свой ключ Groq (бесплатно, console.groq.com):
/key gsk_...

Я ключ не цитирую и храню шифрованным."""

HELP_TEXT = """\
Перешли голосовое — верну текст.

Команды:
/start — статус
/key gsk_... — привязать ключ Groq
/key off — удалить ключ
/lang <code|auto> — язык расшифровки
/stats — статистика
/help — эта справка"""

MSG_NO_KEY = (
    "У меня нет твоего ключа Groq. Сдай /key gsk_... и перешли запись "
    "ещё раз — повторная расшифровка уже бесплатно (кэш)."
)


def _chunk(text: str, limit: int = MAX_REPLY_LEN) -> list[str]:
    """Split long transcripts into Telegram-sized messages."""
    text = text.strip()
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


class Handlers:
    def __init__(
        self,
        bot,
        settings,
        store: Store | None = None,
        vault: KeyVault | None = None,
        groq: GroqClient | None = None,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.store = store
        self.vault = vault
        self.groq = groq

    # ---------- commands ----------

    async def start(self, message) -> None:
        logger.info("start from user_id={}", message.from_user.id)
        if self.store is None:
            await message.answer(START_TEXT)
            return
        row = self.store.get_user(message.from_user.id)
        status = []
        if row and row["groq_key"]:
            status.append(f"Ключ: {mask_key(self.vault.decrypt(row['groq_key']))}")
        else:
            status.append("Ключ: не задан — /key gsk_...")
        status.append(f"Язык: {row['lang'] if row else 'auto'}")
        await message.answer(START_TEXT + "\n\n" + "\n".join(status))

    async def help(self, message) -> None:
        await message.answer(HELP_TEXT)

    async def key(self, message) -> None:
        if self.store is None or self.vault is None or self.groq is None:
            await message.answer("Не готово 🙂")
            return
        user_id = message.from_user.id
        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /key gsk_...   (или /key off)")
            return
        arg = parts[1]

        if arg.lower() == "off":
            self.store.set_groq_key(user_id, None)
            self.store.add_event(user_id, "key_removed", "")
            await message.answer("Ключ удалён.")
            return

        if not KEY_RE.match(arg):
            await message.answer("Похоже, это не ключ Groq (ожидается gsk_...).")
            return

        # Validate with a lightweight request before storing.
        if not await self.groq.validate_key(arg):
            self.store.add_event(user_id, "groq_401", "key validation failed")
            await message.answer(
                "Ключ не сработал. Проверь, что скопирован целиком: /key <новый>"
            )
            return

        self.store.set_groq_key(user_id, self.vault.encrypt(arg))
        self.store.add_event(user_id, "key_saved", mask_key(arg))
        await message.answer(f"Ключ сохранён ✅ ({mask_key(arg)})")

    async def lang(self, message) -> None:
        if self.store is None:
            await message.answer("Не готово 🙂")
            return
        parts = (message.text or "").split()
        if len(parts) < 2:
            row = self.store.get_user(message.from_user.id)
            cur = row["lang"] if row else "auto"
            await message.answer(f"Сейчас: {cur}. Установить: /lang ru, /lang en, /lang auto")
            return
        code = parts[1].lower()
        if code != "auto" and not LANG_RE.match(code):
            await message.answer("Не понял код языка. Примеры: /lang ru, /lang auto")
            return
        self.store.set_lang(message.from_user.id, code)
        self.store.add_event(message.from_user.id, "lang_set", code)
        await message.answer(f"Язык: {code}")

    async def stats(self, message) -> None:
        if self.store is None:
            await message.answer("Не готово 🙂")
            return
        s = self.store.user_stats(message.from_user.id)
        c = s["counts"]
        lines = [
            "📊 Статистика:",
            f"транскрипций: {c.get('ok', 0)} (+кэш-hit: {c.get('cache_hit', 0)})",
            f"длительность: {s['total_duration_s'] // 60} мин {s['total_duration_s'] % 60} с",
        ]
        if s["recent_errors"]:
            lines.append("последние ошибки:")
            lines.extend(f"  {e['kind']}" for e in s["recent_errors"])
        await message.answer("\n".join(lines))

    # ---------- group policy ----------

    def _group_allowed(self, message) -> bool:
        """Policy off|mentions|all for non-private chats."""
        if message.chat.type == "private":
            return True
        policy = self.settings.group_policy
        if policy == "off":
            return False
        if policy == "all":
            return True
        # mentions: bot addressed by @username in text/caption
        me_username = getattr(self.bot, "username", None)
        if not me_username:
            # Fallback: any mention entity
            return any(
                e.type == "mention" for e in (message.entities or [])
            )
        target = f"@{me_username}"
        return target in (message.text or "") or target in (message.caption or "")

    # ---------- audio pipeline ----------

    @staticmethod
    def _extract_audio(message) -> tuple[str, int | None, int | None, str | None, str]:
        """Return (file_id, duration_s, file_size, ext, mime)."""
        if message.voice is not None:
            return (
                message.voice.file_id,
                message.voice.duration,
                message.voice.file_size,
                "oga",
                message.voice.mime_type or "audio/ogg",
            )
        if message.video_note is not None:  # hook for v2 (same audio pipeline)
            # aiogram 2 VideoNote has no mime_type field; it is always mp4
            return (
                message.video_note.file_id,
                getattr(message.video_note, "duration", None),
                message.video_note.file_size,
                "mp4",
                "video/mp4",
            )
        a = message.audio
        return (
            a.file_id,
            a.duration,
            a.file_size,
            a.file_name.rsplit(".", 1)[-1] if a.file_name and "." in a.file_name else None,
            a.mime_type or "audio/mpeg",
        )

    async def audio(self, message) -> None:
        if not self._group_allowed(message):
            return
        user_id = message.from_user.id
        text = await self.process_audio(message, user_id)
        parts = _chunk(text)
        # aiogram 2: reply via the `reply` flag (aiogram 3 uses reply_to_message_id)
        for i, part in enumerate(parts):
            await message.answer(part, reply=(i == 0))

    async def process_audio(self, message, user_id: int) -> str:
        """Full pipeline: limits -> download -> cache -> Groq. Returns reply text."""
        file_id, duration, file_size, ext, mime = self._extract_audio(message)

        # 1) Hard Bot API limit (getFile <= 20 MB) — reject before download.
        limit_bytes = min(
            self.settings.bot_api_max_file_mb, self.settings.groq_max_file_mb
        ) * 1024 * 1024
        if file_size and file_size > limit_bytes:
            mb = file_size / 1024 / 1024
            self.store.add_event(user_id, "too_large", f"{mb:.0f}MB")
            return (
                f"Это {mb:.0f} МБ — выше лимита ({self.settings.bot_api_max_file_mb} МБ). "
                "Попробуй разрезать или платный тариф."
            )

        # 2) Download (in memory only — audio never on disk).
        try:
            data = await download_telegram_file(self.bot, file_id)
        except Exception as e:  # noqa: BLE001 — TelegramAPIError, httpx
            logger.warning("download failed for user={}: {}", user_id, type(e).__name__)
            self.store.add_event(user_id, "download_error", type(e).__name__)
            return "Не удалось скачать файл, попробуй ещё раз."

        # 3) Cache by sha256(audio) — shared across users, so a repeated
        #    forward never pays Groq again (even for a keyless user).
        digest = hashlib.sha256(data).hexdigest()
        cached = self.store.cache_get(digest)
        if cached:
            self.store.add_event(user_id, "cache_hit", f"d={duration or 0}")
            return f"🎙 [{duration or 0} сек, {cached['lang']}]\n{cached['text']}"

        # 4) User key (only needed for a real Groq call).
        row = self.store.get_user(user_id)
        if not row or not row["groq_key"]:
            self.store.add_event(user_id, "no_key", "")
            return MSG_NO_KEY
        key = self.vault.decrypt(row["groq_key"])
        lang = row.get("lang") or "auto"

        # 5) Groq.
        try:
            result = await self.groq.transcribe(key, data, ext=ext, lang=lang, mime=mime)
        except FileTooLarge as e:
            self.store.add_event(user_id, "too_large", f"{e.size / 1024 / 1024:.0f}MB")
            return (
                f"Это {e.size / 1024 / 1024:.0f} МБ — выше лимита "
                f"Groq free tier ({self.settings.groq_max_file_mb} МБ). "
                "Попробуй разрезать или платный тариф."
            )
        except GroqAuthError:
            self.store.add_event(user_id, "groq_401", "")
            return "Ключ не сработал (401). Проверь: /key <новый>"
        except GroqRateLimitError:
            self.store.add_event(user_id, "groq_429", "")
            return "Groq жжёт лимит, подожди ~минуту и перешли ещё раз"
        except GroqTimeoutError:
            self.store.add_event(user_id, "groq_timeout", "")
            return "Groq не ответил за 60 с, попробуй ещё раз"
        except GroqBadRequest as e:
            self.store.add_event(user_id, "groq_400", e.detail)
            return f"Groq отклонил файл ({e.detail}). Попробуй ещё раз или другой формат"
        except GroqError as e:
            self.store.add_event(user_id, "groq_error", str(e))
            return "Ошибка запроса к Groq, попробуй ещё раз"

        # 6) Empty transcript — not an error.
        if not result.text:
            self.store.add_event(user_id, "empty", f"d={duration or 0}")
            return "Расшифровка пустая — похоже, в записи нет речи"

        self.store.cache_set(
            digest,
            user_id,
            lang,
            duration or 0,
            result.text,
        )
        self.store.add_event(user_id, "ok", f"d={duration or 0}")
        logger.info("transcribed user={} d={}s lang={} ({} chars)", user_id, duration, result.language, len(result.text))
        return f"🎙 [{duration or 0} сек, {result.language or lang}]\n{result.text}"

    async def not_audio(self, message) -> None:
        if not self._group_allowed(message):
            return
        await message.answer("Я понимаю только голосовые и кружки 🙂", reply=True)
