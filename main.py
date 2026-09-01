import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from src.access import AccessMiddleware
from src.bot import router
from src.config import ConfigError, config


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config.bot_token:
        raise ConfigError("Не найден BOT_TOKEN. Укажите его в .env.")

    session = AiohttpSession(proxy=config.proxy_url) if config.proxy_url else None
    bot = Bot(token=config.bot_token, session=session) if session else Bot(token=config.bot_token)
    dispatcher = Dispatcher()
    router.message.middleware(AccessMiddleware())
    router.callback_query.middleware(AccessMiddleware())
    dispatcher.include_router(router)

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
