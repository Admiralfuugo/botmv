import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

import db
import web as webapp
from config import BOT_TOKEN, WEB_PORT
from handlers import user
from services import auth, channels
from services.results import updater_loop


async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init()
    await auth.ensure_first_admin()
    await channels.seed_from_env()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(user.router)

    runner = web.AppRunner(webapp.make_app(bot))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEB_PORT).start()
    logging.info("Admin panel: http://localhost:%s", WEB_PORT)

    updater = asyncio.create_task(updater_loop(bot))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member", "inline_query", "chosen_inline_result"])
    finally:
        updater.cancel()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
