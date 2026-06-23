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
COOLDOWN = 1800  # seconds

# =========================
# TRADING CONFIGURATION
# =========================

LEVERAGE = 25
MARGIN_PER_TRADE = 0.84

# =========================
# TECHNICAL FILTER CONFIGURATION
# =========================

ADX_FILTER = 18
MIN_SCORE = 80
ATR_FILTER = 0.4

# =========================
# SYMBOLS CONFIGURATION
# =========================

symbols = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    # 'DOGE/USDT:USDT',   # Removed: 0% WR live + backtest
    # 'INJ/USDT:USDT',    # Removed: 0% WR live + backtest
    'LINK/USDT:USDT',
    'SUI/USDT:USDT',
    'AAVE/USDT:USDT',
    'AVAX/USDT:USDT',
    'NEAR/USDT:USDT',
    'HYPE/USDT:USDT',
    # 'FET/USDT:USDT',    # Removed: 0% WR, worst PnL -28.81%
    'TAO/USDT:USDT',
  ]

# =========================
# AUTO TRADE CONFIGURATION
# =========================

AUTO_TRADE = False
AUTO_TRADE_MIN_GRADE = "A"
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2
MAX_ACTIVE_TRADES = 2  # Global limit for total active positions (LONG + SHORT)

PULLBACK_MIN_DISTANCE_PCT = 0.05   # เดิม 0.15 — ลดให้ fill ได้มากขึ้น

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

TREND_MIN_ADX = 30
TREND_MIN_ATR = 0.45
TREND_HIGH_VOLUME_ONLY = False

# =========================
# ADX CEILING & STRETCH LIMIT FILTERS
# =========================

ADX_CEILING_LIMIT = 50         # Maximum allowed ADX. If higher, trend is overextended. (was 55)
STRETCH_MAX_DISTANCE_PCT = 1.2 # Maximum allowed distance (%) between current price and entry EMA. (was 1.5)

# =========================
# RSI SAFE ZONE FILTERS
# =========================
TREND_SHORT_MIN_RSI = 45      # ห้าม SHORT ใน Trend Mode หาก RSI 15m ต่ำกว่านี้
TREND_LONG_MAX_RSI = 55       # ห้าม LONG ใน Trend Mode หาก RSI 15m สูงกว่านี้

MOMENTUM_SHORT_MIN_RSI = 45   # ห้าม SHORT ใน Momentum Mode หาก RSI 15m ต่ำกว่านี้
MOMENTUM_LONG_MAX_RSI = 55    # ห้าม LONG ใน Momentum Mode หาก RSI 15m สูงกว่านี้

# =========================
# SIDEWAYS STRATEGY FILTERS
# =========================

SIDEWAYS_MAX_ADX = 28
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

MARKET_REGIME_ADX_TRENDING = 30   # BTC ADX >= this → TRENDING
MARKET_REGIME_ADX_SIDEWAYS = 25   # BTC ADX <  this → SIDEWAYS
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

SIGNAL_COOLDOWN = 1800  # seconds (same as COOLDOWN)

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
MOMENTUM_SL_ATR_MULT = 2.0

# TP: higher RR than Trend (2.0) — momentum can run far
MOMENTUM_TP_RR = 2.5

# Auto trade — ON but with stricter filters below
MOMENTUM_AUTO_TRADE = True
MOMENTUM_MIN_GRADE = "A"           # Only A+ (Trend uses "A")
MOMENTUM_MIN_SCORE = 85           # Only score >= 90 (Trend uses 85)
MOMENTUM_MAX_TRADES = 1             # Max 1 position at a time (Trend uses 2)

# =========================
# SCALPING STRATEGY CONFIG
# =========================

# Symbols — เริ่มจาก BTC เท่านั้น (high liquidity, tight spread)
# เพิ่มเหรียญอื่นได้ภายหลังเช่น ETH, SOL
SCALPING_SYMBOLS = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
]

# Scan & Timing
SCALPING_SCAN_INTERVAL = 60        # Scan ทุก 1 นาที (ปกติ 300s)
SCALPING_COOLDOWN = 300            # Cooldown 5 นาที (ปกติ 1800s)
SCALPING_PENDING_EXPIRY = 300      # Pending expire 5 นาที (ปกติ 3600s)

# Entry
SCALPING_ENTRY_TYPE = "MARKET"     # Market order (fill ทันที ไม่รอ pullback)

# Risk Management
SCALPING_SL_ATR_MULT = 1.5        # SL = ATR * 1.5 (wider for scalping safety)
SCALPING_TP_RR = 1.5              # TP = risk * 1.5 (เพิ่ม RR เพื่อให้ Net PnL ชนะค่าธรรมเนียม)
SCALPING_LEVERAGE = 25             # Leverage x25
SCALPING_MARGIN_PER_TRADE = 0.84   # ขั้นต่ำ BingX

# Filters
SCALPING_MIN_ADX = 20             # ต้องมี direction พอสมควร
SCALPING_MIN_ATR_PCT = 0.15       # ATR% ขั้นต่ำ (ต้องมี volatility)
SCALPING_MAX_ADX = 50             # ADX สูงเกินหมายถึง overextended
SCALPING_MIN_SCORE = 70           # Score ขั้นต่ำ
SCALPING_MIN_GRADE = "A"          # Grade ขั้นต่ำสำหรับ auto trade

# RSI Safe Zone
SCALPING_LONG_MAX_RSI = 65        # ห้าม LONG ถ้า RSI > 65
SCALPING_SHORT_MIN_RSI = 35       # ห้าม SHORT ถ้า RSI < 35

# Position Limits
SCALPING_MAX_TRADES = 1           # Max 1 scalping position
SCALPING_AUTO_TRADE = True        # Auto trade ON by default

# Auto-Detection Thresholds (BTC 5m)
# Scalping regime = moderate activity zone (between trend and sideways)
SCALPING_DETECT_ADX_MIN = 15      # ADX ขั้นต่ำ (ต้องไม่ dead)
SCALPING_DETECT_ADX_MAX = 28      # ADX สูงสุด (ถ้าสูงกว่านี้ = trending)
SCALPING_DETECT_ATR_MIN = 0.15    # ATR% ขั้นต่ำ (ต้องมี movement)
SCALPING_DETECT_ATR_MAX = 0.50    # ATR% สูงสุด (ถ้าสูงกว่านี้ = volatile)

# =========================
# PAUSE REGIME CONFIG
# =========================

# Dead Market Protection: When ADX and ATR drop below these levels,
# the market is considered too quiet to trade safely.
PAUSE_MAX_ADX = 15
PAUSE_MAX_ATR = 0.15

# =========================
# TRAILING STOP CONFIGURATION
# =========================

TRAILING_ACTIVATION_ATR = 1.5      # กำไรต้องถึงกี่ ATR ถึงจะเริ่มบังทุน
TRAILING_BUFFER_ATR = 1.0          # ระยะห่างจากราคาปัจจุบัน (Buffer)
TRAILING_STEP_ATR = 0.5            # ขยับ SL ขั้นต่ำกี่ ATR ต่อครั้งเพื่อถนอม API