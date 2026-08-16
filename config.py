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
    'LINK/USDT:USDT',
    'SUI/USDT:USDT',
    'AAVE/USDT:USDT',
    'AVAX/USDT:USDT',
    #'NEAR/USDT:USDT',  <--- แพ้บ่อย แพ้บ่อยมาก แพ้แบบ 100%

]

SCALPING_SYMBOLS = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'LINK/USDT:USDT',
    'SUI/USDT:USDT',
    'AAVE/USDT:USDT',
    'AVAX/USDT:USDT',
    #'NEAR/USDT:USDT',  <--- แพ้บ่อย แพ้บ่อยมาก แพ้แบบ 100%

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

MAX_CONSECUTIVE_LOSSES = 3
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

# SCALPING trailing (tighter)
# Trailing params tuned from actual trade log analysis (Aug 12-13):
# - Avg SL hit distance = 1.62x ATR, min = 0.79x ATR
# - Buffer must be >= 0.6 ATR to survive ETH/SOL micro-wicks
# - Activation 0.8 = starts trail early enough without being too aggressive
# - Step 0.2 = frequent updates, tight profit locking
# FIX: activation raised 0.8→1.5, buffer raised 0.6→0.8
# activation - buffer must exceed fee threshold (0.10% on 37.5 USDT notional)
# Old: 0.8-0.6=0.028% min profit → lost to fee on early trail exits
# New: 1.5-0.8=0.105% min profit → covers fee even at earliest trail exit
# At TP trigger (1.5 RR): profit locked = ~0.203% = ~0.97x RR after fee
SCALP_TRAILING_ACTIVATION_ATR = 1.5  # 0.8 ATR from entry before trail starts
SCALP_TRAILING_BUFFER_ATR = 1.0   # SL trails 1.0 ATR behind price
SCALP_TRAILING_STEP_ATR = 0.2     # updates every 0.2 ATR move

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
        "BASE_TF": "5m",
        "MACRO_TF": "15m",
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
        "MARGIN_PER_TRADE": 1.5,
        # FIX: SL_ATR_MULT raised 1.2 → 1.5 — SL at 1.2 ATR = ~0.13% which is within normal noise,
        # most losses were SL hit by only 0.09-0.20% move, wider SL needed to survive micro-chop
        "SL_ATR_MULT": 1.5,
        # --- Dynamic / Adaptive Stop Loss (per-asset volatility profile) ---
        # If ATR% at signal time >= threshold -> High Noise / Shield Mode (wider SL mult)
        # If ATR% < threshold -> Normal Mode (base SL mult above)
        "DYNAMIC_SL_ENABLED": True,
        "DYNAMIC_SL_ATR_THRESHOLD": 0.22,   # ATR% breakpoint
        "DYNAMIC_SL_MULT_NORMAL": 1.5,      # used when ATR% < threshold
        "DYNAMIC_SL_MULT_HIGH_NOISE": 1.8,  # used when ATR% >= threshold
        # FIX: TP_RR raised 1.5→2.0 — actual RR was 0.61x (WIN avg +0.087 vs LOSS avg -0.143)
        # At 2.0 RR: net WIN ≈ +0.120 USDT vs net LOSS ≈ -0.117 USDT → break-even at 49.4% win rate
        "TP_RR": 2.0,
        # FIX: MAX_TRADES 3→2 — 3 concurrent = 3 simultaneous losses on reversal
        "MAX_TRADES": 2,  # FIX: 3→2, limit concurrent losses on reversal
        # Grade is relative to MIN_SCORE:
        #   A+ = score >= MIN_SCORE+10  (85+)
        #   A  = score >= MIN_SCORE     (75+)  ← target
        #   B  = score >= MIN_SCORE-10  (65+)
        # 75 is reachable even with BTC neutral -15 penalty when EMA+15m+candle+RSI all align
        "MIN_SCORE": 75,
        "MIN_GRADE": "A",
        "FILTERS": {
            # FIX: MIN_ADX raised to 18 — NEAR entered with ADX 11-14 (no momentum = noise)
            "MIN_ADX": 18,
            # FIX: MAX_ADX widened 35 → 45 — allow stronger momentum moves through
            "MAX_ADX": 45,
            # --- ATR Volatility Guard Settings ---
            "MIN_ATR_PCT": 0.15,          # [Floor พื้นล่างสุด] ห้ามต่ำกว่านี้เด็ดขาด ป้องกันตลาดนิ่งจนไม่คุ้มค่าธรรมเนียม
            "MAX_ATR_PCT": 0.45,          # [Hard Ceiling เพดานสูงสุด] ห้ามเกินนี้เด็ดขาด ป้องกันตลาดคลั่ง/แทงไส้ลากกิน SL
            "MIN_CEILING_ATR_PCT": 0.30,  # [Minimum Ceiling เพดานขั้นต่ำ] ยกเพดานให้เหรียญใหญ่ (BTC/ETH) เพื่อให้มีช่วงว่างวิ่งเทรดได้
            "RSI_SAFE_LONG_MAX": 68,
            "RSI_SAFE_SHORT_MIN": 32
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
SCALPING_DETECT_ATR_MIN = 0.15
SCALPING_DETECT_ATR_MAX = 0.50
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

