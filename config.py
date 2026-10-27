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

# =========================
# 3-TIER INFINITY RUN CONFIG
# =========================
INFINITY_ACTIVATION_PCT = 0.025  # แตะ +2.5% เริ่มรัน Trailing
INFINITY_MIN_LOCK_PCT = 0.015    # ล็อกกำไรขั้นต่ำทันทีที่ +1.5%
TRAILING_PCT_TIER1 = 0.012       # Tier 1: Trail 1.2%
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
            "MIN_ADX": 20,
            "MAX_ADX": 50,
            "STRONG_TREND_ADX_BYPASS": 28,
            "RSI_SAFE_LONG_MAX": 60,
            "RSI_SAFE_SHORT_MIN": 38,
            "ANTI_CHASE_ATR_MULT": 0.6,
            "ANTI_CHASE_MIN_PCT": 0.15
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