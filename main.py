"""CYBERTON Telegram Bot - long-polling bot with a health-check endpoint."""

from __future__ import annotations

import html
import logging
import os
import platform
import sys
import time
from datetime import datetime, timezone

import telegram
from aiohttp import web
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import Conflict, InvalidToken, NetworkError
from telegram.ext import Application, CommandHandler, ContextTypes

try:  # Local development only; on Railway real env vars are used.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BOT_NAME = "CYBERTON"
BOT_VERSION = "1.0.0"

# Monotonic clock for accurate uptime, wall clock for display.
STARTED_MONOTONIC = time.monotonic()
STARTED_AT_UTC = datetime.now(timezone.utc)

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    stream=sys.stdout,
)
# httpx logs full request URLs at INFO level, which include the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("cyberton")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def uptime_seconds() -> int:
    return int(time.monotonic() - STARTED_MONOTONIC)


def format_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m {secs}s"


def polling_is_running(application: Application) -> bool:
    updater = application.updater
    return bool(application.running and updater is not None and updater.running)


# --------------------------------------------------------------------------- #
# Command handlers
# --------------------------------------------------------------------------- #
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = html.escape(user.first_name) if user else "there"
    await update.effective_message.reply_text(
        f"👋 Hello, <b>{name}</b>!\n\n"
        f"Welcome to <b>{BOT_NAME}</b>.\n"
        "Send /help to see what I can do.",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "<b>Available commands</b>\n\n"
        "/start - Welcome message\n"
        "/help - Show this list\n"
        "/ping - Check responsiveness and uptime\n"
        "/status - Bot process status and uptime\n"
        "/about - Information about this bot",
        parse_mode=ParseMode.HTML,
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    started = time.perf_counter()
    message = await update.effective_message.reply_text("🏓 Pinging...")
    latency_ms = (time.perf_counter() - started) * 1000
    await message.edit_text(
        f"🏓 <b>Pong!</b>\n"
        f"Round-trip: <code>{latency_ms:.0f} ms</code>\n"
        f"Uptime: <code>{format_uptime(uptime_seconds())}</code>",
        parse_mode=ParseMode.HTML,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    polling = "running ✅" if polling_is_running(context.application) else "not running ❌"
    lines = [
        f"<b>{BOT_NAME} status</b>",
        f"Polling: <code>{polling}</code>",
        f"Uptime: <code>{format_uptime(uptime_seconds())}</code>",
        f"Started (UTC): <code>{STARTED_AT_UTC:%Y-%m-%d %H:%M:%S}</code>",
        f"Process ID: <code>{os.getpid()}</code>",
        f"Python: <code>{platform.python_version()}</code>",
        f"python-telegram-bot: <code>{telegram.__version__}</code>",
    ]
    # Set automatically by Railway; shown only when present.
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME")
    commit = os.getenv("RAILWAY_GIT_COMMIT_SHA")
    if environment:
        lines.append(f"Environment: <code>{html.escape(environment)}</code>")
    if commit:
        lines.append(f"Commit: <code>{html.escape(commit[:7])}</code>")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"<b>{BOT_NAME}</b> v{BOT_VERSION}\n\n"
        "A lightweight Telegram bot running 24/7 on Railway.\n\n"
        f"• Language: Python {platform.python_version()}\n"
        f"• Library: python-telegram-bot {telegram.__version__}\n"
        "• Mode: long polling (single instance)",
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error

    if isinstance(error, Conflict):
        logger.error(
            "Telegram Conflict: another instance is polling with this token. "
            "Stop any local copy or old deployment. (Brief conflicts during a "
            "Railway redeploy are normal.)"
        )
        return
    if isinstance(error, NetworkError):  # includes TimedOut
        logger.warning("Transient network error (will retry automatically): %s", error)
        return

    logger.error("Unhandled exception while processing an update", exc_info=error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong. Please try again later."
            )
        except Exception:  # noqa: BLE001 - never let the error handler crash
            logger.exception("Failed to send error notice to user")


# --------------------------------------------------------------------------- #
# Health-check HTTP server (shares the bot's asyncio event loop)
# --------------------------------------------------------------------------- #
async def health_handler(request: web.Request) -> web.Response:
    application: Application = request.app["telegram_app"]
    healthy = polling_is_running(application)
    return web.json_response(
        {
            "status": "ok" if healthy else "unavailable",
            "polling": healthy,
            "uptime_seconds": uptime_seconds(),
        },
        status=200 if healthy else 503,
    )


async def post_init(application: Application) -> None:
    """Runs once after the application is initialised (token already validated)."""
    web_app = web.Application()
    web_app["telegram_app"] = application
    web_app.router.add_get("/", health_handler)
    web_app.router.add_get("/health", health_handler)

    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, host="0.0.0.0", port=port).start()
    application.bot_data["health_runner"] = runner
    logger.info("Health server listening on 0.0.0.0:%s (GET /health)", port)

    await application.bot.set_my_commands(
        [
            BotCommand("start", "Welcome message"),
            BotCommand("help", "Show available commands"),
            BotCommand("ping", "Check responsiveness and uptime"),
            BotCommand("status", "Bot process status"),
            BotCommand("about", "About this bot"),
        ]
    )
    me = application.bot.bot
    logger.info("Authenticated as @%s (id=%s)", me.username, me.id)


async def post_shutdown(application: Application) -> None:
    runner: web.AppRunner | None = application.bot_data.get("health_runner")
    if runner is not None:
        await runner.cleanup()
        logger.info("Health server stopped")
    logger.info("%s shut down cleanly after %s", BOT_NAME, format_uptime(uptime_seconds()))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.critical("TELEGRAM_BOT_TOKEN is not set. Add it to Railway Variables.")
        sys.exit(1)

    logger.info("Starting %s v%s (Python %s)", BOT_NAME, BOT_VERSION, platform.python_version())

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .connect_timeout(10)
        .read_timeout(15)
        .write_timeout(15)
        .pool_timeout(10)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_error_handler(error_handler)

    try:
        # run_polling handles SIGINT/SIGTERM and shuts down gracefully.
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            bootstrap_retries=-1,  # keep retrying if Telegram is unreachable at startup
        )
    except InvalidToken:
        logger.critical("Telegram rejected the token. Check TELEGRAM_BOT_TOKEN.")
        sys.exit(1)
    except Exception:  # noqa: BLE001
        logger.critical("Fatal error, exiting so Railway can restart the service", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
