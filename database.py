import aiosqlite
import config

DB_PATH = config.DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '',
            full_name TEXT DEFAULT '', joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS agencies (
            user_id INTEGER PRIMARY KEY, level TEXT DEFAULT 'bronze',
            total_sales INTEGER DEFAULT 0, total_commission INTEGER DEFAULT 0,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS gaming_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, volume_gb INTEGER,
            price INTEGER, active INTEGER DEFAULT 1)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS multi_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT,
            price INTEGER, active INTEGER DEFAULT 1)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            username TEXT, full_name TEXT, plan_id INTEGER, plan_name TEXT,
            price INTEGER, original_price INTEGER, coupon_code TEXT,
            status TEXT DEFAULT 'awaiting_receipt',
            payment_method TEXT DEFAULT 'card', receipt_file_id TEXT,
            panel_info TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS wallet (
            user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS wallet_topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            username TEXT, full_name TEXT, amount INTEGER,
            status TEXT DEFAULT 'awaiting_receipt', receipt_file_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER,
            referred_id INTEGER UNIQUE, referred_username TEXT,
            converted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS referral_commissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER,
            referred_id INTEGER, order_id INTEGER, amount INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY, percent INTEGER, max_uses INTEGER,
            used_count INTEGER DEFAULT 0, active INTEGER DEFAULT 1)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT)""")
        await db.commit()
    await seed_defaults()


async def seed_defaults():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM gaming_plans")
        if (await cur.fetchone())[0] == 0:
            await db.executemany("INSERT INTO gaming_plans (volume_gb, price) VALUES (?, ?)",
                [(30, 150000), (60, 280000), (90, 400000), (120, 500000)])
        cur = await db.execute("SELECT COUNT(*) FROM multi_plans")
        if (await cur.fetchone())[0] == 0:
            await db.executemany("INSERT INTO multi_plans (label, price) VALUES (?, ?)",
                [("🔵 پلن اقتصادی (۱ کاربره)", 200000),
                 ("🟣 پلن حرفه‌ای (۲ کاربره)", 350000),
                 ("🟡 پلن نامحدود (۳ کاربره)", 500000)])
        await db.commit()


async def ensure_user(user_id, username="", full_name=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?,?,?)",
            (user_id, username, full_name))
        await db.execute("INSERT OR IGNORE INTO agencies (user_id) VALUES (?)", (user_id,))
        await db.execute("INSERT OR IGNORE INTO wallet (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def get_agency_info(user_id):
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM agencies WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_agency_sales(user_id, amount):
    await ensure_user(user_id)
    info = await get_agency_info(user_id)
    level_info = config.AGENCY_LEVELS.get(info["level"], config.AGENCY_LEVELS["bronze"])
    commission = int(amount * level_info["commission"] / 100)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE agencies SET total_sales = total_sales + ?, total_commission = total_commission + ? WHERE user_id = ?",
            (amount, commission, user_id))
        await db.commit()
    new_info = await get_agency_info(user_id)
    new_level = "bronze"
    for lvl, data in config.AGENCY_LEVELS.items():
        if new_info["total_sales"] >= data["min_sales"]:
            new_level = lvl
    if new_level != new_info["level"]:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE agencies SET level = ? WHERE user_id = ?", (new_level, user_id))
            await db.commit()
        return new_level
    return None


async def get_wallet_balance(user_id):
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM wallet WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else 0


async def add_wallet_balance(user_id, amount):
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE wallet SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()


async def deduct_wallet_balance(user_id, amount):
    await ensure_user(user_id)
    if await get_wallet_balance(user_id) < amount:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE wallet SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        await db.commit()
    return True


async def get_gaming_plans(active_only=True):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM gaming_plans" + (" WHERE active = 1" if active_only else "") + " ORDER BY volume_gb"
        cur = await db.execute(q)
        return [dict(r) for r in await cur.fetchall()]


async def get_gaming_plan(plan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM gaming_plans WHERE id = ?", (plan_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_gaming_price(plan_id, price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE gaming_plans SET price = ? WHERE id = ?", (price, plan_id))
        await db.commit()


async def toggle_gaming_active(plan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE gaming_plans SET active = 1 - active WHERE id = ?", (plan_id,))
        await db.commit()


async def add_gaming_plan(volume, price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO gaming_plans (volume_gb, price) VALUES (?, ?)", (volume, price))
        await db.commit()


async def get_multi_plans(active_only=True):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        q = "SELECT * FROM multi_plans" + (" WHERE active = 1" if active_only else "")
        cur = await db.execute(q)
        return [dict(r) for r in await cur.fetchall()]


async def get_multi_plan(plan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM multi_plans WHERE id = ?", (plan_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_multi_price(plan_id, price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE multi_plans SET price = ? WHERE id = ?", (price, plan_id))
        await db.commit()


async def toggle_multi_active(plan_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE multi_plans SET active = 1 - active WHERE id = ?", (plan_id,))
        await db.commit()


async def add_multi_plan(label, price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO multi_plans (label, price) VALUES (?, ?)", (label, price))
        await db.commit()


async def create_order(user_id, username, full_name, plan_id, plan_name, price):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""INSERT INTO orders 
            (user_id, username, full_name, plan_id, plan_name, price, original_price) 
            VALUES (?,?,?,?,?,?,?)""",
            (user_id, username, full_name, plan_id, plan_name, price, price))
        await db.commit()
        return cur.lastrowid


async def get_order(order_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_user_orders(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", (user_id,))
        return [dict(r) for r in await cur.fetchall()]


async def set_order_status(order_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        await db.commit()


async def attach_receipt(order_id, file_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET receipt_file_id = ?, status = 'pending' WHERE id = ?", (file_id, order_id))
        await db.commit()


async def deliver_order(order_id, panel_info):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = 'delivered', panel_info = ? WHERE id = ?", (panel_info, order_id))
        await db.commit()


async def mark_order_paid_by_wallet(order_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = 'pending', payment_method = 'wallet' WHERE id = ?", (order_id,))
        await db.commit()


async def apply_coupon_to_order(order_id, code, new_price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET coupon_code = ?, price = ? WHERE id = ?", (code, new_price, order_id))
        await db.commit()


async def remove_coupon_from_order(order_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET coupon_code = NULL, price = original_price WHERE id = ?", (order_id,))
        await db.commit()


async def create_wallet_topup(user_id, username, full_name, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("INSERT INTO wallet_topups (user_id, username, full_name, amount) VALUES (?,?,?,?)",
            (user_id, username, full_name, amount))
        await db.commit()
        return cur.lastrowid


async def get_wallet_topup(topup_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM wallet_topups WHERE id = ?", (topup_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_topup_status(topup_id, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE wallet_topups SET status = ? WHERE id = ?", (status, topup_id))
        await db.commit()


async def attach_topup_receipt(topup_id, file_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE wallet_topups SET receipt_file_id = ?, status = 'pending' WHERE id = ?", (file_id, topup_id))
        await db.commit()


async def add_referral(referrer_id, referred_id, referred_username=""):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO referrals (referrer_id, referred_id, referred_username) VALUES (?,?,?)",
                (referrer_id, referred_id, referred_username))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def get_referral_by_referred(referred_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM referrals WHERE referred_id = ?", (referred_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def mark_referral_converted(referred_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE referrals SET converted = 1 WHERE referred_id = ?", (referred_id,))
        await db.commit()


async def count_referrals(referrer_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (referrer_id,))
        return (await cur.fetchone())[0]


async def count_converted_referrals(referrer_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND converted = 1", (referrer_id,))
        return (await cur.fetchone())[0]


async def add_referral_commission(referrer_id, referred_id, order_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO referral_commissions (referrer_id, referred_id, order_id, amount) VALUES (?,?,?,?)",
            (referrer_id, referred_id, order_id, amount))
        await db.commit()


async def get_total_referral_earnings(referrer_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COALESCE(SUM(amount), 0) FROM referral_commissions WHERE referrer_id = ?", (referrer_id,))
        return (await cur.fetchone())[0]


async def get_coupon(code):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM coupons WHERE code = ?", (code,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_coupon(code, percent, max_uses):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO coupons (code, percent, max_uses) VALUES (?,?,?)", (code, percent, max_uses))
        await db.commit()


async def increment_coupon_usage(code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (code,))
        await db.commit()


async def list_coupons():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM coupons ORDER BY code")
        return [dict(r) for r in await cur.fetchall()]


async def toggle_coupon_active(code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coupons SET active = 1 - active WHERE code = ?", (code,))
        await db.commit()


async def delete_coupon(code):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM coupons WHERE code = ?", (code,))
        await db.commit()


async def get_setting(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
        await db.commit()


async def get_welcome_message():
    return await get_setting("welcome_message")


async def set_welcome_message(text):
    await set_setting("welcome_message", text)


async def get_rules_text():
    return await get_setting("rules_text")


async def set_rules_text(text):
    await set_setting("rules_text", text)


async def get_referral_commission_percent():
    val = await get_setting("referral_commission_percent", "10")
    return int(val)


async def get_wallet_bonus_threshold():
    val = await get_setting("wallet_bonus_threshold", "500000")
    return int(val)


async def get_wallet_bonus_percent():
    val = await get_setting("wallet_bonus_percent", "5")
    return int(val)


async def count_agencies():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM agencies")
        return (await cur.fetchone())[0]
