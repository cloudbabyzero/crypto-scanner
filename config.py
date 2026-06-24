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
    'NEAR/USDT:USDT',
    'HYPE/USDT:USDT',
    'TAO/USDT:USDT',
]

SCALPING_SYMBOLS = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
]

# =========================
# AUTO TRADE CONFIGURATION
# =========================

AUTO_TRADE = True
AUTO_TRADE_MIN_GRADE = "A"
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

MAX_CONSECUTIVE_LOSSES = 5
LOSS_STREAK_RESET_ON_WIN = True

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

MODE = "AUTO"

MARKET_REGIME_ADX_TRENDING = 30
MARKET_REGIME_ADX_SIDEWAYS = 25
MARKET_REGIME_ATR_VOLATILE = 1.0

# =========================
# TRAILING STOP CONFIGURATION
# =========================

TRAILING_ACTIVATION_ATR = 1.5
TRAILING_BUFFER_ATR = 1.0
TRAILING_STEP_ATR = 0.5

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
        "ENTRY_TYPE": "LIMIT_PULLBACK",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 0.84,
        "SL_ATR_MULT": 2.0,
        "TP_RR": 1.2,
        "MAX_TRADES": 2,
        "MIN_SCORE": 70,
        "MIN_GRADE": "A",
        "FILTERS": {
            "MIN_ADX": 20,
            "MAX_ADX": 100,
            "MIN_ATR_PCT": 0.25,
            "RSI_SAFE_LONG_MAX": 65,
            "RSI_SAFE_SHORT_MIN": 35
        }
    },
    "SCALPING": {
        "BASE_TF": "3m",
        "MACRO_TF": "15m",
        "SCAN_INTERVAL": 60,
        "COOLDOWN": 300,
        "PENDING_EXPIRY": 300,
        "ENTRY_TYPE": "MARKET",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 0.84,
        "SL_ATR_MULT": 1.5,
        "TP_RR": 1.0,
        "MAX_TRADES": 1,
        "MIN_SCORE": 60,
        "MIN_GRADE": "A",
        "FILTERS": {
            "MIN_ADX": 15,
            "MAX_ADX": 28,
            "RSI_SAFE_LONG_MAX": 45,
            "RSI_SAFE_SHORT_MIN": 55
        }
    },
    "SIDEWAYS": {
        "BASE_TF": "15m",
        "MACRO_TF": "1h",
        "SCAN_INTERVAL": 300,
        "COOLDOWN": 1800,
        "PENDING_EXPIRY": 3600,
        "ENTRY_TYPE": "LIMIT_PULLBACK",
        "LEVERAGE": 25,
        "MARGIN_PER_TRADE": 0.84,
        "SL_ATR_MULT": 2.0,
        "TP_RR": 1.5,
        "MAX_TRADES": 2,
        "MIN_SCORE": 70,
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
