import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

BRAND_NAME = os.getenv("BRAND_NAME", "پاسارگارد")
CARD_NUMBER = os.getenv("CARD_NUMBER", "6037-XXXX-XXXX-XXXX")
CARD_HOLDER = os.getenv("CARD_HOLDER", "نام صاحب کارت")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "PassargadSupport")

DB_PATH = os.getenv("DB_PATH", "/data/bot.db" if os.path.exists("/data") else "bot.db")

AGENCY_LEVELS = {
    "bronze": {"name": "🥉 برنزی", "min_sales": 0, "commission": 10},
    "silver": {"name": "🥈 نقره‌ای", "min_sales": 5_000_000, "commission": 15},
    "gold":   {"name": "🥇 طلایی", "min_sales": 20_000_000, "commission": 20},
    "vip":    {"name": "💎 VIP", "min_sales": 50_000_000, "commission": 25},
}
