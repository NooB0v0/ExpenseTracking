import re
import asyncio
from decimal import Decimal
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

# Import our custom tools from our other files!
from config import supabase, logger
from database import get_or_create_profile, get_group_id_by_chat_id
from utils import require_linked_group, simplify_debts

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start or /help command."""
    welcome_text = (
        "👋 *Group Expense Tracker*\n"
        "────────────────\n"
        "🔗 `/linkgroup` - Connect chat to a ledger\n"
        "💸 `/split` - Record a shared expense\n"
        "🤝 `/settle` - Pay someone back\n"
        "📊 `/balances` - See who owes who\n"
        "➕ `/add` - Add members manually\n\n"
        "_Tip: Use /split 120 @joel by @sarah Dinner_"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def linkgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /linkgroup to interactively link a Telegram group to Supabase."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("⚠️ Please run this command inside a Telegram group chat.")
        return

    # Check if this chat is ALREADY linked (Async)
    existing = await asyncio.to_thread(
        lambda: supabase.table("groups").select("name").eq("telegram_chat_id", str(chat.id)).execute()
    )
    if len(existing.data) > 0:
        await update.message.reply_text(f"ℹ️ This chat is already linked to group: *{existing.data[0]['name']}*", parse_mode="Markdown")
        return

    # Get caller profile ID (Async)
    caller_id_str = str(user.id)
    caller_identifier = f"@{user.username}" if user.username else f"User_{caller_id_str}"

    caller_profile_id = await get_or_create_profile(identifier=caller_identifier, telegram_id=caller_id_str)

    # Fetch groups this user belongs to that aren't linked to Telegram yet (Async)
    response = await asyncio.to_thread(
        lambda: supabase.table("group_members")
        .select("groups(id, name, telegram_chat_id)")
        .eq("profile_id", caller_profile_id)
        .execute()
    )

    unlinked_groups = []
    if response.data:
        unlinked_groups = [
            item['groups'] for item in response.data 
            if item.get('groups') and item['groups'].get('telegram_chat_id') is None
        ]

    # Build Telegram Inline Buttons
    keyboard = []
    for g in unlinked_groups:
        keyboard.append([InlineKeyboardButton(f"📁 {g['name']}", callback_data=f"link_{g['id']}")])
    
    keyboard.append([InlineKeyboardButton(f"➕ Create new group '{chat.title}'", callback_data="link_NEW")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Select an app group to link to *{chat.title}*:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


@require_linked_group
async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows a user to self-enroll into the linked group."""
    chat_id = update.effective_chat.id
    user = update.effective_user

   # 1. Check if group is linked
    group_id = await get_group_id_by_chat_id(chat_id)

    # 2. Get or Create Profile
    caller_id_str = str(user.id)
    caller_identifier = f"@{user.username}" if user.username else f"User_{caller_id_str}"
    profile_id = await get_or_create_profile(identifier=caller_identifier, telegram_id=caller_id_str)

    # 3. Add to Group Members
    try:
        await asyncio.to_thread(
            lambda: supabase.table("group_members").insert({
                "group_id": group_id,
                "profile_id": profile_id
            }).execute()
        )
        display_name = user.username or user.first_name
        await update.message.reply_text(f"👋 Welcome to the group, {display_name}!")
    except Exception as e:
        # If it's a duplicate key error, they are already in the group
        await update.message.reply_text("ℹ️ You are already in this group.")


@require_linked_group
async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows a user to add others (Telegram users or Guests) to the group."""
    text = update.message.text
    chat_id = update.effective_chat.id

    # 1. Check if group is linked
    group_id = await get_group_id_by_chat_id(chat_id)

    # 2. Extract participants
    participants_raw = re.findall(r'[@+]\w+', text)
    if not participants_raw:
        await update.message.reply_text(
            "⚠️ *Missing Names*\n"
            "Please specify who to add using @ or +.\n\n"
            "_Example:_ `/add @joel +Sister`", 
            parse_mode="Markdown"
        )
        return

    # 3. Resolve Profiles (Concurrent)
    tasks = [get_or_create_profile(identifier=user) for user in participants_raw]
    new_member_ids = await asyncio.gather(*tasks)

    # 4. Enroll Users (Concurrent)
    added_count = 0
    async def enroll_user(profile_id):
        nonlocal added_count
        try:
            await asyncio.to_thread(
                lambda: supabase.table("group_members").insert({
                    "group_id": group_id,
                    "profile_id": profile_id
                }).execute()
            )
            added_count += 1
        except Exception:
            pass # Already a member

    await asyncio.gather(*[enroll_user(pid) for pid in list(dict.fromkeys(new_member_ids))])

    # 5. Reply
    if added_count > 0:
        # Clean the symbols out of the names for display
        clean_names = [name.replace('@', '').replace('+', '') for name in participants_raw]
        names_str = ", ".join(clean_names)
        
        await update.message.reply_text(f"✅ Added **{added_count}** member(s): {names_str}", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ Everyone mentioned is already in the group.")


async def link_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline button clicks for linking or creating a group."""
    query = update.callback_query
    await query.answer()

    chat_id = str(update.effective_chat.id)
    chat_title = update.effective_chat.title or "Telegram Group"
    data = query.data
    user = query.from_user

    # Safely construct the identifier even if they don't have a Telegram @username
    caller_id_str = str(user.id)
    caller_identifier = f"@{user.username}" if user.username else f"User_{caller_id_str}"
    
    # Pass both identifier and telegram_id to trigger self-healing
    caller_profile_id = await get_or_create_profile(identifier=caller_identifier, telegram_id=caller_id_str)
    try:
        if data == "link_NEW":
            # Create a brand new group in Supabase (Async)
            new_group = await asyncio.to_thread(
                lambda: supabase.table("groups").insert({
                    "name": chat_title,
                    "created_by": caller_profile_id,
                    "telegram_chat_id": chat_id
                }).execute()
            )
            
            group_id = new_group.data[0]['id']
            
            # Add creator to group_members (Async)
            await asyncio.to_thread(
                lambda: supabase.table("group_members").insert({
                    "group_id": group_id,
                    "profile_id": caller_profile_id
                }).execute()
            )
            await query.edit_message_text(f"🎉 Created and linked new group *{chat_title}*!", parse_mode="Markdown")

        elif data.startswith("link_"):
            target_group_id = data.replace("link_", "")
            
            # Link existing Supabase group (Async)
            await asyncio.to_thread(
                lambda: supabase.table("groups").update({
                    "telegram_chat_id": chat_id
                }).eq("id", target_group_id).execute()
            )
            await query.edit_message_text("🎉 Group linked successfully!", parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error in link_button_callback: {e}")
        await query.edit_message_text("❌ Failed to link group. Please try again.")


@require_linked_group
async def split_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /split command with Smart Accountant and Guest support."""

    # --- ADD THIS NEW BLOCK ---
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "⚠️ *Invalid Format*\n"
            "Please provide an amount right after the command.\n\n"
            "_Example:_ `/split 120 @joel by @sarah Dinner`", 
            parse_mode="Markdown"
        )
        return

    try:
        # We strictly force the FIRST argument to be the amount
        total_amount = float(context.args[0]) 
    except ValueError:
        await update.message.reply_text("⚠️ The amount must be a valid number (e.g., 120 or 12.50).")
        return
    # --------------------------

    text = update.message.text
    chat_id = update.effective_chat.id

    # 1. Check if group is linked
    group_id = await get_group_id_by_chat_id(chat_id)

    # 2. Regex Parsing
    amount_match = re.search(r'\b\d+(\.\d{1,2})?\b', text)
    payer_match = re.search(r'\b(?:by|paid by)\s+([@+]\w+)', text, re.IGNORECASE)
    participants_raw = re.findall(r'[@+]\w+', text)

    if not amount_match:
        await update.message.reply_text(
            "⚠️ *Invalid Format*\n"
            "Please provide a valid amount to split.\n\n"
            "_Example:_ `/split 120 @joel by @sarah Dinner`", 
            parse_mode="Markdown"
        )
        return

    total_amount = Decimal(amount_match.group(0))
    sender = update.message.from_user

    # 3. Determine the Payer AND the Sender
    sender_telegram_id = str(sender.id)
    sender_identifier = f"@{sender.username}" if sender.username else f"User_{sender_telegram_id}"
    
    payer_identifier = None
    payer_telegram_id = None

    if payer_match:
        payer_identifier = payer_match.group(1) 
    else:
        payer_identifier = sender_identifier
        payer_telegram_id = sender_telegram_id

    # 4. Clean up the Description
    desc_clean = re.sub(r'\b(?:by|paid by)\s+[@+]\w+', '', text, flags=re.IGNORECASE)
    desc_raw = re.sub(r'/split|\b\d+(\.\d{1,2})?\b|[@+]\w+', '', desc_clean)
    description = ' '.join(desc_raw.split()) 
    
    if not description:
        description = "Telegram Expense"

    try:
        # 5. Resolve Profiles (Concurrent)
        sender_id = await get_or_create_profile(identifier=sender_identifier, telegram_id=sender_telegram_id)
        payer_id = await get_or_create_profile(identifier=payer_identifier, telegram_id=payer_telegram_id)

        participants_raw = list(dict.fromkeys(re.findall(r'[@+]\w+', text)))
        tasks = [get_or_create_profile(identifier=user) for user in participants_raw]
        tagged_ids = await asyncio.gather(*tasks)

        # Base participants (Payer + anyone explicitly tagged)
        base_participants = list(dict.fromkeys([payer_id] + tagged_ids))

        # SMART ACCOUNTANT LOGIC
        # Check if anyone *other* than the payer or sender was explicitly tagged
        tagged_others = any(pid != payer_id and pid != sender_id for pid in base_participants)

        if payer_id == sender_id:
            # Scenario A: You paid. Always include you.
            if sender_id not in base_participants:
                base_participants.append(sender_id)
        elif not tagged_others:
            # Scenario B: Someone else paid, nobody else tagged. Implied 2-way split.
            if sender_id not in base_participants:
                base_participants.append(sender_id)
        else:
            # Scenario C: Accountant mode. Someone else paid, others are tagged. You are excluded.
            pass 

        # Final cleanup to ensure order and uniqueness
        all_participants = list(dict.fromkeys(base_participants))
        
       # Failsafe: Prevent 1-person splits
        if len(all_participants) < 2:
            await update.message.reply_text(
                "⚠️ *Not Enough People*\n"
                "An expense must be split between at least 2 people (including the payer).",
                parse_mode="Markdown"
            )
            return

        split_amount = round(total_amount / len(all_participants), 2)

        # 6. Auto-Enrollment (Silently ensure everyone is in group_members)
        async def enroll_user(profile_id):
            try:
                await asyncio.to_thread(
                    lambda: supabase.table("group_members").insert({
                        "group_id": group_id,
                        "profile_id": profile_id
                    }).execute()
                )
            except Exception:
                pass # Already a member
                
        await asyncio.gather(*[enroll_user(pid) for pid in all_participants])

        # 7. Construct Payloads for Database
        payers_payload = [{
            "profile_id": payer_id,
            "from_account_id": None,
            "amount_paid": float(total_amount)
        }]

        splits_payload = [{
            "profile_id": p_id,
            "amount": float(split_amount),
            "is_settled": (p_id == payer_id) 
        } for p_id in all_participants]

        # 8. Execute Supabase RPC Function
        await asyncio.to_thread(
            lambda: supabase.rpc("log_transaction", {
                "p_type": "expense",
                "p_total_amount": float(total_amount),
                "p_description": description,
                "p_group_id": group_id,
                "p_category_id": None,
                "p_payers": payers_payload,
                "p_receivers": None,
                "p_splits": splits_payload,
                "p_caller_profile_id": sender_id # Authorize using the sender's permissions
            }).execute()
        )

        # 9. Success Reply
        display_payer = payer_identifier.replace('+', '').replace('@', '')
        
        # Build a dynamic list of who actually owes money
        debtor_names = []
        
        # 1. Add explicitly tagged users (excluding the payer)
        for tag in participants_raw:
            clean_tag = tag.replace('+', '').replace('@', '')
            if clean_tag.lower() != display_payer.lower() and clean_tag not in debtor_names:
                debtor_names.append(clean_tag)
                
        # 2. Add the sender if they were implicitly included (Scenarios A or B)
        clean_sender = sender_identifier.replace('+', '').replace('@', '')
        if sender_id in all_participants and sender_id != payer_id and clean_sender not in debtor_names:
            debtor_names.append(clean_sender)

        # 3. Format the final output cleanly
        if not debtor_names:
            # Edge case: Someone logged an expense entirely for themselves
            reply_text = f"✅ Logged **${total_amount:.2f}** for '{description}' paid by {display_payer}."
        else:
            debtors_str = ", ".join(debtor_names)
            reply_text = (
                f"✅ **{description}** (Total: ${total_amount:.2f})\n"
                f"💸 {debtors_str} each owe {display_payer} **${split_amount:.2f}**"
            )

        await update.message.reply_text(reply_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error logging transaction: {e}")
        await update.message.reply_text(
            "❌ *System Error*\n"
            "Something went wrong saving this to the database. Please try again.",
            parse_mode="Markdown"
        )


@require_linked_group
async def balances_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calculates and displays the simplified debts for the group."""
    chat_id = update.effective_chat.id

    # 1. Check if group is linked
    group_id = await get_group_id_by_chat_id(chat_id)

    try:
        # 2. Fetch the aggregated balances from our Postgres View
        response = await asyncio.to_thread(
            lambda: supabase.table("group_user_balances").select("*").eq("group_id", group_id).execute()
        )
        
        balances = response.data

        if not balances:
            await update.message.reply_text("No one is in this group yet!")
            return

        # 3. Process through the simplification algorithm
        payments = simplify_debts(balances)

        # 4. Format the output
        if not payments:
            await update.message.reply_text("🎉 *All settled up!* Nobody owes anything.", parse_mode="Markdown")
            return

        message_lines = [
            "📊 *Group Settlement Plan*",
            "────────────────"
        ]
        
        for p in payments:
            from_name = p['from'].replace('@', '').replace('+', '')
            to_name = p['to'].replace('@', '').replace('+', '')
            
            # Using an arrow makes the money flow instantly clear
            message_lines.append(f"👤 {from_name} ➡️ {to_name}: **${p['amount']:.2f}**")

        await update.message.reply_text(
            "\n".join(message_lines), 
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error fetching balances: {e}")
        await update.message.reply_text("Oops, couldn't calculate balances right now.")


@require_linked_group
async def settle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles 1-to-1 peer payments to settle debts."""
    text = update.message.text
    chat_id = update.effective_chat.id

    # 1. Check if group is linked
    group_id = await get_group_id_by_chat_id(chat_id)

    # 2. Regex Parsing
    amount_match = re.search(r'\b\d+(\.\d{1,2})?\b', text)
    payer_match = re.search(r'\b(?:by|paid by)\s+([@+]\w+)', text, re.IGNORECASE)
    all_tags = re.findall(r'[@+]\w+', text)

    if not amount_match or not all_tags:
        await update.message.reply_text(
            "⚠️ *Invalid Format*\n"
            "Please provide an amount and tag the person receiving the money.\n\n"
            "_Examples:_\n"
            "• `/settle 50 @joel` _(You paid Joel)_\n"
            "• `/settle 50 @roger by @joel` _(Joel paid Roger)_",
            parse_mode="Markdown"
        )
        return

    total_amount = Decimal(amount_match.group(0))
    sender = update.message.from_user
    
    # 3. Determine Payer and Receiver
    sender_telegram_id = str(sender.id)
    sender_identifier = f"@{sender.username}" if sender.username else f"User_{sender_telegram_id}"

    payer_identifier = None
    payer_telegram_id = None

    if payer_match:
        payer_identifier = payer_match.group(1)
    else:
        payer_identifier = sender_identifier
        payer_telegram_id = sender_telegram_id

    # Find the Receiver (The tag that is NOT the payer)
    # We use a case-insensitive comparison to filter out the payer
    receiver_candidates = [tag for tag in all_tags if tag.lower() != payer_identifier.lower()]

    if len(receiver_candidates) == 0:
        await update.message.reply_text(
            "⚠️ *Invalid Receiver*\n"
            "You cannot settle with yourself. Please tag the person you are paying.",
            parse_mode="Markdown"
        )
        return
    elif len(receiver_candidates) > 1:
        await update.message.reply_text(
            "⚠️ *Multiple Receivers*\n"
            "Settlements can only be 1-to-1. Please tag exactly one person.",
            parse_mode="Markdown"
        )
        return

    receiver_identifier = receiver_candidates[0]

    try:
        # 4. Resolve Profiles (Concurrent)
        sender_id = await get_or_create_profile(identifier=sender_identifier, telegram_id=sender_telegram_id)
        payer_id = await get_or_create_profile(identifier=payer_identifier, telegram_id=payer_telegram_id)
        receiver_id = await get_or_create_profile(identifier=receiver_identifier)

        # 5. Construct Payloads
        # The Payer gives the money
        payers_payload = [{
            "profile_id": payer_id,
            "from_account_id": None,
            "amount_paid": float(total_amount)
        }]

        # The Receiver takes the burden (100% split)
        splits_payload = [{
            "profile_id": receiver_id,
            "amount": float(total_amount),
            "is_settled": True # It's a direct payment, so the split itself is considered settled
        }]

        # 6. Execute Supabase RPC
        await asyncio.to_thread(
            lambda: supabase.rpc("log_transaction", {
                "p_type": "settlement", # Marks this differently from an 'expense' in the DB
                "p_total_amount": float(total_amount),
                "p_description": "Payment",
                "p_group_id": group_id,
                "p_category_id": None,
                "p_payers": payers_payload,
                "p_receivers": None, 
                "p_splits": splits_payload,
                "p_caller_profile_id": sender_id
            }).execute()
        )

        # 7. Success Reply
        display_payer = payer_identifier.replace('+', '').replace('@', '')
        display_receiver = receiver_identifier.replace('+', '').replace('@', '')
        
        await update.message.reply_text(
            f"🤝 **Payment Logged**\n"
            f"💸 {display_payer} paid **${total_amount:.2f}** to {display_receiver}.",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Error logging transaction: {e}")
        await update.message.reply_text(
            "❌ *System Error*\n"
            "Something went wrong saving this to the database. Please try again.",
            parse_mode="Markdown"
        )
