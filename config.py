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
# SCAN & TIMING CONFIGURATION
# =========================

SCAN_INTERVAL = 300  # seconds
COOLDOWN = 3600  # seconds

# =========================
# TRADING CONFIGURATION
# =========================

LEVERAGE = 25
MARGIN_PER_TRADE = 0.84

# =========================
# TECHNICAL FILTER CONFIGURATION
# =========================

ADX_FILTER = 20
MIN_SCORE = 85
ATR_FILTER = 0.4

# =========================
# SYMBOLS CONFIGURATION
# =========================

symbols = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    #'DOGE/USDT:USDT',
    'SOL/USDT:USDT',
    'XRP/USDT:USDT',
    'HYPE/USDT:USDT',
    #'ZEC/USDT:USDT',
    #'INJ/USDT:USDT'
]

# =========================
# AUTO TRADE CONFIGURATION
# =========================

AUTO_TRADE = False
AUTO_TRADE_MIN_GRADE = "A"
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

GRADE_PRIORITY = {
    "A+": 4,
    "A": 3,
    "B": 2,
    "C": 1
}

# =========================
# TREND STRATEGY FILTERS
# =========================

TREND_MIN_ADX = 22
TREND_MIN_ATR = 0.45
TREND_HIGH_VOLUME_ONLY = False

# =========================
# SIDEWAYS STRATEGY FILTERS
# =========================

SIDEWAYS_MAX_ADX = 20
SIDEWAYS_MIN_ATR = 0.20
SIDEWAYS_HIGH_VOLUME_ONLY = False

# =========================
# BINGX API CONFIGURATION
# =========================

BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY")

# =========================
# HEARTBEAT CONFIGURATION
# =========================

HEARTBEAT_INTERVAL = 3600  # seconds (60 minutes)

# =========================
# MARKET REGIME DETECTION
# =========================

MARKET_REGIME_ADX_TRENDING = 22   # BTC ADX >= this → TRENDING
MARKET_REGIME_ADX_SIDEWAYS = 18   # BTC ADX <  this → SIDEWAYS
MARKET_REGIME_ATR_VOLATILE = 1.0  # BTC ATR% >= this → VOLATILE
