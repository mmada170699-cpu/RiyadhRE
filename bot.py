import os
import re
import asyncio
from math import radians, sin, cos, asin, sqrt
from datetime import datetime, timedelta

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# ===== ENV =====
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID   = int(os.getenv("GROUP_ID", "0"))          # مثال: -4942161299
ADMIN_ID   = int(os.getenv("ADMIN_USER_ID", "0"))     # رقمك أنت كمالك البوت

# ===== CONSTANTS =====
RIYADH_CENTER = (24.7136, 46.6753)
RIYADH_RADIUS_KM = 70.0

NON_RIYADH_CITIES = {
    "جدة","مكة","المكه","المدينة","الخبر","الدمام","الظهران","ينبع","تبوك","حائل","جازان",
    "أبها","نجران","الطائف","عرعر","القصيم","بريدة","الهفوف","القطيف",
    "jeddah","mecca","makkah","madinah","medina","khobar","dammam","dhahran",
    "yanbu","tabuk","hail","jazan","abha","najran","taif","arar","qassim","buraidah","hofuf","qatif"
}

OFFTOPIC_KEYWORDS = {
    "loan","loans","تمويل","تقسيط","قرض","قروض","تأمين","insurance",
    "نقل عفش","نقل أثاث","moving","cleaning","تنظيف","كهربائي","سباك",
    "دهان","صيانة","تصميم مواقع","web design","دورات","courses"
}

# FAL / deed (“رقم الصك محدث”)
RE_FAL    = re.compile(r"(?:FAL|فال|رخصة|ترخيص)\s*[:\-]?\s*(\d{7,12})", re.I)
RE_DEED   = re.compile(r"(?:رقم\s*الصك\s*محدث)\s*[:\-]?\s*(\d{6,15})", re.I)
RE_DIGITS = re.compile(r"\b\d{9,12}\b")

POLICY_TEXT = (
    "📌 **سياسة المجموعة**\n"
    "• لصاحب الصفحة حق أتعاب ثابت قدره **1%** من قيمة الصفقة.\n"
    "• الإعلانات داخل **مدينة الرياض** فقط.\n"
    "• يجب كتابة **رقم الرخصة العقارية (FAL)** أو **رقم الصك محدث** في كل إعلان.\n"
    "• يُمنع الإعلان عن الخدمات غير العقارية (تمويل/نقل/تنظيف…)\n"
    "• المخالفات تُحذف، ويتدرج الحظر: 24 ساعة → 3 أيام → 7 أيام → +7 أيام لكل تكرار."
)

# عدّاد مخالفات في الذاكرة (يكفي الآن)
OFFENSES: dict[int, int] = {}

# ===== Helpers =====
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = radians(lat2-lat1)
    dlon = radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2 * R * asin(sqrt(a))

def text_has_license(text: str) -> bool:
    if not text:
        return False
    if RE_FAL.search(text) or RE_DEED.search(text):
        return True
    # بعضهم يلصق الرقم فقط
    return bool(RE_DIGITS.search(text))

def mentions_other_city(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(city in t for city in NON_RIYADH_CITIES)

def looks_offtopic(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in OFFTOPIC_KEYWORDS)

def ban_seconds_for(count: int) -> int:
    if count == 1: return 24*3600
    if count == 2: return 3*24*3600
    if count == 3: return 7*24*3600
    return (7 + 7*(count-3)) * 24*3600

def is_owner(user_id: int) -> bool:
    return ADMIN_ID and user_id == ADMIN_ID

def in_target_group(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == GROUP_ID

async def safe_delete(update: Update):
    try:
        await update.effective_message.delete()
    except Exception:
        pass

async def temp_ban(context: ContextTypes.DEFAULT_TYPE, user_id: int, seconds: int):
    until = datetime.utcnow() + timedelta(seconds=seconds)
    try:
        await context.bot.ban_chat_member(GROUP_ID, user_id, until_date=until)
    except Exception:
        pass

# ===== Moderation =====
async def moderate_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يمر على كل رسالة في القروب ويطبق القوانين."""
    if not in_target_group(update):
        return
    msg = update.effective_message
    if not msg or msg.from_user is None or msg.from_user.is_bot:
        return

    user_id = msg.from_user.id
    text = msg.text or msg.caption or ""

    # 1) خدمات غير عقارية → حذف + باند متدرّج
    if looks_offtopic(text):
        OFFENSES[user_id] = OFFENSES.get(user_id, 0) + 1
        await safe_delete(update)
        seconds = ban_seconds_for(OFFENSES[user_id])
        await temp_ban(context, user_id, seconds)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"تم حذف إعلانك ومنعك مؤقتاً لمدة {seconds//3600} ساعة بسبب إعلان غير عقاري."
            )
        except Exception:
            pass
        return

    # 2) خارج الرياض بالكلمات
    if mentions_other_city(text):
        await safe_delete(update)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="هذه المجموعة مخصصة لإعلانات **الرياض** فقط. تم حذف رسالتك."
            )
        except Exception:
            pass
        return

    # 3) خارج الرياض بالموقع
    if msg.location:
        d = haversine_km(RIYADH_CENTER[0], RIYADH_CENTER[1], msg.location.latitude, msg.location.longitude)
        if d > RIYADH_RADIUS_KM:
            OFFENSES[user_id] = OFFENSES.get(user_id, 0) + 1
            await safe_delete(update)
            seconds = ban_seconds_for(OFFENSES[user_id])
            await temp_ban(context, user_id, seconds)
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"الموقع خارج الرياض (≈{int(d)} كم). تم حذف الرسالة ومنعك مؤقتاً لمدة {seconds//3600} ساعة."
                )
            except Exception:
                pass
            return

    # 4) إلزام الرخصة / الصك
    if not text_has_license(text):
        await safe_delete(update)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="تم حذف إعلانك لعدم وجود **رقم الرخصة العقارية (FAL)** أو **رقم الصك محدث**. "
                     "يرجى إعادة الإرسال مع الرقم."
            )
        except Exception:
            pass
        return
    # مسموح → لا شيء

# ===== Commands =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_chat.send_message(
        "مرحباً 👋\n"
        "أرسل إعلانك في مجموعة الرياض مع **رقم الرخصة (FAL)** أو **رقم الصك محدث**.\n"
        "تذكير: توجد أتعاب ثابتة قدرها **1%** من قيمة الصفقة."
    )

async def whereami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.effective_chat.send_message(
        f"Chat ID: `{chat.id}`\nType: {chat.type}", parse_mode=ParseMode.MARKDOWN
    )

async def policy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_target_group(update):
        return
    await update.effective_chat.send_message(POLICY_TEXT, parse_mode=ParseMode.MARKDOWN)

# أوامر المالك: تثبيت/إزالة تثبيت (بالرد على الرسالة)
async def pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_target_group(update) or not is_owner(update.effective_user.id):
        return
    if not update.effective_message.reply_to_message:
        await update.effective_chat.send_message("الرجاء الرد على الرسالة التي تريد تثبيتها ثم أرسل /pin")
        return
    try:
        await update.effective_chat.pin_message(update.effective_message.reply_to_message.message_id)
    except Exception as e:
        await update.effective_chat.send_message(f"تعذّر التثبيت: {e}")

async def unpin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_target_group(update) or not is_owner(update.effective_user.id):
        return
    if not update.effective_message.reply_to_message:
        await update.effective_chat.send_message("الرجاء الرد على الرسالة التي تريد إلغاء تثبيتها ثم أرسل /unpin")
        return
    try:
        await update.effective_chat.unpin_message(update.effective_message.reply_to_message.message_id)
    except Exception as e:
        await update.effective_chat.send_message(f"تعذّر إلغاء التثبيت: {e}")

# ===== Background 1% reminder (no JobQueue) =====
async def _fee_loop(app: Application):
    await asyncio.sleep(30)  # تأخير بسيط بعد الإقلاع
    while True:
        try:
            await app.bot.send_message(GROUP_ID, "تذكير ودّي: أتعاب الصفحة ثابتة **1%** من قيمة الصفقة. ✅")
        except Exception:
            pass
        await asyncio.sleep(6 * 60 * 60)  # كل 6 ساعات

async def _post_init(app: Application):
    app.create_task(_fee_loop(app))

# ===== App =====
def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)      # تشغيل حلقة التذكير بالخلفية
        .build()
    )

    # أوامر
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("whereami", whereami))
    application.add_handler(CommandHandler("policy", policy))
    application.add_handler(CommandHandler("pin", pin))
    application.add_handler(CommandHandler("unpin", unpin))

    # التقط كل الرسائل داخل القروب (أي نوع)
    group_filter = filters.Chat(GROUP_ID) & filters.ChatType.GROUPS
    application.add_handler(MessageHandler(group_filter, moderate_message))

    application.run_polling()

if __name__ == "__main__":
    main()
