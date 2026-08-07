from decimal import Decimal
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

# Import the database function we need for the decorator
from database import get_group_id_by_chat_id

def require_linked_group(func):
    """Decorator to ensure the chat is linked to a group."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        group_id = await get_group_id_by_chat_id(chat_id)
        
        if not group_id:
            await update.message.reply_text("⚠️ *Group Not Linked*...", parse_mode="Markdown")
            return
            
        context.chat_data['group_id'] = group_id
        return await func(update, context)
    return wrapper

def simplify_debts(balances: list) -> list:
    """Takes a list of user balances and returns a simplified settlement plan."""
    # 1. Ensure balances are floats and separate them
    for b in balances:
        b['net_balance'] = float(b['net_balance'])

    # Debtors owe money (negative), sorted lowest to highest
    debtors = sorted([b for b in balances if b['net_balance'] < -0.01], key=lambda x: x['net_balance'])
    
    # Creditors are owed money (positive), sorted highest to lowest
    creditors = sorted([b for b in balances if b['net_balance'] > 0.01], key=lambda x: x['net_balance'], reverse=True)

    payments = []
    i, j = 0, 0

    # 2. Match Debtors to Creditors
    while i < len(debtors) and j < len(creditors):
        debtor = debtors[i]
        creditor = creditors[j]

        # Transfer the maximum possible amount between the two
        amount = min(abs(debtor['net_balance']), creditor['net_balance'])

        payments.append({
            'from': debtor['display_name'],
            'to': creditor['display_name'],
            'amount': float(round(amount, 2))
        })

        # Update their running balances
        debtors[i]['net_balance'] += amount
        creditors[j]['net_balance'] -= amount

        # If they hit $0 (accounting for floating point math), move to the next person
        if abs(debtors[i]['net_balance']) < 0.01:
            i += 1
        if abs(creditors[j]['net_balance']) < 0.01:
            j += 1

    return payments
