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
    'SOL/USDT:USDT',
    'XRP/USDT:USDT',
    'DOGE/USDT:USDT',
    'INJ/USDT:USDT',
    'LINK/USDT:USDT',
    'SUI/USDT:USDT',
    'AAVE/USDT:USDT',
    'AVAX/USDT:USDT',
    'ADA/USDT:USDT',
    'NEAR/USDT:USDT',
  ]

# =========================
# AUTO TRADE CONFIGURATION
# =========================

AUTO_TRADE = False
AUTO_TRADE_MIN_GRADE = "A"
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2
MAX_ACTIVE_TRADES = 2  # Global limit for total active positions (LONG + SHORT)

PULLBACK_MIN_DISTANCE_PCT = 0.15

GRADE_PRIORITY = {
    "A+": 4,
    "A": 3,
    "B": 2,
    "C": 1
}

# =========================
# A+ PENDING OVERRIDE CONFIGURATION
# =========================

ALLOW_PENDING_OVERRIDE = True   # Toggle the pending order override system
MIN_SCORE_GAP_TO_OVERRIDE = 3   # New A+ score must beat the pending trade's score by at least this amount

# =========================
# LOSS STREAK PROTECTION
# =========================

MAX_CONSECUTIVE_LOSSES = 5  # Pause trading after this many consecutive losses
LOSS_STREAK_RESET_ON_WIN = True  # Reset loss streak counter when a WIN occurs

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

# =========================
# MARKET REGIME CONTROL MODE
# =========================
# AUTO          - Bot controls regime switching automatically
# FORCE_TREND   - Always use trend mode (manual override)
# FORCE_SIDEWAY - Always use sideway mode (manual override)

MODE = "AUTO"

# =========================
# SIGNAL CACHE
# =========================
# Cache of signal IDs/symbols that have been previously processed.
# These caches persist across scan cycles.

SIGNAL_COOLDOWN = 3600  # seconds (same as COOLDOWN)

# =========================
# TOP CANDIDATES
# =========================
# How many top candidates to show after a regime-change rescan

TOP_CANDIDATES_COUNT = 5

# =========================
# DEBUG ORDER STATUS
# =========================
# If True, the bot will send Telegram messages with order status updates for debugging purposes.

DEBUG_ORDER_STATUS = False

# =========================
# MOMENTUM STRATEGY CONFIG
# =========================

# Detection thresholds (checked on 4h BTC candles)
MOMENTUM_MIN_ADX = 30               # ADX must be stronger than TREND (22)
MOMENTUM_MIN_PRICE_DISTANCE = 0.5   # Price must be >= 0.5% away from EMA7
MOMENTUM_MIN_CANDLES = 3            # Consecutive 4h candles in same direction

# Entry: entry = close ± atr * mult  (closer than Trend's ema7 ± atr*0.2)
MOMENTUM_ENTRY_ATR_MULT = 0.3

# SL: tighter than Trend (1.5) — momentum reverses fast
MOMENTUM_SL_ATR_MULT = 1.2

# TP: higher RR than Trend (2.0) — momentum can run far
MOMENTUM_TP_RR = 2.5

# Auto trade — ON but with stricter filters below
MOMENTUM_AUTO_TRADE = True
MOMENTUM_MIN_GRADE = "A+"           # Only A+ (Trend uses "A")
MOMENTUM_MIN_SCORE = 90             # Only score >= 90 (Trend uses 85)
MOMENTUM_MAX_TRADES = 1             # Max 1 position at a time (Trend uses 2)