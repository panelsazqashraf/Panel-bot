import asyncio
import logging
from time import monotonic

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)

import config
import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
BOT_USERNAME = ""


# ---------- Anti-spam ----------
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: float = 0.6):
        self.rate_limit = rate_limit
        self.last_call: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is not None:
            now = monotonic()
            last = self.last_call.get(user.id)
            if last is not None and (now - last) < self.rate_limit:
                if isinstance(event, CallbackQuery):
                    await event.answer("⏳ لطفاً کمی آروم‌تر بزنید!", show_alert=False)
                return
            self.last_call[user.id] = now
        return await handler(event, data)


dp.message.outer_middleware(ThrottlingMiddleware(rate_limit=0.7))
dp.callback_query.outer_middleware(ThrottlingMiddleware(rate_limit=0.4))


# ---------- States ----------
class BuyStates(StatesGroup):
    waiting_for_receipt = State()
    entering_coupon_code = State()


class WalletStates(StatesGroup):
    entering_topup_amount = State()
    waiting_for_topup_receipt = State()


class AdminStates(StatesGroup):
    waiting_for_panel_info = State()
    editing_gaming_price = State()
    editing_multi_price = State()
    adding_gaming_volume = State()
    adding_gaming_price = State()
    adding_multi_label = State()
    adding_multi_price = State()
    editing_welcome_message = State()
    editing_referral_percent = State()
    editing_rules_text = State()
    adding_coupon_code = State()
    adding_coupon_percent = State()
    adding_coupon_maxuses = State()
    editing_wallet_bonus_threshold = State()
    editing_wallet_bonus_percent = State()


# ---------- Keyboards ----------
def main_menu_kb(user_id: int | None = None) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="🛒 خرید سرویس"), KeyboardButton(text="📊 سرویس‌های من")],
        [KeyboardButton(text="💰 کیف پول"), KeyboardButton(text="📈 آمار فروش من")],
        [KeyboardButton(text="🎁 پورسانت و پاداش"), KeyboardButton(text="🤝 زیرمجموعه‌گیری")],
        [KeyboardButton(text="💬 پشتیبانی"), KeyboardButton(text="📜 قوانین")],
    ]
    if user_id is not None and user_id in config.ADMIN_IDS:
        keyboard.append([KeyboardButton(text="🛠 پنل مدیریت")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back:menu")]])


def services_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔵 پاسارگارد | پلن اقتصادی (۱ کاربره)", callback_data="svc:multi")],
        [InlineKeyboardButton(text="🟣 پاسارگارد | پلن حرفه‌ای (۲ کاربره)", callback_data="svc:multi")],
        [InlineKeyboardButton(text="🟡 پاسارگارد | پلن نامحدود (۳ کاربره)", callback_data="svc:multi")],
        [InlineKeyboardButton(text="🎮 سرویس گیمینگ (حجمی)", callback_data="svc:gaming")],
        [InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gaming_plans_kb(plans) -> InlineKeyboardMarkup:
    rows, row = [], []
    for p in plans:
        row.append(InlineKeyboardButton(text=f"{p['volume_gb']} گیگ - {p['price']:,} تومان", callback_data=f"gplan:{p['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back:services")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def multi_plans_kb(plans) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{p['label']} - {p['price']:,} تومان", callback_data=f"mplan:{p['id']}")] for p in plans]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="back:services")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def build_order_summary_kb(order) -> InlineKeyboardMarkup:
    kind = "gaming" if "گیمینگ" in str(order["plan_name"]) else "multi"
    rows = [[InlineKeyboardButton(text="📤 ارسال رسید", callback_data=f"reqreceipt:{order['id']}")]]
    if order["coupon_code"]:
        rows.append([
            InlineKeyboardButton(text="🔄 تغییر کد", callback_data=f"applycoupon:{order['id']}"),
            InlineKeyboardButton(text="🗑 حذف تخفیف", callback_data=f"removecoupon:{order['id']}"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="🎟 اعمال کد تخفیف", callback_data=f"applycoupon:{order['id']}")])
    if order["price"] and order["price"] > 0:
        balance = await db.get_wallet_balance(order["user_id"])
        if balance >= order["price"]:
            rows.append([InlineKeyboardButton(text=f"💰 پرداخت با کیف پول ({balance:,})", callback_data=f"walletpay:{order['id']}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"cancelorder:{order['id']}:{kind}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def waiting_receipt_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"backsummary:{order_id}")]])


def order_summary_text(order) -> str:
    price_block = f"💰 قیمت: {order['price']:,} تومان"
    if order["coupon_code"]:
        price_block = (f"💵 قیمت اصلی: {order['original_price']:,} تومان\n"
                       f"🎟 کد تخفیف: {order['coupon_code']}\n"
                       f"💰 قیمت نهایی: {order['price']:,} تومان")
    return (f"🧾 <b>خلاصه سفارش شما</b>\n━━━━━━━━━━━━━━━\n"
            f"📦 {order['plan_name']}\n{price_block}\n━━━━━━━━━━━━━━━\n\n"
            f"💳 شماره کارت: <code>{config.CARD_NUMBER}</code>\n"
            f"👤 به نام: {config.CARD_HOLDER}\n\n"
            f"ℹ️ پس از واریز، روی «📤 ارسال رسید» بزنید.")


def admin_decision_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"reject:{order_id}"),
    ]])


# ---------- User handlers ----------
@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    await db.ensure_user(message.from_user.id, message.from_user.username or "", message.from_user.full_name)

    args = command.args
    if args and args.startswith("ref_"):
        try:
            referrer_id = int(args[4:])
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != message.from_user.id:
            existing = await db.get_referral_by_referred(message.from_user.id)
            if not existing:
                added = await db.add_referral(referrer_id, message.from_user.id, message.from_user.username or "")
                if added:
                    try:
                        await bot.send_message(referrer_id, "🎉 یک نفر با لینک دعوت شما وارد ربات شد!")
                    except Exception as e:
                        logging.warning(f"Notify referrer failed: {e}")

    custom_welcome = await db.get_welcome_message()
    text = custom_welcome if custom_welcome else (
        f"🛡 <b>پنل نمایندگی {config.BRAND_NAME}</b> 🛡\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👋 نماینده گرامی، خوش آمدید!\n\n"
        f"🎯 <b>خدمات شما:</b>\n"
        f"🛒 خرید سرویس با قیمت نمایندگی\n"
        f"📈 مشاهده آمار فروش\n"
        f"🎁 پورسانت تا <b>۲۵٪</b>\n"
        f"🤝 زیرمجموعه‌گیری و درآمد بیشتر\n\n"
        f"🏆 <b>سطوح نمایندگی:</b>\n"
        f"🥉 برنزی → ۱۰٪\n🥈 نقره‌ای → ۱۵٪\n🥇 طلایی → ۲۰٪\n💎 VIP → ۲۵٪"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_kb(message.from_user.id))


@dp.message(F.text == "🛒 خرید سرویس")
async def show_services(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛒 نوع سرویس مورد نظر رو انتخاب کنید:", reply_markup=services_kb())


@dp.callback_query(F.data == "back:menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("↩️ به منوی اصلی برگشتید.")
    await callback.answer()


@dp.callback_query(F.data == "back:services")
async def back_to_services(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🛒 نوع سرویس مورد نظر رو انتخاب کنید:", reply_markup=services_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("cancelorder:"))
async def cancel_order_and_go_back(callback: CallbackQuery, state: FSMContext):
    _, order_id_str, kind = callback.data.split(":")
    order_id = int(order_id_str)
    order = await db.get_order(order_id)
    if order and order["user_id"] == callback.from_user.id and order["status"] in ("awaiting_receipt", "pending"):
        await db.set_order_status(order_id, "cancelled")
    await state.clear()
    if kind == "gaming":
        plans = await db.get_gaming_plans()
        if not plans:
            await callback.answer("تعرفه‌ای ثبت نشده.", show_alert=True)
            return
        await callback.message.edit_text("🎮 <b>سرویس گیمینگ</b>", parse_mode="HTML", reply_markup=gaming_plans_kb(plans))
    else:
        plans = await db.get_multi_plans()
        if not plans:
            await callback.answer("تعرفه‌ای ثبت نشده.", show_alert=True)
            return
        await callback.message.edit_text("🌍 <b>سرویس پاسارگارد</b>", parse_mode="HTML", reply_markup=multi_plans_kb(plans))
    await callback.answer()


@dp.callback_query(F.data == "svc:gaming")
async def choose_gaming_service(callback: CallbackQuery, state: FSMContext):
    plans = await db.get_gaming_plans()
    if not plans:
        await callback.answer("تعرفه‌ای ثبت نشده.", show_alert=True)
        return
    await callback.message.edit_text("🎮 <b>سرویس گیمینگ پاسارگارد</b>", parse_mode="HTML", reply_markup=gaming_plans_kb(plans))
    await callback.answer()


@dp.callback_query(F.data == "svc:multi")
async def choose_multi_service(callback: CallbackQuery, state: FSMContext):
    plans = await db.get_multi_plans()
    if not plans:
        await callback.answer("تعرفه‌ای ثبت نشده.", show_alert=True)
        return
    await callback.message.edit_text("🌍 <b>سرویس‌های پاسارگارد</b>", parse_mode="HTML", reply_markup=multi_plans_kb(plans))
    await callback.answer()


@dp.callback_query(F.data.startswith("gplan:"))
async def choose_gaming_plan(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_gaming_plan(plan_id)
    if not plan or not plan["active"]:
        await callback.answer("این تعرفه موجود نیست.", show_alert=True)
        return
    order_id = await db.create_order(callback.from_user.id, callback.from_user.username or "",
                                      callback.from_user.full_name, plan_id,
                                      f"🎮 پاسارگارد گیمینگ - {plan['volume_gb']} گیگ", plan["price"])
    await state.clear()
    order = await db.get_order(order_id)
    await callback.message.edit_text(order_summary_text(order), parse_mode="HTML", reply_markup=await build_order_summary_kb(order))
    await callback.answer()


@dp.callback_query(F.data.startswith("mplan:"))
async def choose_multi_plan(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_multi_plan(plan_id)
    if not plan or not plan["active"]:
        await callback.answer("این تعرفه موجود نیست.", show_alert=True)
        return
    order_id = await db.create_order(callback.from_user.id, callback.from_user.username or "",
                                      callback.from_user.full_name, plan_id,
                                      f"🌍 پاسارگارد - {plan['label']}", plan["price"])
    await state.clear()
    order = await db.get_order(order_id)
    await callback.message.edit_text(order_summary_text(order), parse_mode="HTML", reply_markup=await build_order_summary_kb(order))
    await callback.answer()


@dp.callback_query(F.data.startswith("reqreceipt:"))
async def request_receipt(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    if order["status"] not in ("awaiting_receipt", "pending"):
        await callback.answer("این سفارش دیگه قابل ارسال رسید نیست.", show_alert=True)
        return
    await state.update_data(order_id=order_id)
    await state.set_state(BuyStates.waiting_for_receipt)
    await callback.message.edit_text("📸 لطفاً عکس یا فایل رسید پرداخت رو ارسال کنید.", reply_markup=waiting_receipt_kb(order_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("backsummary:"))
async def back_to_order_summary(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(order_summary_text(order), parse_mode="HTML", reply_markup=await build_order_summary_kb(order))
    await callback.answer()


@dp.callback_query(F.data.startswith("applycoupon:"))
async def start_apply_coupon(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    if order["status"] not in ("awaiting_receipt", "pending"):
        await callback.answer("این سفارش قابل ویرایش نیست.", show_alert=True)
        return
    await state.update_data(order_id=order_id)
    await state.set_state(BuyStates.entering_coupon_code)
    await callback.message.edit_text("🎟 کد تخفیف رو وارد کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"backsummary:{order_id}")]]))
    await callback.answer()


@dp.message(BuyStates.entering_coupon_code)
async def apply_coupon_code(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await db.get_order(order_id) if order_id else None
    if not order:
        await message.answer("مشکلی پیش اومد.")
        await state.clear()
        return
    code = (message.text or "").strip().upper()
    coupon = await db.get_coupon(code)
    if not coupon or not coupon["active"]:
        await message.answer("❌ کد معتبر نیست.")
        return
    if coupon["max_uses"] is not None and coupon["used_count"] >= coupon["max_uses"]:
        await message.answer("❌ ظرفیت این کد تموم شده.")
        return
    new_price = int(order["original_price"] * (100 - coupon["percent"]) / 100)
    await db.apply_coupon_to_order(order_id, code, new_price)
    await db.increment_coupon_usage(code)
    await state.clear()
    order = await db.get_order(order_id)
    await message.answer(f"✅ کد {code} ({coupon['percent']}٪) اعمال شد!")
    await message.answer(order_summary_text(order), parse_mode="HTML", reply_markup=await build_order_summary_kb(order))


@dp.callback_query(F.data.startswith("removecoupon:"))
async def remove_coupon(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    await db.remove_coupon_from_order(order_id)
    await state.clear()
    order = await db.get_order(order_id)
    await callback.message.edit_text(order_summary_text(order), parse_mode="HTML", reply_markup=await build_order_summary_kb(order))
    await callback.answer("کد تخفیف حذف شد.")


@dp.callback_query(F.data.startswith("walletpay:"))
async def pay_with_wallet(callback: CallbackQuery, state: FSMContext):
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    if order["status"] not in ("awaiting_receipt", "pending"):
        await callback.answer("این سفارش قابل پرداخت نیست.", show_alert=True)
        return
    ok = await db.deduct_wallet_balance(order["user_id"], order["price"])
    if not ok:
        await callback.answer("موجودی کافی نیست.", show_alert=True)
        return
    await db.mark_order_paid_by_wallet(order_id)
    await state.clear()
    await callback.message.edit_text("✅ پرداخت از کیف پول انجام شد. در انتظار بررسی ادمین.", reply_markup=back_menu_kb())
    await callback.answer()
    caption = (f"🆕 سفارش #{order_id} (💰 پرداخت با کیف پول)\n"
                f"👤 {order['full_name']} (@{order['username'] or '-'})\n"
                f"🆔 {order['user_id']}\n📦 {order['plan_name']}\n💰 {order['price']:,} تومان")
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, caption, reply_markup=admin_decision_kb(order_id))
        except Exception as e:
            logging.warning(f"Admin notify failed: {e}")


@dp.message(BuyStates.waiting_for_receipt, F.photo | F.document)
async def receive_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await message.answer("مشکلی پیش اومد.")
        await state.clear()
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await db.attach_receipt(order_id, file_id)
    order = await db.get_order(order_id)
    await message.answer("🕐 رسید دریافت شد. در انتظار بررسی ادمین.", reply_markup=main_menu_kb(message.from_user.id))
    await state.clear()
    caption = (f"🆕 سفارش #{order_id}\n"
                f"👤 {order['full_name']} (@{order['username'] or '-'})\n"
                f"🆔 {order['user_id']}\n📦 {order['plan_name']}\n💰 {order['price']:,} تومان")
    for admin_id in config.ADMIN_IDS:
        try:
            if message.photo:
                await bot.send_photo(admin_id, photo=file_id, caption=caption, reply_markup=admin_decision_kb(order_id))
            else:
                await bot.send_document(admin_id, document=file_id, caption=caption, reply_markup=admin_decision_kb(order_id))
        except Exception as e:
            logging.warning(f"Admin notify failed: {e}")


@dp.message(BuyStates.waiting_for_receipt)
async def waiting_receipt_wrong_input(message: Message):
    await message.answer("لطفاً عکس یا فایل رسید بفرستید 📸")


ORDER_STATUS_MAP = {
    "awaiting_receipt": "⏳ در انتظار رسید",
    "pending": "🕐 در حال بررسی",
    "approved": "✅ تأیید شده",
    "rejected": "❌ رد شده",
    "delivered": "📦 تحویل داده شده",
    "cancelled": "🚫 لغو شده",
}
ORDER_STATUS_ICON = {"awaiting_receipt": "⏳", "pending": "🕐", "approved": "✅",
                     "rejected": "❌", "delivered": "📦", "cancelled": "🚫"}


def my_orders_kb(orders) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{ORDER_STATUS_ICON.get(o['status'],'•')} #{o['id']} - {o['plan_name']}",
                                   callback_data=f"vieworder:{o['id']}")] for o in orders]
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_detail_text(order) -> str:
    text = (f"🆔 <b>سفارش #{order['id']}</b>\n━━━━━━━━━━━━━━━\n"
            f"📦 {order['plan_name']}\n💰 {order['price']:,} تومان\n"
            f"📌 وضعیت: {ORDER_STATUS_MAP.get(order['status'], order['status'])}")
    if order["status"] == "delivered" and order["panel_info"]:
        text += f"\n\n🔑 اطلاعات سرویس:\n{order['panel_info']}"
    return text


@dp.message(F.text == "📊 سرویس‌های من")
async def my_orders(message: Message, state: FSMContext):
    await state.clear()
    orders = await db.get_user_orders(message.from_user.id)
    if not orders:
        await message.answer("هنوز سفارشی ثبت نکردید.", reply_markup=back_menu_kb())
        return
    await message.answer("📊 <b>سرویس‌های من</b>", parse_mode="HTML", reply_markup=my_orders_kb(orders))


@dp.callback_query(F.data.startswith("vieworder:"))
async def view_order_detail(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order or order["user_id"] != callback.from_user.id:
        await callback.answer("سفارش پیدا نشد.", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="myorders:list")]])
    await callback.message.edit_text(order_detail_text(order), parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "myorders:list")
async def back_to_my_orders_list(callback: CallbackQuery):
    orders = await db.get_user_orders(callback.from_user.id)
    if not orders:
        await callback.message.edit_text("هنوز سفارشی ثبت نکردید.")
        await callback.answer()
        return
    await callback.message.edit_text("📊 <b>سرویس‌های من</b>", parse_mode="HTML", reply_markup=my_orders_kb(orders))
    await callback.answer()


# ---------- Wallet ----------
def topup_summary_text(topup) -> str:
    return (f"🧾 <b>شارژ کیف پول</b>\n━━━━━━━━━━━━━━━\n💰 {topup['amount']:,} تومان\n━━━━━━━━━━━━━━━\n\n"
            f"💳 کارت: <code>{config.CARD_NUMBER}</code>\n👤 {config.CARD_HOLDER}\n\n"
            f"ℹ️ پس از واریز، روی «📤 ارسال رسید» بزنید.")


def topup_summary_kb(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 ارسال رسید", callback_data=f"topupreq:{topup_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"topupcancel:{topup_id}")],
    ])


def topup_waiting_receipt_kb(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"topupback:{topup_id}")]])


def topup_decision_kb(topup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ تأیید", callback_data=f"wapprove:{topup_id}"),
        InlineKeyboardButton(text="❌ رد", callback_data=f"wreject:{topup_id}"),
    ]])


@dp.message(F.text == "💰 کیف پول")
async def wallet_handler(message: Message, state: FSMContext):
    await state.clear()
    balance = await db.get_wallet_balance(message.from_user.id)
    threshold = await db.get_wallet_bonus_threshold()
    bonus_percent = await db.get_wallet_bonus_percent()
    text = (f"💰 <b>کیف پول شما</b>\n\nموجودی: <b>{balance:,} تومان</b>\n\n"
            f"🎁 شارژ {threshold:,} تومان به بالا = <b>{bonus_percent}٪ هدیه</b>")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ شارژ کیف پول", callback_data="topupwallet")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="back:menu")],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data == "topupwallet")
async def start_topup(callback: CallbackQuery, state: FSMContext):
    threshold = await db.get_wallet_bonus_threshold()
    bonus_percent = await db.get_wallet_bonus_percent()
    await state.set_state(WalletStates.entering_topup_amount)
    await callback.message.edit_text(
        f"💳 مبلغ مورد نظر برای شارژ رو وارد کنید (فقط عدد):\n\n🎁 نکته: شارژ {threshold:,}+ = {bonus_percent}٪ هدیه",
        reply_markup=back_menu_kb())
    await callback.answer()


@dp.message(WalletStates.entering_topup_amount)
async def receive_topup_amount(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("فقط عدد بزرگ‌تر از صفر بفرستید.")
        return
    topup_id = await db.create_wallet_topup(message.from_user.id, message.from_user.username or "",
                                             message.from_user.full_name, int(text))
    await state.clear()
    topup = await db.get_wallet_topup(topup_id)
    await message.answer(topup_summary_text(topup), parse_mode="HTML", reply_markup=topup_summary_kb(topup_id))


@dp.callback_query(F.data.startswith("topupcancel:"))
async def cancel_topup(callback: CallbackQuery, state: FSMContext):
    topup_id = int(callback.data.split(":")[1])
    topup = await db.get_wallet_topup(topup_id)
    if topup and topup["user_id"] == callback.from_user.id and topup["status"] in ("awaiting_receipt", "pending"):
        await db.set_topup_status(topup_id, "cancelled")
    await state.clear()
    await callback.message.edit_text("🚫 لغو شد.", reply_markup=back_menu_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("topupreq:"))
async def request_topup_receipt(callback: CallbackQuery, state: FSMContext):
    topup_id = int(callback.data.split(":")[1])
    topup = await db.get_wallet_topup(topup_id)
    if not topup or topup["user_id"] != callback.from_user.id:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    if topup["status"] not in ("awaiting_receipt", "pending"):
        await callback.answer("قابل ارسال رسید نیست.", show_alert=True)
        return
    await state.update_data(topup_id=topup_id)
    await state.set_state(WalletStates.waiting_for_topup_receipt)
    await callback.message.edit_text("📸 رسید واریزی رو ارسال کنید.", reply_markup=topup_waiting_receipt_kb(topup_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("topupback:"))
async def back_to_topup_summary(callback: CallbackQuery, state: FSMContext):
    topup_id = int(callback.data.split(":")[1])
    topup = await db.get_wallet_topup(topup_id)
    if not topup or topup["user_id"] != callback.from_user.id:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(topup_summary_text(topup), parse_mode="HTML", reply_markup=topup_summary_kb(topup_id))
    await callback.answer()


@dp.message(WalletStates.waiting_for_topup_receipt, F.photo | F.document)
async def receive_topup_receipt(message: Message, state: FSMContext):
    data = await state.get_data()
    topup_id = data.get("topup_id")
    if not topup_id:
        await message.answer("مشکلی پیش اومد.")
        await state.clear()
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await db.attach_topup_receipt(topup_id, file_id)
    topup = await db.get_wallet_topup(topup_id)
    await message.answer("🕐 رسید دریافت شد.", reply_markup=main_menu_kb(message.from_user.id))
    await state.clear()
    caption = (f"💰 شارژ کیف پول #{topup_id}\n"
                f"👤 {topup['full_name']} (@{topup['username'] or '-'})\n"
                f"🆔 {topup['user_id']}\n💵 {topup['amount']:,} تومان")
    for admin_id in config.ADMIN_IDS:
        try:
            if message.photo:
                await bot.send_photo(admin_id, photo=file_id, caption=caption, reply_markup=topup_decision_kb(topup_id))
            else:
                await bot.send_document(admin_id, document=file_id, caption=caption, reply_markup=topup_decision_kb(topup_id))
        except Exception as e:
            logging.warning(f"Admin notify failed: {e}")


@dp.message(WalletStates.waiting_for_topup_receipt)
async def waiting_topup_receipt_wrong_input(message: Message):
    await message.answer("لطفاً عکس یا فایل رسید بفرستید 📸")


@dp.callback_query(F.data.startswith("wapprove:"))
async def admin_approve_topup(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    topup_id = int(callback.data.split(":")[1])
    topup = await db.get_wallet_topup(topup_id)
    if not topup or topup["status"] == "approved":
        await callback.answer("قبلاً تأیید شده.", show_alert=True)
        return
    await db.set_topup_status(topup_id, "approved")
    threshold = await db.get_wallet_bonus_threshold()
    bonus_percent = await db.get_wallet_bonus_percent()
    bonus = int(topup["amount"] * bonus_percent / 100) if topup["amount"] >= threshold else 0
    await db.add_wallet_balance(topup["user_id"], topup["amount"] + bonus)
    new_balance = await db.get_wallet_balance(topup["user_id"])
    bonus_note = f"\n🎁 هدیه: {bonus:,} تومان" if bonus > 0 else ""
    try:
        await bot.send_message(topup["user_id"],
            f"✅ کیف پول شارژ شد: {topup['amount']:,} تومان{bonus_note}\n💰 موجودی: {new_balance:,} تومان")
    except Exception as e:
        logging.warning(f"User notify failed: {e}")
    await callback.message.answer(f"✅ شارژ #{topup_id} تأیید شد.")
    await callback.answer()


@dp.callback_query(F.data.startswith("wreject:"))
async def admin_reject_topup(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    topup_id = int(callback.data.split(":")[1])
    topup = await db.get_wallet_topup(topup_id)
    if not topup:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    await db.set_topup_status(topup_id, "rejected")
    try:
        await bot.send_message(topup["user_id"], "❌ درخواست شارژ رد شد.")
    except Exception as e:
        logging.warning(f"User notify failed: {e}")
    await callback.message.answer(f"❌ شارژ #{topup_id} رد شد.")
    await callback.answer()


# ---------- Support & Rules ----------
@dp.message(F.text == "💬 پشتیبانی")
async def support_handler(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 ارتباط با پشتیبانی", url=f"https://t.me/{config.SUPPORT_USERNAME}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="back:menu")],
    ])
    await message.answer("💬 برای ارتباط با پشتیبانی روی دکمه زیر بزنید:", reply_markup=kb)


DEFAULT_RULES_TEXT = (
    "📜 <b>قوانین پنل نمایندگی پاسارگارد</b>\n\n"
    "۱️⃣ خرید سرویس = پذیرش قوانین.\n"
    "۲️⃣ اطلاعات سرویس فقط برای استفاده شخصی شماست.\n"
    "۳️⃣ پس از ارسال رسید، سفارش در سریع‌ترین زمان بررسی می‌شه.\n"
    "۴️⃣ پورسانت به‌صورت خودکار بعد از تحویل سرویس واریز می‌شه.\n"
    "۵️⃣ وجه سرویس‌های تحویل‌داده‌شده قابل استرداد نیست.\n"
    "۶️⃣ استفاده از سرویس‌ها برای فعالیت غیرقانونی ممنوعه.\n"
    "۷️⃣ قیمت‌ها ممکنه بدون اطلاع تغییر کنن.\n\n"
    "با تشکر از اعتماد شما 🙏"
)


@dp.message(F.text == "📜 قوانین")
async def rules_handler(message: Message, state: FSMContext):
    await state.clear()
    custom_rules = await db.get_rules_text()
    text = custom_rules if custom_rules else DEFAULT_RULES_TEXT
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


# ---------- Agency Stats ----------
@dp.message(F.text == "📈 آمار فروش من")
async def sales_stats(message: Message, state: FSMContext):
    await state.clear()
    agency = await db.get_agency_info(message.from_user.id)
    level_info = config.AGENCY_LEVELS.get(agency["level"], config.AGENCY_LEVELS["bronze"])
    next_level = None
    for lvl, data in config.AGENCY_LEVELS.items():
        if data["min_sales"] > agency["total_sales"]:
            next_level = (lvl, data)
            break
    text = (f"📈 <b>آمار فروش نمایندگی شما</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🏆 سطح: <b>{level_info['name']}</b>\n"
            f"💵 مجموع فروش: <b>{agency['total_sales']:,} تومان</b>\n"
            f"🎁 مجموع پورسانت: <b>{agency['total_commission']:,} تومان</b>\n"
            f"📊 نرخ پورسانت: <b>{level_info['commission']}٪</b>\n")
    if next_level:
        remaining = next_level[1]["min_sales"] - agency["total_sales"]
        text += (f"\n━━━━━━━━━━━━━━━━━━\n🎯 تا <b>{next_level[1]['name']}</b>:\n"
                  f"💰 {remaining:,} تومان فروش دیگه\n📈 پورسانت جدید: {next_level[1]['commission']}٪")
    else:
        text += "\n\n💎 <b>شما در بالاترین سطح هستید!</b>"
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "🎁 پورسانت و پاداش")
async def commission_menu(message: Message, state: FSMContext):
    await state.clear()
    agency = await db.get_agency_info(message.from_user.id)
    balance = await db.get_wallet_balance(message.from_user.id)
    level_info = config.AGENCY_LEVELS.get(agency["level"], config.AGENCY_LEVELS["bronze"])
    text = (f"🎁 <b>پورسانت و پاداش پاسارگارد</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"💰 موجودی: <b>{balance:,} تومان</b>\n"
            f"🎯 پورسانت هر فروش: <b>{level_info['commission']}٪</b>\n\n"
            f"📌 <b>سطوح نمایندگی:</b>\n")
    for lvl, data in config.AGENCY_LEVELS.items():
        emoji = "▶️" if lvl == agency["level"] else "▫️"
        text += f"{emoji} {data['name']} - {data['min_sales']:,}+ → {data['commission']}٪\n"
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


@dp.message(F.text == "🤝 زیرمجموعه‌گیری")
async def sub_agencies(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    total = await db.count_referrals(user_id)
    converted = await db.count_converted_referrals(user_id)
    total_earned = await db.get_total_referral_earnings(user_id)
    percent = await db.get_referral_commission_percent()
    text = (f"🤝 <b>زیرمجموعه‌گیری پاسارگارد</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🔗 لینک اختصاصی:\n<code>{link}</code>\n\n"
            f"👥 تعداد دعوت‌شده: <b>{total}</b>\n"
            f"✅ خرید موفق: <b>{converted}</b>\n"
            f"💰 مجموع پورسانت: <b>{total_earned:,} تومان</b>\n\n"
            f"🎁 هر خرید موفق زیرمجموعه = <b>{percent}٪ پورسانت دائمی</b>")
    await message.answer(text, parse_mode="HTML", reply_markup=back_menu_kb())


# ---------- Admin ----------
def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 تعرفه‌های گیمینگ", callback_data="admintariff:gaming")],
        [InlineKeyboardButton(text="🌍 تعرفه‌های پاسارگارد", callback_data="admintariff:multi")],
        [InlineKeyboardButton(text="✉️ پیام خوش‌آمد", callback_data="adminwelcome")],
        [InlineKeyboardButton(text="📜 ویرایش قوانین", callback_data="adminrules")],
        [InlineKeyboardButton(text="🎟 کدهای تخفیف", callback_data="admincoupons")],
        [InlineKeyboardButton(text="🤝 تنظیمات رفرال", callback_data="adminreferral")],
        [InlineKeyboardButton(text="💳 تخفیف شارژ کیف پول", callback_data="adminwalletbonus")],
        [InlineKeyboardButton(text="🔙 بازگشت به منو", callback_data="back:menu")],
    ])


back_to_admin_root_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="admintariff:root")]])
ADMIN_ROOT_TEXT = "⚙️ <b>پنل مدیریت پاسارگارد</b>"


@dp.message(Command("admin"))
@dp.message(F.text == "🛠 پنل مدیریت")
async def admin_panel_entry(message: Message, state: FSMContext):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    await state.clear()
    await message.answer(ADMIN_ROOT_TEXT, parse_mode="HTML", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "admintariff:root")
async def admintariff_root(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(ADMIN_ROOT_TEXT, parse_mode="HTML", reply_markup=admin_menu_kb())
    await callback.answer()


async def gaming_admin_list_kb() -> InlineKeyboardMarkup:
    plans = await db.get_gaming_plans(active_only=False)
    rows = []
    for p in plans:
        status = "✅" if p["active"] else "🚫"
        rows.append([
            InlineKeyboardButton(text=f"{status} {p['volume_gb']} گیگ - {p['price']:,}", callback_data=f"gpriceedit:{p['id']}"),
            InlineKeyboardButton(text="🚫" if p["active"] else "✅", callback_data=f"gtoggle:{p['id']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ افزودن تعرفه", callback_data="gadd")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admintariff:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def multi_admin_list_kb() -> InlineKeyboardMarkup:
    plans = await db.get_multi_plans(active_only=False)
    rows = []
    for p in plans:
        status = "✅" if p["active"] else "🚫"
        rows.append([
            InlineKeyboardButton(text=f"{status} {p['label']} - {p['price']:,}", callback_data=f"mpriceedit:{p['id']}"),
            InlineKeyboardButton(text="🚫" if p["active"] else "✅", callback_data=f"mtoggle:{p['id']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ افزودن تعرفه", callback_data="madd")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admintariff:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "admintariff:gaming")
async def admintariff_gaming(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text("🎮 <b>تعرفه‌های گیمینگ</b>", parse_mode="HTML", reply_markup=await gaming_admin_list_kb())
    await callback.answer()


@dp.callback_query(F.data == "admintariff:multi")
async def admintariff_multi(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await callback.message.edit_text("🌍 <b>تعرفه‌های پاسارگارد</b>", parse_mode="HTML", reply_markup=await multi_admin_list_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("gtoggle:"))
async def toggle_gaming(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await db.toggle_gaming_active(int(callback.data.split(":")[1]))
    await callback.message.edit_reply_markup(reply_markup=await gaming_admin_list_kb())
    await callback.answer("تغییر کرد.")


@dp.callback_query(F.data.startswith("mtoggle:"))
async def toggle_multi(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await db.toggle_multi_active(int(callback.data.split(":")[1]))
    await callback.message.edit_reply_markup(reply_markup=await multi_admin_list_kb())
    await callback.answer("تغییر کرد.")


@dp.callback_query(F.data.startswith("gpriceedit:"))
async def start_edit_gaming_price(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_gaming_plan(plan_id)
    if not plan:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    await state.update_data(plan_id=plan_id)
    await state.set_state(AdminStates.editing_gaming_price)
    await callback.message.answer(f"قیمت جدید برای {plan['volume_gb']} گیگ:")
    await callback.answer()


@dp.message(AdminStates.editing_gaming_price)
async def save_gaming_price(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", "").strip()
    if not text.isdigit():
        await message.answer("فقط عدد بفرستید.")
        return
    data = await state.get_data()
    await db.update_gaming_price(data["plan_id"], int(text))
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=await gaming_admin_list_kb())


@dp.callback_query(F.data.startswith("mpriceedit:"))
async def start_edit_multi_price(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_multi_plan(plan_id)
    if not plan:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    await state.update_data(plan_id=plan_id)
    await state.set_state(AdminStates.editing_multi_price)
    await callback.message.answer(f"قیمت جدید برای «{plan['label']}»:")
    await callback.answer()


@dp.message(AdminStates.editing_multi_price)
async def save_multi_price(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", "").strip()
    if not text.isdigit():
        await message.answer("فقط عدد بفرستید.")
        return
    data = await state.get_data()
    await db.update_multi_price(data["plan_id"], int(text))
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=await multi_admin_list_kb())


@dp.callback_query(F.data == "gadd")
async def start_add_gaming(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.adding_gaming_volume)
    await callback.message.answer("حجم جدید (گیگ):")
    await callback.answer()


@dp.message(AdminStates.adding_gaming_volume)
async def add_gaming_volume(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("فقط عدد.")
        return
    await state.update_data(volume=int(message.text))
    await state.set_state(AdminStates.adding_gaming_price)
    await message.answer("قیمت (تومان):")


@dp.message(AdminStates.adding_gaming_price)
async def add_gaming_price(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", "").strip()
    if not text.isdigit():
        await message.answer("فقط عدد.")
        return
    data = await state.get_data()
    await db.add_gaming_plan(data["volume"], int(text))
    await state.clear()
    await message.answer("✅ اضافه شد.", reply_markup=await gaming_admin_list_kb())


@dp.callback_query(F.data == "madd")
async def start_add_multi(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.adding_multi_label)
    await callback.message.answer("عنوان پلن جدید:")
    await callback.answer()


@dp.message(AdminStates.adding_multi_label)
async def add_multi_label(message: Message, state: FSMContext):
    label = (message.text or "").strip()
    if not label:
        await message.answer("عنوان معتبر بفرستید.")
        return
    await state.update_data(label=label)
    await state.set_state(AdminStates.adding_multi_price)
    await message.answer("قیمت (تومان):")


@dp.message(AdminStates.adding_multi_price)
async def add_multi_price(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", "").strip()
    if not text.isdigit():
        await message.answer("فقط عدد.")
        return
    data = await state.get_data()
    await db.add_multi_plan(data["label"], int(text))
    await state.clear()
    await message.answer("✅ اضافه شد.", reply_markup=await multi_admin_list_kb())


# ---------- Admin: Approve / Reject orders ----------
@dp.callback_query(F.data.startswith("approve:"))
async def admin_approve(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    await db.set_order_status(order_id, "approved")
    await state.update_data(order_id=order_id)
    await state.set_state(AdminStates.waiting_for_panel_info)
    await callback.message.answer(f"✅ سفارش #{order_id} تأیید شد. اطلاعات سرویس رو بفرستید:")
    await callback.answer()


@dp.message(AdminStates.waiting_for_panel_info)
async def admin_send_panel_info(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    order = await db.get_order(order_id)
    if not order:
        await message.answer("سفارش پیدا نشد.")
        await state.clear()
        return
    panel_info = message.text or message.caption or ""
    await db.deliver_order(order_id, panel_info)
    await state.clear()

    try:
        await bot.send_message(order["user_id"],
            f"🎉 سفارش #{order_id} تحویل داده شد!\n\n🔑 اطلاعات سرویس:\n{panel_info}")
    except Exception as e:
        await message.answer(f"⚠️ ارسال به کاربر ناموفق: {e}")

    # 🎁 پورسانت نمایندگی
    try:
        agency = await db.get_agency_info(order["user_id"])
        level_info = config.AGENCY_LEVELS.get(agency["level"], config.AGENCY_LEVELS["bronze"])
        commission = int(order["price"] * level_info["commission"] / 100)
        if commission > 0:
            await db.add_wallet_balance(order["user_id"], commission)
            new_level = await db.update_agency_sales(order["user_id"], order["price"])
            try:
                await bot.send_message(order["user_id"],
                    f"🎁 پورسانت واریز شد!\n\n💵 فروش: {order['price']:,}\n"
                    f"📊 پورسانت ({level_info['commission']}٪): <b>{commission:,} تومان</b>\n"
                    f"💰 موجودی: {await db.get_wallet_balance(order['user_id']):,}",
                    parse_mode="HTML")
                if new_level:
                    new_info = config.AGENCY_LEVELS[new_level]
                    await bot.send_message(order["user_id"],
                        f"🏆 سطح شما به <b>{new_info['name']}</b> ارتقا یافت!\nپورسانت جدید: <b>{new_info['commission']}٪</b>",
                        parse_mode="HTML")
            except Exception as e:
                logging.warning(f"Commission notify failed: {e}")
    except Exception as e:
        logging.error(f"Commission error: {e}")

    # 🤝 پورسانت رفرال
    referral = await db.get_referral_by_referred(order["user_id"])
    if referral:
        if not referral["converted"]:
            await db.mark_referral_converted(order["user_id"])
        referrer_id = referral["referrer_id"]
        if order["price"] and order["price"] > 0:
            commission_percent = await db.get_referral_commission_percent()
            commission_amount = int(order["price"] * commission_percent / 100)
            if commission_amount > 0:
                await db.add_wallet_balance(referrer_id, commission_amount)
                await db.add_referral_commission(referrer_id, order["user_id"], order_id, commission_amount)
                try:
                    await bot.send_message(referrer_id,
                        f"💸 یکی از دعوت‌شده‌هات خرید کرد!\n"
                        f"مبلغ {commission_amount:,} تومان ({commission_percent}٪) به کیف پولت اضافه شد 🎉")
                except Exception as e:
                    logging.warning(f"Referral notify failed: {e}")

    await message.answer(f"✅ سفارش #{order_id} تحویل و پورسانت واریز شد.")


@dp.callback_query(F.data.startswith("reject:"))
async def admin_reject(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)
    if not order:
        await callback.answer("پیدا نشد.", show_alert=True)
        return
    await db.set_order_status(order_id, "rejected")
    refund_note = ""
    if order["payment_method"] == "wallet" and order["price"] > 0:
        await db.add_wallet_balance(order["user_id"], order["price"])
        refund_note = f"\n💰 {order['price']:,} تومان به کیف پول برگشت."
    try:
        await bot.send_message(order["user_id"], f"❌ سفارش #{order_id} رد شد.{refund_note}")
    except Exception as e:
        logging.warning(f"User notify failed: {e}")
    await callback.message.answer(f"❌ سفارش #{order_id} رد شد.")
    await callback.answer()


# ---------- Admin: Welcome / Rules ----------
@dp.callback_query(F.data == "adminwelcome")
async def admin_edit_welcome(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    current = await db.get_welcome_message()
    current_display = current if current else "(پیش‌فرض)"
    await state.set_state(AdminStates.editing_welcome_message)
    await callback.message.edit_text(
        f"✉️ <b>پیام خوش‌آمد فعلی:</b>\n\n{current_display}\n\n━━━━━━━━━━━━━━━\nمتن جدید رو بفرستید:",
        parse_mode="HTML", reply_markup=back_to_admin_root_kb)
    await callback.answer()


@dp.message(AdminStates.editing_welcome_message)
async def save_welcome_message(message: Message, state: FSMContext):
    text = message.text or message.caption
    if not text:
        await message.answer("متن معتبر بفرستید.")
        return
    await db.set_welcome_message(text)
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "adminrules")
async def admin_edit_rules(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    current = await db.get_rules_text()
    current_display = current if current else DEFAULT_RULES_TEXT
    await state.set_state(AdminStates.editing_rules_text)
    await callback.message.edit_text(
        f"📜 <b>قوانین فعلی:</b>\n\n{current_display}\n\n━━━━━━━━━━━━━━━\nمتن جدید:",
        parse_mode="HTML", reply_markup=back_to_admin_root_kb)
    await callback.answer()


@dp.message(AdminStates.editing_rules_text)
async def save_rules_text(message: Message, state: FSMContext):
    text = message.text or message.caption
    if not text:
        await message.answer("متن معتبر بفرستید.")
        return
    await db.set_rules_text(text)
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=admin_menu_kb())


# ---------- Admin: Referral ----------
def referral_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ تغییر درصد پورسانت", callback_data="editrefpercent")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admintariff:root")],
    ])


@dp.callback_query(F.data == "adminreferral")
async def admin_referral_settings(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    percent = await db.get_referral_commission_percent()
    await callback.message.edit_text(
        f"🤝 <b>تنظیمات رفرال</b>\n\n💸 درصد پورسانت فعلی: <b>{percent}٪</b>",
        parse_mode="HTML", reply_markup=referral_settings_kb())
    await callback.answer()


@dp.callback_query(F.data == "editrefpercent")
async def start_edit_referral_percent(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.editing_referral_percent)
    await callback.message.edit_text("درصد پورسانت (۱ تا ۱۰۰):", reply_markup=back_to_admin_root_kb)
    await callback.answer()


@dp.message(AdminStates.editing_referral_percent)
async def save_referral_percent(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await message.answer("عدد بین ۱ تا ۱۰۰.")
        return
    await db.set_setting("referral_commission_percent", int(text))
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=referral_settings_kb())


# ---------- Admin: Coupons ----------
async def coupons_admin_kb() -> InlineKeyboardMarkup:
    coupons = await db.list_coupons()
    rows = []
    for c in coupons:
        status = "✅" if c["active"] else "🚫"
        usage = f"{c['used_count']}/{c['max_uses']}" if c["max_uses"] is not None else f"{c['used_count']}/∞"
        rows.append([
            InlineKeyboardButton(text=f"{status} {c['code']} - {c['percent']}٪ ({usage})", callback_data=f"coupontoggle:{c['code']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"coupondelete:{c['code']}"),
        ])
    rows.append([InlineKeyboardButton(text="➕ ساخت کد جدید", callback_data="coupadd")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admintariff:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "admincoupons")
async def admin_coupons_root(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🎟 <b>کدهای تخفیف</b>", parse_mode="HTML", reply_markup=await coupons_admin_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("coupontoggle:"))
async def toggle_coupon(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await db.toggle_coupon_active(callback.data.split(":", 1)[1])
    await callback.message.edit_text("🎟 <b>کدهای تخفیف</b>", parse_mode="HTML", reply_markup=await coupons_admin_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("coupondelete:"))
async def delete_coupon_handler(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await db.delete_coupon(callback.data.split(":", 1)[1])
    await callback.message.edit_text("🎟 <b>کدهای تخفیف</b>", parse_mode="HTML", reply_markup=await coupons_admin_kb())
    await callback.answer("حذف شد.")


@dp.callback_query(F.data == "coupadd")
async def start_add_coupon(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.adding_coupon_code)
    await callback.message.edit_text("کد تخفیف (فقط حروف و عدد):", reply_markup=back_to_admin_root_kb)
    await callback.answer()


@dp.message(AdminStates.adding_coupon_code)
async def add_coupon_step_code(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    if not code.isalnum():
        await message.answer("فقط حروف و عدد.")
        return
    if await db.get_coupon(code):
        await message.answer("این کد موجوده.")
        return
    await state.update_data(coupon_code=code)
    await state.set_state(AdminStates.adding_coupon_percent)
    await message.answer("درصد تخفیف (۱ تا ۱۰۰):")


@dp.message(AdminStates.adding_coupon_percent)
async def add_coupon_step_percent(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await message.answer("عدد ۱ تا ۱۰۰.")
        return
    await state.update_data(coupon_percent=int(text))
    await state.set_state(AdminStates.adding_coupon_maxuses)
    await message.answer("حداکثر استفاده (0 = نامحدود):")


@dp.message(AdminStates.adding_coupon_maxuses)
async def add_coupon_step_maxuses(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("فقط عدد.")
        return
    max_uses = int(text) if int(text) > 0 else None
    data = await state.get_data()
    await db.create_coupon(data["coupon_code"], data["coupon_percent"], max_uses)
    await state.clear()
    usage_text = f"{max_uses} بار" if max_uses else "نامحدود"
    await message.answer(f"✅ کد <b>{data['coupon_code']}</b> با {data['coupon_percent']}٪ و ظرفیت {usage_text} ساخته شد.",
        parse_mode="HTML", reply_markup=await coupons_admin_kb())


# ---------- Admin: Wallet Bonus ----------
def wallet_bonus_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ تغییر آستانه", callback_data="editwalletthreshold")],
        [InlineKeyboardButton(text="✏️ تغییر درصد", callback_data="editwalletbonuspercent")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admintariff:root")],
    ])


@dp.callback_query(F.data == "adminwalletbonus")
async def admin_wallet_bonus_settings(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.clear()
    threshold = await db.get_wallet_bonus_threshold()
    percent = await db.get_wallet_bonus_percent()
    await callback.message.edit_text(
        f"💳 <b>تخفیف پلکانی کیف پول</b>\n\n📊 آستانه: <b>{threshold:,} تومان</b>\n🎁 درصد: <b>{percent}٪</b>",
        parse_mode="HTML", reply_markup=wallet_bonus_kb())
    await callback.answer()


@dp.callback_query(F.data == "editwalletthreshold")
async def start_edit_wallet_threshold(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.editing_wallet_bonus_threshold)
    await callback.message.edit_text("حداقل مبلغ برای هدیه (تومان):", reply_markup=back_to_admin_root_kb)
    await callback.answer()


@dp.message(AdminStates.editing_wallet_bonus_threshold)
async def save_wallet_threshold(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("عدد مثبت.")
        return
    await db.set_setting("wallet_bonus_threshold", int(text))
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=wallet_bonus_kb())


@dp.callback_query(F.data == "editwalletbonuspercent")
async def start_edit_wallet_bonus_percent(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return
    await state.set_state(AdminStates.editing_wallet_bonus_percent)
    await callback.message.edit_text("درصد هدیه (۱ تا ۱۰۰):", reply_markup=back_to_admin_root_kb)
    await callback.answer()


@dp.message(AdminStates.editing_wallet_bonus_percent)
async def save_wallet_bonus_percent(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or not (1 <= int(text) <= 100):
        await message.answer("عدد ۱ تا ۱۰۰.")
        return
    await db.set_setting("wallet_bonus_percent", int(text))
    await state.clear()
    await message.answer("✅ بروزرسانی شد.", reply_markup=wallet_bonus_kb())


# ---------- Startup ----------
async def main():
    global BOT_USERNAME
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN تنظیم نشده!")
    if not config.ADMIN_IDS:
        logging.warning("ADMIN_IDS تنظیم نشده!")

    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    BOT_USERNAME = me.username
    logging.info(f"✅ Bot started as @{BOT_USERNAME}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
