import asyncio
from config import supabase 

group_cache = {}

async def get_or_create_profile(identifier: str, telegram_id: str = None) -> str:
    """
    Fetches a profile UUID.
    - Handles Guests via '+Name' syntax (no Telegram data stored).
    - Handles Telegram users via '@username' syntax.
    - Self-heals the database by capturing permanent telegram_id when users interact.
    """
    identifier = identifier.strip()
    
    # ==========================================
    # SCENARIO 1: GUEST USER (+Name)
    # ==========================================
    if identifier.startswith('+'):
        guest_name = identifier[1:].strip() # Remove the '+'
        
        # Look for existing guest (telegram_username must be explicitly null)
        res = await asyncio.to_thread(
            lambda: supabase.table("profiles")
            .select("id")
            .eq("display_name", guest_name)
            .is_("telegram_username", "null")
            .execute()
        )
        if len(res.data) > 0:
            return res.data[0]['id']
            
        # Create new Guest profile
        new_guest = {
            "display_name": guest_name,
            "is_shadow": True
        }
        insert_res = await asyncio.to_thread(lambda: supabase.table("profiles").insert(new_guest).execute())
        return insert_res.data[0]['id']

    # ==========================================
    # SCENARIO 2: TELEGRAM USER
    # ==========================================
    username_clean = identifier.replace("@", "").strip() if identifier else None
    
    # If the user is executing the command, we have their permanent ID
    if telegram_id:
        # 1. Search by permanent ID first
        res_by_id = await asyncio.to_thread(
            lambda: supabase.table("profiles").select("*").eq("telegram_id", telegram_id).execute()
        )
        if len(res_by_id.data) > 0:
            profile = res_by_id.data[0]
            # Self-heal: Update username if it changed
            if username_clean and profile.get("telegram_username") != username_clean:
                await asyncio.to_thread(
                    lambda: supabase.table("profiles").update({"telegram_username": username_clean}).eq("id", profile["id"]).execute()
                )
            return profile["id"]
            
        # 2. Search by username (they were mentioned previously)
        if username_clean:
            res_by_name = await asyncio.to_thread(
                lambda: supabase.table("profiles").select("*").eq("telegram_username", username_clean).execute()
            )
            if len(res_by_name.data) > 0:
                profile = res_by_name.data[0]
                # Self-heal: Lock their ID to the shadow profile
                await asyncio.to_thread(
                    lambda: supabase.table("profiles").update({"telegram_id": telegram_id}).eq("id", profile["id"]).execute()
                )
                return profile["id"]
                
        # 3. Create a brand new full profile
        new_profile = {
            "telegram_id": telegram_id,
            "telegram_username": username_clean,
            "display_name": username_clean or f"User_{telegram_id}",
            "is_shadow": True
        }
        insert_res = await asyncio.to_thread(lambda: supabase.table("profiles").insert(new_profile).execute())
        return insert_res.data[0]['id']
        
    # If we only have the @username (e.g., they were mentioned in a command)
    else:
        if not username_clean:
            raise ValueError("Must provide either telegram_id or username")
            
        res_by_name = await asyncio.to_thread(
            lambda: supabase.table("profiles").select("id").eq("telegram_username", username_clean).execute()
        )
        if len(res_by_name.data) > 0:
            return res_by_name.data[0]["id"]
            
        # Create a naked shadow profile (will self-heal when they eventually interact)
        new_profile = {
            "telegram_username": username_clean,
            "display_name": username_clean,
            "is_shadow": True
        }
        insert_res = await asyncio.to_thread(lambda: supabase.table("profiles").insert(new_profile).execute())
        return insert_res.data[0]['id']
    

async def get_group_id_by_chat_id(chat_id: int) -> str | None:
    """Finds the Supabase group UUID mapped to a Telegram Chat ID."""
    if chat_id in group_cache:
        return group_cache[chat_id]
    response = await asyncio.to_thread(
        lambda: supabase.table("groups").select("id").eq("telegram_chat_id", str(chat_id)).execute()
    )
    if len(response.data) > 0:
        group_id = response.data[0]['id']
        group_cache[chat_id] = group_id  # Add this line!
        return group_id
    return None

def clear_group_cache(chat_id: int):
    """Removes a chat_id from the cache so the next request fetches fresh from the database."""
    if chat_id in group_cache:
        del group_cache[chat_id]