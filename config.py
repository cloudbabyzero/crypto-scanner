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
# AUTO TRADE EXECUTION FILTERS
# =========================

AUTO_TRADE_MIN_ATR = 0.45
AUTO_TRADE_MIN_ADX = 22
AUTO_TRADE_HIGH_VOLUME_ONLY = False

# =========================
# BINGX API CONFIGURATION
# =========================

BINGX_API_KEY = os.getenv("BINGX_API_KEY")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY")
