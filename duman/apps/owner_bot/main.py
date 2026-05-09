"""Owner bot entry point. Long-poll, no webhook tunnel needed."""

from __future__ import annotations

import logging

from telegram.ext import Application

from apps.owner_bot.handlers import register_handlers
from happycake.settings import settings
from happycake.storage import init_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s — %(message)s",
)


def build_app() -> Application:
    init_db()
    token = settings.telegram_owner_bot_token.get_secret_value()
    if not token or token == "missing" or token.startswith("replace-with"):
        raise SystemExit(
            "TELEGRAM_OWNER_BOT_TOKEN is not set in .env. "
            "Create a bot via @BotFather and paste the token."
        )
    app = Application.builder().token(token).build()
    register_handlers(app)
    return app


def main() -> None:
    app = build_app()
    print("HappyCake owner bot starting on long-poll. Send /start in Telegram.")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
