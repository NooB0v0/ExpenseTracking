from telegram.ext import Application, CommandHandler, CallbackQueryHandler
from config import TELEGRAM_BOT_TOKEN

from handlers import (
    start_command, linkgroup_command, join_command, 
    add_command, split_command, balances_command, 
    settle_command, link_button_callback
)

async def post_init(application: Application):
    await application.bot.set_my_commands([
        ("split", "Record a shared expense"),
        ("balances", "See who owes who"),
        ("settle", "Log a 1-to-1 payment"),
        ("linkgroup", "Connect chat to a Supabase group")
    ])

if __name__ == '__main__':
    # Build the app using the token from config.py
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Register all the handlers imported from handlers.py
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("linkgroup", linkgroup_command))
    app.add_handler(CommandHandler("join", join_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("split", split_command))
    app.add_handler(CommandHandler("balances", balances_command))
    app.add_handler(CommandHandler("settle", settle_command))
    app.add_handler(CallbackQueryHandler(link_button_callback, pattern="^link_"))

    print("🚀 Telegram Bot is running...")
    app.run_polling()