"""aiogram app wiring: Bot + Dispatcher + handler registration."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher, types
from loguru import logger

from .crypto import KeyVault
from .groq import GroqClient
from .handlers import Handlers
from .store import Store


async def _heartbeat(store: Store, interval_s: int) -> None:
    """Hourly heartbeat: log line + poll_alive event (v1 monitoring)."""
    while True:
        await asyncio.sleep(interval_s)
        logger.info("heartbeat: bot alive")
        try:
            store.add_event(None, "poll_alive", "")
        except Exception:  # noqa: BLE001 — heartbeat must never kill the bot
            logger.exception("heartbeat event write failed")


def create_bot(settings) -> tuple[Bot, Dispatcher, Handlers, Store]:
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(bot)

    store = Store(settings.db_path)
    vault = KeyVault(settings.encryption_key)
    groq = GroqClient(
        base_url=settings.groq_base_url,
        model=settings.groq_model,
        timeout_s=settings.http_timeout_s,
        max_bytes=settings.groq_max_file_mb * 1024 * 1024,
    )

    handlers = Handlers(bot=bot, settings=settings, store=store, vault=vault, groq=groq)
    dp.register_message_handler(handlers.start, commands=["start"])
    dp.register_message_handler(handlers.help, commands=["help"])
    dp.register_message_handler(handlers.key, commands=["key"])
    dp.register_message_handler(handlers.lang, commands=["lang"])
    dp.register_message_handler(handlers.stats, commands=["stats"])
    dp.register_message_handler(
        handlers.audio,
        content_types={
            types.ContentType.VOICE,
            types.ContentType.AUDIO,
            types.ContentType.VIDEO_NOTE,
        },
    )
    dp.register_message_handler(
        handlers.not_audio,
        content_types={
            types.ContentType.PHOTO,
            types.ContentType.DOCUMENT,
            types.ContentType.VIDEO,
            types.ContentType.STICKER,
        },
    )

    return bot, dp, handlers, store


async def on_startup(bot: Bot, store: Store, settings) -> None:
    me = await bot.get_me()
    logger.info("bot @{} started, group_policy={}", me.username, settings.group_policy)
    asyncio.get_running_loop().create_task(_heartbeat(store, settings.heartbeat_interval_s))
