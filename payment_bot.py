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

deals = load_deals()
pending_tx = load_pending()

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
    
    # ============ ESCROW FORM DETECTION ============
    if re.search(r'ESCROW\s*DEAL\s*FORM', message_text, re.IGNORECASE):
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
            "payment_received": False, "payment_txid": None, "seller_upi": None, "release_requested": False
        }
        save_deals(deals)
        
        await update.message.reply_text(f"""
🔷 ESCROW DEAL CREATED 🔷

DEAL ID: `{deal_id}`

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
""", parse_mode="Markdown")
        
        await context.bot.send_message(chat_id=OWNER_ID, text=f"🆕 NEW DEAL!\nID: {deal_id}\n₹{amount}\n@{buyer} → @{seller}")
        return
    
    # ============ AGREE DETECTION ============
    agree_words = ['agree', 'agre', 'argee', 'agr', 'yes', 'done', 'ok', 'y']
    is_agree = any(word == text_lower or text_lower.startswith(word) for word in agree_words)
    
    if is_agree:
        for deal_id, deal in deals.items():
            if deal["status"] != "pending":
                continue
            
            if deal["buyer"].lower() == username:
                deal["buyer_agreed"] = True
                deal["buyer_id"] = user.id
                save_deals(deals)
                await update.message.reply_text(f"✅ @{user.username}, you agreed as BUYER for deal `{deal_id}`!", parse_mode="Markdown")
                
                if deal["seller_agreed"]:
                    await process_both_agreed(context, deal_id, deal)
                return
            
            elif deal["seller"].lower() == username:
                deal["seller_agreed"] = True
                deal["seller_id"] = user.id
                save_deals(deals)
                await update.message.reply_text(f"✅ @{user.username}, you agreed as SELLER for deal `{deal_id}`!", parse_mode="Markdown")
                
                if deal["buyer_agreed"]:
                    await process_both_agreed(context, deal_id, deal)
                return
        
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
            caption=f"🔷 PAYMENT QR CODE\n\nDeal ID: `{deal_id}`\nOriginal Amount: ₹{deal['amount']}\nFee: ₹{deal['fee']}\n\nPay this exact amount: ₹{qr_amount}\n\nAfter payment, bot will auto-detect.\n\n❌ DON'T PAY IN DMS",
            parse_mode="Markdown"
        )
    
    await context.bot.send_message(
        chat_id=deal["chat_id"],
        text=f"✅ BOTH AGREED!\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\n\nBuyer @{deal['buyer']} has received QR code.\nPay EXACT ₹{qr_amount} for auto-verification!",
        parse_mode="Markdown"
    )
    
    await context.bot.send_message(chat_id=OWNER_ID, text=f"✅ BOTH AGREED!\nDeal ID: {deal_id}\n@{deal['buyer']} and @{deal['seller']}")

# ============ BUYER RELEASE COMMAND ============
async def buyer_release(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if len(context.args) < 1:
        await update.message.reply_text("❌ Usage: `/release DEAL_ID`\n\nExample: `/release ABC123XYZ`", parse_mode="Markdown")
        return
    
    deal_id = context.args[0].upper()
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text(f"❌ Deal `{deal_id}` not found!", parse_mode="Markdown")
        return
    
    if user_id != deal.get("buyer_id"):
        await update.message.reply_text("❌ Only buyer can release the deal after receiving product!")
        return
    
    if deal["status"] != "payment_confirmed":
        await update.message.reply_text(f"❌ Deal `{deal_id}` is not in payment confirmed status!\nCurrent status: {deal['status']}", parse_mode="Markdown")
        return
    
    if deal.get("release_requested"):
        await update.message.reply_text("❌ Release already requested! Waiting for seller's UPI.")
        return
    
    deal["release_requested"] = True
    save_deals(deals)
    
    await update.message.reply_text(f"✅ Release requested for deal `{deal_id}`!\n\nSeller @{deal['seller']} will now provide their UPI ID.", parse_mode="Markdown")
    
    if deal.get("seller_id"):
        await context.bot.send_message(
            chat_id=deal["seller_id"],
            text=f"🔷 RELEASE REQUEST RECEIVED!\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\n\nBuyer @{deal['buyer']} has confirmed receiving the product.\n\nPlease send your UPI ID to receive payment:\n`/sendupi {deal_id} YOUR_UPI_ID`\n\nExample: `/sendupi {deal_id} yourname@okhdfcbank`",
            parse_mode="Markdown"
        )

async def send_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if len(context.args) < 2:
        await update.message.reply_text("❌ Usage: `/sendupi DEAL_ID UPI_ID`\n\nExample: `/sendupi ABC123XYZ seller@okhdfcbank`", parse_mode="Markdown")
        return
    
    deal_id = context.args[0].upper()
    upi_id = context.args[1]
    
    if not re.match(r'^[\w\.\-]+@[\w\.\-]+$', upi_id):
        await update.message.reply_text("❌ Invalid UPI ID! Format: name@bank")
        return
    
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text(f"❌ Deal `{deal_id}` not found!", parse_mode="Markdown")
        return
    
    if user_id != deal.get("seller_id"):
        await update.message.reply_text("❌ Only seller can send UPI ID!")
        return
    
    if not deal.get("release_requested"):
        await update.message.reply_text("❌ Buyer has not requested release yet!")
        return
    
    deal["seller_upi"] = upi_id
    save_deals(deals)
    
    await update.message.reply_text(f"✅ UPI ID received for deal `{deal_id}`!\n\nWaiting for owner to complete the transfer.\n\n⏰ Owner will process within 10-20 minutes.", parse_mode="Markdown")
    
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"💰 RELEASE REQUEST PENDING!\n\nDeal ID: {deal_id}\nAmount: ₹{deal['amount']}\nBuyer: @{deal['buyer']}\nSeller: @{deal['seller']}\nSeller UPI: {upi_id}\n\nUse `/complete {deal_id}` to mark deal as completed after transferring money to seller."
    )

async def complete_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only owner can complete the deal!")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("❌ Usage: `/complete DEAL_ID`", parse_mode="Markdown")
        return
    
    deal_id = context.args[0].upper()
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text(f"❌ Deal `{deal_id}` not found!", parse_mode="Markdown")
        return
    
    if deal["status"] != "payment_confirmed":
        await update.message.reply_text(f"❌ Deal `{deal_id}` payment not confirmed yet!", parse_mode="Markdown")
        return
    
    if not deal.get("release_requested"):
        await update.message.reply_text("❌ Buyer hasn't requested release yet!")
        return
    
    if not deal.get("seller_upi"):
        await update.message.reply_text("❌ Seller hasn't provided UPI ID yet!")
        return
    
    deal["status"] = "completed"
    save_deals(deals)
    
    await context.bot.send_message(
        chat_id=deal["chat_id"],
        text=f"✅ DEAL COMPLETED! ✅\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\nBuyer: @{deal['buyer']}\nSeller: @{deal['seller']}\n\n🎉 Transaction successfully completed!",
        parse_mode="Markdown"
    )
    
    if deal.get("buyer_id"):
        await context.bot.send_message(
            chat_id=deal["buyer_id"],
            text=f"✅ DEAL COMPLETED! ✅\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\n\nThank you for using ESCROW BOT!",
            parse_mode="Markdown"
        )
    
    if deal.get("seller_id"):
        await context.bot.send_message(
            chat_id=deal["seller_id"],
            text=f"✅ DEAL COMPLETED! ✅\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\n\nFunds have been transferred to your UPI: {deal['seller_upi']}\n\nThank you for using ESCROW BOT!",
            parse_mode="Markdown"
        )
    
    await update.message.reply_text(f"✅ Deal `{deal_id}` marked as completed!", parse_mode="Markdown")

# ============ AUTO SMS VERIFICATION ============
async def sms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    
    text = update.message.text
    sms_amount = extract_amount_from_sms(text)
    
    if not sms_amount:
        await update.message.reply_text("❌ Could not extract amount from SMS.")
        return
    
    deal_id, deal = find_deal_by_qr_amount(sms_amount)
    
    if deal_id and deal:
        deal["payment_received"] = True
        deal["status"] = "payment_confirmed"
        save_deals(deals)
        
        await update.message.reply_text(f"✅ PAYMENT AUTO-VERIFIED!\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}", parse_mode="Markdown")
        
        if deal.get("buyer_id"):
            await context.bot.send_message(
                chat_id=deal["buyer_id"],
                text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\n\nAfter receiving product, use:\n`/release {deal_id}`",
                parse_mode="Markdown"
            )
        
        if deal.get("seller_id"):
            await context.bot.send_message(
                chat_id=deal["seller_id"],
                text=f"✅ PAYMENT RECEIVED!\n\nDeal ID: `{deal_id}`\nAmount: ₹{deal['amount']}\n\nWaiting for buyer to confirm product receipt.",
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(f"⚠️ Payment detected but no matching deal!\nAmount: ₹{sms_amount}")

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
🔷 ESCROW BOT 🔷

I help secure your deals with auto-verification!

Use /help to see how to use this bot.
""")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
📚 HOW TO USE ESCROW BOT

━━━━━━━━━━━━━━━━━━
🔹 CREATE A DEAL
━━━━━━━━━━━━━━━━━━

Type this in ANY GROUP (where bot is admin):

ESCROW DEAL FORM !!!

DEAL AMOUNT : 1000
BUYER : @buyer_username
SELLER : @seller_username
DEAL DETAIL : Product name
RLS UPI : your@upi

━━━━━━━━━━━━━━━━━━
🔹 AGREE TO DEAL
━━━━━━━━━━━━━━━━━━

Both buyer and seller type: AGREE

━━━━━━━━━━━━━━━━━━
🔹 MAKE PAYMENT
━━━━━━━━━━━━━━━━━━

1. Buyer receives QR code
2. Pay EXACT amount shown
3. SMS auto-verifies payment

━━━━━━━━━━━━━━━━━━
🔹 AFTER PRODUCT
━━━━━━━━━━━━━━━━━━

After receiving product, buyer types:
/release DEAL_ID

Seller provides UPI:
/sendupi DEAL_ID UPI_ID

━━━━━━━━━━━━━━━━━━
🔹 CHECK STATUS
━━━━━━━━━━━━━━━━━━

/status DEAL_ID

━━━━━━━━━━━━━━━━━━
🔹 CANCEL DEAL
━━━━━━━━━━━━━━━━━━

/cancel DEAL_ID

━━━━━━━━━━━━━━━━━━
👑 Developer: @iflexvenom
""")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: `/status DEAL_ID`", parse_mode="Markdown")
        return
    
    deal_id = context.args[0].upper()
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text(f"❌ Deal `{deal_id}` not found!", parse_mode="Markdown")
        return
    
    status_map = {
        "pending": "⏳ Waiting for agreement",
        "awaiting_payment": "💳 Waiting for payment",
        "payment_confirmed": "✅ Payment confirmed, waiting for product delivery",
        "completed": "🎉 Deal completed",
        "cancelled": "❌ Cancelled"
    }
    
    await update.message.reply_text(f"""
📋 DEAL STATUS

Deal ID: `{deal_id}`
Status: {status_map.get(deal['status'], deal['status'])}
Amount: ₹{deal['amount']}
Buyer: @{deal['buyer']}
Seller: @{deal['seller']}
""", parse_mode="Markdown")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: `/cancel DEAL_ID`", parse_mode="Markdown")
        return
    
    deal_id = context.args[0].upper()
    deal = deals.get(deal_id)
    
    if not deal:
        await update.message.reply_text(f"❌ Deal `{deal_id}` not found!", parse_mode="Markdown")
        return
    
    if user_id not in ADMIN_IDS and user_id != deal.get("buyer_id") and user_id != deal.get("seller_id"):
        await update.message.reply_text("❌ Not authorized!")
        return
    
    if deal["status"] in ["completed", "payment_confirmed"]:
        await update.message.reply_text("❌ Cannot cancel now!")
        return
    
    deal["status"] = "cancelled"
    save_deals(deals)
    await update.message.reply_text(f"❌ Deal `{deal_id}` cancelled!", parse_mode="Markdown")

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    
    active = [d for d in deals.values() if d["status"] not in ["completed", "cancelled"]]
    completed = [d for d in deals.values() if d["status"] == "completed"]
    total_volume = sum([d["amount"] for d in completed])
    
    await update.message.reply_text(f"""
👑 ADMIN PANEL

Active Deals: {len(active)}
Completed Deals: {len(completed)}
Total Volume: ₹{total_volume}

Commands:
/complete DEAL_ID - Mark deal completed
""")

# ============ MAIN ============
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # User commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("cancel", cancel_cmd))
    application.add_handler(CommandHandler("release", buyer_release))
    application.add_handler(CommandHandler("sendupi", send_upi))
    
    # Admin commands
    application.add_handler(CommandHandler("complete", complete_deal))
    application.add_handler(CommandHandler("admin", admin_cmd))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sms_handler))
    
    print("=" * 50)
    print("🔷 ESCROW BOT STARTED - FULLY FIXED")
    print(f"👑 Owner: {OWNER_ID}")
    print("=" * 50)
    
    application.run_polling()

if __name__ == "__main__":
    main()
