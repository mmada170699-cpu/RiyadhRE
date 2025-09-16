import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ====== ENV VARIABLES ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")  # e.g. -4942161299

# ====== COMMANDS ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("مرحباً 👋 أرسل لي تفاصيل عقارك وسأنشره في المجموعة ✅")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text=f"📌 إعلان جديد:\n\n{text}"
    )
    await update.message.reply_text("تم نشر إعلانك ✅")

# ====== APP ======
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling()

if __name__ == "__main__":
    main()
