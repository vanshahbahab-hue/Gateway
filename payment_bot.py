import re
import json
import os
import asyncio
import threading
from flask import Flask
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ============ FLASK FOR RENDER PORT ============
flask_app = Flask(__name__)

@flask_app.route('/')
@flask_app.route('/health')
def health():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host='0.0.0.0', port=port)

# ============ CONFIG ============
BOT_TOKEN = "8679581798:AAGZtycapDdwpwYR8ro5M4xZNFiIR4QuetI"
OWNER_ID = 8586849798

# Files
PENDING_FILE = "pending.json"
COMPLETED_FILE = "completed.json"

# ============ FILE FUNCTIONS ============
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

pending_tx = load_pending()
completed_tx = load_completed()

# ============ HELPERS ============
def extract_tx_id(text):
    patterns = [
        r'Txn ID[:\s]*(\d+)',
        r'Transaction ID[:\s]*(\d+)',
        r'TX[:\s]*(\d+)',
        r'ID[:\s]*(\d+)',
        r'Tx[:\s]*(\d+)',
        r'(\d{10,15})'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def extract_amount(text):
    patterns = [
        r'Rs\.?\s*(\d+\.?\d*)',
        r'₹\s*(\d+\.?\d*)',
        r'debited\s*Rs\.?\s*(\d+\.?\d*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None

# ============ BOT COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✅ Verify Payment", callback_data="verify")],
        [InlineKeyboardButton("📜 My Payments", callback_data="history")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    await update.message.reply_text(
        "💳 **PAYMENT VERIFICATION BOT**\n\n"
        "After payment, send: `/verify YOUR_TRANSACTION_ID`\n\n"
        "Or tap button below 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide Transaction ID.\n\n"
            "Usage: `/verify 652832203385`",
            parse_mode="Markdown"
        )
        return
    
    tx_id = context.args[0]
    
    # Check in pending
    if tx_id in pending_tx:
        amount = pending_tx[tx_id].get('amount', 'Unknown')
        
        # Move to completed
        completed_tx.append({
            "user_id": user_id,
            "tx_id": tx_id,
            "amount": amount,
            "verified_at": str(datetime.now())
        })
        save_completed(completed_tx)
        
        # Remove from pending
        del pending_tx[tx_id]
        save_pending(pending_tx)
        
        await update.message.reply_text(
            f"✅ **PAYMENT RECEIVED!** ✅\n\n"
            f"💰 Amount: ₹{amount}\n"
            f"🔖 Transaction ID: `{tx_id}`\n\n"
            f"🎉 **CONTINUE YOUR DEAL** 🎉",
            parse_mode="Markdown"
        )
        
        # Notify owner
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"✅ User {user_id} verified payment: {tx_id} (₹{amount})"
        )
    
    elif any(p['tx_id'] == tx_id for p in completed_tx):
        await update.message.reply_text(
            f"❌ **TRANSACTION ALREADY USED**\n\n"
            f"Transaction ID `{tx_id}` has already been verified by another user.",
            parse_mode="Markdown"
        )
    
    else:
        await update.message.reply_text(
            f"❌ **INCORRECT TRANSACTION ID**\n\n"
            f"Transaction ID `{tx_id}` not found in our records.\n\n"
            f"📝 **Possible reasons:**\n"
            f"• Payment hasn't been processed yet\n"
            f"• You entered the wrong ID\n"
            f"• SMS hasn't been forwarded to bot yet\n\n"
            f"💡 Wait 2-3 minutes and try again.",
            parse_mode="Markdown"
        )

async def sms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle SMS forwarded from SMS Forwarder app"""
    user_id = update.effective_user.id
    
    # Only owner can forward SMS
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized to forward SMS.")
        return
    
    text = update.message.text
    tx_id = extract_tx_id(text)
    amount = extract_amount(text)
    
    if not tx_id:
        await update.message.reply_text(
            f"❌ Could not extract Transaction ID from SMS.\n\n"
            f"📱 Raw SMS:\n`{text[:300]}`",
            parse_mode="Markdown"
        )
        return
    
    pending_tx[tx_id] = {
        "tx_id": tx_id,
        "amount": amount,
        "raw_sms": text[:300],
        "timestamp": str(datetime.now())
    }
    save_pending(pending_tx)
    
    await update.message.reply_text(
        f"✅ **PAYMENT SMS DETECTED!** ✅\n\n"
        f"🔖 **Transaction ID:** `{tx_id}`\n"
        f"💰 **Amount:** ₹{amount if amount else 'Unknown'}\n\n"
        f"📝 User can now verify using:\n`/verify {tx_id}`",
        parse_mode="Markdown"
    )

async def add_tx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Manually add transaction - /addtx TXID AMOUNT"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Only owner can use this.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/addtx TXN_ID AMOUNT`\n\n"
            "Example: `/addtx 652832203385 20`",
            parse_mode="Markdown"
        )
        return
    
    tx_id = context.args[0]
    try:
        amount = float(context.args[1])
    except:
        await update.message.reply_text("❌ Invalid amount.")
        return
    
    pending_tx[tx_id] = {
        "tx_id": tx_id,
        "amount": amount,
        "added_by": "admin",
        "timestamp": str(datetime.now())
    }
    save_pending(pending_tx)
    
    await update.message.reply_text(
        f"✅ **Transaction Added!**\n\n"
        f"🔖 TXN ID: `{tx_id}`\n"
        f"💰 Amount: ₹{amount}\n\n"
        f"User can now verify using `/verify {tx_id}`",
        parse_mode="Markdown"
    )

async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Check pending transactions"""
    if update.effective_user.id != OWNER_ID:
        return
    
    if not pending_tx:
        await update.message.reply_text("📭 No pending transactions.")
        return
    
    msg = "📋 **PENDING TRANSACTIONS**\n\n"
    for tx_id, data in pending_tx.items():
        msg += f"🔖 `{tx_id}` - ₹{data.get('amount', '?')}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def force_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Force verify user - /fverify USER_ID TXN_ID"""
    if update.effective_user.id != OWNER_ID:
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/fverify USER_ID TXN_ID`\n\nExample: `/fverify 8586849798 652832203385`", parse_mode="Markdown")
        return
    
    try:
        user_id = int(context.args[0])
        tx_id = context.args[1]
    except:
        await update.message.reply_text("❌ Invalid USER_ID")
        return
    
    if tx_id in pending_tx:
        amount = pending_tx[tx_id].get('amount', 'Unknown')
        
        completed_tx.append({
            "user_id": user_id,
            "tx_id": tx_id,
            "amount": amount,
            "verified_at": str(datetime.now())
        })
        save_completed(completed_tx)
        del pending_tx[tx_id]
        save_pending(pending_tx)
        
        await update.message.reply_text(f"✅ User {user_id} verified: {tx_id} (₹{amount})")
        
        # Notify user
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ **PAYMENT RECEIVED!** ✅\n\n💰 Amount: ₹{amount}\n🔖 TXN: `{tx_id}`\n\n**CONTINUE YOUR DEAL** 🚀",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(f"❌ TXN {tx_id} not found in pending")

async def my_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User: Check own payment history"""
    user_id = update.effective_user.id
    
    user_payments = [p for p in completed_tx if p['user_id'] == user_id]
    
    if not user_payments:
        await update.message.reply_text(
            "📭 **No payment history found**\n\n"
            "To verify a payment, send:\n`/verify YOUR_TRANSACTION_ID`",
            parse_mode="Markdown"
        )
        return
    
    msg = "📜 **YOUR PAYMENT HISTORY** 📜\n\n"
    for p in user_payments[-5:]:
        msg += f"🔖 `{p['tx_id']}` - ₹{p['amount']}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Bot statistics"""
    if update.effective_user.id != OWNER_ID:
        return
    
    total_amount = sum([p.get('amount', 0) for p in completed_tx])
    
    msg = f"📊 **BOT STATISTICS**\n\n"
    msg += f"⏳ Pending: {len(pending_tx)}\n"
    msg += f"✅ Completed: {len(completed_tx)}\n"
    msg += f"💰 Total Amount: ₹{total_amount}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
❓ **HOW TO USE**

1️⃣ **Make payment** as instructed
2️⃣ **Wait for SMS** from your bank
3️⃣ **Send Transaction ID** using:
   `/verify YOUR_TRANSACTION_ID`

📝 **Example:** `/verify 652832203385`

✅ **Valid ID:** "PAYMENT RECEIVED - CONTINUE YOUR DEAL"
❌ **Invalid ID:** "INCORRECT TRANSACTION ID"

👑 **Admin Commands:**
• `/addtx TXID AMOUNT` - Add transaction manually
• `/pending` - Check pending transactions
• `/fverify USER_ID TXID` - Force verify user
• `/stats` - Bot statistics
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "verify":
        await query.message.reply_text(
            "📝 Send your Transaction ID:\n\n`/verify YOUR_TRANSACTION_ID`\n\nExample: `/verify 652832203385`",
            parse_mode="Markdown"
        )
    elif query.data == "history":
        await my_payments(update, context)
    elif query.data == "help":
        await help_command(update, context)

# ============ MAIN ============
def main():
    # Start Flask for Render
    threading.Thread(target=run_flask, daemon=True).start()
    
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # User commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("verify", verify_command))
    application.add_handler(CommandHandler("payments", my_payments))
    application.add_handler(CommandHandler("help", help_command))
    
    # Admin commands
    application.add_handler(CommandHandler("addtx", add_tx_command))
    application.add_handler(CommandHandler("pending", pending_command))
    application.add_handler(CommandHandler("fverify", force_verify))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sms_handler))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    print("=" * 50)
    print("🤖 PAYMENT VERIFICATION BOT STARTED")
    print(f"👑 Owner ID: {OWNER_ID}")
    print(f"📁 Pending: {len(pending_tx)}")
    print(f"✅ Completed: {len(completed_tx)}")
    print("=" * 50)
    
    application.run_polling()

if __name__ == "__main__":
    main()
