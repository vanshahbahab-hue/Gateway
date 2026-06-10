import re
import json
import os
import asyncio
import threading
import qrcode
import random
import string
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from PIL import Image, ImageDraw, ImageFont

# ============ FLASK FOR RENDER ============
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/health')
def health():
    return "ESCROW Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ============ CONFIG ============
BOT_TOKEN = "8679581798:AAGZtycapDdwpwYR8ro5M4xZNFiIR4QuetI"
OWNER_ID = 8586849798
ADMIN_IDS = [OWNER_ID]

# Files
DEALS_FILE = "deals.json"
PENDING_FILE = "pending.json"
COMPLETED_FILE = "completed.json"

# ============ FILE FUNCTIONS ============
def load_deals():
    if os.path.exists(DEALS_FILE):
        with open(DEALS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_deals(data):
    with open(DEALS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_pending():
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_pending(data):
    with open(PENDING_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def load_completed():
    if os.path.exists(COMPLETED_FILE):
        with open(COMPLETED_FILE, 'r') as f:
            return json.load(f)
    return []

def save_completed(data):
    with open(COMPLETED_FILE, 'w') as f:
        json.dump(data, f, indent=2)

deals = load_deals()
pending_tx = load_pending()
completed_tx = load_completed()

# ============ HELPERS ============
def generate_deal_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def generate_pin():
    return ''.join(random.choices(string.digits, k=6))

def generate_random_paise():
    return random.randint(1, 99)

def calculate_fee(amount):
    if amount <= 400:
        return 10
    elif amount <= 2000:
        return int(amount * 0.03)
    elif amount <= 5000:
        return int(amount * 0.035)
    else:
        return int(amount * 0.03)

def get_qr_amount(original_amount):
    base_amount = original_amount + calculate_fee(original_amount)
    random_paise = generate_random_paise()
    qr_amount = base_amount + (random_paise / 100)
    return round(qr_amount, 2), random_paise

def generate_qr_with_bg(upi_id, qr_amount, original_amount, deal_id):
    upi_link = f"upi://pay?pa={upi_id}&pn=ESCROW&am={qr_amount}&cu=INR&tn={deal_id}"
    
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(upi_link)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")
    qr_pil = qr_img.get_image()
    
    bg = Image.new('RGB', (500, 600), color='#1a1a2e')
    draw = ImageDraw.Draw(bg)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    draw.text((250, 30), "ESCROW PAYMENT", fill="white", anchor="mt", font=font)
    draw.text((250, 65), f"Deal ID: {deal_id}", fill="#aaaaaa", anchor="mt", font=font_small)
    draw.text((250, 100), f"Amount to Pay: ₹{qr_amount}", fill="#00ff00", anchor="mt", font=font)
    draw.text((250, 135), f"(Original Deal: ₹{original_amount})", fill="#888888", anchor="mt", font=font_small)
    
    qr_position = ((500 - qr_pil.size[0]) // 2, 170)
    bg.paste(qr_pil, qr_position)
    
    draw.text((250, 480), "SEND SCREENSHOT AFTER PAYMENT", fill="#00ff00", anchor="mt", font=font_small)
    draw.text((250, 510), "Auto-verify will happen when SMS is received", fill="#ffcc00", anchor="mt", font=font_small)
    draw.text((250, 540), "DON'T PAY IN DMS", fill="red", anchor="mt", font=font_small)
    
    img_bytes = BytesIO()
    bg.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

def extract_amount_from_sms(text):
    patterns = [
        r'Rs\.?\s*(\d+\.?\d*)',
        r'₹\s*(\d+\.?\d*)',
        r'debited\s*Rs\.?\s*(\d+\.?\d*)',
        r'credited\s*Rs\.?\s*(\d+\.?\d*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None

def extract_tx_id(text):
    patterns = [
        r'Txn ID[:\s]*(\d+)',
        r'Transaction ID[:\s]*(\d+)',
        r'TX[:\s]*(\d+)',
        r'ID[:\s]*(\d+)',
        r'(\d{10,15})'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def find_deal_by_qr_amount(qr_amount):
    for deal_id, deal in deals.items():
        if deal.get("qr_amount") == qr_amount and deal["status"] == "awaiting_payment":
            return deal_id, deal
    return None, None

# ============ ESCROW FORM PARSER ============
async def handle_escrow_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    chat_id = update.effective_chat.id
    
    if not re.search(r'ESCROW\s*DEAL\s*FORM', message_text, re.IGNORECASE):
        return
    
    amount_match = re.search(r'DEAL\s*AMOUNT\s*:?\s*[-\s]*(\d+)', message_text, re.IGNORECASE)
    buyer_match = re.search(r'BUYERS?\s*:?\s*[-\s]*@?(\w+)', message_text, re.IGNORECASE)
    seller_match = re.search(r'SELLER\s*:?\s*[-\s]*@?(\w+)', message_text, re.IGNORECASE)
    deal_detail_match = re.search(r'DEAL\s*DETAIL\s*:?\s*[-\s]*(.+)', message_text, re.IGNORECASE)
    upi_match = re.search(r'RLS\s*UPI\s*:?\s*[-\s]*(\S+@\S+)', message_text, re.IGNORECASE)
    condition_match = re.search(r'CONDITION\s*:?\s*[-\s]*(.+)', message_text, re.IGNORECASE)
    till_match = re.search(r'ESCROW\s*TILL\s*:?\s*[-\s]*(.+)', message_text, re.IGNORECASE)
    
    if not amount_match:
        await update.message.reply_text("❌ Missing DEAL AMOUNT")
        return
    
    amount = int(amount_match.group(1))
    buyer = buyer_match.group(1) if buyer_match else None
    seller = seller_match.group(1) if seller_match else None
    deal_detail = deal_detail_match.group(1) if deal_detail_match else "N/A"
    upi_id = upi_match.group(1) if upi_match else "venomxpay@naviaxis"
    condition = condition_match.group(1) if condition_match else "N/A"
    escrow_till = till_match.group(1) if till_match else "N/A"
    
    if not buyer or not seller:
        await update.message.reply_text("❌ Need BUYER and SELLER")
        return
    
    fee = calculate_fee(amount)
    total_with_fee = amount + fee
    qr_amount, random_paise = get_qr_amount(amount)
    
    deal_id = generate_deal_id()
    
    deals[deal_id] = {
        "deal_id": deal_id,
        "amount": amount,
        "fee": fee,
        "total_with_fee": total_with_fee,
        "qr_amount": qr_amount,
        "random_paise": random_paise,
        "buyer": buyer,
        "seller": seller,
        "deal_detail": deal_detail,
        "upi_id": upi_id,
        "condition": condition,
        "escrow_till": escrow_till,
        "buyer_agreed": False,
        "seller_agreed": False,
        "status": "pending",
        "chat_id": chat_id,
        "created_at": str(datetime.now()),
        "buyer_id": None,
        "seller_id": None,
        "payment_received": False,
        "payment_txid": None,
        "released": False
    }
    save_deals(deals)
    
    group_msg = f"""
🔷 ESCROW DEAL CREATED 🔷

📋 DEAL ID: {deal_id}
💰 Amount: ₹{amount}
📊 Fee: ₹{fee}
💵 Total to Pay: ₹{total_with_fee}

👤 Buyer: @{buyer}
👥 Seller: @{seller}
📝 Details: {deal_detail}
💳 UPI: {upi_id}
📋 Condition: {condition}
⏰ Escrow Till: {escrow_till}

⚠️ ESCROW FEES IS NON-REFUNDABLE

✅ @{buyer} - Type AGREE to confirm
✅ @{seller} - Type AGREE to confirm

🕐 Both must agree within 10 minutes!
"""
    
    await update.message.reply_text(group_msg)
    
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"🆕 NEW DEAL!\nID: {deal_id}\n₹{amount}\n@{buyer} → @{seller}"
    )
    
    context.job_queue.run_once(check_agreement, 600, data=deal_id)

async def check_agreement(context):
    deal_id = context.job.data
    deal = deals.get(deal_id)
    
    if not deal or deal["status"] != "pending":
        return
    
    if not deal["buyer_agreed"] or not deal["seller_agreed"]:
        await context.bot.send_message(
            chat_id=deal["chat_id"],
            text=f"❌ DEAL TIMEOUT!\nDeal {deal_id} cancelled."
        )
        deal["status"] = "cancelled"
        save_deals(deals)

async def handle_agreement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    username = user.username.lower() if user.username else ""
    text = update.message.text.lower()
    
    agree_words = ['agree', 'agre', 'argee', 'agr', 'yes', 'done', 'ok', 'y']
    
    if not any(word in text for word in agree_words):
        return
    
    for deal_id, deal in deals.items():
        if deal["status"] != "pending":
            continue
        
        if deal["buyer"].lower() == username:
            deal["buyer_agreed"] = True
            deal["buyer_id"] = user.id
            save_deals(deals)
            await update.message.reply_text(f"✅ @{user.username}, you agreed!")
            
        elif deal["seller"].lower() == username:
            deal["seller_agreed"] = True
            deal["seller_id"] = user.id
            save_deals(deals)
            await update.message.reply_text(f"✅ @{user.username}, you agreed!")
        else:
            continue
        
        if deal["buyer_agreed"] and deal["seller_agreed"]:
            qr_amount = deal["qr_amount"]
            
            img_bytes = generate_qr_with_bg(deal["upi_id"], qr_amount, deal["amount"], deal_id)
            photo = InputFile(img_bytes, filename="qr.png")
            
            await context.bot.send_photo(
                chat_id=deal["buyer_id"],
                photo=photo,
                caption=f"🔷 PAYMENT QR CODE\n\nDeal ID: {deal_id}\nOriginal: ₹{deal['amount']}\nFee: ₹{deal['fee']}\n\nPay this exact amount: ₹{qr_amount}\n\nAfter payment, bot will auto-detect.\n\nDON'T PAY IN DMS"
            )
            
            await context.bot.send_message(
                chat_id=deal["chat_id"],
                text=f"✅ BOTH AGREED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nBuyer @{deal['buyer']} has received QR code.\nPay EXACT ₹{qr_amount} for auto-verification!"
            )
            
            deal["status"] = "awaiting_payment"
            save_deals(deals)
            
            schedule_reminders(context, deal_id)
        return

def schedule_reminders(context, deal_id):
    context.job_queue.run_once(send_reminder, 300, data=deal_id)
    context.job_queue.run_once(send_reminder, 1800, data=deal_id)
    context.job_queue.run_once(send_reminder, 3600, data=deal_id)

async def send_reminder(context):
    deal_id = context.job.data
    deal = deals.get(deal_id)
    
    if not deal or deal["status"] != "awaiting_payment":
        return
    
    await context.bot.send_message(
        chat_id=deal["buyer_id"],
        text=f"⏰ PAYMENT REMINDER!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['qr_amount']}\n\nPlease complete payment."
    )

# ============ SMS HANDLER ============
async def sms_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    text = update.message.text
    sms_amount = extract_amount_from_sms(text)
    tx_id = extract_tx_id(text)
    
    if not sms_amount:
        await update.message.reply_text(f"❌ Could not extract amount from SMS.\n\n{text[:200]}")
        return
    
    deal_id, deal = find_deal_by_qr_amount(sms_amount)
    
    if deal_id and deal:
        deal["payment_received"] = True
        deal["payment_txid"] = tx_id
        deal["status"] = "payment_confirmed"
        save_deals(deals)
        
        if tx_id:
            pending_tx[tx_id] = {
                "tx_id": tx_id,
                "amount": sms_amount,
                "deal_id": deal_id,
                "timestamp": str(datetime.now())
            }
            save_pending(pending_tx)
        
        await update.message.reply_text(f"✅ PAYMENT AUTO-VERIFIED!\n\nDeal ID: {deal_id}\nAmount: ₹{sms_amount}\nTXN: {tx_id}")
        
        if deal.get("buyer_id"):
            await context.bot.send_message(
                chat_id=deal["buyer_id"],
                text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nCONTINUE YOUR DEAL"
            )
        
        if deal.get("seller_id"):
            await context.bot.send_message(
                chat_id=deal["seller_id"],
                text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nWaiting for release."
            )
        
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"💰 PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\nTXN: {tx_id}\n\nUse /release {deal_id}"
        )
    else:
        if tx_id:
            pending_tx[tx_id] = {
                "tx_id": tx_id,
                "amount": sms_amount,
                "raw_sms": text[:300],
                "timestamp": str(datetime.now())
            }
            save_pending(pending_tx)
        
        await update.message.reply_text(f"⚠️ Payment detected but no matching deal!\n\nAmount: ₹{sms_amount}\nTXN: {tx_id}\n\nManual verification required.")

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
🔷 ESCROW BOT 🔷

I help secure your deals with auto-verification!

Create a deal in any group:
ESCROW DEAL FORM !!!

DEAL AMOUNT : 1000
BUYER : @username
SELLER : @username
DEAL DETAIL : Product
RLS UPI : your@upi
CONDITION : After payment
ESCROW TILL : 2024-12-31

Commands:
/status DEAL_ID - Check deal status
/cancel DEAL_ID - Cancel deal

Admin commands:
/release DEAL_ID - Release funds
/deals - List all deals
/verify TXN DEAL - Manual verify

Developer: @iflexvenom
""")

async def release_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /release DEAL_ID")
        return
    
    deal_id = context.args[0]
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text("❌ Deal not found!")
        return
    
    if deal["status"] != "payment_confirmed":
        await update.message.reply_text("❌ Payment not confirmed yet!")
        return
    
    deal["status"] = "completed"
    deal["released"] = True
    save_deals(deals)
    
    if deal.get("seller_id"):
        await context.bot.send_message(
            chat_id=deal["seller_id"],
            text=f"✅ DEAL RELEASED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\nFee: ₹{deal['fee']}"
        )
    
    await context.bot.send_message(
        chat_id=deal["chat_id"],
        text=f"✅ DEAL COMPLETED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n@{deal['buyer']} ↔ @{deal['seller']}"
    )
    
    await update.message.reply_text(f"✅ Deal {deal_id} released!")

async def cancel_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /cancel DEAL_ID")
        return
    
    deal_id = context.args[0]
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text("❌ Deal not found!")
        return
    
    if user_id not in ADMIN_IDS and user_id != deal.get("buyer_id") and user_id != deal.get("seller_id"):
        await update.message.reply_text("❌ Not authorized!")
        return
    
    if deal["status"] in ["completed", "released"]:
        await update.message.reply_text("❌ Deal already completed!")
        return
    
    deal["status"] = "cancelled"
    save_deals(deals)
    
    await update.message.reply_text(f"❌ Deal {deal_id} cancelled!")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /status DEAL_ID")
        return
    
    deal_id = context.args[0]
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text("❌ Deal not found!")
        return
    
    status_map = {
        "pending": "⏳ Waiting for agreement",
        "awaiting_payment": "💳 Waiting for payment",
        "payment_confirmed": "✅ Payment confirmed",
        "completed": "🎉 Completed",
        "cancelled": "❌ Cancelled"
    }
    
    await update.message.reply_text(f"""
📋 DEAL STATUS

ID: {deal_id}
Status: {status_map.get(deal['status'], deal['status'])}
Amount: ₹{deal['amount']}
QR Amount: ₹{deal['qr_amount']}
Buyer: @{deal['buyer']}
Seller: @{deal['seller']}
""")

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    active = [d for d in deals.values() if d["status"] not in ["completed", "cancelled"]]
    completed = [d for d in deals.values() if d["status"] == "completed"]
    total_volume = sum([d["amount"] for d in completed])
    
    await update.message.reply_text(f"""
👑 ADMIN PANEL

Active: {len(active)}
Completed: {len(completed)}
Volume: ₹{total_volume}

Commands:
/release DEAL_ID
/deals
/verify TXN DEAL
""")

async def list_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    if not deals:
        await update.message.reply_text("No deals.")
        return
    
    msg = "📋 DEALS\n\n"
    for deal_id, deal in list(deals.items())[-10:]:
        msg += f"{deal_id} - ₹{deal['amount']} - {deal['status']}\n"
        msg += f"@{deal['buyer']} → @{deal['seller']}\n\n"
    
    await update.message.reply_text(msg)

async def verify_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /verify TXN_ID DEAL_ID")
        return
    
    tx_id = context.args[0]
    deal_id = context.args[1]
    
    if tx_id not in pending_tx:
        await update.message.reply_text(f"❌ Transaction {tx_id} not found!")
        return
    
    amount = pending_tx[tx_id].get('amount')
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text("❌ Deal not found!")
        return
    
    deal["payment_received"] = True
    deal["payment_txid"] = tx_id
    deal["status"] = "payment_confirmed"
    save_deals(deals)
    
    completed_tx.append({
        "tx_id": tx_id,
        "amount": amount,
        "deal_id": deal_id,
        "verified_at": str(datetime.now())
    })
    save_completed(completed_tx)
    
    del pending_tx[tx_id]
    save_pending(pending_tx)
    
    await update.message.reply_text(f"✅ Payment verified for deal {deal_id}!")
    
    if deal.get("buyer_id"):
        await context.bot.send_message(
            chat_id=deal["buyer_id"],
            text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nCONTINUE YOUR DEAL"
        )

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /addadmin USER_ID")
        return
    
    try:
        new_admin = int(context.args[0])
        if new_admin not in ADMIN_IDS:
            ADMIN_IDS.append(new_admin)
            await update.message.reply_text(f"✅ Added admin: {new_admin}")
    except:
        await update.message.reply_text("❌ Invalid ID")

# ============ MAIN ============
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # User commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_deal))
    
    # Admin commands
    application.add_handler(CommandHandler("release", release_command))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("deals", list_deals))
    application.add_handler(CommandHandler("verify", verify_manual))
    application.add_handler(CommandHandler("addadmin", add_admin))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_escrow_form))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_agreement))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sms_forward_handler))
    
    print("=" * 50)
    print("🔷 ESCROW BOT STARTED")
    print(f"👑 Owner: {OWNER_ID}")
    print("=" * 50)
    
    application.run_polling()

if __name__ == "__main__":
    main()
