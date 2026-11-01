"""
Configuration file for Crypto Scanner Bot.
Architecture: Spot Signal-Driven DCA + 3-Tier Infinity Run (The Core 5)
"""

import os
import telebot

# =========================
# TELEGRAM CONFIGURATION
# =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# =========================
# BINGX API KEYS
# =========================
BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY")

# =========================
# THE CORE 5 UNIVERSE (SPOT)
# =========================
symbols = [
    'AAVE/USDT',
    'SOL/USDT',
    'BTC/USDT',
    'ETH/USDT',
    'LINK/USDT'
]
SCALPING_SYMBOLS = symbols

# =========================
# SPOT CAPITAL ALLOCATION
# =========================
AUTO_TRADE = True
ORDER_AMOUNT_USDT = 6.0       # ขนาดไม้คงที่ 6 USDT
MIN_USDT_BALANCE = 6.0        # ยอดเงินสดขั้นต่ำที่ยอมให้ยิงซื้อ
MAX_ACTIVE_COINS = 5          # จำนวนเหรียญสูงสุดที่ถือพร้อมกัน
MIN_DCA_DRAWDOWN_PCT = 3.0    # ต้องย่อต่ำกว่าทุนเฉลี่ยอย่างน้อย 3.0% ถึงจะช้อนไม้ถัดไป

# =========================
# 3-TIER INFINITY RUN CONFIG
# =========================
INFINITY_ACTIVATION_PCT = 0.030  # แตะ +3.0% เริ่มรัน Trailing
INFINITY_MIN_LOCK_PCT = 0.018    # ล็อกกำไรขั้นต่ำทันทีที่ +1.8%
TRAILING_PCT_TIER1 = 0.018       # Tier 1: Trail 1.8%
TRAILING_PCT_TIER2 = 0.025       # Tier 2: Trail 2.5%
TRAILING_PCT_TIER3 = 0.040       # Tier 3: Trail 4.0%

# =========================
# SCANNER & SYSTEM CONFIG
# =========================
SCAN_INTERVAL = 60
HEARTBEAT_INTERVAL = 3600
COOLDOWN = 900
MODE = "FORCE_SCALPING"          # บังคับวิ่งเข้า analyze_scalping 100%

# =====================================================================
# STRATEGY_CONFIG (ใส่ COOLDOWN และ TP_RR ป้องกัน KeyError)
# =====================================================================
STRATEGY_CONFIG = {
    "SCALPING": {
        "BASE_TF": "15m",
        "MACRO_TF": "1h",
        "SCAN_INTERVAL": 60,
        "COOLDOWN": 900,
        "WIN_COOLDOWN": 600,
        "LOSS_COOLDOWN": 1800,
        "TP_RR": 2.0,             # ป้องกัน KeyError ใน analyze_scalping
        "SL_ATR_MULT": 1.5,
        "FILTERS": {
            "MIN_ADX": 18,                            # ลดจาก 20 เป็น 18 เพื่อรองรับช่วงเริ่มสะสมพลัง/กลับตัวต้นคลื่น
            "MAX_ADX": 50,
            "STRONG_TREND_ADX_BYPASS": 25,            # ลดจาก 28 เป็น 25 (ใช้คู่กับเงื่อนไข EMA25 Slope > 0 ใน main.py)
            "STOCH_OVERBOUGHT_MAX": 85,               # ขยายเพดาน StochRSI ปกติจาก 80 เป็น 85 (ไม่บล็อกจังหวะเริ่มไต่เทรนด์)
            "ENABLE_UPTREND_STOCH_BYPASS": True,      # อนุญาตให้ข้ามเพดาน StochRSI เมื่อเป็นขาขึ้น Grinding Uptrend ที่ผ่านโครงสร้างหลักครบ
            "UPTREND_SLOPE_BYPASS_MIN": 0.03,         # ความชัน 15m EMA25 ขั้นต่ำ (%) ที่ยืนยันว่าเป็นขาขึ้นจริงสำหรับข้ามเพดาน StochRSI
            "RSI_SAFE_LONG_MAX": 68,                  # ขยายจาก 60 เป็น 68 (เปิดรับโมเมนตัมขาขึ้นที่แข็งแรง)
            "RSI_SAFE_SHORT_MIN": 38,
            "ANTI_CHASE_ATR_MULT": 0.8,                # ขยายตัวคูณจาก 0.6 เป็น 0.8
            "ANTI_CHASE_MIN_PCT": 0.30,                # ขยายระยะห่างขั้นต่ำจาก 0.15% เป็น 0.30%

            # --- Spot DCA entry-quality gates (ปิด/เปิดได้ทีละข้อ) ---
            "ENABLE_1H_STOCH_OVERSOLD_BLOCK": False,  # False = ไม่บล็อกตอน 1H StochRSI ต่ำ (DCA ต้องซื้อโซนย่อ)
            "ENABLE_EMA99_ROOM": True,                # ห้ามซื้อถ้าราคาอยู่ใต้ 15m EMA99 แต่เหลือพื้นที่ไม่ถึง X ATR
            "EMA99_ROOM_ATR": 1.2,
            "ENABLE_SWING_HIGH_CLEARANCE": True,      # ห้ามซื้อใต้ Swing High 12 แท่ง ถ้าห่างไม่ถึง X ATR
            "SWING_HIGH_CLEARANCE_ATR": 1.3,
            "ENABLE_5M_MICRO_TREND": True,            # 5m ต้อง EMA7>=EMA25 และ close>=EMA7
            "ENABLE_15M_LOWER_HIGHS": True,           # ห้ามซื้อถ้า 15m ทำยอดเตี้ยลง 3 แท่งติด

            # --- Scout & Sniper (2-Stage Architecture: หน่วยเฝ้าระวัง + มือสไนเปอร์คุม Gate) ---
            "ENABLE_SCOUT_SNIPER": True,              # เปิดระบบแยก 2 ฝั่ง: Scout (เฝ้าระวัง) + Sniper (คุม Gate ลั่นไก)
            "SCOUT_TTL_SECONDS": 3600,                # อายุการเฝ้าระวังใน Hot Watchlist (60 นาที = 4 แท่ง 15m)
            "SCOUT_MIN_ADX_REVERSAL": 14,             # ผ่อนปรน ADX ขั้นต่ำเหลือ 14 เฉพาะเหรียญใน Watchlist ที่กำลังกลับตัวจากฐาน
            "SCOUT_OVERSOLD_RSI": 45,                 # เกณฑ์ 15m RSI โซนสะสมพลัง/ขายมากเกินไปสำหรับ Scout
            "SCOUT_OVERSOLD_STOCH": 35,               # เกณฑ์ 15m StochRSI โซนฐานราคาสำหรับ Scout
            "SCOUT_VOL_SPIKE_MULT": 1.35,             # เกณฑ์ Volume สูงกว่าค่าเฉลี่ยสำหรับตรวจจับแรงรับของ (Absorption)
            "SNIPER_SINGLE_CANDLE_VWAP": True,        # ถ้า 5m Micro-Trend แข็งแรง อนุญาตให้ยืนเหนือ VWAP แท่งเดียวผ่านได้เลย ไม่ต้องรอดีเลย์ 2 แท่ง

            # --- Dynamic ATR-Scaled ADX & StochRSI (ปรับเกณฑ์อัตโนมัติตามความผันผวน ATR%) ---
            "ENABLE_DYNAMIC_ATR_ADX_STOCH": True,     # เปิดใช้การปรับ MIN_ADX และเพดาน StochRSI ตามค่า ATR%
            "DYNAMIC_ATR_LOW_PCT": 0.60,              # ATR% <= 0.60% (ตลาดนิ่ง/แคบ) ➔ เข้มงวด: MIN_ADX = 20.0, STOCH_MAX = 80.0
            "DYNAMIC_ATR_HIGH_PCT": 1.50,             # ATR% >= 1.50% (ผันผวนสูง/กลับตัวแรง) ➔ ไวขึ้น: MIN_ADX = 14.0, STOCH_MAX = 92.0
            "DYNAMIC_ADX_QUIET_MAX": 20.0,            # เพดานขั้นต่ำ ADX ตอนตลาดนิ่ง
            "DYNAMIC_ADX_VOLATILE_MIN": 14.0,         # เพดานขั้นต่ำ ADX ตอนตลาดผันผวนสูง
            "DYNAMIC_STOCH_QUIET_MAX": 80.0,          # เพดาน StochRSI ตอนตลาดนิ่ง (กันซื้อยอดกรอบไซด์เวย์)
            "DYNAMIC_STOCH_VOLATILE_MAX": 92.0        # เพดาน StochRSI ตอนตลาดผันผวนสูง (กันบล็อกค้างตอนแท่งเขียวพุ่งแรง)
        }
    },
    "TRENDING": {
        "FILTERS": {"MIN_ADX": 20, "MIN_ATR_PCT": 0.25, "RSI_SAFE_LONG_MAX": 65, "RSI_SAFE_SHORT_MIN": 35},
        "MIN_SCORE": 75, "MIN_GRADE": "A+", "SL_ATR_MULT": 1.5, "TP_RR": 1.5, "COOLDOWN": 1800
    },
    "SIDEWAYS": {
        "FILTERS": {"MAX_ADX": 25, "MIN_ATR_PCT": 0.20},
        "MIN_SCORE": 75, "MIN_GRADE": "A", "SL_ATR_MULT": 1.5, "TP_RR": 1.5, "COOLDOWN": 1800
    },
    "MOMENTUM": {
        "FILTERS": {"MIN_ADX": 30, "RSI_MIN_LONG": 55, "RSI_MAX_SHORT": 45},
        "MIN_SCORE": 85, "MIN_GRADE": "A", "SL_ATR_MULT": 1.2, "TP_RR": 1.66, "COOLDOWN": 1800, "MAX_TRADES": 2
    }
}

def get_strategy_config(mode):
    return STRATEGY_CONFIG.get(mode, STRATEGY_CONFIG["SCALPING"])

# =====================================================================
# BACKWARD COMPATIBILITY CONSTANTS (ป้องกัน ImportError ใน main.py)
# =====================================================================
MAX_LONG_TRADES = 5
MAX_SHORT_TRADES = 0
MAX_ACTIVE_TRADES = 5
GRADE_PRIORITY = {"A+": 4, "A": 3, "B": 2, "C": 1}
TOP_CANDIDATES_COUNT = 5
MAX_CONSECUTIVE_LOSSES = 5
LOSS_STREAK_RESET_ON_WIN = True
PULLBACK_MIN_DISTANCE_PCT = 0.05
ALLOW_PENDING_OVERRIDE = False
MIN_SCORE_GAP_TO_OVERRIDE = 3
SCALPING_DETECT_ADX_MIN = 15
SCALPING_DETECT_ADX_MAX = 28
SCALPING_DETECT_ATR_MIN = 0.20
SCALPING_DETECT_ATR_MAX = 1.20
PAUSE_MAX_ADX = 15
PAUSE_MAX_ATR = 0.15
MARKET_REGIME_ADX_TRENDING = 30
MARKET_REGIME_ADX_SIDEWAYS = 25
MARKET_REGIME_ATR_VOLATILE = 1.0

# Momentum detection thresholds (ใช้ใน indicators.detect_momentum)
MOMENTUM_MIN_PRICE_DISTANCE = 0.5   # % ระยะห่างของ close จาก EMA7 (4h)
MOMENTUM_MIN_CANDLES = 3            # จำนวนแท่งเทียนทิศเดียวกันขั้นต่ำ

# =====================================================================
# ACTIVE EQUAL-WEIGHTED INDEX & RISK SAFEGUARDS CONFIG
# =====================================================================
MAX_PORTFOLIO_ALLOC_PCT = 0.20          # เพดานสูงสุด 20% ของมูลค่าพอร์ตต่อเหรียญ
MIN_PORTFOLIO_ALLOC_FLOOR_USDT = 12.0   # เพดานขั้นต่ำสำหรับพอร์ตเล็ก (ซื้อได้ 2 ไม้เสมอ)

FLASH_CRASH_DROP_PCT = 12.0             # แท่ง 1H ร่วงเกิน 12% ➔ พักช้อน
FLASH_CRASH_FREEZE_HOURS = 12           # ระยะเวลาแช่แข็งการซื้อ (ชั่วโมง)

SCALED_TP_ENABLED = True
SCALED_TP_MULTI_ENTRY_PCT = 0.020       # สะสม >= 2 ไม้ ลดเป้า Infinity Run เหลือ +2.0%
SCALED_TP_MULTI_LOCK_PCT = 0.012        # สะสม >= 2 ไม้ ล็อกกำไรขั้นต่ำ +1.2%

SPOT_ROUNDTRIP_FEE_PCT = 0.002          # ค่าธรรมเนียมไป-กลับรวม 0.2%
DEPOSIT_DETECT_MIN_USDT = 10.0          # ยอดเงินเข้าขั้นต่ำที่ถือว่าเป็นการเติมเงิน

def get_activation_params(entry_count):
    """คืน (activation_pct, min_lock_pct) ตามจำนวนไม้ที่สะสม (Scaled-Down TP)"""
    if SCALED_TP_ENABLED and int(entry_count or 1) >= 2:
        return SCALED_TP_MULTI_ENTRY_PCT, SCALED_TP_MULTI_LOCK_PCT
    return INFINITY_ACTIVATION_PCT, INFINITY_MIN_LOCK_PCT

# =====================================================================
# SCOUT & SNIPER + DYNAMIC ATR DCA STEP + STAGED EXIT (50/50) CONFIG
# =====================================================================
SCOUT_NOTIFY_TELEGRAM = True            # แจ้งเตือน Telegram เมื่อหน่วย Scout ล็อกเป้าเหรียญเข้า Watchlist
SCOUT_ALERT_COOLDOWN = 7200             # Cooldown แจ้งเตือน Scout ต่อเหรียญ (2 ชั่วโมง กันสแปม)

DYNAMIC_DCA_STEP_ENABLED = True         # ขยายระยะช้อนไม้ถัดไปตามความผันผวน (ATR%) อัตโนมัติ
DYNAMIC_DCA_ATR_MULT = 1.8              # ตัวคูณ ATR: Step = max(MIN_DCA_DRAWDOWN_PCT, ATR% * 1.8)
DYNAMIC_DCA_MAX_PCT = 8.0               # เพดานระยะย่อตัวสูงสุดไม่เกิน 8.0%

STAGED_EXIT_ENABLED = True              # แบ่งขายดึงเงินสดทันทีเมื่อแตะเป้า Infinity Run
STAGED_EXIT_MIN_ENTRIES = 2             # แบ่งขายเฉพาะเมื่อสะสมตั้งแต่ 2 ไม้ขึ้นไป (1 ไม้ขาย 100% ตามเดิม)
STAGED_EXIT_RATIO = 0.50                # สัดส่วนแบ่งขายก้อนแรก (0.50 = ขาย 50% ดึงเงินสด / 50% รัน Trailing ต่อ)
STAGED_EXIT_MIN_NOTIONAL_USDT = 5.0     # มูลค่าขั้นต่ำต่อก้อน (USDT) ป้องกันติดกฎ Minimum Notional บนกระดาน

def get_dynamic_dca_step(atr_pct=0.0):
    """คำนวณระยะย่อตัวขั้นต่ำ (%) สำหรับช้อนไม้ถัดไป ตามความผันผวน ATR ปัจจุบัน"""
    base_step = float(MIN_DCA_DRAWDOWN_PCT)
    if not DYNAMIC_DCA_STEP_ENABLED or not atr_pct or float(atr_pct) <= 0:
        return base_step
    dyn_step = round(float(atr_pct) * float(DYNAMIC_DCA_ATR_MULT), 2)
    return min(float(DYNAMIC_DCA_MAX_PCT), max(base_step, dyn_step))

def get_dynamic_adx_stoch_bounds(atr_pct=0.0, is_fast_reversal=False):
    """คำนวณ (dynamic_min_adx, dynamic_stoch_max) ผันแปรตามความผันผวน ATR% แบบ Linear Scaling:
    - ตลาดนิ่ง (ATR% ต่ำ <= 0.60%): เข้มงวดขึ้น (MIN_ADX = 20.0, STOCH_MAX = 80.0) กันสัญญาณหลอกในกรอบแคบ
    - ตลาดผันผวน/เริ่มวิ่ง (ATR% สูง >= 1.50%): ไวขึ้น (MIN_ADX = 14.0, STOCH_MAX = 92.0) จับต้นคลื่นโดยไม่ติดแล็ก ADX หรือ Stoch ค้าง"""
    filters = STRATEGY_CONFIG.get("SCALPING", {}).get("FILTERS", {})
    if not filters.get("ENABLE_DYNAMIC_ATR_ADX_STOCH", True) or not atr_pct or float(atr_pct) <= 0:
        base_adx = float(filters.get("SCOUT_MIN_ADX_REVERSAL", 14) if is_fast_reversal else filters.get("MIN_ADX", 18))
        base_stoch = float(filters.get("STOCH_OVERBOUGHT_MAX", 85))
        return base_adx, base_stoch

    low_atr = float(filters.get("DYNAMIC_ATR_LOW_PCT", 0.60))
    high_atr = float(filters.get("DYNAMIC_ATR_HIGH_PCT", 1.50))
    adx_quiet = float(filters.get("DYNAMIC_ADX_QUIET_MAX", 20.0))
    adx_vol = float(filters.get("DYNAMIC_ADX_VOLATILE_MIN", 14.0))
    stoch_quiet = float(filters.get("DYNAMIC_STOCH_QUIET_MAX", 80.0))
    stoch_vol = float(filters.get("DYNAMIC_STOCH_VOLATILE_MAX", 92.0))

    span = max(0.01, high_atr - low_atr)
    ratio = min(1.0, max(0.0, (float(atr_pct) - low_atr) / span))

    # Inverse scaling สำหรับ ADX (ATR สูง -> ใช้ ADX ต่ำลงเพื่อเข้าไว)
    dyn_min_adx = round(adx_quiet - (ratio * (adx_quiet - adx_vol)), 1)
    if is_fast_reversal:
        dyn_min_adx = min(dyn_min_adx, float(filters.get("SCOUT_MIN_ADX_REVERSAL", 14.0)))

    # Direct scaling สำหรับเพดาน StochRSI (ATR สูง -> ขยายเพดานขึ้นเพื่อไม่ให้บล็อกค้างตอนออกตัวแรง)
    dyn_stoch_max = round(stoch_quiet + (ratio * (stoch_vol - stoch_quiet)), 1)
    return dyn_min_adx, dyn_stoch_max