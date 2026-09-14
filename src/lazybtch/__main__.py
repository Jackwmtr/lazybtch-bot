"""Entry point: uv run lazybtch."""
from __future__ import annotations

import sys

from aiogram import executor
from loguru import logger

from .bot import create_bot
from .config import get_settings


def _setup_logging(settings) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    # Policy: audio bytes, keys and transcript text must never be logged.


def main() -> None:
    settings = get_settings()
    _setup_logging(settings)
    bot, dp, _, store = create_bot(settings)

    async def _ttl_cleanup() -> None:
        removed = store.cache_purge_older_than(settings.cache_ttl_days)
        if removed:
            logger.info("cache TTL cleanup: removed {} rows", removed)

    from .bot import on_startup

    async def startup(dp) -> None:
        # aiogram 2 executor passes the dispatcher into on_startup
        await _ttl_cleanup()
        await on_startup(bot, store, settings)

    logger.info("starting long polling (no inbound ports)")
    executor.start_polling(dp, skip_updates=True, on_startup=startup)


if __name__ == "__main__":
    main()
