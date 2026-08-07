import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY or not TELEGRAM_BOT_TOKEN:
    raise ValueError("Missing environment variables!")

# Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)