import re
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ============ CONFIG ============
BOT_TOKEN = "8679581798:AAGZtycapDdwpwYR8ro5M4xZNFiIR4QuetI"  # Apna bot token daalo
OWNER_ID = 8586849798   # Apna Telegram ID daalo

# Files to store data
PENDING_FILE = "pending_tx.json"
COMPLETED_FILE = "completed_tx.json"

# Load/Save functions
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

# Store pending transactions from SMS
pending_tx = load_pending()
completed_tx = load_completed()

# ============ EXTRACT TRANSACTION ID FROM SMS ============
def extract_tx_id(sms_text):
    """Extract transaction ID from SMS"""
    patterns = [
        r'Txn ID[:\s]*(\d+)',
        r'Transaction ID[:\s]*(\d+)',
        r'Tx[:\s]*(\d+)',
        r'ID[:\s]*(\d+)',
        r'(\d{10,15})',  # Any 10-15 digit number (last resort)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, sms_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def extract_amount(sms_text):
    """Extract amount from SMS"""
    patterns = [
        r'Rs\.?\s*(\d+\.?\d*)',
        r'₹\s*(\d+\.?\d*)',
        r'debited\s*Rs\.?\s*(\d+\.?\d*)',
        r'credited\s*Rs\.?\s*(\d+\.?\d*)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, sms_text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None

def extract_type(sms_text):
    """Check if debited or credited"""
    if 'debited' in sms_text.lower():
        return 'DEBITED'
    elif 'credited' in sms_text.lower():
        return 'CREDITED'
    return 'UNKNOWN'

# ============ TELEGRAM HANDLERS ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    keyboard = [
        [InlineKeyboardButton("💰 Check Payment", callback_data="check")],
        [InlineKeyboardButton("📜 Transaction History", callback_data="history")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "💳 **PAYMENT VERIFICATION BOT** 💳\n\n"
        "I automatically detect payment SMS and store transaction IDs.\n\n"
        "🔹 **To verify your payment:** Send the Transaction ID\n"
        "🔹 **Example:** `/verify 652832203385`\n\n"
        "Or tap buttons below 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def sms_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle SMS forwarded from Android app"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Only owner's messages are considered as SMS forwards
    if user_id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized to forward SMS.")
        return
    
    # Extract data from SMS
    tx_id = extract_tx_id(message_text)
    amount = extract_amount(message_text)
    tx_type = extract_type(message_text)
    
    if not tx_id:
        await update.message.reply_text(
            "❌ Could not extract Transaction ID from this SMS.\n\n"
            f"📱 Raw SMS:\n`{message_text[:200]}`",
            parse_mode="Markdown"
        )
        return
    
    # Store pending transaction
    pending_tx[tx_id] = {
        "tx_id": tx_id,
        "amount": amount,
        "type": tx_type,
        "raw_sms": message_text,
        "status": "pending",
        "timestamp": str(update.message.date)
    }
    save_pending(pending_tx)
    
    # Reply to owner
    response = f"""
✅ **PAYMENT SMS DETECTED!**

🔖 **Transaction ID:** `{tx_id}`
💰 **Amount:** ₹{amount if amount else 'Unknown'}
📊 **Type:** {tx_type}

📝 **Raw SMS:**
`{message_text[:200]}`

➡️ User can now verify using `/verify {tx_id}`
"""
    await update.message.reply_text(response, parse_mode="Markdown")

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User sends transaction ID to verify"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide Transaction ID.\n\n"
            "Usage: `/verify TRANSACTION_ID`\n\n"
            "Example: `/verify 652832203385`",
            parse_mode="Markdown"
        )
        return
    
    tx_id = context.args[0]
    
    # Check if transaction exists in pending
    if tx_id in pending_tx:
        tx_data = pending_tx[tx_id]
        amount = tx_data.get('amount', 'Unknown')
        
        # Mark as completed
        completed_tx.append({
            "user_id": user_id,
            "tx_id": tx_id,
            "amount": amount,
            "verified_at": str(update.message.date)
        })
        save_completed(completed_tx)
        
        # Remove from pending
        del pending_tx[tx_id]
        save_pending(pending_tx)
        
        # Success response
        response = f"""
✅ **PAYMENT RECEIVED!** ✅

💰 **Amount:** ₹{amount}
🔖 **Transaction ID:** `{tx_id}`

🎉 **CONTINUE YOUR DEAL** 🎉

Your payment has been verified successfully!
"""
        await update.message.reply_text(response, parse_mode="Markdown")
        
        # Notify owner
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"✅ User {user_id} verified payment: {tx_id} (₹{amount})"
        )
    
    else:
        # Check if already completed
        already_used = False
        for comp in completed_tx:
            if comp['tx_id'] == tx_id:
                already_used = True
                break
        
        if already_used:
            response = f"""
❌ **TRANSACTION ALREADY USED** ❌

Transaction ID `{tx_id}` has already been verified by another user.

Please check your transaction ID and try again.
"""
        else:
            response = f"""
❌ **INCORRECT TRANSACTION ID** ❌

Transaction ID `{tx_id}` not found in our records.

📝 **Possible reasons:**
• The payment hasn't been processed yet
• You entered the wrong ID
• The SMS hasn't been forwarded to bot yet

💡 **Solution:** Wait 2-3 minutes and try again.
"""
        await update.message.reply_text(response, parse_mode="Markdown")

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user has any verified payments"""
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
    for p in user_payments[-5:]:  # Last 5 payments
        msg += f"🔖 `{p['tx_id']}` - ₹{p['amount']}\n"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin stats command"""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    
    msg = f"""
📊 **BOT STATISTICS**

⏳ **Pending Transactions:** {len(pending_tx)}
✅ **Completed Transactions:** {len(completed_tx)}
💰 **Total Amount Verified:** ₹{sum([p['amount'] for p in completed_tx if p.get('amount')])}

📋 **Pending IDs:**
{chr(10).join([f'• {tx_id}' for tx_id in pending_tx.keys()]) if pending_tx else 'None'}
"""
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "check":
        await check_command(update, context)
    elif query.data == "history":
        user_id = query.from_user.id
        user_payments = [p for p in completed_tx if p['user_id'] == user_id]
        if user_payments:
            msg = "📜 **Your Payments:**\n\n"
            for p in user_payments[-5:]:
                msg += f"🔖 `{p['tx_id']}` - ₹{p['amount']}\n"
            await query.edit_message_text(msg, parse_mode="Markdown")
        else:
            await query.edit_message_text("📭 No payment history found.")
    elif query.data == "help":
        help_text = """
❓ **HOW TO USE**

1️⃣ **Make payment** as instructed
2️⃣ **Wait for SMS** from your bank
3️⃣ **Send Transaction ID** using `/verify TXID`
4️⃣ **Get confirmation** "PAYMENT RECEIVED - CONTINUE YOUR DEAL"

📝 **Example:** `/verify 652832203385`
"""
        await query.edit_message_text(help_text, parse_mode="Markdown")

# ============ MAIN ============
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("verify", verify_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("stats", stats_command))
    
    # Message handler for SMS forwards (only from owner)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, sms_forward_handler))
    
    # Callback handler for inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    print("🤖 Payment Verification Bot Started!")
    print(f"Owner ID: {OWNER_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()