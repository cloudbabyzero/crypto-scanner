"""
Configuration file for Crypto Scanner Bot.
Contains all configuration constants and initialization.
"""

import os
import telebot

# =========================
# TELEGRAM CONFIGURATION
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Create bot instance
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# =========================
# SYMBOLS CONFIGURATION
# =========================

symbols = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'SUI/USDT:USDT',
    'AAVE/USDT:USDT',
    'AVAX/USDT:USDT',
    'XRP/USDT:USDT',
]

SCALPING_SYMBOLS = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'SUI/USDT:USDT',
    'AAVE/USDT:USDT',
    'AVAX/USDT:USDT',
    'XRP/USDT:USDT',
]

# =========================
# AUTO TRADE CONFIGURATION
# =========================

AUTO_TRADE = True
MAX_ACTIVE_TRADES = 2  # Global limit for total active positions (LONG + SHORT)
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2


PULLBACK_MIN_DISTANCE_PCT = 0.05

GRADE_PRIORITY = {
    "A+": 4,
    "A": 3,
    "B": 2,
    "C": 1
}

ALLOW_PENDING_OVERRIDE = True
MIN_SCORE_GAP_TO_OVERRIDE = 3

MAX_CONSECUTIVE_LOSSES = 2  # FIX (Aug 19): 3→2, pause bot sooner after consecutive losses
LOSS_STREAK_RESET_ON_WIN = True
# Auto-resume trading after loss streak pause (minutes). Set 0 to disable auto-resume.
PAUSE_RESUME_MINUTES = 60

# =========================
# BINGX API CONFIGURATION
# =========================

BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY")

# =========================
# SYSTEM CONFIGURATION
# =========================

HEARTBEAT_INTERVAL = 3600
DEBUG_ORDER_STATUS = False
TOP_CANDIDATES_COUNT = 5

MODE = "FORCE_SCALPING"  # MOMENTUM, TRENDING, SCALPING, SIDEWAYS, AUTO, SCALP_SIDEWAYS, FORCE_SCALPING

MARKET_REGIME_ADX_TRENDING = 30
MARKET_REGIME_ADX_SIDEWAYS = 25
MARKET_REGIME_ATR_VOLATILE = 1.0

# =========================
# TRAILING STOP CONFIGURATION
# =========================

# TREND trailing (wider)
TRAILING_ACTIVATION_ATR = 1.5
TRAILING_BUFFER_ATR = 1.0
TRAILING_STEP_ATR = 0.5

# SCALPING trailing — SCALP_TRAILING_ACTIVATION_ATR/BUFFER_ATR are used for the
# initial Phase 1->2 hand-off only (the first SL move once trailing takes over).
# This replaced the old single-phase 1.5/1.0/0.2 tuning (Aug 12-13) — that logic
# path no longer runs, so the values were changed in place rather than kept as
# a second unused set.
SCALP_TRAILING_ACTIVATION_ATR = 2.0
SCALP_TRAILING_BUFFER_ATR = 1.2  # trail sits 1.2x ATR behind price (locks in >= 0.8x ATR profit)

# =========================
# 2-PHASE PROFIT MANAGEMENT (SCALPING, Aug 20 upgrade)
# =========================
# Phase 1 — Auto-Breakeven: as soon as price reaches this many ATR in profit,
# pull SL to entry +/- a small offset so the trade is risk-free.
SCALP_BREAKEVEN_ACTIVATION_ATR = 1.2
SCALP_BREAKEVEN_OFFSET_PCT = 0.15   # locked at entry +/- 0.15% to cover round-trip taker fees

# Phase 2 hand-off (first move into trailing) uses SCALP_TRAILING_ACTIVATION_ATR / BUFFER above.
# Every subsequent SL update while already in Phase 2 uses the Tiered Dynamic
# Tightening below instead of a fixed multiplier — replaces the old fixed
# 0.3x ATR step trail (Aug 20 refactor, SCALPING only).

# =========================
# TIERED TRAILING STOP (SCALPING Phase 2, Aug 20 refactor)
# =========================
# Trailing buffer tightens as current RR (relative to the trade's ORIGINAL
# stop distance) climbs — locks in more profit the further price runs.
TRAILING_TIER_1_ATR_MULT = 1.2  # RR < 2.0
TRAILING_TIER_2_ATR_MULT = 0.8  # 2.0 <= RR < 3.0
TRAILING_TIER_3_ATR_MULT = 0.5  # RR >= 3.0

# =========================
# STRATEGY CONFIGURATION (ISOLATED)
# =========================

STRATEGY_CONFIG = {
    "MOMENTUM": {
        "BASE_TF": "3m",
        "MACRO_TF": "1h",
        "SCAN_INTERVAL": 300,
        "COOLDOWN": 1800,
        "PENDING_EXPIRY": 3600,
        "ENTRY_TYPE": "MARKET",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 0.84,
        "SL_ATR_MULT": 1.2,
        "TP_RR": 1.66,
        "MAX_TRADES": 2,
        "MIN_SCORE": 85,
        "MIN_GRADE": "A",
        "FILTERS": {
            "MIN_ADX": 30,
            "MAX_ADX": 100,
            "MIN_ATR_PCT": 0.0,
            "RSI_MIN_LONG": 55,
            "RSI_MAX_SHORT": 45
        }
    },
    "TRENDING": {
        "BASE_TF": "15m",
        "MACRO_TF": "1h",
        "SCAN_INTERVAL": 300,
        "COOLDOWN": 1800,
        "PENDING_EXPIRY": 3600,
        "ENTRY_TYPE": "LIMIT",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 0.84,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 1.5,
        "MAX_TRADES": 2,
        "MIN_SCORE": 75,
        "MIN_GRADE": "A+",
        "FILTERS": {
            "MIN_ADX": 20,
            "MAX_ADX": 100,
            "MIN_ATR_PCT": 0.25,
            "RSI_SAFE_LONG_MAX": 65,
            "RSI_SAFE_SHORT_MIN": 35
        }
    },
    "SCALPING": {
        # FIX (Aug 20): BASE_TF 5m → 15m — 5m bottom wicks routinely run -0.15% to -0.25%,
        # which swallowed the old 1.1x ATR SL (-0.13% to -0.22%) mid-wick. 15m candles have
        # proportionally smaller noise-to-range ratio, so a same-multiple ATR SL survives normal chop.
        "BASE_TF": "15m",
        # FIX (Aug 20): MACRO_TF 15m → 1h — confirmation timeframe raised to match new base TF,
        # keeps macro trend read one full step above entry TF.
        "MACRO_TF": "1h",
        "SCAN_INTERVAL": 60,
        # FIX: COOLDOWN raised 300→900s — NEAR entered 9x in 48min, 900s=15min gap between re-entries
        "COOLDOWN": 900,
        # Post-exit cooldowns, keyed off the actual close result of the last trade (last_closed_trade)
        "WIN_COOLDOWN": 600,       # พัก 10 นาทีหลังปิดไม้กำไร (TRAILED / TP)
        # FIX: LOSS_COOLDOWN — after a LOSS, block same symbol for 30 min
        # NEAR had 3 consecutive losses = 78% of total losses; 1800s prevents re-entry too soon
        "LOSS_COOLDOWN": 1800,     # พัก 30 นาทีหลังปิดไม้แพ้ (SL)
        "PENDING_EXPIRY": 300,
        "ENTRY_TYPE": "MARKET",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 1.0,
        # FIX (Aug 20): SL_ATR_MULT 1.1 → 1.5 — restore wider SL now that it's measured against
        # 15m ATR (proportionally larger candles), so 1.5x ATR gives room to survive normal wicks
        # without ballooning the actual % risk versus the old 5m 1.1x setting.
        "SL_ATR_MULT": 1.5,
        # --- Dynamic / Adaptive Stop Loss (per-asset volatility profile) ---
        # If ATR% at signal time >= threshold -> High Noise / Shield Mode (wider SL mult)
        # If ATR% < threshold -> Normal Mode (base SL mult above)
        "DYNAMIC_SL_ENABLED": True,
        "DYNAMIC_SL_ATR_THRESHOLD": 1.00,   # ATR% breakpoint, recalibrated for 15m candles
        "DYNAMIC_SL_MULT_NORMAL": 1.5,      # used when ATR% < threshold
        "DYNAMIC_SL_MULT_HIGH_NOISE": 1.8,  # used when ATR% >= threshold
        # TP_RR kept at 2.0 — net WIN should stay meaningfully larger than net LOSS after fees
        "TP_RR": 2.0,
        # FIX: MAX_TRADES 3→2 — 3 concurrent = 3 simultaneous losses on reversal
        "MAX_TRADES": 2,  # FIX: 3→2, limit concurrent losses on reversal
        # Grade is relative to MIN_SCORE:
        #   A+ = score >= MIN_SCORE+10  (90+)
        #   A  = score >= MIN_SCORE     (80+)  ← target
        #   B  = score >= MIN_SCORE-10  (70+)
        "MIN_SCORE": 80,
        "MIN_GRADE": "A",
        # --- Max holding time & inactivity exit (new: Aug 20) ---
        "MAX_HOLDING_HOURS": 8,          # hard cap — force-close any position held longer than this
        "INACTIVITY_TIMEOUT_MIN": 360,   # after 6h, if PnL is stuck within +-0.3%, force-close to free margin
        "INACTIVITY_PNL_BAND_PCT": 0.3,  # PnL%% band considered "stuck" for the inactivity exit
        "FILTERS": {
            # FIX (Aug 28): MIN_ADX 20 → 25 — cut low-momentum chop entries (e.g. AAVE ADX 23.06
            # was scoring as tradeable under the old floor)
            "MIN_ADX": 20,
            # FIX (Aug 27): MAX_ADX widened 50 → 55 — allow strong-trend coins like SOL/SUI through
            "MAX_ADX": 55,
            # --- ATR Volatility Guard Settings, recalibrated for 15m candles ---
            "MIN_ATR_PCT": 0.20,          # [Floor พื้นล่างสุด] ห้ามต่ำกว่านี้เด็ดขาด ป้องกันตลาดนิ่งจนไม่คุ้มค่าธรรมเนียม (ผ่อนจาก 0.35 — BTC/AAVE ~0.25-0.30% เดิมโดนบล็อกเป็น Chop ทั้งที่วิ่งปกติ)
            "MAX_ATR_PCT": 1.50,          # [Hard Ceiling เพดานสูงสุด] ห้ามเกินนี้เด็ดขาด ป้องกันตลาดคลั่ง/แทงไส้ลากกิน SL
            "MIN_CEILING_ATR_PCT": 0.65,  # [Minimum Ceiling เพดานขั้นต่ำ] ยกเพดานให้เหรียญใหญ่ (BTC/ETH) เพื่อให้มีช่วงว่างวิ่งเทรดได้
            # FIX (Aug 27): tightened 68/32 → 60/38 — block LONG entries near overbought peak
            # and SHORT entries near oversold bottom (RSI_SAFE_LONG_MAX / RSI_SAFE_SHORT_MIN)
            "RSI_SAFE_LONG_MAX": 60,
            "RSI_SAFE_SHORT_MIN": 38
        }
    },
    "SIDEWAYS": {
        "BASE_TF": "15m",
        "MACRO_TF": "1h",
        "SCAN_INTERVAL": 300,
        "COOLDOWN": 1800,
        "PENDING_EXPIRY": 3600,
        "ENTRY_TYPE": "LIMIT_EDGE",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 0.84,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 1.5,
        "MAX_TRADES": 2,
        "MIN_SCORE": 75,
        "MIN_GRADE": "A",
        "FILTERS": {
            "MAX_ADX": 25,
            "MIN_ATR_PCT": 0.20,
            "RSI_SAFE_LONG_MAX": 55,
            "RSI_SAFE_SHORT_MIN": 45
        }
    }
}

def get_strategy_config(mode):
    return STRATEGY_CONFIG.get(mode, STRATEGY_CONFIG["TRENDING"])

# Auto-Detection Thresholds
SCALPING_DETECT_ADX_MIN = 15
SCALPING_DETECT_ADX_MAX = 28
SCALPING_DETECT_ATR_MIN = 0.20
SCALPING_DETECT_ATR_MAX = 1.20
PAUSE_MAX_ADX = 15
PAUSE_MAX_ATR = 0.15

# MOMENTUM DETECTION CONFIG
MOMENTUM_MIN_PRICE_DISTANCE = 0.5
MOMENTUM_MIN_CANDLES = 3

# =========================
# AI FILTER CONFIGURATION
# =========================

AI_FILTER_ENABLED = True
AI_FILTER_SHADOW_MODE = True
AI_FILTER_PROVIDER = "google"
AI_FILTER_MODEL = "gemini-3.5-flash"
AI_FILTER_MIN_CONFIDENCE = 75
AI_FILTER_TIMEOUT = 5

