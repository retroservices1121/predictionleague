import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from app.database import SessionLocal
from app import crud

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = SessionLocal()
    crud.create_user(db, str(update.effective_user.id), update.effective_user.username)
    await update.message.reply_text("Welcome to Predictions League! Type /predict to see open predictions.")
    db.close()

def run_bot():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
