import re
import json
import os
import threading
import qrcode
import random
import string
from io import BytesIO
from datetime import datetime
from flask import Flask
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
    random_paise = random.randint(1, 99)
    qr_amount = base_amount + (random_paise / 100)
    return round(qr_amount, 2), random_paise

def generate_qr(upi_id, qr_amount, deal_id):
    upi_link = f"upi://pay?pa={upi_id}&pn=ESCROW&am={qr_amount}&cu=INR&tn={deal_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(upi_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_bytes = BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    return img_bytes

def extract_amount_from_sms(text):
    patterns = [r'Rs\.?\s*(\d+\.?\d*)', r'₹\s*(\d+\.?\d*)', r'debited\s*Rs\.?\s*(\d+\.?\d*)']
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None

def extract_tx_id(text):
    patterns = [r'Txn ID[:\s]*(\d+)', r'Transaction ID[:\s]*(\d+)', r'TX[:\s]*(\d+)', r'(\d{10,15})']
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

# ============ MAIN MESSAGE HANDLER ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.username.lower() if user.username else ""
    text_lower = message_text.lower()
    
    print(f"[DEBUG] Message from @{username}: {text_lower[:50]}")
    
    # ============ FIRST CHECK: IS THIS AN ESCROW FORM? ============
    if re.search(r'ESCROW\s*DEAL\s*FORM', message_text, re.IGNORECASE):
        print("[DEBUG] Detected ESCROW FORM")
        
        amount_match = re.search(r'DEAL\s*AMOUNT\s*:?\s*[-\s]*(\d+)', message_text, re.IGNORECASE)
        buyer_match = re.search(r'BUYERS?\s*:?\s*[-\s]*@?(\w+)', message_text, re.IGNORECASE)
        seller_match = re.search(r'SELLER\s*:?\s*[-\s]*@?(\w+)', message_text, re.IGNORECASE)
        deal_detail_match = re.search(r'DEAL\s*DETAIL\s*:?\s*[-\s]*(.+)', message_text, re.IGNORECASE)
        upi_match = re.search(r'RLS\s*UPI\s*:?\s*[-\s]*(\S+@\S+)', message_text, re.IGNORECASE)
        
        if not amount_match:
            await update.message.reply_text("❌ Missing DEAL AMOUNT!")
            return
        
        amount = int(amount_match.group(1))
        buyer = buyer_match.group(1) if buyer_match else None
        seller = seller_match.group(1) if seller_match else None
        deal_detail = deal_detail_match.group(1) if deal_detail_match else "N/A"
        upi_id = upi_match.group(1) if upi_match else "venomxpay@naviaxis"
        
        if not buyer or not seller:
            await update.message.reply_text("❌ Need BUYER and SELLER!")
            return
        
        fee = calculate_fee(amount)
        total_with_fee = amount + fee
        qr_amount, random_paise = get_qr_amount(amount)
        deal_id = generate_deal_id()
        
        deals[deal_id] = {
            "deal_id": deal_id, "amount": amount, "fee": fee, "total_with_fee": total_with_fee,
            "qr_amount": qr_amount, "buyer": buyer, "seller": seller, "deal_detail": deal_detail,
            "upi_id": upi_id, "buyer_agreed": False, "seller_agreed": False, "status": "pending",
            "chat_id": chat_id, "created_at": str(datetime.now()), "buyer_id": None, "seller_id": None,
            "payment_received": False, "payment_txid": None
        }
        save_deals(deals)
        
        await update.message.reply_text(f"""
🔷 ESCROW DEAL CREATED 🔷

📋 DEAL ID: {deal_id}
💰 Amount: ₹{amount}
📊 Fee: ₹{fee}
💵 Total to Pay: ₹{total_with_fee}

👤 Buyer: @{buyer}
👥 Seller: @{seller}
📝 Details: {deal_detail}
💳 UPI: {upi_id}

⚠️ ESCROW FEES IS NON-REFUNDABLE

✅ @{buyer} - Type AGREE to confirm
✅ @{seller} - Type AGREE to confirm

🕐 Both must agree within 10 minutes!
""")
        
        await context.bot.send_message(chat_id=OWNER_ID, text=f"🆕 NEW DEAL!\nID: {deal_id}\n₹{amount}\n@{buyer} → @{seller}")
        return
    
    # ============ SECOND CHECK: IS THIS AN AGREE MESSAGE? ============
    agree_words = ['agree', 'agre', 'argee', 'agr', 'yes', 'done', 'ok', 'y']
    is_agree = any(word == text_lower or text_lower.startswith(word) for word in agree_words)
    
    if is_agree:
        print(f"[DEBUG] Detected AGREE from @{username}")
        
        # Find pending deal where user is buyer or seller
        found_deal = False
        for deal_id, deal in deals.items():
            if deal["status"] != "pending":
                continue
            
            if deal["buyer"].lower() == username:
                deal["buyer_agreed"] = True
                deal["buyer_id"] = user.id
                save_deals(deals)
                found_deal = True
                await update.message.reply_text(f"✅ @{user.username}, you agreed as BUYER for deal {deal_id}!")
                
                if deal["seller_agreed"]:
                    await process_both_agreed(context, deal_id, deal)
                break
            
            elif deal["seller"].lower() == username:
                deal["seller_agreed"] = True
                deal["seller_id"] = user.id
                save_deals(deals)
                found_deal = True
                await update.message.reply_text(f"✅ @{user.username}, you agreed as SELLER for deal {deal_id}!")
                
                if deal["buyer_agreed"]:
                    await process_both_agreed(context, deal_id, deal)
                break
        
        if not found_deal:
            await update.message.reply_text("❌ You don't have any pending deal. First create a deal using ESCROW DEAL FORM!")
        return

async def process_both_agreed(context, deal_id, deal):
    deal["status"] = "awaiting_payment"
    save_deals(deals)
    
    qr_amount = deal["qr_amount"]
    img_bytes = generate_qr(deal["upi_id"], qr_amount, deal_id)
    photo = InputFile(img_bytes, filename="qr.png")
    
    if deal.get("buyer_id"):
        await context.bot.send_photo(
            chat_id=deal["buyer_id"],
            photo=photo,
            caption=f"🔷 PAYMENT QR CODE\n\nDeal ID: {deal_id}\nOriginal: ₹{deal['amount']}\nFee: ₹{deal['fee']}\n\nPay this exact amount: ₹{qr_amount}\n\nAfter payment, bot will auto-detect.\n\nDON'T PAY IN DMS"
        )
    
    await context.bot.send_message(
        chat_id=deal["chat_id"],
        text=f"✅ BOTH AGREED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nBuyer @{deal['buyer']} has received QR code.\nPay EXACT ₹{qr_amount} for auto-verification!"
    )
    
    await context.bot.send_message(chat_id=OWNER_ID, text=f"✅ BOTH AGREED!\nDeal ID: {deal_id}\n@{deal['buyer']} and @{deal['seller']}")

# ============ SMS HANDLER ============
async def sms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    text = update.message.text
    sms_amount = extract_amount_from_sms(text)
    tx_id = extract_tx_id(text)
    
    if not sms_amount:
        await update.message.reply_text("❌ Could not extract amount from SMS.")
        return
    
    deal_id, deal = find_deal_by_qr_amount(sms_amount)
    
    if deal_id and deal:
        deal["payment_received"] = True
        deal["payment_txid"] = tx_id
        deal["status"] = "payment_confirmed"
        save_deals(deals)
        
        if tx_id:
            pending_tx[tx_id] = {"tx_id": tx_id, "amount": sms_amount, "deal_id": deal_id, "timestamp": str(datetime.now())}
            save_pending(pending_tx)
        
        await update.message.reply_text(f"✅ PAYMENT AUTO-VERIFIED!\n\nDeal ID: {deal_id}\nAmount: ₹{sms_amount}\nTXN: {tx_id}")
        
        if deal.get("buyer_id"):
            await context.bot.send_message(chat_id=deal["buyer_id"], text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nCONTINUE YOUR DEAL")
        
        if deal.get("seller_id"):
            await context.bot.send_message(chat_id=deal["seller_id"], text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nWaiting for release.")
        
        await context.bot.send_message(chat_id=OWNER_ID, text=f"💰 PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\nTXN: {tx_id}\n\nUse /release {deal_id}")
    else:
        if tx_id:
            pending_tx[tx_id] = {"tx_id": tx_id, "amount": sms_amount, "timestamp": str(datetime.now())}
            save_pending(pending_tx)
        
        await update.message.reply_text(f"⚠️ Payment detected but no matching deal!\n\nAmount: ₹{sms_amount}\nTXN: {tx_id}")

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
🔷 ESCROW BOT 🔷

Create a deal:
ESCROW DEAL FORM !!!

DEAL AMOUNT : 1000
BUYER : @username
SELLER : @username
DEAL DETAIL : Product
RLS UPI : your@upi

Then both type AGREE

Commands:
/status DEAL_ID - Check status
/cancel DEAL_ID - Cancel deal

Admin:
/release DEAL_ID - Release funds
/deals - List deals
/verify TXN DEAL - Manual verify

Developer: @iflexvenom
""")

async def release_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
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
        await update.message.reply_text("❌ Payment not confirmed!")
        return
    deal["status"] = "completed"
    save_deals(deals)
    if deal.get("seller_id"):
        await context.bot.send_message(chat_id=deal["seller_id"], text=f"✅ DEAL RELEASED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}")
    await context.bot.send_message(chat_id=deal["chat_id"], text=f"✅ DEAL COMPLETED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}")
    await update.message.reply_text(f"✅ Deal {deal_id} released!")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    if deal["status"] in ["completed", "payment_confirmed"]:
        await update.message.reply_text("❌ Cannot cancel now!")
        return
    deal["status"] = "cancelled"
    save_deals(deals)
    await update.message.reply_text(f"❌ Deal {deal_id} cancelled!")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /status DEAL_ID")
        return
    deal_id = context.args[0]
    deal = deals.get(deal_id)
    if not deal:
        await update.message.reply_text("❌ Deal not found!")
        return
    status_map = {"pending": "⏳ Waiting for agreement", "awaiting_payment": "💳 Waiting for payment", "payment_confirmed": "✅ Payment confirmed", "completed": "🎉 Completed", "cancelled": "❌ Cancelled"}
    await update.message.reply_text(f"📋 DEAL STATUS\n\nID: {deal_id}\nStatus: {status_map.get(deal['status'], deal['status'])}\nAmount: ₹{deal['amount']}\nBuyer: @{deal['buyer']}\nSeller: @{deal['seller']}")

async def deals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not deals:
        await update.message.reply_text("No deals.")
        return
    msg = "📋 DEALS\n\n"
    for deal_id, deal in list(deals.items())[-10:]:
        msg += f"{deal_id} - ₹{deal['amount']} - {deal['status']}\n@{deal['buyer']} → @{deal['seller']}\n\n"
    await update.message.reply_text(msg)

async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
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
    completed_tx.append({"tx_id": tx_id, "amount": amount, "deal_id": deal_id})
    save_completed(completed_tx)
    del pending_tx[tx_id]
    save_pending(pending_tx)
    await update.message.reply_text(f"✅ Payment verified for deal {deal_id}!")
    if deal.get("buyer_id"):
        await context.bot.send_message(chat_id=deal["buyer_id"], text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\n\nCONTINUE YOUR DEAL")

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    active = [d for d in deals.values() if d["status"] not in ["completed", "cancelled"]]
    completed = [d for d in deals.values() if d["status"] == "completed"]
    total_volume = sum([d["amount"] for d in completed])
    await update.message.reply_text(f"👑 ADMIN PANEL\n\nActive: {len(active)}\nCompleted: {len(completed)}\nVolume: ₹{total_volume}\n\nCommands:\n/release DEAL_ID\n/deals\n/verify TXN DEAL")

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("cancel", cancel_cmd))
    application.add_handler(CommandHandler("release", release_cmd))
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CommandHandler("deals", deals_cmd))
    application.add_handler(CommandHandler("verify", verify_cmd))
    application.add_handler(CommandHandler("addadmin", addadmin_cmd))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sms_handler))
    
    print("=" * 50)
    print("🔷 ESCROW BOT STARTED - FIXED")
    print(f"👑 Owner: {OWNER_ID}")
    print("=" * 50)
    
    application.run_polling()

if __name__ == "__main__":
    main()
