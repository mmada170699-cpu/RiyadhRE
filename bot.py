import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def whereami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(
        f"Chat ID: {chat.id}\nType: {chat.type}\nTitle: {chat.title or ''}"
    )

def app():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("whereami", whereami))
    return application

if __name__ == "__main__":
    application = app()
    application.run_polling(drop_pending_updates=True)
