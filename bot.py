import os, re
import aiosqlite
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, ChatMemberUpdated
)
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters, ChatMemberHandler
)

# ========= ENV =========
BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
GROUP_ID    = os.getenv("GROUP_ID")          # e.g. -4942161299
CHANNEL_ID  = os.getenv("CHANNEL_ID")        # e.g. @YourChannel or -100...
ADMIN_USER  = int(os.getenv("ADMIN_USER_ID", "0"))
DB_PATH     = "listings.db"

# ========= STATES for /new =========
(
    C_LANG, C_DEAL, C_TYPE, C_DISTRICT, C_PRICE, C_SIZE, C_BEDS, C_BATHS,
    C_DESC, C_CONTACT, C_LICENSE, C_DEED, C_LOCATION, C_PHOTOS
) = range(14)

# ========= RULES =========
RIYADH_CENTER = (24.7136, 46.6753)
RIYADH_RADIUS_KM = 70.0

OFFTOPIC_KEYWORDS = {
    "loan","loans","تمويل","تقسيط","قرض","قروض","تأمين","insurance",
    "نقل عفش","نقل أثاث","moving","cleaning","تنظيف","كهربائي","سباك",
    "دهان","صيانة","تصميم مواقع","web design","دورات","courses"
}
NON_RIYADH_CITIES = {
    "جدة","مكة","المدينة","الخبر","الدمام","الظهران","ينبع","تبوك","حائل","جازان",
    "أبها","نجران","الطائف","عرعر","القصيم","بريدة","الهفوف","القطيف",
    "jeddah","mecca","makkah","madinah","medina","khobar","dammam","dhahran",
    "yanbu","tabuk","hail","jazan","abha","najran","taif","arar","qassim","buraidah","hofuf","qatif"
}
LICENSE_RE = re.compile(r"(?:FAL|فال|رخصة|ترخيص)\s*[:\-]?\s*(\d{7,12})", re.I)

# ========= TEXT =========
TXT = {
    "start": "مرحباً 👋 هذا بوت **عقارات الرياض** للوكلاء والمالكين.\n"
             "• أرسل /new في الخاص لإضافة عقار (مع رقم الرخصة FAL)\n"
             "• /search للبحث، /my_listings لرؤية إعلاناتك.\n\n"
             "Rules: Riyadh only • FAL required • 1% fee applies.",
    "choose_lang": "اختر اللغة / Choose language:",
    "deal": {"ar":"نوع العرض؟","en":"Deal type?"},
    "deal_btns": [("بيع • Sale","sale"),("إيجار • Rent","rent")],
    "type": {"ar":"نوع العقار؟ (شقة/فيلا/أرض/مكتب...)", "en":"Property type?"},
    "district": {"ar":"اسم الحي داخل **الرياض**:", "en":"District/area in Riyadh:"},
    "price": {"ar":"السعر (ريال):", "en":"Price (SAR):"},
    "size": {"ar":"المساحة (م²):", "en":"Size (m²):"},
    "beds": {"ar":"عدد الغرف:", "en":"Bedrooms:"},
    "baths": {"ar":"عدد الحمامات:", "en":"Bathrooms:"},
    "desc": {"ar":"وصف مختصر:", "en":"Short description:"},
    "contact": {"ar":"وسيلة التواصل (جوال/واتساب):", "en":"Contact (phone/WhatsApp):"},
    "license": {"ar":"اكتب **رقم الرخصة العقارية (FAL)**:", "en":"Enter your Real Estate License (FAL):"},
    "license_invalid": {"ar":"رقم الرخصة غير صالح (٧–١٢ أرقام).", "en":"Invalid FAL (7–12 digits)."},
    "deed": {"ar":"(اختياري للمالك) **رقم الصك مُحدّث**؟ اكتب الأرقام فقط أو اكتب 'تخطي'.\n"
                  "سيُحفظ للادارة فقط ولن يُنشر للعامة.",
             "en":"(Owner optional) Updated **deed number**? digits only or 'skip'.\n"
                  "This is stored privately for admin review and not published."},
    "deed_invalid": {"ar":"الرجاء أرقام فقط بين 5–20 خانة أو اكتب 'تخطي'.", "en":"Digits only (5–20) or 'skip'."},
    "location": {"ar":"أرسل موقعًا (اختياري) أو اكتب 'تخطي'", "en":"Send a location (optional) or type 'skip'"},
    "photos": {"ar":"أرسل 1–10 صور. اكتب 'تم' عند الانتهاء.", "en":"Send 1–10 photos. Type 'done' when finished."},
    "submitted": {"ar":"تم الإرسال ✅ بانتظار المراجعة. رقم الطلب: #{id}", "en":"Submitted ✅ Waiting for review. ID: #{id}"},
    "invalid": {"ar":"قيمة غير صحيحة، حاول مجددًا.", "en":"Invalid value, try again."},
    "pending_none": {"ar":"لا يوجد طلبات قيد المراجعة.", "en":"No pending listings."},
    "only_admin": {"ar":"هذا الأمر للمشرف فقط.", "en":"Admins only."},
    "approved_user": {"ar":"تمت الموافقة ونُشر الإعلان ✅", "en":"Approved & published ✅"},
    "rejected_user": {"ar":"مرفوض ❌ سبب: {reason}", "en":"Rejected ❌ Reason: {reason}"},
    "search_intro": "اكتب هكذا:\n/search sale 300000-1500000 الملز\n/search rent 3000-8000 Olaya",
    "saved_loc": {"ar":"تم حفظ الموقع ✅", "en":"Location saved ✅"},
    "skipped_loc": {"ar":"تم التخطي.", "en":"Skipped."},
}

def t(lang: str, key: str) -> str:
    v = TXT.get(key)
    return v.get(lang, next(iter(v.values()))) if isinstance(v, dict) else v

# ========= DB =========
INIT_SQL = """
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL,
  language TEXT NOT NULL,
  deal TEXT NOT NULL,
  ptype TEXT NOT NULL,
  district TEXT NOT NULL,
  price INTEGER NOT NULL,
  size INTEGER NOT NULL,
  beds INTEGER NOT NULL,
  baths INTEGER NOT NULL,
  descr TEXT NOT NULL,
  contact TEXT NOT NULL,
  license_no TEXT NOT NULL,
  deed_no TEXT,
  lat REAL, lon REAL,
  photo_file_ids TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""
OFFENSES_SQL = """
CREATE TABLE IF NOT EXISTS offenses (
  user_id INTEGER PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0,
  last_reason TEXT,
  updated_at TEXT NOT NULL
);
"""

async def db():
    conn = await aiosqlite.connect(DB_PATH)
    await conn.execute("PRAGMA journal_mode=WAL;")
    return conn

async def init_db():
    async with await db() as conn:
        await conn.execute(INIT_SQL)
        await conn.execute(OFFENSES_SQL)
        await conn.commit()

# ========= Helpers =========
def parse_int(s: str) -> Optional[int]:
    try: return int("".join(ch for ch in s if ch.isdigit()))
    except: return None

def contains_license(text: str) -> bool:
    if not text: return False
    return bool(LICENSE_RE.search(text))

def looks_offtopic(text: str) -> bool:
    if not text: return False
    L = text.lower()
    return any(k in L for k in (x.lower() for x in OFFTOPIC_KEYWORDS))

def mentions_other_city(text: str) -> bool:
    if not text: return False
    L = text.lower()
    return any(c in L for c in (x.lower() for x in NON_RIYADH_CITIES))

def is_admin(uid: int) -> bool:
    return uid == ADMIN_USER

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    from math import radians, sin, cos, asin, sqrt
    R = 6371.0
    dlat = radians(lat2-lat1); dlon = radians(lon2-lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    return 2*R*asin(sqrt(a))

def listing_caption(d: Dict[str, Any]) -> str:
    lines = [
        f"🏷️ {'للبيع' if d['deal']=='sale' else 'للإيجار'} • {d['ptype'].title()}",
        f"📍 {d['district']} — الرياض",
        f"💰 {d['price']:,} ر.س  •  📐 {d['size']:,} م²",
        f"🛏️ {d['beds']}  •  🛁 {d['baths']}",
        f"📝 {d['descr']}",
        f"☎️ {d['contact']}",
        f"🔖 FAL: {d['license_no']}",
    ]
    if d.get("lat") and d.get("lon"):
        lines.append(f"📌 https://maps.google.com/?q={d['lat']},{d['lon']}")
    return "\n".join(lines)

# ========= Auto policy pinning =========
POLICY_TEXT = (
    "📌 **سياسة المجموعة/القناة**\n"
    "• لصاحب الصفحة حق أتعاب ثابت قدره **1%** من قيمة الصفقة النهائية.\n"
    "• الإعلانات داخل **مدينة الرياض** فقط.\n"
    "• يجب كتابة **رقم الرخصة العقارية (FAL)** في كل إعلان.\n"
    "• (اختياري للمالكين) يمكن تزويد **رقم الصك مُحدّث** — يُحفظ للادارة فقط.\n"
    "• يمنع الإعلان عن خدمات غير عقارية. المخالفات تُحذف ويُطبق الحظر المؤقت.\n\n"
    "Penalties: 24h → 3 days → 7 days → +7d for further.\n"
    "تذكير دوري: توجد **رسوم 1%** من الأرباح لصاحب الصفحة."
)

async def post_and_pin_policy(context: ContextTypes.DEFAULT_TYPE):
    if not GROUP_ID: return
    try:
        msg = await context.bot.send_message(int(GROUP_ID), POLICY_TEXT, parse_mode=ParseMode.MARKDOWN)
        await context.bot.pin_chat_message(int(GROUP_ID), msg.message_id, disable_notification=True)
    except Exception:
        # ignore (e.g. missing permission); admin can run /pin_policy if needed
        pass

async def fee_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    if GROUP_ID:
        try:
            await context.bot.send_message(int(GROUP_ID),
                "تذكير: توجد **رسوم 1%** من الأرباح لصاحب الصفحة.",
                parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

# ========= Commands =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TXT["start"])

async def cmd_whereami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = update.effective_chat
    await update.message.reply_text(f"Chat ID: {c.id}\nType: {c.type}\nTitle: {c.title or ''}")

async def cmd_pin_policy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(TXT["only_admin"]); return
    await post_and_pin_policy(context)
    await update.message.reply_text("Policy pinned (if permissions allow).")

# ========= Conversation (/new) =========
def lang_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("العربية","lang:ar"),
                                  InlineKeyboardButton("English","lang:en")]])
def deal_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton(t,"deal:"+c) for (t,c) in TXT["deal_btns"]]])

async def new_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TXT["choose_lang"], reply_markup=lang_kb()); return C_LANG

async def choose_lang_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    lang = q.data.split(":")[1]; context.user_data["lang"] = lang
    await q.edit_message_text(t(lang,"deal"), reply_markup=deal_kb()); return C_DEAL

async def pick_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    context.user_data["deal"] = q.data.split(":")[1]
    lang = context.user_data.get("lang","ar")
    await q.edit_message_text(t(lang,"type")); return C_TYPE

async def ask_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ptype"] = (update.message.text or "").strip().lower()
    lang = context.user_data.get("lang","ar")
    await update.message.reply_text(t(lang,"district")); return C_DISTRICT

async def ask_district(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["district"] = (update.message.text or "").strip()
    lang = context.user_data.get("lang","ar")
    await update.message.reply_text(t(lang,"price")); return C_PRICE

async def ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    p = parse_int(update.message.text); lang = context.user_data.get("lang","ar")
    if p is None: await update.message.reply_text(t(lang,"invalid")); return C_PRICE
    context.user_data["price"] = p; await update.message.reply_text(t(lang,"size")); return C_SIZE

async def ask_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = parse_int(update.message.text); lang = context.user_data.get("lang","ar")
    if n is None: await update.message.reply_text(t(lang,"invalid")); return C_SIZE
    context.user_data["size"] = n; await update.message.reply_text(t(lang,"beds")); return C_BEDS

async def ask_beds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = parse_int(update.message.text); lang = context.user_data.get("lang","ar")
    if n is None: await update.message.reply_text(t(lang,"invalid")); return C_BEDS
    context.user_data["beds"] = n; await update.message.reply_text(t(lang,"baths")); return C_BATHS

async def ask_baths(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = parse_int(update.message.text); lang = context.user_data.get("lang","ar")
    if n is None: await update.message.reply_text(t(lang,"invalid")); return C_BATHS
    context.user_data["baths"] = n; await update.message.reply_text(t(lang,"desc")); return C_DESC

async def ask_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descr"] = (update.message.text or "").strip()
    lang = context.user_data.get("lang","ar")
    await update.message.reply_text(t(lang,"contact")); return C_CONTACT

async def ask_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["contact"] = (update.message.text or "").strip()
    lang = context.user_data.get("lang","ar")
    await update.message.reply_text(t(lang,"license")); return C_LICENSE

async def ask_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang","ar")
    digits = "".join(ch for ch in (update.message.text or "") if ch.isdigit())
    if not (7 <= len(digits) <= 12):
        await update.message.reply_text(t(lang,"license_invalid")); return C_LICENSE
    context.user_data["license_no"] = digits
    await update.message.reply_text(t(lang,"deed")); return C_DEED

async def ask_deed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang","ar")
    txt = (update.message.text or "").strip().lower()
    if txt in ("تخطي","skip","لا","no"):
        context.user_data["deed_no"] = None
    else:
        d = "".join(ch for ch in txt if ch.isdigit())
        if not (5 <= len(d) <= 20):
            await update.message.reply_text(t(lang,"deed_invalid")); return C_DEED
        context.user_data["deed_no"] = d
    kb = ReplyKeyboardMarkup([[KeyboardButton("📍 Send location", request_location=True)]],
                             resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(t(lang,"location"), reply_markup=kb); return C_LOCATION

async def ask_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang","ar")
    if update.message.location:
        context.user_data["lat"] = update.message.location.latitude
        context.user_data["lon"] = update.message.location.longitude
        await update.message.reply_text(TXT["saved_loc"][lang])
    else:
        await update.message.reply_text(TXT["skipped_loc"][lang])
    context.user_data["photos"] = []
    await update.message.reply_text(TXT["photos"][lang]); return C_PHOTOS

async def collect_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = context.user_data.get("lang","ar")
    if update.message.photo:
        fid = update.message.photo[-1].file_id
        photos = context.user_data.get("photos", [])
        if len(photos) < 10: photos.append(fid); context.user_data["photos"] = photos
        await update.message.reply_text("تم ✅ أرسل المزيد أو اكتب 'تم' / 'done'")
        return C_PHOTOS
    if (update.message.text or "").lower() in ("done","تم","انتهيت"):
        u = context.user_data
        async with await db() as conn:
            await conn.execute("""
                INSERT INTO listings
                (agent_id, language, deal, ptype, district, price, size, beds, baths,
                 descr, contact, license_no, deed_no, lat, lon, photo_file_ids, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    update.effective_user.id, u.get("lang","ar"), u["deal"], u["ptype"], u["district"], u["price"],
                    u["size"], u["beds"], u["baths"], u["descr"], u["contact"], u["license_no"], u.get("deed_no"),
                    u.get("lat"), u.get("lon"), ",".join(u.get("photos", [])), datetime.utcnow().isoformat()
                )
            )
            await conn.commit()
            cur = await conn.execute("SELECT last_insert_rowid()"); new_id = (await cur.fetchone())[0]
        await update.message.reply_text(TXT["submitted"][lang].format(id=new_id))
        try: await context.bot.send_message(ADMIN_USER, f"🧾 New listing pending: #{new_id}")
        except: pass
        return ConversationHandler.END
    return C_PHOTOS

# ========= Search & My listings =========
async def cmd_my_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    async with await db() as conn:
        cur = await conn.execute(
            "SELECT id, status, deal, ptype, district, price FROM listings WHERE agent_id=? ORDER BY id DESC LIMIT 12", (uid,)
        )
        rows = await cur.fetchall()
    if not rows: await update.message.reply_text("No listings. Use /new"); return
    lines = ["Your listings:"]
    for r in rows:
        lines.append(f"#{r[0]} • {r[2]}/{r[3]} • {r[4]} • SAR {r[5]:,} • {r[1]}")
    await update.message.reply_text("\n".join(lines))

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    if not args: await update.message.reply_text(TXT["search_intro"]); return
    deal = args[0].lower() if args else ""
    pr_min = pr_max = None; district_kw = None
    if len(args) >= 2 and "-" in args[1]:
        a,b = args[1].split("-",1); pr_min = parse_int(a); pr_max = parse_int(b)
    if len(args) >= 3: district_kw = " ".join(args[2:])
    q = "SELECT id, deal, ptype, district, price, size, beds, baths FROM listings WHERE status='approved'"
    params: List[Any] = []
    if deal in ("sale","rent"): q += " AND deal=?"; params.append(deal)
    if pr_min is not None: q += " AND price>=?"; params.append(pr_min)
    if pr_max is not None: q += " AND price<=?"; params.append(pr_max)
    if district_kw: q += " AND district LIKE ?"; params.append(f"%{district_kw}%")
    q += " ORDER BY id DESC LIMIT 10"
    async with await db() as conn:
        cur = await conn.execute(q, params); rows = await cur.fetchall()
    if not rows: await update.message.reply_text("No matches."); return
    lines = ["Top matches:"]
    for r in rows:
        lines.append(f"#{r[0]} • {r[1]}/{r[2]} • {r[3]} • SAR {r[4]:,} • {r[6]}BR/{r[7]}BA • {r[5]} m²")
    await update.message.reply_text("\n".join(lines))

# ========= Admin review =========
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(TXT["only_admin"]); return
    async with await db() as conn:
        cur = await conn.execute("SELECT id, agent_id, deal, ptype, district, price FROM listings WHERE status='pending' ORDER BY id ASC LIMIT 20")
        rows = await cur.fetchall()
    if not rows: await update.message.reply_text(TXT["pending_none"]); return
    lines = ["Pending IDs:"]
    for r in rows:
        lines.append(f"#{r[0]} • {r[2]}/{r[3]} • {r[4]} • SAR {r[5]:,} • agent {r[1]}")
    await update.message.reply_text("\n".join(lines))

async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text(TXT["only_admin"]); return
    parts = (update.message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit(): await update.message.reply_text("Usage: /approve <ID>"); return
    lid = int(parts[1])
    async with await db() as conn:
        cur = await conn.execute("SELECT * FROM listings WHERE id=?", (lid,))
        row = await cur.fetchone()
        if not row: await update.message.reply_text("Not found."); return
        cols = [d[0] for d in cur.description]; data = dict(zip(cols, row))
        if data["status"] == "approved": await update.message.reply_text("Already approved."); return
        caption = listing_caption(data)
        photos = [p for p in (data["photo_file_ids"] or "").split(",") if p]
        # publish to channel if configured
        if CHANNEL_ID:
            if photos:
                media = [InputMediaPhoto(media=pf, caption=caption if i==0 else None) for i,pf in enumerate(photos[:10])]
                await context.bot.send_media_group(CHANNEL_ID, media)
            else:
                await context.bot.send_message(CHANNEL_ID, caption)
        await conn.execute("UPDATE listings SET status='approved' WHERE id=?", (lid,)); await conn.commit()
    await update.message.reply_text(f"Approved #{lid} ✅")
    try: await context.bot.send_message(data["agent_id"], TXT["approved_user"])
    except: pass

async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): await update.message.reply_text(TXT["only_admin"]); return
    parts = (update.message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit(): await update.message.reply_text("Usage: /reject <ID> [reason]"); return
    lid = int(parts[1]); reason = parts[2] if len(parts)>=3 else "Not specified"
    async with await db() as conn:
        await conn.execute("UPDATE listings SET status='rejected' WHERE id=?", (lid,)); await conn.commit()
    await update.message.reply_text(f"Rejected #{lid} ❌")
    try:
        async with await db() as conn:
            cur = await conn.execute("SELECT agent_id FROM listings WHERE id=?", (lid,))
            r = await cur.fetchone()
        if r: await context.bot.send_message(r[0], TXT["rejected_user"]["en"].format(reason=reason))
    except: pass

# ========= Moderation =========
async def ban_record(uid: int, reason: str) -> int:
    async with await db() as conn:
        cur = await conn.execute("SELECT count FROM offenses WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if row:
            c = row[0]+1; await conn.execute("UPDATE offenses SET count=?, last_reason=?, updated_at=? WHERE user_id=?",
                                             (c, reason, datetime.utcnow().isoformat(), uid))
        else:
            c = 1; await conn.execute("INSERT INTO offenses (user_id, count, last_reason, updated_at) VALUES (?,?,?,?)",
                                      (uid, 1, reason, datetime.utcnow().isoformat()))
        await conn.commit()
    return c

def ban_seconds(count: int) -> int:
    if count == 1: return 24*3600
    if count == 2: return 3*24*3600
    if count == 3: return 7*24*3600
    return (7 + 7*(count-3))*24*3600

async def moderation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or update.effective_chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    if GROUP_ID and str(update.effective_chat.id) != str(GROUP_ID): return
    if msg.from_user and is_admin(msg.from_user.id): return

    text = msg.text or msg.caption or ""
    missing_license = not contains_license(text)
    offtopic = looks_offtopic(text)
    outside_riyadh = mentions_other_city(text)
    if msg.location:
        d = haversine_km(msg.location.latitude, msg.location.longitude, *RIYADH_CENTER)
        outside_riyadh = outside_riyadh or (d > RIYADH_RADIUS_KM)

    reason = None
    if offtopic: reason = "off-topic"
    elif outside_riyadh: reason = "outside-riyadh"
    elif missing_license: reason = "no-license"

    if not reason: return

    try: await msg.delete()
    except: pass

    if reason in ("off-topic", "outside-riyadh"):
        count = await ban_record(msg.from_user.id, reason)
        seconds = ban_seconds(count)
        until = datetime.utcnow() + timedelta(seconds=seconds)
        try: await context.bot.ban_chat_member(update.effective_chat.id, msg.from_user.id, until_date=until)
        except: pass
        try:
            await context.bot.send_message(
                msg.from_user.id,
                f"تم حذف إعلانك وحظرك مؤقتًا ({'خارج الرياض' if reason=='outside-riyadh' else 'خدمات غير عقارية'}). "
                f"المدة: {seconds//3600} ساعة. الرجاء الالتزام: الرياض فقط + إعلان عقاري + كتابة FAL."
            )
        except: pass
    else:
        # no-license → just inform privately
        try:
            await context.bot.send_message(msg.from_user.id, "تم حذف إعلانك لعدم وجود رقم الرخصة العقارية (FAL). أعد النشر مع كتابة الرقم.")
        except: pass

# ========= Bot lifecycle hooks =========
async def on_startup(context: ContextTypes.DEFAULT_TYPE):
    # auto pin policy once
    await post_and_pin_policy(context)
    # start 6-hour reminders
    context.job_queue.run_repeating(fee_reminder_job, interval=6*60*60, first=60)

async def on_chatmember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If the bot is added to the configured group, (re)post policy."""
    if update.my_chat_member and str(update.my_chat_member.chat.id) == str(GROUP_ID):
        await post_and_pin_policy(context)

# ========= Wire app =========
def build_app():
    app = Application.builder().token(BOT_TOKEN).build()

    # lifecycle
    app.job_queue.run_once(lambda ctx: on_startup(ctx), when=5)  # small delay after boot
    app.add_handler(ChatMemberHandler(on_chatmember, ChatMemberHandler.MY_CHAT_MEMBER))

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("whereami", cmd_whereami))
    app.add_handler(CommandHandler("pin_policy", cmd_pin_policy))
    app.add_handler(CommandHandler("my_listings", cmd_my_listings))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))

    # /new conversation
    conv = ConversationHandler(
        entry_points=[CommandHandler("new", new_entry)],
        states={
            C_LANG: [CallbackQueryHandler(choose_lang_cb, pattern=r"^lang:")],
            C_DEAL: [CallbackQueryHandler(pick_deal, pattern=r"^deal:")],
            C_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_type)],
            C_DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_district)],
            C_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_price)],
            C_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_size)],
            C_BEDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_beds)],
            C_BATHS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_baths)],
            C_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_desc)],
            C_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_contact)],
            C_LICENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_license)],
            C_DEED: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_deed)],
            C_LOCATION: [MessageHandler((filters.LOCATION | filters.TEXT) & ~filters.COMMAND, ask_location)],
            C_PHOTOS: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, collect_photos)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True
    )
    app.add_handler(conv)

    # group moderation
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, moderation))
    return app

if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
    application = build_app()
    application.run_polling(drop_pending_updates=True)
