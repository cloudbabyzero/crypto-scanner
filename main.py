import requests
import time
import os
import traceback
import csv
import threading
import uuid
import json
from telebot import types

# =========================
# GOOGLE SHEETS - Import google_sheet module
# =========================

import google_sheet

# =========================
# CONFIG - Import from config.py
# =========================

from config import (
    bot,
    TELEGRAM_TOKEN,
    CHAT_ID,
    symbols,
    SCALPING_SYMBOLS,
    AUTO_TRADE,
    MAX_LONG_TRADES,
    MAX_SHORT_TRADES,
    MAX_ACTIVE_TRADES,
    GRADE_PRIORITY,
    HEARTBEAT_INTERVAL,
    MARKET_REGIME_ADX_TRENDING,
    MARKET_REGIME_ADX_SIDEWAYS,
    MARKET_REGIME_ATR_VOLATILE,
    MODE,
    TOP_CANDIDATES_COUNT,
    MAX_CONSECUTIVE_LOSSES,
    LOSS_STREAK_RESET_ON_WIN,
    PULLBACK_MIN_DISTANCE_PCT,
    ALLOW_PENDING_OVERRIDE,
    MIN_SCORE_GAP_TO_OVERRIDE,
    SCALPING_DETECT_ADX_MIN,
    SCALPING_DETECT_ADX_MAX,
    SCALPING_DETECT_ATR_MIN,
    SCALPING_DETECT_ATR_MAX,
    PAUSE_MAX_ADX,
    PAUSE_MAX_ATR,
    STRATEGY_CONFIG,
    get_strategy_config
)
import config

# =========================
# DYNAMIC CONFIG DEFAULTS
# =========================
TREND_HIGH_VOLUME_ONLY = False
SIDEWAYS_HIGH_VOLUME_ONLY = False
ADX_FILTER = 18                   # Used to add bonus score for strong trend
MIN_SCORE = 80                    # Global fallback for telegram overrides

# ADX and Stretch Filters Limits
ADX_CEILING_LIMIT = 60.0          # Maximum allowed ADX (overextended trend penalty)
STRETCH_MAX_DISTANCE_PCT = 2.5    # Maximum allowed distance from EMA in % (stretch penalty)

# =========================
# INDICATORS - Import from indicators.py
# =========================

from indicators import get_dataframe, get_btc_trend, detect_momentum, detect_symbol_regime

# =========================
# EXCHANGE CLIENT
# =========================

from exchange_client import get_exchange, load_markets_if_needed

# Create and share exchange instance for other modules
exchange = get_exchange()

# Import handlers after bot is created
import bingx_client
import telegram_commands
import trade_manager
import backtest

last_alert = {}

active_trades = {}
state_lock = threading.RLock()

BOT_START_TIME = time.time()

scan_results = {}

# Lifetime statistics
scan_counters = {
    "Total Scans": 0,
    "Signal Generated": 0,
    "Sideways Market": 0,
    "Score Below MIN_SCORE": 0,
    "Cooldown": 0,
    "Candle Too Big": 0,
    "Too Close EMA99": 0,
    "Error": 0,
}

# Current scan cycle statistics
cycle_counters = {
    "Total Scans": 0,
    "Signal Generated": 0,
    "Sideways Market": 0,
    "Score Below MIN_SCORE": 0,
    "Cooldown": 0,
    "Candle Too Big": 0,
    "Too Close EMA99": 0,
    "Error": 0,
}

# =========================
# LOSS STREAK TRACKING
# =========================

current_wins = 0
current_losses = 0
current_loss_streak = 0
pause_trading = False

def reset_cycle_counters():
    global cycle_counters

    cycle_counters = {
        "Total Scans": 0,
        "Signal Generated": 0,
        "Sideways Market": 0,
        "Score Below MIN_SCORE": 0,
        "Cooldown": 0,
        "Candle Too Big": 0,
        "Too Close EMA99": 0,
        "Error": 0,
    }

    print("[CYCLE_COUNTERS] Reset", flush=True)


def set_scan_result(symbol, data):
    """Store scan result and update counters."""

    global scan_results
    global scan_counters
    global cycle_counters

    scan_counters["Total Scans"] += 1
    cycle_counters["Total Scans"] += 1

    status = data.get("status", "Unknown")

    if status in scan_counters:
        scan_counters[status] += 1

    if status in cycle_counters:
        cycle_counters[status] += 1

    scan_results[symbol] = data


def calculate_sideways_levels(entry, atr, bb_mid, side, ai_override=None):
    """Calculate SL and TP for sideways mean reversion trades.

    SL: ATR * STRATEGY_CONFIG['SIDEWAYS']['SL_ATR_MULT']
    TP: Bollinger Middle Band
    """
    import config as main_config
    sl_mult = STRATEGY_CONFIG['SIDEWAYS']['SL_ATR_MULT']
    
    if ai_override and ai_override.get("approved") and ai_override.get("confidence", 0) >= getattr(main_config, 'AI_FILTER_MIN_CONFIDENCE', 75):
        if ai_override.get("sl_atr_mult"):
            sl_mult = ai_override.get("sl_atr_mult")

    if side == "LONG":
        sl = round(entry - atr * sl_mult, 4)
        tp = round(bb_mid, 4)
        risk = entry - sl
        reward = tp - entry
    else:
        sl = round(entry + atr * sl_mult, 4)
        tp = round(bb_mid, 4)
        risk = sl - entry
        reward = entry - tp

    rr = round(reward / risk, 2) if risk > 0 else 0
    return sl, tp, rr


# =========================
# MARKET REGIME STATE
# =========================

MARKET_MODE = "TRENDING"

CURRENT_REGIME = "UNKNOWN"
LAST_REGIME = "UNKNOWN"
LAST_REGIME_CHECK = 0
REGIME_CHECK_INTERVAL = 300  # 5 minutes (sync with scan cycle)

# Feature 3 & 4: Signal cache and cooldown bypass
candidate_signals = {}      # Symbols that generated signals (for top candidates)
rejected_signals = set()    # Symbols rejected in current regime
signal_cache = {}           # Cache of signal results by symbol
ignore_cooldown_once = False  # Feature 4: Bypass cooldown for one rescan

# Feature 8: Override mode
CONTROL_MODE = MODE  # "AUTO", "FORCE_TREND", "FORCE_SIDEWAY"

# =========================
# PERSISTENT STORAGE (Feature 7)
# =========================

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
if os.path.isdir(DATA_DIR):
    REGIME_STORAGE_FILE = os.path.join(DATA_DIR, "regime_storage.json")
else:
    REGIME_STORAGE_FILE = "regime_storage.json"


def save_regime_storage():
    """Save current_mode and control_mode to persistent storage.
    
    Feature 7: Current mode must survive restart.
    """
    try:
        data = {
            "MARKET_MODE": MARKET_MODE,
            "CONTROL_MODE": CONTROL_MODE,
            "CURRENT_REGIME": CURRENT_REGIME,
        }
        with open(REGIME_STORAGE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Regime storage saved: {data}", flush=True)
    except Exception as e:
        print(f"Error saving regime storage: {e}", flush=True)


def load_regime_storage():
    """Load saved regime settings from persistent storage.
    
    Feature 7: On startup, load saved configuration before scanning.
    """
    global MARKET_MODE, CONTROL_MODE, CURRENT_REGIME
    try:
        if os.path.exists(REGIME_STORAGE_FILE):
            with open(REGIME_STORAGE_FILE, "r") as f:
                data = json.load(f)
            MARKET_MODE = data.get("MARKET_MODE", MARKET_MODE)
            CONTROL_MODE = data.get("CONTROL_MODE", CONTROL_MODE)
            CURRENT_REGIME = data.get("CURRENT_REGIME", CURRENT_REGIME)
            print(f"Regime storage loaded: {data}", flush=True)
        else:
            print("No regime storage file found, using defaults", flush=True)
    except Exception as e:
        print(f"Error loading regime storage: {e}, using defaults", flush=True)

# =========================
# STATE STORAGE (ACTIVE TRADES)
# =========================

if os.path.isdir(DATA_DIR):
    STATE_STORAGE_FILE = os.path.join(DATA_DIR, "active_trades.json")
else:
    STATE_STORAGE_FILE = "active_trades.json"

def load_active_trades():
    """Load active trades from disk to prevent amnesia on restart."""
    global active_trades
    try:
        if os.path.exists(STATE_STORAGE_FILE):
            with open(STATE_STORAGE_FILE, "r") as f:
                data = json.load(f)
                with state_lock:
                    active_trades = data
            msg = f"🔄 [SYSTEM] โหลดสถานะบอทสำเร็จ\nเรียกคืน {len(active_trades)} ออเดอร์จากหน่วยความจำ"
            send_telegram(msg)
            print(f"[STATE] Loaded {len(active_trades)} trades from {STATE_STORAGE_FILE}", flush=True)
    except Exception as e:
        print(f"[STATE] Error loading {STATE_STORAGE_FILE}: {e}", flush=True)

def save_state_loop():
    """Background loop to auto-save active trades."""
    import time as pytime
    while True:
        try:
            with state_lock:
                data = dict(active_trades)
            with open(STATE_STORAGE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            pass
        pytime.sleep(10)


# =========================
# SIGNAL CACHE MANAGEMENT (Feature 3)
# =========================

def reset_signal_cache():
    """Clear all cached signals when regime changes.
    
    Feature 3: Signals rejected in SIDEWAYS mode may become valid in TRENDING mode.
    Old regime data must not affect the new regime.
    
    Clears:
        candidate_signals - Symbols that generated signals
        rejected_signals  - Symbols rejected in current regime
        signal_cache      - Cached signal results
    """
    global candidate_signals, rejected_signals, signal_cache
    
    print("🔄 Clearing signal cache for regime change...", flush=True)
    
    # Clear all signal caches
    candidate_signals = {}
    rejected_signals = set()
    signal_cache = {}
    
    print("✅ Signal cache cleared", flush=True)


# =========================
# TELEGRAM
# =========================

def send_telegram(message):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:

        requests.post(
            url,
            data=payload,
            timeout=10
        )

    except Exception as e:

        print(
            "Telegram Error:",
            e,
            flush=True
        )


# =========================
# STARTUP MARKET SCAN (Feature 1)
# =========================

def startup_market_scan():
    """Detect current BTC regime immediately after loading storage.
    
    Feature 1: After loading regime_storage.json, detect current regime
    because saved regime may be outdated if market changed while bot was offline.
    
    Flow:
    load_regime_storage()
    → startup_market_scan()
    → immediate_full_rescan()
    → send_top_candidates()
    → bot_ready()
    """
    global CURRENT_REGIME, MARKET_MODE, LAST_REGIME_CHECK
    
    print("[STARTUP_SCAN] Beginning startup market scan", flush=True)
    
    send_telegram("🚀 Bot Started")
    send_telegram("🔍 Startup Market Scan")
    
    try:
        # Detect current BTC market regime
        startup_regime, btc_adx, btc_atr_pct = detect_market_regime()
        btc_trend_str = get_btc_trend().upper()
        
        # Send regime info
        send_telegram(
            f"Regime: {startup_regime}\n"
            f"BTC Trend: {btc_trend_str}\n"
            f"BTC ADX: {btc_adx}\n"
            f"BTC ATR: {btc_atr_pct}%"
        )
        
        print(
            f"[STARTUP_SCAN] Detected regime: {startup_regime}, "
            f"BTC Trend: {btc_trend_str}, "
            f"BTC ADX: {btc_adx}, BTC ATR: {btc_atr_pct}%",
            flush=True
        )
        
        # Update state with live detection
        CURRENT_REGIME = startup_regime
        
        # Set market mode based on control mode
        if CONTROL_MODE == "FORCE_TREND":
            MARKET_MODE = "TRENDING"
            send_telegram("✅ Trend Mode Activated (FORCE_TREND override)")
        elif CONTROL_MODE == "FORCE_SIDEWAY":
            MARKET_MODE = "SIDEWAYS"
            send_telegram("✅ Sideways Mode Activated (FORCE_SIDEWAY override)")
        else:
            new_mode = determine_mode_from_regime(startup_regime)
            MARKET_MODE = new_mode
            send_telegram(f"✅ {new_mode} Mode Activated (auto)")
        
        # Save updated state to persistent storage
        save_regime_storage()
        
        LAST_REGIME_CHECK = time.time()

        # Feature 5: Run immediate full rescan after startup scan (read-only)
        send_telegram("🔄 Startup Full Rescan")
        immediate_full_rescan(is_startup=True, read_only=True)
        
        # Feature 6: Send top candidates from startup rescan
        send_top_candidates()
        
        # Mode summary report
        mode_counts = {}
        mode_text = "📊 COIN MODES REPORT\n\n"
        for sym, res in scan_results.items():
            if isinstance(res, dict):
                m = res.get("mode", "UNKNOWN")
                mode_counts[m] = mode_counts.get(m, 0) + 1
                mode_text += f"{sym}: {m}\n"
        mode_text += "\nSummary:\n"
        for m, c in mode_counts.items():
            mode_text += f"{m}: {c}\n"
        send_telegram(mode_text)

        send_telegram("✅ Bot Ready")
        
    except Exception as e:
        print(f"[STARTUP_SCAN] Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        CURRENT_REGIME = "TRENDING"
        MARKET_MODE = "TRENDING"
        send_telegram("⚠️ Startup regime detection failed, defaulting to TRENDING")


def startup_cleanup():
    """Cancel stale pending limit orders and report open positions.

    Steps:
    1. Fetch all open orders from BingX.
    2. Cancel any pending LIMIT orders.
    3. Count how many were cancelled and send a Telegram notification.
    4. Fetch open positions and report them via Telegram (do not close).
    5. Log a debug entry to Google Sheet.
    """
    try:
        # 1. Fetch open orders
        open_orders = exchange.fetch_open_orders()
        cancelled_count = 0
        for order in open_orders:
            # Identify pending limit orders (type == 'limit' and not filled)
            order_type = (order.get('type') or '').lower()
            status = (order.get('status') or '').lower()
            if order_type == 'limit' and status in ['open', 'new', 'active']:
                try:
                    exchange.cancel_order(order['id'], order['symbol'])
                    cancelled_count += 1
                except Exception as cancel_err:
                    print(f"Failed to cancel order {order.get('id')}: {cancel_err}", flush=True)

        # 3. Send Telegram notification about cancelled orders
        send_telegram(f"🧹 STARTUP CLEANUP\n\nCancelled {cancelled_count} stale pending orders.")

        # 4. Check for open positions (do not close them)
        positions = exchange.fetch_positions()
        open_positions = []
        for pos in positions:
            try:
                contracts = float(pos.get('contracts') or 0)
            except (TypeError, ValueError):
                contracts = 0
            if contracts > 0:
                symbol = pos.get('symbol') or 'UNKNOWN'
                side_raw = (pos.get('side') or pos.get('positionSide') or '').upper()
                side = 'LONG' if side_raw in ['LONG', 'BUY'] else ('SHORT' if side_raw in ['SHORT', 'SELL'] else side_raw)
                open_positions.append(f"{symbol} {side}")
        if open_positions:
            positions_msg = "\n".join(open_positions)
            send_telegram(f"⚠️ OPEN POSITIONS DETECTED\n\n{positions_msg}\n\nManual review required.")

        # 5. Log debug entry to Google Sheet
        google_sheet.log_debug(
            "STARTUP_CLEANUP",
            f"Cancelled {cancelled_count} orders, {len(open_positions)} open positions"
        )
    except Exception as e:
        print(f"Startup cleanup error: {e}", flush=True)
        traceback.print_exc()


# =========================
# AUTO REGIME SWITCHING (Feature 2)
# =========================

def auto_switch_regime(old_regime, new_regime, btc_adx, btc_atr_pct):
    """Automatically switch market mode when regime changes.
    
    Feature 2: Remove dependency on Telegram buttons.
    
    When market regime changes:
    1. Update current mode based on regime
    2. Save mode to persistent storage
    3. Send notification
    4. Clear signal cache (Feature 3)
    5. Set ignore_cooldown_once flag (Feature 4)
    6. Trigger immediate full rescan (Feature 5)
    
    Feature 8: If CONTROL_MODE is FORCE_TREND or FORCE_SIDEWAY,
    auto switching is disabled.
    """
    global MARKET_MODE, CURRENT_REGIME, LAST_REGIME
    global ignore_cooldown_once
    
    # Feature 8: Skip auto-switch if override mode is active
    if CONTROL_MODE == "FORCE_TREND":
        print(f"Regime changed to {new_regime}, but FORCE_TREND override active", flush=True)
        send_telegram(
            f"🚨 MARKET REGIME CHANGED\n\n"
            f"{old_regime} → {new_regime}\n\n"
            f"BTC ADX: {btc_adx}\n"
            f"BTC ATR: {btc_atr_pct}%\n\n"
            f"🔒 FORCE_TREND override active\n"
            f"No mode switch applied."
        )
        CURRENT_REGIME = new_regime
        return
    
    if CONTROL_MODE == "FORCE_SIDEWAY":
        print(f"Regime changed to {new_regime}, but FORCE_SIDEWAY override active", flush=True)
        send_telegram(
            f"🚨 MARKET REGIME CHANGED\n\n"
            f"{old_regime} → {new_regime}\n\n"
            f"BTC ADX: {btc_adx}\n"
            f"BTC ATR: {btc_atr_pct}%\n\n"
            f"🔒 FORCE_SIDEWAY override active\n"
            f"No mode switch applied."
        )
        CURRENT_REGIME = new_regime
        return

    if CONTROL_MODE == "FORCE_MOMENTUM":
        print(f"Regime changed to {new_regime}, but FORCE_MOMENTUM override active", flush=True)
        send_telegram(
            f"🚨 MARKET REGIME CHANGED\n\n"
            f"{old_regime} → {new_regime}\n\n"
            f"BTC ADX: {btc_adx}\n"
            f"BTC ATR: {btc_atr_pct}%\n\n"
            f"🔒 FORCE_MOMENTUM override active\n"
            f"No mode switch applied."
        )
        CURRENT_REGIME = new_regime
        return

    if CONTROL_MODE == "FORCE_SCALPING":
        print(f"Regime changed to {new_regime}, but FORCE_SCALPING override active", flush=True)
        send_telegram(
            f"🚨 MARKET REGIME CHANGED\n\n"
            f"{old_regime} → {new_regime}\n\n"
            f"BTC ADX: {btc_adx}\n"
            f"BTC ATR: {btc_atr_pct}%\n\n"
            f"🔒 FORCE_SCALPING override active\n"
            f"No mode switch applied."
        )
        CURRENT_REGIME = new_regime
        return
        
    if CONTROL_MODE == "FORCE_PAUSE":
        print(f"Regime changed to {new_regime}, but FORCE_PAUSE override active", flush=True)
        # Suppress notifications if it's dead, unless user specifically asks
        CURRENT_REGIME = new_regime
        return
    
    # Determine the new market mode based on regime
    new_mode = determine_mode_from_regime(new_regime)
    
    # Update state
    old_mode = MARKET_MODE
    LAST_REGIME = old_regime
    CURRENT_REGIME = new_regime
    MARKET_MODE = new_mode
    
    # Feature 3: Clear signal cache
    reset_signal_cache()
    # Feature 3b: Cancel pending orders on regime change
    trade_manager.cancel_pending_orders("Market Regime Changed")
    
    # Feature 4: Set ignore_cooldown_once to bypass cooldown for next rescan
    ignore_cooldown_once = True
    print(f"🔄 ignore_cooldown_once = True (regime changed)", flush=True)
    
    # Save to persistent storage (Feature 7)
    save_regime_storage()
    
    # Feature 5: Run immediate full rescan after regime change (read-only)
    immediate_full_rescan(is_startup=False, read_only=True)
    
    # Feature 4: Disable cooldown bypass after rescan completes
    ignore_cooldown_once = False
    
    # Feature 6: Send top candidates from the rescan
    send_top_candidates()
    
    # Build notification message
    if new_regime == "TRENDING":
        regime_icon = "📈"
    elif new_regime == "SIDEWAYS":
        regime_icon = "📉"
    elif new_regime == "MOMENTUM":
        regime_icon = "🚀"
    else:
        regime_icon = "⚡"
    mode_text = f"{regime_icon} Auto Switched To {new_mode} Mode"
    
    notification = (
        f"🚨 MARKET REGIME CHANGED\n\n"
        f"{old_regime} → {new_regime}\n\n"
        f"BTC ADX: {btc_adx}\n"
        f"BTC ATR: {btc_atr_pct}%\n\n"
        f"✅ {mode_text}\n\n"
        f"🔄 Immediate Rescan Complete - Top Candidates Sent"
    )
    
    print(
        f"Regime auto-switch: {old_regime} -> {new_regime}, "
        f"Mode: {old_mode} -> {new_mode}",
        flush=True
    )
    send_telegram(notification)


def determine_mode_from_regime(regime):
    """Map a market regime to the corresponding strategy mode.
    
    TRENDING  -> TRENDING strategy
    SIDEWAYS  -> SIDEWAYS strategy
    VOLATILE  -> TRENDING strategy (trend following for volatility breakout)
    MOMENTUM  -> MOMENTUM strategy
    SCALPING  -> SCALPING strategy
    """
    if regime == "SIDEWAYS":
        return "SIDEWAYS"
    if regime == "MOMENTUM":
        return "MOMENTUM"
    if regime == "SCALPING":
        return "SCALPING"
    if regime == "PAUSE":
        return "PAUSE"
    # TRENDING, VOLATILE, and default all use TREND mode
    return "TRENDING"


# =========================
# IMMEDIATE FULL RESCAN (Feature 5)
# =========================

def immediate_full_rescan(is_startup=False, read_only=True):
    """Run a complete market scan immediately.
    
    Feature 5: When regime changes, run a complete market scan immediately.
    Feature 1: On startup, scan immediately.
    
    Requirements:
    1. Loop through all symbols.
    2. Call analyze(symbol, bypass_cooldown=True)
    3. Rebuild candidate_signals.
    4. Print start/end logs.
    5. Return total scanned count.
    
    Args:
        is_startup: If True, this is a startup scan.
        read_only: If True, do not save signals, create active_trades, or execute trades.
                   All rescans are read-only — they only build candidate_signals for TOP_CANDIDATES.
    
    This does NOT wait for the next scan interval.
    """
    global ignore_cooldown_once, scan_results, candidate_signals
    
    scan_label = "Startup" if is_startup else "Regime Change"
    print(f"[RESCAN_START] {scan_label} Full Rescan Started", flush=True)
    
    # Reset scan results for fresh data
    scan_results = {}
    
    # Clear previous candidate signals for fresh top candidates
    candidate_signals = {}
    
    scanned_count = 0
    
    for symbol in symbols:
        try:
            # Always bypass cooldown, silence signals, and skip all signal creation during rescan
            analyze(symbol, bypass_cooldown=True, silent_mode=True, signal_only=read_only)
            scanned_count += 1
            time.sleep(2)
        except Exception as e:
            print(f"Error scanning {symbol} during rescan: {e}", flush=True)
    
    print(f"[RESCAN_END] {scan_label} Full Rescan Completed - {scanned_count} symbols scanned", flush=True)
    return scanned_count


# =========================
# TOP CANDIDATES (Feature 6)
# =========================

def send_top_candidates():
    """After immediate rescan, send strongest setups found.
    
    Feature 6: Send only symbols that pass all filters.
    
    Sorts candidate_signals by score descending and sends
    the top TOP_CANDIDATES_COUNT results.
    """
    if not candidate_signals:
        print("No candidate signals to report", flush=True)
        return
    
    # Sort candidates by score descending
    sorted_candidates = sorted(
        candidate_signals.values(),
        key=lambda x: x.get("score", 0),
        reverse=True
    )
    
    # Take top N
    top_count = min(TOP_CANDIDATES_COUNT, len(sorted_candidates))
    top_candidates = sorted_candidates[:top_count]
    
    message = "📊 TOP CANDIDATES\n\n"
    
    for i, candidate in enumerate(top_candidates, 1):
        symbol = candidate.get("symbol", "UNKNOWN")
        score = candidate.get("score", 0)
        side = candidate.get("side", "N/A")
        grade = candidate.get("grade", "N/A")
        strategy = candidate.get("strategy", "N/A")
        icon = "🚀" if side == "LONG" else "🔻"
        
        message += (
            f"{i}. {symbol}\n"
            f"   Score: {score}\n"
            f"   Side: {icon} {side}\n"
            f"   Grade: {grade}\n"
            f"   Strategy: {strategy}\n\n"
        )
    
    send_telegram(message.strip())
    print(f"Top candidates sent: {len(top_candidates)} signals", flush=True)


# =========================
# CONFIG PERSISTENCE
# =========================

def save_config():
    """Save strategy filter configuration to config.json"""
    try:
        config_data = {
            "STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX']": STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX'],
            "STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ATR_PCT']": STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ATR_PCT'],
            "TREND_HIGH_VOLUME_ONLY": TREND_HIGH_VOLUME_ONLY,
            "STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX']": STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX'],
            "STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MIN_ATR_PCT']": STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MIN_ATR_PCT'],
            "SIDEWAYS_HIGH_VOLUME_ONLY": SIDEWAYS_HIGH_VOLUME_ONLY,
        }
        with open('config.json', 'w') as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}", flush=True)

def load_config():
    """Load strategy filter configuration from config.json"""
    global TREND_HIGH_VOLUME_ONLY, STRATEGY_CONFIG
    global SIDEWAYS_HIGH_VOLUME_ONLY
    
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r') as f:
                config_data = json.load(f)
                
            STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX'] = config_data.get('TREND_MIN_ADX', STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX'])
            STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ATR_PCT'] = config_data.get('TREND_MIN_ATR', STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ATR_PCT'])
            TREND_HIGH_VOLUME_ONLY = config_data.get('TREND_HIGH_VOLUME_ONLY', TREND_HIGH_VOLUME_ONLY)
            STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX'] = config_data.get('SIDEWAYS_MAX_ADX', STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX'])
            STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MIN_ATR_PCT'] = config_data.get('SIDEWAYS_MIN_ATR', STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MIN_ATR_PCT'])
            SIDEWAYS_HIGH_VOLUME_ONLY = config_data.get('SIDEWAYS_HIGH_VOLUME_ONLY', SIDEWAYS_HIGH_VOLUME_ONLY)
            
            print("Config loaded from config.json", flush=True)
        else:
            # Create default config file
            save_config()
            print("Created default config.json", flush=True)
    except Exception as e:
        print(f"Error loading config: {e}, using defaults", flush=True)

# =========================
# SAVE SIGNAL
# =========================

def save_signal(
    signal_id,
    symbol,
    side,
    grade,
    score,
    entry,
    sl,
    tp1,
    tp2
):

    file_exists = os.path.isfile(
        'signals.csv'
    )

    with open(
        'signals.csv',
        'a',
        newline=''
    ) as file:

        writer = csv.writer(file)

        if not file_exists:

            writer.writerow([
                'signal_id',
                'time',
                'symbol',
                'side',
                'grade',
                'score',
                'entry',
                'sl',
                'tp1',
                'tp2',
                'result'
            ])

        writer.writerow([
            signal_id,
            int(time.time()),
            symbol,
            side,
            grade,
            score,
            entry,
            sl,
            tp1,
            tp2,
            'OPEN'
        ])

# =========================
# UPDATE RESULT
# =========================

def update_signal_result(
    signal_id,
    result
):
    global current_wins, current_losses, current_loss_streak, pause_trading

    rows = []

    with open(
        'signals.csv',
        'r'
    ) as file:

        reader = csv.reader(file)

        for row in reader:

            if (
                len(row) > 0
                and row[0] == str(signal_id)
            ):

                row[-1] = result

            rows.append(row)

    with open(
        'signals.csv',
        'w',
        newline=''
    ) as file:

        writer = csv.writer(file)

        writer.writerows(rows)

    # =========================
    # LOSS STREAK TRACKING
    # =========================

    was_paused = pause_trading  # Track previous state

    if result == "WIN":
        current_wins += 1
        current_loss_streak = 0  # Reset on win
    elif result == "LOSS":
        current_losses += 1
        current_loss_streak += 1

    # Check if loss streak threshold exceeded
    if current_loss_streak >= MAX_CONSECUTIVE_LOSSES:
        pause_trading = True
        # Send notification only on first trigger (False -> True transition)
        if not was_paused:
            send_telegram(
                f"🛑 LOSS STREAK DETECTED\n\n"
                f"Consecutive Losses: {current_loss_streak}\n\n"
                f"Trading Paused"
            )
    else:
        pause_trading = False

    # Send debug message with loss streak status
    send_telegram(
        f"📊 LOSS STREAK DEBUG\n\n"
        f"Current Wins: {current_wins}\n"
        f"Current Losses: {current_losses}\n"
        f"Current Loss Streak: {current_loss_streak}"
    )
# =========================
# TELEGRAM COMMANDS HANDLERS
# =========================
# All handlers are registered in telegram_commands.py module
# Import after bot is created below


# =========================
# HELPER FUNCTIONS
# =========================

def calculate_trade_levels(
    entry,
    atr,
    side,
    regime="TRENDING",
    ai_override=None
):
    # Dynamic RR based on config
    import config as main_config
    strat_config = STRATEGY_CONFIG.get(regime, STRATEGY_CONFIG['TRENDING'])
    sl_mult = strat_config.get('SL_ATR_MULT', 2.0)
    tp_rr = strat_config.get('TP_RR', 1.5)

    if ai_override and ai_override.get("approved") and ai_override.get("confidence", 0) >= getattr(main_config, 'AI_FILTER_MIN_CONFIDENCE', 75):
        if ai_override.get("sl_atr_mult"):
            sl_mult = ai_override.get("sl_atr_mult")
        if ai_override.get("tp_rr_ratio"):
            tp_rr = ai_override.get("tp_rr_ratio")

    sl_dist = atr * sl_mult

    if side == "LONG":
        sl = round(
            entry - sl_dist,
            4
        )
        risk = entry - sl
        tp1 = round(
            entry + risk,
            4
        )
        tp2 = round(
            entry + (risk * tp_rr),
            4
        )
        rr = round(
            (tp2 - entry) / risk,
            2
        ) if risk > 0 else 0
        return sl, tp1, tp2, rr

    sl = round(
        entry + sl_dist,
        4
    )
    risk = sl - entry
    tp1 = round(
        entry - risk,
        4
    )
    tp2 = round(
        entry - (risk * tp_rr),
        4
    )
    rr = round(
        (entry - tp2) / risk,
        2
    ) if risk > 0 else 0
    return sl, tp1, tp2, rr

# =========================

def build_signal_message(
    symbol,
    side,
    grade,
    score,
    entry,
    sl,
    tp1,
    tp2,
    rr,
    rsi,
    adx,
    atr_percent,
    volume_high,
    btc_trend,
    local_regime="",
    btc_regime=""
):
    icon = "🚀" if side == "LONG" else "🔻"
    return f"""
{icon} {side} SIGNAL

{symbol}

Coin Regime:
{local_regime}

Active Strategy:
TREND

Grade:
{grade}

Score:
{score}/100

Pullback Entry:
{entry}

SL:
{sl}

TP2:
{tp2}

RR:
1:{rr}

RSI:
{round(rsi,2)}

ADX:
{round(adx,2)}

ATR %:
{round(atr_percent,2)}

Volume:
{"HIGH" if volume_high else "NORMAL"}

BTC Trend:
{btc_trend}

Plan:
- Full TP2 target
- Fixed SL
- No partial close
"""

# =========================

def get_side_config(side):
    if side == "LONG":
        return {
            "stop_side": "sell",
            "position_side": "LONG"
        }

    return {
        "stop_side": "buy",
        "position_side": "SHORT"
    }

# =========================

# place_protection_orders moved to bingx_client.py

# =========================
# AUTO TRADE HELPERS
# =========================

def passes_grade_filter(grade, min_grade):
    """Check if grade meets minimum auto trade grade."""
    if grade not in GRADE_PRIORITY or min_grade not in GRADE_PRIORITY:
        return False
    return GRADE_PRIORITY[grade] >= GRADE_PRIORITY[min_grade]

def can_open_trade(side):
    """Check if new trade can be opened based on position limits."""
    with state_lock:
        trade_items = list(active_trades.values())
    
    # Count active trades (PENDING or OPEN) regardless of side
    active_longs = sum(
        1 for t in trade_items
        if t.get("status") in ["PENDING", "OPEN"]
        and t.get("side") == "LONG"
    )
    
    active_shorts = sum(
        1 for t in trade_items
        if t.get("status") in ["PENDING", "OPEN"]
        and t.get("side") == "SHORT"
    )
    
    total_active_positions = active_longs + active_shorts
    
    # Debug logging
    print(f"[POSITION_LIMIT] Active positions: {total_active_positions}/{MAX_ACTIVE_TRADES} (LONG: {active_longs}, SHORT: {active_shorts})", flush=True)
    
    # Check global limit first
    if total_active_positions >= MAX_ACTIVE_TRADES:
        print(f"[POSITION_LIMIT] Global limit reached: {total_active_positions} >= {MAX_ACTIVE_TRADES}", flush=True)
        return False
    
    # If global limit not reached, check side-specific limits
    if side.upper() == "LONG":
        return active_longs < MAX_LONG_TRADES
    else:
        return active_shorts < MAX_SHORT_TRADES

def check_trend_filters(atr_percent, adx, volume_high):
    """Check if trade meets TREND strategy filter requirements.
    
    Returns:
        (passes, reason) tuple
        passes: True if all filters pass, False otherwise
        reason: Skip reason string if fails, None if passes
    """
    
    if atr_percent < STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ATR_PCT']:
        return False, "ATR too low"
    
    if adx < STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX']:
        return False, "ADX too low"
    
    if TREND_HIGH_VOLUME_ONLY and not volume_high:
        return False, "Volume not high"
    
    return True, None

def check_sideways_filters(atr_percent, adx, volume_high):
    """Check if trade meets SIDEWAYS strategy filter requirements.
    
    Returns:
        (passes, reason) tuple
        passes: True if all filters pass, False otherwise
        reason: Skip reason string if fails, None if passes
    """
    
    if atr_percent < STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MIN_ATR_PCT']:
        return False, "ATR too low"
    
    if adx > STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX']:
        return False, "ADX too high"
    
    if SIDEWAYS_HIGH_VOLUME_ONLY and not volume_high:
        return False, "Volume not high"
    
    return True, None

# =========================
# TRADING FUNCTIONS
# =========================

def get_latest_signal(symbol, side=None):
    """Get the latest signal for a symbol.
    
    Bug Fix: เพิ่ม side parameter เพื่อหา signal ที่ตรงกับ side ที่ขอ
    ป้องกันกรณีที่มีทั้ง LONG และ SHORT signal อยู่พร้อมกัน
    แล้วบอทเลือก signal ผิด side ทำให้ execute_trade fail
    
    Args:
        symbol: Trading symbol
        side: 'LONG' or 'SHORT' (optional) — ถ้าระบุจะหาเฉพาะ side นี้
    """
    with state_lock:
        trade_items = list(active_trades.values())

    # เรียงตาม created_at ล่าสุดก่อน
    trade_items.sort(key=lambda t: t.get('created_at', 0), reverse=True)

    for trade in trade_items:

        if (
            trade['symbol'] == symbol
            and trade['status'] == "SIGNAL"
        ):
            # ถ้าระบุ side ให้ตรวจว่าตรงกัน
            if side and trade.get('side', '').upper() != side.upper():
                continue

            return {
                "signal": trade['side'],
                "entry": trade['entry'],
                "sl": trade['sl'],
                "tp": trade['tp2'],
                "atr": abs(
                    trade['entry'] - trade['sl']
                ) / 1.5,
                "signal_regime": trade.get("signal_regime", "UNKNOWN"),
                "strategy": trade.get("strategy", "UNKNOWN"),
                "grade": trade.get("grade", "C"),
                "score": trade.get("score", 0)
            }

    return None


# execute_trade moved to bingx_client.py

# =========================
# MARKET REGIME DETECTION
# =========================

def detect_market_regime():
    """Detect current BTC market regime: MOMENTUM, TRENDING, SCALPING, PAUSE, SIDEWAYS, or VOLATILE.

    Priority: MOMENTUM > VOLATILE > TRENDING > SCALPING > PAUSE > SIDEWAYS

    SCALPING regime = moderate activity zone:
    - ADX between SCALPING_DETECT_ADX_MIN and SCALPING_DETECT_ADX_MAX
    - ATR% between SCALPING_DETECT_ATR_MIN and SCALPING_DETECT_ATR_MAX
    - Not too trending, not too dead — price oscillating enough for scalps

    Returns:
        (regime, btc_adx, btc_atr_percent) tuple
    """
    try:
        df_1h = get_dataframe('BTC/USDT:USDT', '1h')
        btc = df_1h.iloc[-2]

        btc_adx = round(btc['adx'], 2)
        btc_atr_percent = round((btc['atr'] / btc['close']) * 100, 2)
        
        # EMA alignment for trend confirmation
        is_uptrend = btc['ema7'] > btc['ema25'] > btc['ema99']
        is_downtrend = btc['ema7'] < btc['ema25'] < btc['ema99']
        has_trend_alignment = is_uptrend or is_downtrend

        # MOMENTUM: strongest trend, price far from EMA7, consecutive candles
        momentum_info = detect_momentum('BTC/USDT:USDT')
        if momentum_info['is_momentum']:
            return "MOMENTUM", btc_adx, btc_atr_percent

        # VOLATILE has next priority
        if btc_atr_percent >= MARKET_REGIME_ATR_VOLATILE:
            return "VOLATILE", btc_adx, btc_atr_percent

        if btc_adx >= MARKET_REGIME_ADX_TRENDING:
            if has_trend_alignment:
                return "TRENDING", btc_adx, btc_atr_percent
            elif btc_atr_percent >= SCALPING_DETECT_ATR_MIN:
                # Fallback to scalping if ADX is high but EMAs aren't perfectly aligned
                return "SCALPING", btc_adx, btc_atr_percent

        # SCALPING: moderate activity zone (between TRENDING and SIDEWAYS)
        if (SCALPING_DETECT_ADX_MIN <= btc_adx < SCALPING_DETECT_ADX_MAX
            and SCALPING_DETECT_ATR_MIN <= btc_atr_percent <= SCALPING_DETECT_ATR_MAX):
            return "SCALPING", btc_adx, btc_atr_percent

        # PAUSE: Dead market zone (Too tight for scalping or anything)
        if btc_adx < PAUSE_MAX_ADX and btc_atr_percent < PAUSE_MAX_ATR:
            return "PAUSE", btc_adx, btc_atr_percent

        if btc_adx < MARKET_REGIME_ADX_SIDEWAYS:
            return "SIDEWAYS", btc_adx, btc_atr_percent

        # Default to SIDEWAYS if between thresholds (safer than TRENDING)
        return "SIDEWAYS", btc_adx, btc_atr_percent

    except Exception:
        print("Market regime detection error", flush=True)
        print(traceback.format_exc(), flush=True)
        # Bug fix: เปลี่ยน fallback จาก TRENDING → SIDEWAYS
        # TRENDING fallback อันตราย เพราะทำให้บอทส่ง signal สวนตลาดได้
        # SIDEWAYS ปลอดภัยกว่า รอสัญญาณชัดก่อนเข้า
        return "SIDEWAYS", 0, 0


# =========================
# ANALYZE - Strategy Dispatcher
# =========================

def analyze(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False):
    """Route to the correct analysis strategy based on MARKET_MODE and per-coin regime.
    
    Feature 8: Respect CONTROL_MODE override.
    - If FORCE_TREND: always use trend analysis
    - If FORCE_SIDEWAY: always use sideways analysis
    - If AUTO: use per-coin regime (local_regime) instead of BTC regime
    """
    try:
        df_15m = get_dataframe(symbol, '15m')
        df_1h = get_dataframe(symbol, '1h')
    except Exception as e:
        print(f"[ERROR] Failed to fetch data for {symbol}: {e}")
        set_scan_result(symbol, {"status": "Error", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time(), "mode": "UNKNOWN"})
        return {"symbol": symbol, "result": "error"}

    local_regime = detect_symbol_regime(df_1h)
    
    # Determine effective mode
    effective_mode = local_regime if local_regime != "PAUSE" else MARKET_MODE
    if CONTROL_MODE == "FORCE_TREND":
        effective_mode = "TRENDING"
    elif CONTROL_MODE == "FORCE_SIDEWAY":
        effective_mode = "SIDEWAYS"
    elif CONTROL_MODE == "FORCE_SCALPING":
        effective_mode = "SCALPING"
    elif CONTROL_MODE == "FORCE_PAUSE":
        effective_mode = "PAUSE"
    elif CONTROL_MODE == "AUTO":
        effective_mode = local_regime

    if effective_mode == "PAUSE":
        set_scan_result(symbol, {"status": "Market Paused", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        return {"symbol": symbol, "result": "paused"}

    btc_regime = MARKET_MODE

    kwargs = {
        'bypass_cooldown': bypass_cooldown,
        'silent_mode': silent_mode,
        'signal_only': signal_only,
        'df_15m': df_15m,
        'df_1h': df_1h,
        'local_regime': local_regime,
        'btc_regime': btc_regime
    }

    if effective_mode == "SIDEWAYS":
        res = analyze_sideways(symbol, **kwargs)
    elif effective_mode == "MOMENTUM":
        # res = analyze_momentum(symbol, **kwargs)
        # MOMENTUM mode disabled (per A/B test results), fallback to TREND which performs much better in high ADX
        res = analyze_trend(symbol, **kwargs)
    elif effective_mode == "SCALPING":
        res = analyze_scalping(symbol, **kwargs)
    else:
        res = analyze_trend(symbol, **kwargs)
        
    if symbol in scan_results and isinstance(scan_results[symbol], dict):
        scan_results[symbol]['mode'] = effective_mode
        
    return res



# =========================
# ANALYZE SCALPING
# =========================

def analyze_scalping(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False, df_15m=None, df_1h=None, local_regime="", btc_regime=""):
    """Scalping regime analysis — fast entry via market order on 5m timeframe.

    Key differences from other strategies:
    - Uses 5m as primary TF (15m for confirmation only)
    - Market order entry (no pullback wait)
    - Tight SL: ATR(5m) * 0.8
    - TP: risk * 1.5 (modest RR, high win-rate target)
    - Cooldown: 300s (vs 1800s for Trend)
    - Max 1 position
    """

    global pause_trading

    if pause_trading:
        set_scan_result(symbol, {"status": "Market Paused", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        return {"symbol": symbol, "result": "paused"}

    # Only scalp symbols from the dedicated list
    if symbol not in SCALPING_SYMBOLS:
        set_scan_result(symbol, {"status": "Not in SCALPING_SYMBOLS", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        return {"symbol": symbol, "result": "skipped"}

    try:
        now = time.time()

        # =========================
        # STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN']
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "SCALPING"))
            if last_time and now - last_time < STRATEGY_CONFIG['SCALPING']['COOLDOWN']:
                set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                google_sheet.log_debug(symbol, "Cooldown (SCALPING)", strategy="SCALPING", score=0, adx=0, atr=0, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}

        # =========================
        # GET DATA (3m primary, 15m confirmation)
        # =========================

        df_3m = get_dataframe(symbol, '3m')
        if df_15m is None:
            df_15m = get_dataframe(symbol, '15m')

        m3  = df_3m.iloc[-2]   # last closed 3m candle
        m15 = df_15m.iloc[-2]  # last closed 15m candle

        now_ts = time.time()
        signal_id = str(uuid.uuid4())[:8]

        atr_percent = (m3['atr'] / m3['close']) * 100
        volume_high = m3['volume'] > m3['vol_avg'] * 1.5  # stricter volume filter for scalping
        vol_status  = "HIGH" if volume_high else "NORMAL"
        adx_val     = round(m3['adx'], 2)
        atr_val     = round(atr_percent, 2)
        rsi_val     = round(m3['rsi'], 2)

        # =========================
        # FOMO FILTER
        # =========================

        candle_size = abs(m3['close'] - m3['open'])
        if candle_size > m3['atr'] * 2.0:
            set_scan_result(symbol, {"status": "Candle Too Big", "score": 0, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            google_sheet.log_debug(symbol, "Candle Too Big (SCALPING)", strategy="SCALPING", score=0, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # SCORE (max 100 pts)
        # =========================

        long_score  = 0
        short_score = 0
        btc_trend   = get_btc_trend()
        
        vwap_val = m3['vwap']
        is_above_vwap = m3['close'] > vwap_val
        
        is_green = m3['close'] > m3['open']
        is_red = m3['close'] < m3['open']

        # =========================
        # NEW SCORING SYSTEM (max 100 pts)
        # ต้องผ่านหลาย conditions ถึงจะได้คะแนนสูง
        # =========================

        # 1. EMA Trend (3m) — 30 pts
        #    ต้อง EMA7 ห่างจาก EMA25 จริง ไม่ใช่แค่เพิ่ง cross
        ema_gap_pct = abs(m3['ema7'] - m3['ema25']) / m3['ema25'] * 100
        if m3['ema7'] > m3['ema25'] and ema_gap_pct >= 0.05:
            long_score += 30
        if m3['ema7'] < m3['ema25'] and ema_gap_pct >= 0.05:
            short_score += 30

        # 2. 15m Confirmation — 20 pts
        if m15['ema7'] > m15['ema25']: long_score += 20
        if m15['ema7'] < m15['ema25']: short_score += 20

        # 3. Candle Direction — 15 pts
        #    เทียนล่าสุดต้องเป็นสีตรงกับทิศทาง
        if is_green: long_score += 15
        if is_red: short_score += 15

        # 4. RSI Momentum Zone — 15 pts
        #    RSI อยู่ใน sweet spot (มีแรงวิ่งต่อ)
        if 40 <= m3['rsi'] <= 58: long_score += 15
        if 42 <= m3['rsi'] <= 60: short_score += 15

        # 5. VWAP Position — 10 pts (soft filter, ไม่ block)
        if is_above_vwap: long_score += 10
        if not is_above_vwap: short_score += 10

        # 6. Volume — 10 pts
        if m3['volume'] > m3['vol_avg'] * 1.5:
            long_score += 10
            short_score += 10

        # --- Exhaustion Penalty ---
        stoch_rsi = m3.get('stoch_rsi', 50)
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100

        if stoch_rsi > 80: long_score -= 20
        if m3['close'] > m3['ema25'] and stretch_pct > 1.0: long_score -= 15

        if stoch_rsi < 20: short_score -= 20
        if m3['close'] < m3['ema25'] and stretch_pct > 1.0: short_score -= 15

        # --- Pinbar Bonus ---
        body_size = abs(m3['close'] - m3['open'])
        upper_wick = m3['high'] - max(m3['close'], m3['open'])
        lower_wick = min(m3['close'], m3['open']) - m3['low']
        if lower_wick > body_size * 2: long_score += 10
        if upper_wick > body_size * 2: short_score += 10

        # =========================
        # BTC FILTER
        # =========================
        if symbol != 'BTC/USDT:USDT':
            if btc_trend == "bullish":
                short_score = 0
            elif btc_trend == "bearish":
                long_score = 0
            elif btc_trend == "neutral":
                long_score -= 15
                short_score -= 15

        long_score  = min(long_score, 100)
        short_score = min(short_score, 100)

        # =========================
        # GRADE
        # =========================

        score = max(long_score, short_score)
        grade = "C"
        min_score = STRATEGY_CONFIG['SCALPING']['MIN_SCORE']
        
        if score >= min_score + 10:
            grade = "A+"
        elif score >= min_score:
            grade = "A"
        elif score >= min_score - 10:
            grade = "B"

        # =========================
        # SCORE FILTER
        # =========================

        if score < STRATEGY_CONFIG['SCALPING']['MIN_SCORE']:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            google_sheet.log_debug(symbol, f"Score Below MIN_SCORE (SCALPING {score})", strategy="SCALPING", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # DETERMINE SIDE
        # =========================

        if long_score >= short_score and long_score >= STRATEGY_CONFIG['SCALPING']['MIN_SCORE']:
            if rsi_val > STRATEGY_CONFIG['SCALPING']['FILTERS']['RSI_SAFE_LONG_MAX']:
                set_scan_result(symbol, {"status": "RSI Too High", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, f"RSI Too High SCALPING ({round(rsi_val, 2)} > {STRATEGY_CONFIG['SCALPING']['FILTERS']['RSI_SAFE_LONG_MAX']})", strategy="SCALPING", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}
            # BTC trend filter for LONG
            if btc_trend == "bearish":
                set_scan_result(symbol, {"status": "BTC Bearish", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, "BTC Bearish - no LONG scalp", strategy="SCALPING", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}
            side  = "LONG"
            entry = round(m3['ema25'], 4)  # Limit order on 3m EMA25 pullback
        elif short_score > long_score and short_score >= STRATEGY_CONFIG['SCALPING']['MIN_SCORE']:
            if rsi_val < STRATEGY_CONFIG['SCALPING']['FILTERS']['RSI_SAFE_SHORT_MIN']:
                set_scan_result(symbol, {"status": "RSI Too Low", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, f"RSI Too Low SCALPING ({round(rsi_val, 2)} < {STRATEGY_CONFIG['SCALPING']['FILTERS']['RSI_SAFE_SHORT_MIN']})", strategy="SCALPING", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}
            # BTC trend filter for SHORT
            if btc_trend == "bullish":
                set_scan_result(symbol, {"status": "BTC Bullish", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, "BTC Bullish - no SHORT scalp", strategy="SCALPING", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}
            side  = "SHORT"
            entry = round(m3['ema25'], 4)  # Limit order on 3m EMA25 pullback
        else:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # SL / TP (tight for scalping)
        # =========================

        atr = m3['atr']
        if side == "LONG":
            sl   = round(entry - atr * STRATEGY_CONFIG['SCALPING']['SL_ATR_MULT'], 4)
            risk = entry - sl
            tp2  = round(entry + risk * STRATEGY_CONFIG['SCALPING']['TP_RR'], 4)
            tp1  = round(entry + risk, 4)
            rr   = round((tp2 - entry) / (entry - sl), 2)
        else:
            sl   = round(entry + atr * STRATEGY_CONFIG['SCALPING']['SL_ATR_MULT'], 4)
            risk = sl - entry
            tp2  = round(entry - risk * STRATEGY_CONFIG['SCALPING']['TP_RR'], 4)
            tp1  = round(entry - risk, 4)
            rr   = round((entry - tp2) / (sl - entry), 2)

        # =========================
        # BUILD MESSAGE
        # =========================

        icon = "\U0001f680" if side == "LONG" else "\U0001f53b"

        message = f"""
{icon} {side} SIGNAL

{symbol}

Strategy:
SCALPING

Grade:
{grade}

Score:
{score}/100

Market Entry:
{entry}

SL:
{sl}

TP:
{tp2}

RR:
1:{rr}

RSI:
{round(rsi_val, 2)}

ADX:
{adx_val}

ATR %:
{atr_val}

Volume:
{vol_status}

BTC Trend:
{btc_trend}

Plan:
- Market order entry
- Tight SL ({STRATEGY_CONFIG['SCALPING']['SL_ATR_MULT']}x ATR)
- TP at {STRATEGY_CONFIG['SCALPING']['TP_RR']}:1 RR
"""

        print(message, flush=True)

        if not silent_mode:
            send_telegram(message)

        if not signal_only:
            save_signal(signal_id, symbol, side, grade, score, entry, sl, tp1, tp2)

            signal_regime = CURRENT_REGIME

            with state_lock:
                active_trades[signal_id] = {
                    "symbol": symbol,
                    "status": "SIGNAL",
                    "side": side,
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "signal_regime": signal_regime,
                    "created_at": time.time(),
                    "grade": grade,
                    "score": score,
                    "strategy": "SCALPING",
                }
                last_alert[(symbol, "SCALPING")] = now_ts

        set_scan_result(symbol, {
            "status": "Signal Generated",
            "score": score,
            "adx": adx_val,
            "atr": atr_val,
            "volume": vol_status,
            "timestamp": now_ts,
            "strategy": "SCALPING",
        })

        # Track candidate signal for top candidates
        candidate_signals[symbol] = {
            "side": side,
            "grade": grade,
            "score": score,
            "symbol": symbol,
            "strategy": "SCALPING",
        }

        # =========================
        # AUTO TRADE (Market Order)
        # =========================

        if AUTO_TRADE and not signal_only:

            skip_reason = None

            # Grade filter
            if config.GRADE_PRIORITY.get(grade, 0) < config.GRADE_PRIORITY.get(STRATEGY_CONFIG['SCALPING']['MIN_GRADE'], 0):
                skip_reason = f"Scalping Grade: {grade} < {STRATEGY_CONFIG['SCALPING']['MIN_GRADE']}"

            # Max 1 active scalping position
            if not skip_reason:
                with state_lock:
                    scalp_count = sum(
                        1 for t in active_trades.values()
                        if t.get("status") in ["PENDING", "OPEN"]
                        and t.get("strategy") == "SCALPING"
                    )
                if scalp_count >= STRATEGY_CONFIG['SCALPING']['MAX_TRADES']:
                    skip_reason = f"Scalping max {STRATEGY_CONFIG['SCALPING']['MAX_TRADES']} position reached"

            # Regime still valid
            if not skip_reason and CURRENT_REGIME != signal_regime:
                skip_reason = "MARKET_REGIME_CHANGED"

            if not skip_reason:
                print(f"[SCALPING_AUTO_TRADE] {symbol} {side} — executing market order", flush=True)
                try:
                    bingx_client.execute_scalp_trade(symbol, side.lower())
                except Exception:
                    print(f"[SCALPING_AUTO_TRADE] execute_scalp_trade error", flush=True)
                    print(traceback.format_exc(), flush=True)
            else:
                print(f"[SCALPING_AUTO_TRADE] {symbol} skipped — {skip_reason}", flush=True)

                if skip_reason != "MARKET_REGIME_CHANGED":
                    send_telegram(
                        f"\U0001f916 SCALP AUTO TRADE SKIPPED\n\n"
                        f"Symbol: {symbol}\n"
                        f"Side: {side}\n"
                        f"Reason: {skip_reason}\n"
                        f"Grade: {grade}\n"
                        f"Score: {score}"
                    )

        # Google Sheets logging
        try:
            google_sheet.log_signal(
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                grade=grade,
                score=score,
                entry=entry,
                sl=sl,
                tp=tp2,
                atr=atr_val,
                adx=adx_val,
                volume=vol_status,
                btc_trend=btc_trend,
                status="SIGNAL",
                strategy="SCALPING",
                allocation_decision="ALLOCATED",
                skip_reason="",
                vwap_position="ABOVE" if is_above_vwap else "BELOW",
                stoch_rsi=m3['stoch_rsi'],
                stretch_pct=round(abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100, 2),
                candle_color="GREEN" if is_green else "RED",
                local_regime=local_regime,
                btc_regime=btc_regime
            )
        except Exception as e:
            print(f"[SCALPING] Google Sheets log error: {e}", flush=True)

        google_sheet.log_fill_analysis(
            symbol=symbol,
            side=side,
            current_price=m3['close'],
            entry_price=entry,
            grade=grade,
            score=score,
            atr=atr_val,
            adx=adx_val,
            btc_trend=btc_trend,
            fill_status="OPEN",
            local_regime=local_regime,
            btc_regime=btc_regime
        )
        
        # BACKTEST
        backtest.record_signal(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp2,
            grade=grade,
            score=score,
            rr=rr,
            strategy="SCALPING",
            local_regime=local_regime,
            btc_regime=btc_regime,
            adx=adx_val,
            atr_pct=atr_val,
            vol_status=vol_status,
            btc_trend=btc_trend,
            vwap_pos="ABOVE" if is_above_vwap else "BELOW",
            stoch_rsi=round(m3['stoch_rsi'], 2),
            stretch_pct=round(abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100, 2),
            candle_color="GREEN" if is_green else "RED"
        )

        return {"symbol": symbol, "result": "signal", "side": side, "score": score}

    except Exception:
        print(f"[SCALPING ERROR] {symbol}", flush=True)
        print(traceback.format_exc(), flush=True)
        set_scan_result(symbol, {"status": "Error", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        google_sheet.log_debug(symbol, "Error (SCALPING)", strategy="SCALPING", score=0, adx=0, atr=0, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
        return {"symbol": symbol, "result": "error"}


# =========================
# ANALYZE MOMENTUM
# =========================

def analyze_momentum(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False, df_15m=None, df_1h=None, local_regime="", btc_regime=""):
    """Momentum regime analysis — entry near current price, no pullback wait."""

    global pause_trading

    if pause_trading:
        return {"symbol": symbol, "result": "paused"}

    try:
        now = time.time()

        # =========================
        # STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN']
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "MOMENTUM"))
            if last_time and now - last_time < STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN']:
                set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                google_sheet.log_debug(symbol, "Cooldown", strategy="MOMENTUM", score=0, adx=0, atr=0, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}

        # =========================
        # GET DATA
        # =========================

        df_4h = get_dataframe(symbol, '4h')
        if df_1h is None:
            df_1h = get_dataframe(symbol, '1h')
        if df_15m is None:
            df_15m = get_dataframe(symbol, '15m')
        df_3m = get_dataframe(symbol, '3m')

        h4  = df_4h.iloc[-2]
        h1  = df_1h.iloc[-2]
        m15 = df_15m.iloc[-2]
        m3  = df_3m.iloc[-2]

        now_ts = time.time()
        signal_id = str(uuid.uuid4())[:8]

        atr_percent = (m3['atr'] / m3['close']) * 100
        volume_high = m3['volume'] > m3['vol_avg'] * 1.3
        vol_status  = "HIGH" if volume_high else "NORMAL"
        adx_val     = round(m3['adx'], 2)
        atr_val     = round(atr_percent, 2)

        # =========================
        # TIMEFRAME CONFLUENCE
        # =========================
        if m3['adx'] < 20:
            set_scan_result(symbol, {"status": "3m ADX Too Weak", "score": 0, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            google_sheet.log_debug(symbol, "3m ADX Too Weak for Momentum", strategy="MOMENTUM", score=0, adx=adx_val, atr=atr_val, vwap_position="", stoch_rsi=0, stretch_pct=0, candle_color="")
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # SCORE
        # =========================

        long_score  = 0
        short_score = 0
        btc_trend   = get_btc_trend()

        # =========================
        # MOMENTUM LOGIC (Aligned with Backtest 3m)
        # =========================

        # LONG SCORE
        if m3['ema7'] > m3['ema25']: long_score += 50
        if m3['ema25'] > m3['ema99']: long_score += 35
        if m3['adx'] > 25: long_score += 10
        if m3['rsi'] <= 45: long_score += 25

        # SHORT SCORE
        if m3['ema7'] < m3['ema25']: short_score += 50
        if m3['ema25'] < m3['ema99']: short_score += 35
        if m3['adx'] > 25: short_score += 10
        if m3['rsi'] >= 55: short_score += 25

        # --- Exhaustion Penalty ---
        stoch_rsi = m3.get('stoch_rsi', 50)
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100

        # LONG Penalty
        if stoch_rsi > 80: long_score -= 30
        if m3['close'] > m3['ema25'] and stretch_pct > 1.0: long_score -= 20
        
        # SHORT Penalty
        if stoch_rsi < 20: short_score -= 30
        if m3['close'] < m3['ema25'] and stretch_pct > 1.0: short_score -= 20

        # Volume confirmation (10pts)
        if volume_high:
            long_score  += 10
            short_score += 10

        # =========================
        # BTC FILTER (Strict Macro Alignment)
        # =========================
        if symbol != 'BTC/USDT:USDT':
            if btc_trend == "bullish":
                short_score = 0  # Forbid SHORT in Bull Market
            elif btc_trend == "bearish":
                long_score = 0   # Forbid LONG in Bear Market
            elif btc_trend == "neutral":
                long_score -= 20
                short_score -= 20

        long_score  = min(long_score, 100)
        short_score = min(short_score, 100)

        # =========================
        # GRADE
        # =========================

        score = max(long_score, short_score)
        grade = "C"
        min_score = STRATEGY_CONFIG['MOMENTUM']['MIN_SCORE']
        
        if score >= min_score + 10 and adx_val > 35 and volume_high:
            grade = "A+"
        elif score >= min_score and adx_val > 25:
            grade = "A"
        elif score >= min_score - 10:
            grade = "B"

        # =========================
        # SCORE FILTER
        # =========================

        if score < STRATEGY_CONFIG['MOMENTUM']['MIN_SCORE']:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            google_sheet.log_debug(symbol, "Score Below MIN_SCORE", strategy="MOMENTUM", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m3', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm3' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # DETERMINE SIDE
        # =========================

        rsi_val = m3['rsi']
        if long_score >= short_score and long_score >= STRATEGY_CONFIG['MOMENTUM']['MIN_SCORE'] and btc_trend == "bullish":
            if rsi_val < STRATEGY_CONFIG['MOMENTUM']['FILTERS']['RSI_MIN_LONG']:
                set_scan_result(symbol, {"status": "RSI Too Low for Momentum", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, f"RSI Too Low ({round(rsi_val, 2)} < {STRATEGY_CONFIG['MOMENTUM']['FILTERS']['RSI_MIN_LONG']})", strategy="MOMENTUM", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m3', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm3' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}
            side  = "LONG"
            entry = round(m3['ema7'], 4)
        elif short_score > long_score and short_score >= STRATEGY_CONFIG['MOMENTUM']['MIN_SCORE']:
            if rsi_val > STRATEGY_CONFIG['MOMENTUM']['FILTERS']['RSI_MAX_SHORT']:
                set_scan_result(symbol, {"status": "RSI Too High for Momentum", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, f"RSI Too High ({round(rsi_val, 2)} > {STRATEGY_CONFIG['MOMENTUM']['FILTERS']['RSI_MAX_SHORT']})", strategy="MOMENTUM", score=score, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m3', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm3' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}
            side  = "SHORT"
            entry = round(m3['ema7'], 4)
        else:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # SL / TP
        # =========================

        atr = m3['atr']
        if side == "LONG":
            sl   = round(entry - atr * STRATEGY_CONFIG['MOMENTUM']['SL_ATR_MULT'], 4)
            risk = entry - sl
            tp2  = round(entry + risk * STRATEGY_CONFIG['MOMENTUM']['TP_RR'], 4)
            tp1  = round(entry + risk, 4)
            rr   = round((tp2 - entry) / (entry - sl), 2)
        else:
            sl   = round(entry + atr * STRATEGY_CONFIG['MOMENTUM']['SL_ATR_MULT'], 4)
            risk = sl - entry
            tp2  = round(entry - risk * STRATEGY_CONFIG['MOMENTUM']['TP_RR'], 4)
            tp1  = round(entry - risk, 4)
            rr   = round((entry - tp2) / (sl - entry), 2)

        # =========================
        # DISTANCE LOG
        # =========================

        current_price = m3['close']
        distance_pct  = abs(current_price - entry) / current_price * 100

        print(
            f"[MOMENTUM_FILTER] {symbol} | side={side} | current={current_price} "
            f"| entry={entry} | distance={round(distance_pct, 3)}%",
            flush=True
        )

        # =========================
        # BUILD MESSAGE
        # =========================

        icon = "🚀" if side == "LONG" else "🔻"
        momentum_info = detect_momentum(symbol)
        strength = "STRONG" if momentum_info['adx'] >= 35 else "MODERATE"

        message = f"""
{icon} {side} SIGNAL

{symbol}

Strategy:
MOMENTUM

Grade:
{grade}

Score:
{score}/100

Entry:
{entry}

SL:
{sl}

TP2:
{tp2}

RR:
1:{rr}

ADX:
{round(m3['adx'], 2)}

ATR %:
{round(atr_percent, 2)}

Volume:
{vol_status}

BTC Trend:
{btc_trend}

Coin Regime:
{local_regime}

Active Strategy:
MOMENTUM

Momentum Strength:
{strength} ({momentum_info['consecutive_candles']} candles)

Distance from price:
{round(distance_pct, 3)}%

Plan:
- Full TP2 target
- Fixed SL
- No partial close
"""

        print(message, flush=True)

        if not silent_mode:
            send_telegram(message)

        if not signal_only:
            save_signal(signal_id, symbol, side, grade, score, entry, sl, tp1, tp2)

            signal_regime = CURRENT_REGIME

            with state_lock:
                active_trades[signal_id] = {
                    "symbol": symbol,
                    "status": "SIGNAL",
                    "side": side,
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "signal_regime": signal_regime,
                    "created_at": time.time(),
                    "grade": grade,
                    "score": score,
                    "strategy": "MOMENTUM"
                }
                last_alert[(symbol, "MOMENTUM")] = now_ts

        set_scan_result(symbol, {
            "status": "Signal Generated",
            "score": score,
            "adx": adx_val,
            "atr": atr_val,
            "volume": vol_status,
            "timestamp": now_ts
        })

        # Google Sheets logging
        try:
            google_sheet.log_signal(
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                grade=grade,
                score=score,
                entry=entry,
                sl=sl,
                tp=tp2,
                atr=atr_val,
                adx=adx_val,
                volume=vol_status,
                btc_trend=btc_trend,
                status="SIGNAL",
                strategy="MOMENTUM",
                allocation_decision="ALLOCATED",
                skip_reason="",
                vwap_position="ABOVE" if m3.get('vwap') and m3['close'] > m3['vwap'] else "BELOW" if m3.get('vwap') else "",
                stoch_rsi=round(m3.get('stoch_rsi', 0), 2) if 'stoch_rsi' in m3 else 0,
                stretch_pct=round(distance_pct, 2) if 'distance_pct' in locals() else 0,
                candle_color="GREEN" if m3['close'] > m3['open'] else "RED",
                local_regime=local_regime,
                btc_regime=btc_regime
            )
            google_sheet.log_fill_analysis(
                symbol=symbol,
                side=side,
                current_price=m3['close'],
                entry_price=entry,
                grade=grade,
                score=score,
                atr=atr_val,
                adx=adx_val,
                btc_trend=btc_trend,
                fill_status="OPEN",
                local_regime=local_regime,
                btc_regime=btc_regime
            )
            if signal_id:
                backtest.record_signal(
                    signal_id=signal_id,
                    symbol=symbol,
                    side=side,
                    entry=entry,
                    sl=sl,
                    tp=tp2,
                    grade=grade,
                    score=score,
                    rr=rr,
                    strategy="MOMENTUM",
                    local_regime=local_regime,
                    btc_regime=btc_regime,
                    adx=adx_val,
                    atr_pct=atr_val,
                    vol_status=vol_status,
                    btc_trend=btc_trend,
                    vwap_pos="ABOVE" if m3.get('vwap') and m3['close'] > m3['vwap'] else "BELOW" if m3.get('vwap') else "",
                    stoch_rsi=round(m3.get('stoch_rsi', 0), 2) if 'stoch_rsi' in m3 else 0,
                    stretch_pct=round(distance_pct, 2) if 'distance_pct' in locals() else 0,
                    candle_color="GREEN" if m3['close'] > m3['open'] else "RED"
                )
        except Exception as e:
            print(f"[MOMENTUM] Google Sheets log error: {e}", flush=True)

        # =========================
        # AUTO TRADE
        # =========================

        if AUTO_TRADE and not signal_only:

            skip_reason = None

            # Grade must be A+
            if grade != STRATEGY_CONFIG['MOMENTUM']['MIN_GRADE']:
                skip_reason = f"Momentum Grade: {grade} < {STRATEGY_CONFIG['MOMENTUM']['MIN_GRADE']}"

            # Max 1 active position for momentum
            if not skip_reason:
                with state_lock:
                    active_count = sum(
                        1 for t in active_trades.values()
                        if t.get("status") in ["PENDING", "OPEN"]
                    )
                if active_count >= STRATEGY_CONFIG['MOMENTUM']['MAX_TRADES']:
                    skip_reason = f"Momentum max {STRATEGY_CONFIG['MOMENTUM']['MAX_TRADES']} position reached"

            # Regime still valid
            if not skip_reason and CURRENT_REGIME != signal_regime:
                skip_reason = "MARKET_REGIME_CHANGED"

            if not skip_reason:
                print(f"[MOMENTUM_AUTO_TRADE] {symbol} {side} — executing", flush=True)
                try:
                    if symbol not in scan_results or not isinstance(scan_results[symbol], dict):
                        scan_results[symbol] = {}
                    scan_results[symbol]["strategy"] = "MOMENTUM"
                    bingx_client.execute_trade(symbol, side, skip_pullback_check=True)
                except Exception:
                    print(f"[MOMENTUM_AUTO_TRADE] execute_trade error", flush=True)
                    print(traceback.format_exc(), flush=True)
            else:
                print(f"[MOMENTUM_AUTO_TRADE] {symbol} skipped — {skip_reason}", flush=True)

        return {"symbol": symbol, "result": "signal", "side": side, "score": score}

    except Exception:
        print(f"[MOMENTUM ERROR] {symbol}", flush=True)
        print(traceback.format_exc(), flush=True)
        set_scan_result(symbol, {"status": "Error", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        google_sheet.log_debug(symbol, "Error", strategy="MOMENTUM", score=0, adx=0, atr=0, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m3', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm3' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
        return {"symbol": symbol, "result": "error"}


# =========================
# ANALYZE TREND
# =========================

def analyze_trend(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False, df_15m=None, df_1h=None, local_regime="", btc_regime=""):

    # =========================
    # PAUSE TRADING CHECK
    # =========================
    
    global pause_trading

    if pause_trading:
        return {"symbol": symbol, "result": "paused"}

    try:

        now = time.time()

        # =========================
        # STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN'] (Feature 4: Bypass if ignore_cooldown_once is set)
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "TREND"))

            if last_time and now - last_time < STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN']:
                set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                # Google Sheets debug logging
                google_sheet.log_debug(symbol, "Cooldown", score=0, adx=0, atr=0, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
                return {"symbol": symbol, "result": "skipped"}

        # =========================
        # GET DATA
        # =========================

        df_1d = get_dataframe(
            symbol,
            '1d'
        )

        df_4h = get_dataframe(
            symbol,
            '4h'
        )

        if df_1h is None:
            df_1h = get_dataframe(
                symbol,
                '1h'
            )

        if df_15m is None:
            df_15m = get_dataframe(
                symbol,
                '15m'
            )

        # =========================
        # CLOSED CANDLES
        # =========================

        d1 = df_1d.iloc[-2]

        h4 = df_4h.iloc[-2]

        h1 = df_1h.iloc[-2]

        m15 = df_15m.iloc[-2]

        # =========================
        # PRE-FILTER (Early rejection for ADX/ATR)
        # =========================
        adx_val = round(m15['adx'], 2)
        atr_pct = (m15['atr'] / m15['close']) * 100
        atr_percent = atr_pct
        vol_high = m15['volume'] > m15['vol_avg'] * 1.3
        volume_high = vol_high
        
        passes_exec, exec_reason = check_trend_filters(atr_pct, adx_val, vol_high)
        if not passes_exec:
            set_scan_result(symbol, {"status": exec_reason, "score": 0, "adx": adx_val, "atr": atr_pct, "volume": "HIGH" if vol_high else "NORMAL", "timestamp": now})
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # FOMO FILTER
        # =========================

        candle_size = abs(
            m15['close'] - m15['open']
        )
        is_green = m15['close'] > m15['open']

        if candle_size > m15['atr'] * 1.5:
            print(
                f"{symbol} skipped - candle too big",
                flush=True
            )

            adx_val = round(m15['adx'], 2)
            atr_val = round((m15['atr'] / m15['close']) * 100, 2)
            vol_status = "HIGH" if m15['volume'] > m15['vol_avg'] * 1.3 else "NORMAL"
            set_scan_result(symbol, {"status": "Candle Too Big", "score": 0, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now})
            # Track rejected signal (Feature 3)
            rejected_signals.add(symbol)
            # Google Sheets debug logging
            google_sheet.log_debug(symbol, "Candle Too Big", score=0, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # NO TRADE ZONE
        # =========================

        if (
            45 < m15['rsi'] < 55
            and m15['adx'] < 18
        ):
            print(
                f"{symbol} skipped - sideways market",
                flush=True
            )

            adx_val = round(m15['adx'], 2)
            atr_val = round((m15['atr'] / m15['close']) * 100, 2)
            vol_status = "HIGH" if m15['volume'] > m15['vol_avg'] * 1.3 else "NORMAL"
            set_scan_result(symbol, {"status": "Sideways Market", "score": 0, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now})
            # Track rejected signal (Feature 3)
            rejected_signals.add(symbol)
            # Google Sheets debug logging
            google_sheet.log_debug(symbol, "Sideways Market", score=0, adx=adx_val, atr=atr_val, vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # ADX CEILING & STRETCH LIMIT FILTERS
        # Prevent chasing overextended trends and buying at absolute top
        # =========================

        adx_val = round(m15['adx'], 2)
        h1_adx_val = round(h1['adx'], 2)
        current_price = m15['close']
        entry_ema = m15['ema25']  # Entry EMA reference

        # ADX Ceiling Check - apply penalty if ADX is overextended
        adx_ceiling_penalty = False
        if h1_adx_val > ADX_CEILING_LIMIT:
            print(
                f"[SKIP] 1H ADX is overextended ({h1_adx_val:.2f} > {ADX_CEILING_LIMIT}). Applying -50 penalty.",
                flush=True
            )
            adx_ceiling_penalty = True

        # Stretch Limit Check - calculate distance from entry EMA
        # LONG: price too far ABOVE EMA (overextended upside)
        # SHORT: price too far BELOW EMA (overextended downside)
        long_stretch_penalty = False
        short_stretch_penalty = False
        distance_pct = abs(current_price - entry_ema) / entry_ema * 100
        if distance_pct > STRETCH_MAX_DISTANCE_PCT:
            print(
                f"[SKIP] Price is too stretched from EMA ({distance_pct:.2f}% > {STRETCH_MAX_DISTANCE_PCT}%). Applying -25 penalty.",
                flush=True
            )
            if current_price > entry_ema:
                long_stretch_penalty = True  # Price above EMA - penalize LONG
            else:
                short_stretch_penalty = True  # Price below EMA - penalize SHORT

        # =========================
        # VWAP ULTIMATE FILTER
        # =========================
        vwap_val = m15['vwap']
        is_above_vwap = current_price > vwap_val

        # =========================
        # SCORE
        # =========================

        long_score = 0
        short_score = 0

        # ห้ามสวน VWAP เด็ดขาด
        if not is_above_vwap:
            long_score -= 1000
        if is_above_vwap:
            short_score -= 1000

        btc_trend = get_btc_trend()
        signal_id = str(uuid.uuid4())[:8]

        # Apply ADX ceiling and stretch penalties
        if adx_ceiling_penalty:
            long_score -= 50
            short_score -= 50
        if long_stretch_penalty:
            long_score -= 25
        if short_stretch_penalty:
            short_score -= 25

        # =========================
        # TREND LOGIC (Aligned with Backtest 15m)
        # =========================

        # Early Crossover Check (5 candles lookback)
        prev = df_15m.iloc[-7:-2]
        early_long_cross  = any(prev.iloc[i]['ema7'] < prev.iloc[i]['ema25'] and
                                prev.iloc[i+1]['ema7'] >= prev.iloc[i+1]['ema25']
                                for i in range(len(prev)-1))
        early_short_cross = any(prev.iloc[i]['ema7'] > prev.iloc[i]['ema25'] and
                                prev.iloc[i+1]['ema7'] <= prev.iloc[i+1]['ema25']
                                for i in range(len(prev)-1))

        # LONG SCORE
        if early_long_cross:           long_score += 60
        elif m15['ema7'] > m15['ema25']: long_score += 25
        if m15['ema25'] > m15['ema99']: long_score += 20
        if m15['close'] > m15['ema7']:  long_score += 10
        if vol_high:                   long_score += 15
        if 20 <= adx_val <= 35:        long_score += 15
        elif adx_val > 45:             long_score -= 20

        # SHORT SCORE
        if early_short_cross:            short_score += 60
        elif m15['ema7'] < m15['ema25']:  short_score += 25
        if m15['ema25'] < m15['ema99']:  short_score += 20
        if m15['close'] < m15['ema7']:   short_score += 10
        if vol_high:                     short_score += 15
        if 20 <= adx_val <= 35:          short_score += 15
        elif adx_val > 45:               short_score -= 20

        # =========================
        # BTC FILTER (Strict Macro Alignment)
        # =========================
        if symbol != 'BTC/USDT:USDT':
            if btc_trend == "bullish":
                short_score = 0  # Forbid SHORT in Bull Market
            elif btc_trend == "bearish":
                long_score = 0   # Forbid LONG in Bear Market
            elif btc_trend == "neutral":
                long_score -= 20
                short_score -= 20

        # =========================
        # LIMIT SCORE
        # =========================
        long_score = min(long_score, 100)
        short_score = min(short_score, 100)

        # =========================
        # GRADE
        # =========================
        score = max(long_score, short_score)
        grade = "C"
        min_score = STRATEGY_CONFIG['TRENDING']['MIN_SCORE']
        
        if score >= min_score + 20 and (early_long_cross or early_short_cross):
            grade = "A+"
        elif score >= min_score:
            grade = "A"
        elif score >= min_score - 10:
            grade = "B"

        # =========================
        # MARKET ENTRY
        # ENTRY_TYPE = MARKET, entry = current close price
        # =========================

        current_price = df_15m.iloc[-1]['close']
        
        long_pullback = round(current_price, 4)
        short_pullback = round(current_price, 4)

        # =========================
        # FAIL REASON LOGGING
        # =========================
        fail_reason = None
        long_micro_aligned = (m15['close'] > m15['ema7'] and m15['ema7'] > m15['ema25'])
        short_micro_aligned = (m15['close'] < m15['ema7'] and m15['ema7'] < m15['ema25'])
        
        if long_score < STRATEGY_CONFIG['TRENDING']['MIN_SCORE'] and short_score < STRATEGY_CONFIG['TRENDING']['MIN_SCORE']:
            fail_reason = f"Score Below MIN_SCORE ({max(long_score, short_score)})"
        elif long_score >= STRATEGY_CONFIG['TRENDING']['MIN_SCORE']:
            if btc_trend != "bullish":
                fail_reason = "BTC Trend Mismatch (Needs Bullish)"
            elif m15['rsi'] > STRATEGY_CONFIG['TRENDING']['FILTERS']['RSI_SAFE_LONG_MAX']:
                fail_reason = "RSI Too High for LONG"
            elif not long_micro_aligned:
                fail_reason = "Micro-Alignment Failed (Price > EMA7 > EMA25)"
        elif short_score >= STRATEGY_CONFIG['TRENDING']['MIN_SCORE']:
            if btc_trend != "bearish":
                fail_reason = "BTC Trend Mismatch (Needs Bearish)"
            elif m15['rsi'] < STRATEGY_CONFIG['TRENDING']['FILTERS']['RSI_SAFE_SHORT_MIN']:
                fail_reason = "RSI Too Low for SHORT"
            elif not short_micro_aligned:
                fail_reason = "Micro-Alignment Failed (Price < EMA7 < EMA25)"

        # =========================
        # LONG SIGNAL
        # =========================

        if (
            long_score >= STRATEGY_CONFIG['TRENDING']['MIN_SCORE']
            and btc_trend == "bullish"
            and m15['rsi'] <= STRATEGY_CONFIG['TRENDING']['FILTERS']['RSI_SAFE_LONG_MAX']
            and long_micro_aligned
        ):
            
            current_price = df_15m.iloc[-1]['close']
            if current_price < long_pullback:
                print(f"{symbol} LONG skipped - price already below support (Reversal Risk)", flush=True)
                set_scan_result(symbol, {"status": "Reversal Risk (Broke Support)", "score": long_score, "adx": adx_val, "atr": atr_pct, "volume": "HIGH" if vol_high else "NORMAL", "timestamp": time.time()})
                return {"symbol": symbol, "result": "skipped"}

            entry = round(
                long_pullback,
                4
            )

            atr = m15['atr']
            
            # =========================
            # AI FILTER (Pre-Execution)
            # =========================
            ai_override = None
            import ai_filter
            ai_instance = ai_filter.get_ai_filter()
            if ai_instance:
                indicators = {
                    "ema7": float(m15['ema7']), "ema25": float(m15['ema25']), "ema99": float(m15['ema99']),
                    "rsi": float(m15['rsi']), "adx": float(adx_val), "atr": float(atr_pct),
                    "stoch_rsi": float(m15.get('stoch_rsi', 0)), "volume_ratio": float(m15['volume'] / m15['vol_avg']) if m15.get('vol_avg', 0) > 0 else 0
                }
                ohlcv = df_15m[['open', 'high', 'low', 'close', 'volume']].tail(5).to_dict('records')
                ai_override = ai_instance.analyze_signal(symbol, "LONG", indicators, ohlcv)
                
                if not ai_instance.shadow_mode and not ai_override.get('approved', True):
                    set_scan_result(symbol, {"status": f"AI Rejected: {ai_override.get('reason')}", "score": long_score, "adx": adx_val, "atr": atr_pct, "volume": "HIGH" if vol_high else "NORMAL", "timestamp": time.time()})
                    return {"symbol": symbol, "result": "skipped"}

            sl, tp1, tp2, rr = calculate_trade_levels(
                entry,
                atr,
                "LONG",
                regime=local_regime,
                ai_override=ai_override
            )

            message = build_signal_message(
                symbol=symbol,
                side="LONG",
                grade=grade,
                score=long_score,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                rr=rr,
                rsi=m15['rsi'],
                adx=m15['adx'],
                atr_percent=atr_percent,
                volume_high=volume_high,
                btc_trend=btc_trend,
                local_regime=local_regime,
                btc_regime=btc_regime
            )

            print(
                message,
                flush=True
            )

            # Store signal for manual trading (or if auto trade skipped)
            if not silent_mode:
                send_telegram(message)
            
            if not signal_only:
                save_signal(
                    signal_id,
                    symbol,
                    "LONG",
                    grade,
                    long_score,
                    entry,
                    sl,
                    tp1,
                    tp2
                )

                signal_regime = CURRENT_REGIME

                with state_lock:
                    active_trades[signal_id] = {
                    "symbol": symbol,
                    "status": "SIGNAL",
                    "side": "LONG",
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "signal_regime": signal_regime,
                    "created_at": time.time(),
                    "grade": grade,
                    "score": long_score,
                    "strategy": "TREND"
                }
                # Update last_alert after storing the signal
                last_alert[(symbol, "TREND")] = now

            # =========================
            # AUTO TRADE LOGIC
            # =========================

            if AUTO_TRADE and not signal_only:
                
                skip_reason = None
                
                # Check grade filter
                min_req_grade = STRATEGY_CONFIG['TRENDING']['MIN_GRADE']
                if not passes_grade_filter(grade, min_req_grade):
                    skip_reason = f"Grade: {grade} < {min_req_grade}"
                
                # Check execution filters (only if grade passed)
                elif not skip_reason:
                    passes_exec, exec_reason = check_trend_filters(
                        atr_percent,
                        m15['adx'],
                        volume_high
                    )
                    if not passes_exec:
                        skip_reason = exec_reason
                
                # Check position limit (only if all other filters passed)
                if not skip_reason:
                    if not can_open_trade("LONG"):
                        # =========================
                        # GRADE OVERRIDE LOGIC (A+ and A)
                        # Runs BEFORE final rejection so high-grade signals
                        # can replace lower-grade / lower-score PENDING orders.
                        # =========================
                        override_executed = False
                        is_high_grade = grade in ("A+", "A") and config.ALLOW_PENDING_OVERRIDE

                        if is_high_grade:
                            with state_lock:
                                pending_trades = [
                                    (tid, t) for tid, t in active_trades.items()
                                    if t.get("status") == "PENDING"
                                ]
                            target_tid = None
                            target_trade = None

                            # Priority 1 (A+ incoming): kick any Grade B or lower pending
                            if grade == "A+":
                                for tid, t in pending_trades:
                                    if config.GRADE_PRIORITY.get(t.get("grade", "C"), 0) < config.GRADE_PRIORITY["A"]:
                                        target_tid, target_trade = tid, t
                                        break

                            # Priority 2 (A+ incoming): kick a Grade A pending
                            if not target_tid and grade == "A+":
                                for tid, t in pending_trades:
                                    if t.get("grade") == "A":
                                        target_tid, target_trade = tid, t
                                        break

                            # Priority 3 (A incoming): kick any Grade B or lower pending
                            if not target_tid and grade == "A":
                                for tid, t in pending_trades:
                                    if config.GRADE_PRIORITY.get(t.get("grade", "C"), 0) < config.GRADE_PRIORITY["A"]:
                                        target_tid, target_trade = tid, t
                                        break

                            # Priority 4: kick a same-grade A+ pending only if score gap is wide enough
                            if not target_tid and grade == "A+":
                                for tid, t in pending_trades:
                                    if t.get("grade") == "A+":
                                        if long_score - t.get("score", 0) >= config.MIN_SCORE_GAP_TO_OVERRIDE:
                                            target_tid, target_trade = tid, t
                                            break

                            if target_tid and target_trade:
                                try:
                                    bingx_client.cancel_order(target_trade["order_id"], target_trade["symbol"])
                                    with state_lock:
                                        active_trades.pop(target_tid, None)
                                    send_telegram(
                                        f"🔄 GRADE OVERRIDE ({grade})\n\n"
                                        f"❌ ยกเลิก: {target_trade['symbol']} "
                                        f"[{target_trade.get('grade','?')} score={target_trade.get('score',0)}]\n"
                                        f"✅ แทนที่: {symbol} [{grade} score={long_score}]\n\n"
                                        f"Side: LONG | Strategy: TREND"
                                    )
                                    # Log Grade Override event to Debug sheet
                                    google_sheet.log_aplus_override(
                                        symbol=symbol,
                                        strategy="TREND",
                                        grade=grade,
                                        score=long_score,
                                        cancelled_symbol=target_trade["symbol"],
                                        cancelled_grade=target_trade.get("grade", "?"),
                                        cancelled_score=target_trade.get("score", 0),
                                        adx=round(m15['adx'], 2),
                                        atr=round(atr_percent, 2),
                                    )
                                    override_executed = True
                                except Exception as ov_err:
                                    send_telegram(f"⚠️ Override cancel failed: {ov_err}")

                        if not override_executed:
                            skip_reason = f"Max {MAX_ACTIVE_TRADES} positions reached"

                # Check market regime (only if all other filters passed)
                if not skip_reason:
                    if CURRENT_REGIME != signal_regime:
                        skip_reason = "MARKET_REGIME_CHANGED"

                # Execute if no skip reason
                if not skip_reason:
                    vol_status = "HIGH" if volume_high else "NORMAL"
                    active_longs = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'LONG'])
                    active_shorts = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'SHORT'])
                    total_active = active_longs + active_shorts
                    pos_status = f"{total_active}/{MAX_ACTIVE_TRADES} (L:{active_longs}, S:{active_shorts})"
                    
                    send_telegram(
                        f"🤖 AUTO TRADE DECISION\n\n"
                        f"Symbol: {symbol}\n"
                        f"Side: LONG\n"
                        f"Result: EXECUTED\n"
                        f"Grade: {grade}\n"
                        f"ATR: {round(atr_percent, 2)}%\n"
                        f"ADX: {round(m15['adx'], 2)}\n"
                        f"Volume: {vol_status}\n"
                        f"Positions: {pos_status}"
                    )
                    
                    if symbol not in scan_results or not isinstance(scan_results[symbol], dict):
                        scan_results[symbol] = {}
                    scan_results[symbol]["strategy"] = "TREND"
                    threading.Thread(
                        target=lambda: bingx_client.execute_trade(symbol, "long"),
                        daemon=True
                    ).start()
                elif skip_reason == "MARKET_REGIME_CHANGED":
                    send_telegram(
                        f"⚠️ Auto Trade Cancelled\n\n"
                        f"Reason: Market Regime Changed\n\n"
                        f"Signal Regime:\n{signal_regime}\n\n"
                        f"Current Regime:\n{CURRENT_REGIME}"
                    )
                else:
                    vol_status = "HIGH" if volume_high else "NORMAL"
                    active_longs = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'LONG'])
                    active_shorts = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'SHORT'])
                    total_active = active_longs + active_shorts
                    pos_status = f"{total_active}/{MAX_ACTIVE_TRADES} (L:{active_longs}, S:{active_shorts})"
                    
                    send_telegram(
                        f"🤖 AUTO TRADE DECISION\n\n"
                        f"Symbol: {symbol}\n"
                        f"Side: LONG\n"
                        f"Result: SKIPPED\n"
                        f"Reason: {skip_reason}\n"
                        f"Grade: {grade}\n"
                        f"ATR: {round(atr_percent, 2)}%\n"
                        f"ADX: {round(m15['adx'], 2)}\n"
                        f"Volume: {vol_status}\n"
                        f"Longs: {pos_status}"
                    )

            vol_status = "HIGH" if volume_high else "NORMAL"
            set_scan_result(symbol, {"status": "Signal Generated", "score": long_score, "adx": round(m15['adx'], 2), "atr": round(atr_percent, 2), "volume": vol_status, "timestamp": now, "strategy": "TREND"})
            # Track candidate signal for top candidates (Feature 6)
            candidate_signals[symbol] = {
                "side": "LONG",
                "grade": grade,
                "score": long_score,
                "symbol": symbol,
                "strategy": "TREND",
            }
            
            # Google Sheets logging
            signal_id = google_sheet.log_signal(
                symbol=symbol,
                side="LONG",
                grade=grade,
                score=long_score,
                entry=entry,
                sl=sl,
                tp=tp2,
                atr=round(atr_percent, 2),
                adx=round(m15['adx'], 2),
                volume=vol_status,
                btc_trend=btc_trend,
                status="SIGNAL",
                strategy="TREND",
                allocation_decision="ALLOCATED",
                skip_reason="",
                vwap_position="ABOVE" if is_above_vwap else "BELOW",
                stoch_rsi=round(m15['stoch_rsi'], 2),
                stretch_pct=round(distance_pct, 2),
                candle_color="GREEN" if is_green else "RED",
                ai_approved=ai_override.get('approved') if ai_override else "",
                ai_confidence=ai_override.get('confidence') if ai_override else "",
                ai_reason=ai_override.get('reason') if ai_override else "",
                ai_sl_mult=ai_override.get('sl_atr_mult') if ai_override else "",
                ai_tp_rr=ai_override.get('tp_rr_ratio') if ai_override else ""
            )
            google_sheet.log_fill_analysis(
                symbol=symbol,
                side="LONG",
                current_price=m15['close'],
                entry_price=entry,
                grade=grade,
                score=long_score,
                atr=round(atr_percent, 2),
                adx=round(m15['adx'], 2),
                btc_trend=btc_trend,
                fill_status="OPEN"
            )

            # BACKTEST: บันทึก signal สำหรับ evaluate ผล 4h ทีหลัง
            if signal_id:
                backtest.record_signal(
                    signal_id=signal_id,
                    symbol=symbol,
                    side="LONG",
                    entry=entry,
                    sl=sl,
                    tp=tp2,
                    grade=grade,
                    score=long_score,
                    rr=rr,
                    strategy="TREND",
                    local_regime=local_regime,
                    btc_regime=btc_regime,
                    adx=round(m15['adx'], 2),
                    atr_pct=round(atr_percent, 2),
                    vol_status=vol_status,
                    btc_trend=btc_trend,
                    vwap_pos="ABOVE" if is_above_vwap else "BELOW",
                    stoch_rsi=round(m15['stoch_rsi'], 2),
                    stretch_pct=round(distance_pct, 2),
                    candle_color="GREEN" if is_green else "RED"
                )

            return {"symbol": symbol, "result": "signal"}
        
        # =========================
        # SHORT SIGNAL
        # =========================

        elif (
            short_score >= STRATEGY_CONFIG['TRENDING']['MIN_SCORE']
            and btc_trend == "bearish"
            and m15['rsi'] >= STRATEGY_CONFIG['TRENDING']['FILTERS']['RSI_SAFE_SHORT_MIN']
            and short_micro_aligned
        ):

            current_price = df_15m.iloc[-1]['close']
            if current_price > short_pullback:
                print(f"{symbol} SHORT skipped - price already above resistance (Reversal Risk)", flush=True)
                set_scan_result(symbol, {"status": "Reversal Risk (Broke Resistance)", "score": short_score, "adx": adx_val, "atr": atr_pct, "volume": "HIGH" if vol_high else "NORMAL", "timestamp": time.time()})
                return {"symbol": symbol, "result": "skipped"}

            entry = round(
                short_pullback,
                4
            )

            atr = m15['atr']
            
            # =========================
            # AI FILTER (Pre-Execution)
            # =========================
            ai_override = None
            import ai_filter
            ai_instance = ai_filter.get_ai_filter()
            if ai_instance:
                indicators = {
                    "ema7": float(m15['ema7']), "ema25": float(m15['ema25']), "ema99": float(m15['ema99']),
                    "rsi": float(m15['rsi']), "adx": float(adx_val), "atr": float(atr_pct),
                    "stoch_rsi": float(m15.get('stoch_rsi', 0)), "volume_ratio": float(m15['volume'] / m15['vol_avg']) if m15.get('vol_avg', 0) > 0 else 0
                }
                ohlcv = df_15m[['open', 'high', 'low', 'close', 'volume']].tail(5).to_dict('records')
                ai_override = ai_instance.analyze_signal(symbol, "SHORT", indicators, ohlcv)
                
                if not ai_instance.shadow_mode and not ai_override.get('approved', True):
                    set_scan_result(symbol, {"status": f"AI Rejected: {ai_override.get('reason')}", "score": short_score, "adx": adx_val, "atr": atr_pct, "volume": "HIGH" if vol_high else "NORMAL", "timestamp": time.time()})
                    return {"symbol": symbol, "result": "skipped"}

            sl, tp1, tp2, rr = calculate_trade_levels(
                entry,
                atr,
                "SHORT",
                regime=local_regime,
                ai_override=ai_override
            )

            message = build_signal_message(
                symbol=symbol,
                side="SHORT",
                grade=grade,
                score=short_score,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                rr=rr,
                rsi=m15['rsi'],
                adx=m15['adx'],
                atr_percent=atr_percent,
                volume_high=volume_high,
                btc_trend=btc_trend,
                local_regime=local_regime,
                btc_regime=btc_regime
            )

            print(
                message,
                flush=True
            )

            # Store signal for manual trading (or if auto trade skipped)
            if not silent_mode:
                send_telegram(message)
            
            if not signal_only:
                save_signal(
                    signal_id,
                    symbol,
                    "SHORT",
                    grade,
                    short_score,
                    entry,
                    sl,
                    tp1,
                    tp2
                )

                signal_regime = CURRENT_REGIME

                with state_lock:
                    active_trades[signal_id] = {
                    "symbol": symbol,
                    "status": "SIGNAL",
                    "side": "SHORT",
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "signal_regime": signal_regime,
                    "created_at": time.time(),
                    "grade": grade,
                    "score": short_score,
                    "strategy": "TREND"
                }
                # Update last_alert after storing the signal
                last_alert[(symbol, "TREND")] = now

            # =========================
            # AUTO TRADE LOGIC
            # =========================

            if AUTO_TRADE and not signal_only:
                
                skip_reason = None
                
                # Check grade filter
                min_req_grade = STRATEGY_CONFIG['TRENDING']['MIN_GRADE']
                if not passes_grade_filter(grade, min_req_grade):
                    skip_reason = f"Grade: {grade} < {min_req_grade}"
                
                # Check execution filters (only if grade passed)
                elif not skip_reason:
                    passes_exec, exec_reason = check_trend_filters(
                        atr_percent,
                        m15['adx'],
                        volume_high
                    )
                    if not passes_exec:
                        skip_reason = exec_reason
                
                # Check position limit (only if all other filters passed)
                if not skip_reason:
                    if not can_open_trade("SHORT"):
                        # =========================
                        # GRADE OVERRIDE LOGIC (A+ and A)
                        # Runs BEFORE final rejection so high-grade signals
                        # can replace lower-grade / lower-score PENDING orders.
                        # =========================
                        override_executed = False
                        is_high_grade = grade in ("A+", "A") and config.ALLOW_PENDING_OVERRIDE

                        if is_high_grade:
                            with state_lock:
                                pending_trades = [
                                    (tid, t) for tid, t in active_trades.items()
                                    if t.get("status") == "PENDING"
                                ]
                            target_tid = None
                            target_trade = None

                            # Priority 1 (A+ incoming): kick any Grade B or lower pending
                            if grade == "A+":
                                for tid, t in pending_trades:
                                    if config.GRADE_PRIORITY.get(t.get("grade", "C"), 0) < config.GRADE_PRIORITY["A"]:
                                        target_tid, target_trade = tid, t
                                        break

                            # Priority 2 (A+ incoming): kick a Grade A pending
                            if not target_tid and grade == "A+":
                                for tid, t in pending_trades:
                                    if t.get("grade") == "A":
                                        target_tid, target_trade = tid, t
                                        break

                            # Priority 3 (A incoming): kick any Grade B or lower pending
                            if not target_tid and grade == "A":
                                for tid, t in pending_trades:
                                    if config.GRADE_PRIORITY.get(t.get("grade", "C"), 0) < config.GRADE_PRIORITY["A"]:
                                        target_tid, target_trade = tid, t
                                        break

                            # Priority 4: kick a same-grade A+ pending only if score gap is wide enough
                            if not target_tid and grade == "A+":
                                for tid, t in pending_trades:
                                    if t.get("grade") == "A+":
                                        if short_score - t.get("score", 0) >= config.MIN_SCORE_GAP_TO_OVERRIDE:
                                            target_tid, target_trade = tid, t
                                            break

                            if target_tid and target_trade:
                                try:
                                    bingx_client.cancel_order(target_trade["order_id"], target_trade["symbol"])
                                    with state_lock:
                                        active_trades.pop(target_tid, None)
                                    send_telegram(
                                        f"🔄 GRADE OVERRIDE ({grade})\n\n"
                                        f"❌ ยกเลิก: {target_trade['symbol']} "
                                        f"[{target_trade.get('grade','?')} score={target_trade.get('score',0)}]\n"
                                        f"✅ แทนที่: {symbol} [{grade} score={short_score}]\n\n"
                                        f"Side: SHORT | Strategy: TREND"
                                    )
                                    # Log Grade Override event to Debug sheet
                                    google_sheet.log_aplus_override(
                                        symbol=symbol,
                                        strategy="TREND",
                                        grade=grade,
                                        score=short_score,
                                        cancelled_symbol=target_trade["symbol"],
                                        cancelled_grade=target_trade.get("grade", "?"),
                                        cancelled_score=target_trade.get("score", 0),
                                        adx=round(m15['adx'], 2),
                                        atr=round(atr_percent, 2),
                                    )
                                    override_executed = True
                                except Exception as ov_err:
                                    send_telegram(f"⚠️ Override cancel failed: {ov_err}")

                        if not override_executed:
                            skip_reason = f"Max {MAX_ACTIVE_TRADES} positions reached"

                # Check market regime (only if all other filters passed)
                if not skip_reason:
                    if CURRENT_REGIME != signal_regime:
                        skip_reason = "MARKET_REGIME_CHANGED"

                # Execute if no skip reason
                if not skip_reason:
                    vol_status = "HIGH" if volume_high else "NORMAL"
                    active_longs = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'LONG'])
                    active_shorts = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'SHORT'])
                    total_active = active_longs + active_shorts
                    pos_status = f"{total_active}/{MAX_ACTIVE_TRADES} (L:{active_longs}, S:{active_shorts})"
                    
                    send_telegram(
                        f"🤖 AUTO TRADE DECISION\n\n"
                        f"Symbol: {symbol}\n"
                        f"Side: SHORT\n"
                        f"Result: EXECUTED\n"
                        f"Grade: {grade}\n"
                        f"ATR: {round(atr_percent, 2)}%\n"
                        f"ADX: {round(m15['adx'], 2)}\n"
                        f"Volume: {vol_status}\n"
                        f"Positions: {pos_status}"
                    )
                    
                    if symbol not in scan_results or not isinstance(scan_results[symbol], dict):
                        scan_results[symbol] = {}
                    scan_results[symbol]["strategy"] = "TREND"
                    threading.Thread(
                        target=lambda: bingx_client.execute_trade(symbol, "short"),
                        daemon=True
                    ).start()
                elif skip_reason == "MARKET_REGIME_CHANGED":
                    send_telegram(
                        f"⚠️ Auto Trade Cancelled\n\n"
                        f"Reason: Market Regime Changed\n\n"
                        f"Signal Regime:\n{signal_regime}\n\n"
                        f"Current Regime:\n{CURRENT_REGIME}"
                    )
                else:
                    vol_status = "HIGH" if volume_high else "NORMAL"
                    active_longs = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'LONG'])
                    active_shorts = len([t for t in list(active_trades.values()) if t.get('status') in ['PENDING', 'OPEN'] and t.get('side') == 'SHORT'])
                    total_active = active_longs + active_shorts
                    pos_status = f"{total_active}/{MAX_ACTIVE_TRADES} (L:{active_longs}, S:{active_shorts})"
                    
                    send_telegram(
                        f"🤖 AUTO TRADE DECISION\n\n"
                        f"Symbol: {symbol}\n"
                        f"Side: SHORT\n"
                        f"Result: SKIPPED\n"
                        f"Reason: {skip_reason}\n"
                        f"Grade: {grade}\n"
                        f"ATR: {round(atr_percent, 2)}%\n"
                        f"ADX: {round(m15['adx'], 2)}\n"
                        f"Volume: {vol_status}\n"
                        f"Positions: {pos_status}"
                    )

            vol_status = "HIGH" if volume_high else "NORMAL"
            set_scan_result(symbol, {"status": "Signal Generated", "score": short_score, "adx": round(m15['adx'], 2), "atr": round(atr_percent, 2), "volume": vol_status, "timestamp": now, "strategy": "TREND"})
            # Track candidate signal for top candidates (Feature 6)
            candidate_signals[symbol] = {
                "side": "SHORT",
                "grade": grade,
                "score": short_score,
                "symbol": symbol,
                "strategy": "TREND",
            }
            
            # Google Sheets logging
            signal_id = google_sheet.log_signal(
                symbol=symbol,
                side="SHORT",
                grade=grade,
                score=short_score,
                entry=entry,
                sl=sl,
                tp=tp2,
                atr=round(atr_percent, 2),
                adx=round(m15['adx'], 2),
                volume=vol_status,
                btc_trend=btc_trend,
                status="SIGNAL",
                strategy="TREND",
                allocation_decision="ALLOCATED",
                skip_reason="",
                vwap_position="ABOVE" if is_above_vwap else "BELOW",
                stoch_rsi=round(m15['stoch_rsi'], 2),
                stretch_pct=round(distance_pct, 2),
                candle_color="GREEN" if is_green else "RED",
                ai_approved=ai_override.get('approved') if ai_override else "",
                ai_confidence=ai_override.get('confidence') if ai_override else "",
                ai_reason=ai_override.get('reason') if ai_override else "",
                ai_sl_mult=ai_override.get('sl_atr_mult') if ai_override else "",
                ai_tp_rr=ai_override.get('tp_rr_ratio') if ai_override else ""
            )
            google_sheet.log_fill_analysis(
                symbol=symbol,
                side="SHORT",
                current_price=m15['close'],
                entry_price=entry,
                grade=grade,
                score=short_score,
                atr=round(atr_percent, 2),
                adx=round(m15['adx'], 2),
                btc_trend=btc_trend,
                fill_status="OPEN"
            )

            # BACKTEST: บันทึก signal สำหรับ evaluate ผล 4h ทีหลัง
            if signal_id:
                backtest.record_signal(
                    signal_id=signal_id,
                    symbol=symbol,
                    side="SHORT",
                    entry=entry,
                    sl=sl,
                    tp=tp2,
                    grade=grade,
                    score=short_score,
                    rr=rr,
                    strategy="TREND",
                    local_regime=local_regime,
                    btc_regime=btc_regime,
                    adx=round(m15['adx'], 2),
                    atr_pct=round(atr_percent, 2),
                    vol_status=vol_status,
                    btc_trend=btc_trend,
                    vwap_pos="ABOVE" if is_above_vwap else "BELOW",
                    stoch_rsi=round(m15['stoch_rsi'], 2),
                    stretch_pct=round(distance_pct, 2),
                    candle_color="GREEN" if is_green else "RED"
                )

            return {"symbol": symbol, "result": "signal"}
    
    except Exception:
        print(
            f"{symbol} ERROR",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

        set_scan_result(symbol, {"status": "Error", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        return {"symbol": symbol, "result": "error"}

    # No LONG or SHORT signal generated — fall through from try
    vol_status = "HIGH" if volume_high else "NORMAL"
    score = max(long_score, short_score)
    missing_points = max(STRATEGY_CONFIG['TRENDING']['MIN_SCORE'] - score, 0)
    
    if 'fail_reason' not in locals() or not fail_reason:
        fail_reason = f"Score Below MIN_SCORE ({missing_points} needed)" if missing_points > 0 else "Other Criteria Failed"
        
    set_scan_result(symbol, {"status": fail_reason, "score": score, "adx": round(m15['adx'], 2), "atr": round(atr_percent, 2), "volume": vol_status, "timestamp": now, "long_score": long_score, "short_score": short_score, "missing_points": missing_points})
    # Track rejected signal (Feature 3)
    if score > 0:
        rejected_signals.add(symbol)
    # Google Sheets debug logging
    google_sheet.log_debug(symbol, fail_reason, score=score, adx=round(m15['adx'], 2), atr=round(atr_percent, 2), vwap_position="ABOVE" if locals().get('is_above_vwap') else "BELOW" if 'is_above_vwap' in locals() else "", stoch_rsi=round(locals().get('m3', locals().get('m15', {})).get('stoch_rsi', 0), 2) if 'm3' in locals() or 'm15' in locals() else "", stretch_pct=round(locals().get('distance_pct', 0), 2) if 'distance_pct' in locals() else "", candle_color="GREEN" if locals().get('is_green') else "RED" if 'is_green' in locals() else "")
    return {"symbol": symbol, "result": "skipped"}


# =========================
# ANALYZE SIDEWAYS
# =========================

def build_sideways_message(symbol, grade, score, side, entry, sl, tp, rr, rsi, adx, atr_percent, volume_high, local_regime="", btc_regime=""):
    icon = "🚀" if side == "LONG" else "🔻"
    return f"""
{icon} {side} SIGNAL
{symbol}

Coin Regime:
{local_regime}

Active Strategy:
SIDEWAYS

Grade:
{grade}

Score:
{score}/100

Mean Reversion Entry:
{entry}

SL:
{sl}

TP:
{tp}

RR:
1:{rr}

RSI:
{round(rsi, 2)}

ADX:
{round(adx, 2)}

ATR %:
{round(atr_percent, 2)}

Volume:
{"HIGH" if volume_high else "NORMAL"}

Plan:
- Mean reversion to BB middle
- Fixed SL
"""


def analyze_sideways(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False, df_15m=None, df_1h=None, local_regime="", btc_regime=""):

    # =========================
    # PAUSE TRADING CHECK
    # =========================
    
    global pause_trading

    if pause_trading:
        return {"symbol": symbol, "result": "paused"}

    try:

        now = time.time()

        # =========================
        # STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN'] (Feature 4: Bypass if ignore_cooldown_once is set)
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "SIDEWAYS"))

                if last_time and now - last_time < STRATEGY_CONFIG.get(local_regime, STRATEGY_CONFIG['TRENDING'])['COOLDOWN']:
                    set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                    return {"symbol": symbol, "result": "skipped"}

        # =========================
        # GET DATA
        # =========================

        if df_15m is None:
            df_15m = get_dataframe(symbol, '15m')
            
        if df_1h is None:
            df_1h = get_dataframe(symbol, '1h')
            
        df_3m = get_dataframe(symbol, '3m')

        m15 = df_15m.iloc[-2]
        h1  = df_1h.iloc[-2]
        m3  = df_3m.iloc[-2]

        # =========================
        # PRE-FILTER (Early rejection for ADX/ATR)
        # =========================
        adx_val = round(m15['adx'], 2)
        atr_pct = (m15['atr'] / m15['close']) * 100
        vol_high = m15['volume'] > m15['vol_avg'] * 1.3
        
        passes_exec, exec_reason = check_sideways_filters(atr_pct, adx_val, vol_high)
        if not passes_exec:
            set_scan_result(symbol, {"status": exec_reason, "score": 0, "adx": adx_val, "atr": atr_pct, "volume": "HIGH" if vol_high else "NORMAL", "timestamp": now})
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # FOMO FILTER
        # =========================

        candle_size = abs(
            m15['close'] - m15['open']
        )

        if candle_size > m15['atr'] * 1.5:
            print(
                f"{symbol} skipped - candle too big",
                flush=True
            )

            adx_val = round(m15['adx'], 2)
            atr_val = round((m15['atr'] / m15['close']) * 100, 2)
            vol_status = "HIGH" if m15['volume'] > m15['vol_avg'] * 1.3 else "NORMAL"
            set_scan_result(symbol, {"status": "Candle Too Big", "score": 0, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now})
            # Track rejected signal (Feature 3)
            rejected_signals.add(symbol)
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # INDICATORS
        # =========================

        rsi = h1['rsi']
        bb_lower = h1['bb_lower']
        bb_upper = h1['bb_upper']
        bb_mid = h1['bb_mid']
        stoch_rsi = h1.get('stoch_rsi', 50)
        atr = h1['atr']
        
        # We keep adx, and close from m15 for risk calculation and message
        close = m15['close']
        adx = m15['adx']

        atr_percent = round((atr / close) * 100, 2)
        volume_high = m15['volume'] > m15['vol_avg'] * 1.3
        signal_id = str(uuid.uuid4())[:8]

        # =========================
        # SIDEWAYS CONDITION CHECK
        # =========================

        # =========================
        # TREND FILTER (Bug Fix: ป้องกัน LONG ในตลาดที่ downtrend ชัดเจน)
        # =========================
        ema7 = h1['ema7']
        ema25 = h1['ema25']

        # LONG: RSI < 45, Low <= BB Lower, ADX < 28
        # + ต้องไม่ downtrend ชัด: ema7 ต้องไม่ต่ำกว่า ema25 มากเกิน 1%
        long_trend_ok = ema7 >= ema25 * 0.99  # ยอมให้ต่ำกว่าได้นิดหน่อย แต่ไม่ downtrend ชัด
        long_condition = (
            rsi < 45
            and h1['low'] <= bb_lower
            and adx <= STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX']
            and long_trend_ok
        )

        # SHORT: RSI > 55, High >= BB Upper, ADX < 25
        # + ต้องไม่ uptrend ชัด: ema7 ต้องไม่สูงกว่า ema25 มากเกิน 1%
        short_trend_ok = ema7 <= ema25 * 1.01
        short_condition = (
            rsi > 55
            and h1['high'] >= bb_upper
            and adx <= STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX']
            and short_trend_ok
        )

        if not long_condition and not short_condition:
            set_scan_result(symbol, {"status": "Sideways Market", "score": 0, "adx": round(adx, 2), "atr": atr_percent, "volume": "HIGH" if volume_high else "NORMAL", "timestamp": now})
            # Track rejected signal (Feature 3)
            rejected_signals.add(symbol)
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # PICK SIDE
        # =========================

        if long_condition and short_condition:
            # Both conditions met — pick the more extreme RSI
            side = "LONG" if abs(40 - rsi) > abs(60 - rsi) else "SHORT"
        elif long_condition:
            side = "LONG"
        else:
            side = "SHORT"

        # =========================
        # CALCULATE LEVELS
        # =========================

        if side == "LONG":
            entry = round(close, 4)
        else:
            entry = round(close, 4)

        sl, tp, rr = calculate_sideways_levels(
            entry, atr, bb_mid, side
        )

        # =========================
        # SCORE (คำนวณก่อน grade เพราะ grade ต้องใช้ score)
        # RSI rescale สำหรับ sideways range จริงๆ
        # SHORT: RSI 60=0pts, 75=60pts(max)
        # LONG:  RSI 40=0pts, 25=60pts(max)
        # =========================
        
        base_score = 0
        if (side == "LONG" and h1['low'] <= bb_lower) or (side == "SHORT" and h1['high'] >= bb_upper):
            base_score = 20
            
        # === RSI DIVERGENCE DETECTION ===
        # ตรวจว่าราคา New High แต่ RSI ไม่ New High (Bearish Divergence)
        # ใช้ 5 candles ย้อนหลัง
        recent_h1 = df_1h.iloc[-7:-2]
        price_made_high = h1['high'] > recent_h1['high'].max()
        rsi_made_high   = h1['rsi'] > recent_h1['rsi'].max()

        bearish_divergence = price_made_high and not rsi_made_high  # Short signal
        bullish_divergence = (h1['low'] < recent_h1['low'].min()) and (h1['rsi'] > recent_h1['rsi'].min())  # Long signal

        # เพิ่ม Bonus score เข้าใน scoring section ของ sideways
        if side == 'SHORT' and bearish_divergence:
            base_score += 25  # Divergence confirmation
        if side == 'LONG' and bullish_divergence:
            base_score += 25

        if side == "LONG":
            rsi_score = max(0, min(60, int((40 - rsi) * 4)))  # RSI 40→0pts, 25→60pts
        else:
            rsi_score = max(0, min(60, int((rsi - 60) * 4)))  # RSI 60→0pts, 75→60pts

        # RR component: max 40 pts
        rr_score = max(0, min(40, int((rr - 1.0) * 20)))

        sideways_score = base_score + rsi_score + rr_score
        
        # StochRSI Filter
        if side == "LONG" and stoch_rsi > 20:
            sideways_score -= 50
        elif side == "SHORT" and stoch_rsi < 80:
            sideways_score -= 50

        # =========================
        # BTC FILTER (Strict Macro Alignment)
        # =========================
        btc_trend = get_btc_trend()
        if symbol != 'BTC/USDT:USDT':
            if btc_trend == "bullish" and side == "SHORT":
                sideways_score = 0  # Forbid SHORT in Bull Market
            elif btc_trend == "bearish" and side == "LONG":
                sideways_score = 0   # Forbid LONG in Bear Market
            elif btc_trend == "neutral":
                sideways_score -= 20

        # =========================
        # GRADE (ขึ้นกับ score รวม)
        # A+ = RSI extreme มาก + RR ดี (score 70+)
        # A  = RSI overbought/oversold ชัด + RR ดี (score 50+)
        # B  = RSI พอใช้ หรือ RR ดีมาก (score 35+)
        # C  = RSI แค่แตะ threshold
        # =========================
        min_score = STRATEGY_CONFIG['SIDEWAYS']['MIN_SCORE']
        if sideways_score < min_score:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": sideways_score, "adx": round(adx, 2), "atr": atr_percent, "volume": "HIGH" if volume_high else "NORMAL", "timestamp": now})
            return {"symbol": symbol, "result": "skipped"}

        if sideways_score >= min_score + 10 and adx_val < 20:
            grade = "A+"
        elif sideways_score >= min_score:
            grade = "A"
        elif sideways_score >= min_score - 15:
            grade = "B"
        else:
            grade = "C"

        # =========================
        # AI FILTER (Pre-Execution)
        # =========================
        ai_override = None
        import ai_filter
        ai_instance = ai_filter.get_ai_filter()
        if ai_instance:
            indicators = {
                "ema7": float(m15['ema7']), "ema25": float(m15['ema25']), "ema99": float(m15['ema99']),
                "rsi": float(rsi), "adx": float(adx), "atr": float(atr_percent),
                "stoch_rsi": float(stoch_rsi), "volume_ratio": float(m15['volume'] / m15['vol_avg']) if m15.get('vol_avg', 0) > 0 else 0
            }
            ohlcv = df_15m[['open', 'high', 'low', 'close', 'volume']].tail(5).to_dict('records')
            ai_override = ai_instance.analyze_signal(symbol, side, indicators, ohlcv)
            
            if not ai_instance.shadow_mode and not ai_override.get('approved', True):
                set_scan_result(symbol, {"status": f"AI Rejected: {ai_override.get('reason')}", "score": sideways_score, "adx": adx, "atr": atr_percent, "volume": "HIGH" if volume_high else "NORMAL", "timestamp": time.time()})
                return {"symbol": symbol, "result": "skipped"}

        # Recalculate if AI suggested new multipliers
        if ai_override and ai_override.get("sl_atr_mult"):
            sl, tp, rr = calculate_sideways_levels(
                entry, atr, bb_mid, side, ai_override=ai_override
            )

        # =========================
        # SIGNAL MESSAGE
        # =========================

        message = build_sideways_message(
            symbol=symbol,
            grade=grade,
            score=sideways_score,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
            rr=rr,
            rsi=rsi,
            adx=adx,
            atr_percent=atr_percent,
            volume_high=volume_high,
            local_regime=local_regime,
            btc_regime=btc_regime
        )

        print(
            message,
            flush=True
        )

        # Store signal for manual trading (or if auto trade skipped)
        if not silent_mode:
            send_telegram(message)

        if not signal_only:
            save_signal(
                signal_id,
                symbol,
                side,
                grade,
                sideways_score,
                entry,
                sl,
                tp,
                tp
            )

            signal_regime = CURRENT_REGIME

            with state_lock:
                active_trades[signal_id] = {
                "symbol": symbol,
                "status": "SIGNAL",
                "side": side,
                "entry": entry,
                "sl": sl,
                "tp1": tp,
                "tp2": tp,
                "signal_regime": signal_regime,
                "created_at": time.time(),
                "grade": grade,
                "score": sideways_score,
                "strategy": "SIDEWAYS"
            }
            # Update last_alert after storing the signal
            last_alert[(symbol, "SIDEWAYS")] = now

        # =========================
        # AUTO TRADE LOGIC
        # =========================

        if AUTO_TRADE and not signal_only:

            skip_reason = None

            # Check grade filter FIRST (Bug Fix: sideways ไม่เคย check grade filter)
            min_req_grade = STRATEGY_CONFIG['SIDEWAYS']['MIN_GRADE']
            if not passes_grade_filter(grade, min_req_grade):
                skip_reason = f"Grade: {grade} < {min_req_grade}"

            # Check execution filters (only if grade passed)
            if not skip_reason:
                passes_exec, exec_reason = check_sideways_filters(
                    atr_percent,
                    adx,
                    volume_high
                )
                if not passes_exec:
                    skip_reason = exec_reason

            # Check position limit (only if all other filters passed)
            if not skip_reason:
                if not can_open_trade(side):
                    # =========================
                    # GRADE OVERRIDE LOGIC (A+ and A)
                    # Runs BEFORE final rejection so high-grade signals
                    # can replace lower-grade / lower-score PENDING orders.
                    # =========================
                    override_executed = False
                    is_high_grade = grade in ("A+", "A") and config.ALLOW_PENDING_OVERRIDE

                    if is_high_grade:
                        with state_lock:
                            pending_trades = [
                                (tid, t) for tid, t in active_trades.items()
                                if t.get("status") == "PENDING"
                            ]
                        target_tid = None
                        target_trade = None

                        # Priority 1 (A+ incoming): kick any Grade B or lower pending
                        if grade == "A+":
                            for tid, t in pending_trades:
                                if config.GRADE_PRIORITY.get(t.get("grade", "C"), 0) < config.GRADE_PRIORITY["A"]:
                                    target_tid, target_trade = tid, t
                                    break

                        # Priority 2 (A+ incoming): kick a Grade A pending
                        if not target_tid and grade == "A+":
                            for tid, t in pending_trades:
                                if t.get("grade") == "A":
                                    target_tid, target_trade = tid, t
                                    break

                        # Priority 3 (A incoming): kick any Grade B or lower pending
                        if not target_tid and grade == "A":
                            for tid, t in pending_trades:
                                if config.GRADE_PRIORITY.get(t.get("grade", "C"), 0) < config.GRADE_PRIORITY["A"]:
                                    target_tid, target_trade = tid, t
                                    break

                        # Priority 4 (A+ only): kick same-grade A+ pending (no score gap required for SIDEWAYS)
                        if not target_tid and grade == "A+":
                            for tid, t in pending_trades:
                                if t.get("grade") == "A+":
                                    target_tid, target_trade = tid, t
                                    break

                        if target_tid and target_trade:
                            try:
                                bingx_client.cancel_order(target_trade["order_id"], target_trade["symbol"])
                                with state_lock:
                                    active_trades.pop(target_tid, None)
                                send_telegram(
                                    f"🔄 GRADE OVERRIDE ({grade})\n\n"
                                    f"❌ ยกเลิก: {target_trade['symbol']} "
                                    f"[{target_trade.get('grade','?')} score={target_trade.get('score',0)}]\n"
                                    f"✅ แทนที่: {symbol} [{grade}]\n\n"
                                    f"Side: {side} | Strategy: SIDEWAYS"
                                )
                                # Log Grade Override event to Debug sheet
                                google_sheet.log_aplus_override(
                                    symbol=symbol,
                                    strategy="SIDEWAYS",
                                    grade=grade,
                                    cancelled_symbol=target_trade["symbol"],
                                    cancelled_grade=target_trade.get("grade", "?"),
                                    cancelled_score=target_trade.get("score", 0),
                                )
                                override_executed = True
                            except Exception as ov_err:
                                send_telegram(f"⚠️ Override cancel failed: {ov_err}")

                    if not override_executed:
                        skip_reason = f"Max {MAX_ACTIVE_TRADES} positions reached"

            # Check market regime (only if all other filters passed)
            if not skip_reason:
                if CURRENT_REGIME != signal_regime:
                    skip_reason = "MARKET_REGIME_CHANGED"

            # Execute if no skip reason
            if not skip_reason:
                vol_status = "HIGH" if volume_high else "NORMAL"

                send_telegram(
                    f"🤖 AUTO TRADE DECISION\n\n"
                    f"Symbol: {symbol}\n"
                    f"Side: {side}\n"
                    f"Strategy: SIDEWAYS\n"
                    f"Result: EXECUTED\n"
                    f"Grade: {grade}\n"
                    f"ATR: {atr_percent}%\n"
                    f"ADX: {round(adx, 2)}\n"
                    f"Volume: {vol_status}"
                )

                threading.Thread(
                    target=lambda: bingx_client.execute_trade(symbol, side.lower()),
                    daemon=True
                ).start()
            elif skip_reason == "MARKET_REGIME_CHANGED":
                send_telegram(
                    f"⚠️ Auto Trade Cancelled\n\n"
                    f"Reason: Market Regime Changed\n\n"
                    f"Signal Regime:\n{signal_regime}\n\n"
                    f"Current Regime:\n{CURRENT_REGIME}"
                )
            else:
                vol_status = "HIGH" if volume_high else "NORMAL"

                send_telegram(
                    f"🤖 AUTO TRADE DECISION\n\n"
                    f"Symbol: {symbol}\n"
                    f"Side: {side}\n"
                    f"Strategy: SIDEWAYS\n"
                    f"Result: SKIPPED\n"
                    f"Reason: {skip_reason}\n"
                    f"Grade: {grade}\n"
                    f"ATR: {atr_percent}%\n"
                    f"ADX: {round(adx, 2)}\n"
                    f"Volume: {vol_status}"
                )

        vol_status = "HIGH" if volume_high else "NORMAL"
        set_scan_result(symbol, {"status": "Signal Generated", "score": sideways_score, "adx": round(adx, 2), "atr": atr_percent, "volume": vol_status, "timestamp": now, "strategy": "SIDEWAYS"})
        # Track candidate signal for top candidates (Feature 6)
        candidate_signals[symbol] = {
            "side": side,
            "grade": grade,
            "score": sideways_score,
            "symbol": symbol,
            "strategy": "SIDEWAYS",
        }

        # BACKTEST: บันทึก signal เพื่อให้ evaluate หลัง 4h จบแท่ง
        try:
            google_sheet.log_signal(
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                grade=grade,
                score=sideways_score,
                entry=entry,
                sl=sl,
                tp=tp,
                atr=atr_percent,
                adx=round(adx, 2),
                volume=vol_status,
                btc_trend=get_btc_trend(),
                status="SIGNAL",
                strategy="SIDEWAYS",
                allocation_decision="ALLOCATED",
                skip_reason="",
                vwap_position="",
                stoch_rsi=round(m3.get('stoch_rsi', 0), 2) if 'stoch_rsi' in m3 else 0,
                stretch_pct=round(abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100, 2),
                candle_color="GREEN" if m3['close'] > m3['open'] else "RED",
                local_regime=local_regime,
                btc_regime=btc_regime,
                ai_approved=ai_override.get('approved') if ai_override else "",
                ai_confidence=ai_override.get('confidence') if ai_override else "",
                ai_reason=ai_override.get('reason') if ai_override else "",
                ai_sl_mult=ai_override.get('sl_atr_mult') if ai_override else "",
                ai_tp_rr=ai_override.get('tp_rr_ratio') if ai_override else ""
            )
        except Exception as e:
            print(f"[SIDEWAYS] Google Sheets log error: {e}", flush=True)

        backtest.record_signal(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
            grade=grade,
            score=sideways_score,
            rr=rr,
            strategy="SIDEWAYS",
            local_regime=local_regime,
            btc_regime=btc_regime
        )

        return {"symbol": symbol, "result": "signal"}

    except Exception:
        print(
            f"{symbol} ERROR",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

        set_scan_result(symbol, {"status": "Error", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": time.time()})
        return {"symbol": symbol, "result": "error"}


# =========================
# TELEGRAM POLLING
# =========================

def telegram_polling():

    while True:

        try:

            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60
            )

        except Exception:

            print(
                "Telegram polling error",
                flush=True
            )

            print(
                traceback.format_exc(),
                flush=True
            )

            time.sleep(10)


# =========================
# HEARTBEAT THREAD
# =========================

def heartbeat_thread():

    while True:

        try:

            time.sleep(HEARTBEAT_INTERVAL)

            uptime_seconds = int(
                time.time() - BOT_START_TIME
            )

            uptime_hours = uptime_seconds // 3600
            uptime_minutes = (uptime_seconds % 3600) // 60

            uptime_str = (
                f"{uptime_hours}h {uptime_minutes}m"
            )

            with state_lock:
                active_count = len(
                    [
                        t
                        for t in active_trades.values()
                        if t.get("status")
                        in ["PENDING", "OPEN"]
                    ]
                )

            auto_trade_status = (
                "ON" if AUTO_TRADE else "OFF"
            )

            current_time = time.strftime(
                "%Y-%m-%d %H:%M:%S UTC",
                time.gmtime()
            )

            market_mode_text = MARKET_MODE

            current_regime_text = CURRENT_REGIME

            # Also show control mode in heartbeat
            control_mode_text = CONTROL_MODE

            message = f"""
💓 HEARTBEAT

Status: ONLINE
Uptime: {uptime_str}

📊 BOT STATUS

Active Trades: {active_count}
Coins: {len(symbols)}
Auto Trade: {auto_trade_status}

📈 MARKET

Market Mode: {market_mode_text}
Market Regime: {current_regime_text}
Control Mode: {control_mode_text}

📋 SCAN STATS

Signals Generated: {scan_counters['Signal Generated']}
Cooldown Rejects: {scan_counters['Cooldown']}
Low Score Rejects: {scan_counters['Score Below MIN_SCORE']}

Time: {current_time}
"""

            send_telegram(message)

        except Exception:

            print(
                "Heartbeat error",
                flush=True
            )

            print(
                traceback.format_exc(),
                flush=True
            )


# =========================
# HOURLY STATS UPDATE
# =========================

def hourly_stats_update():
    """Update stats to Google Sheets every 60 minutes."""
    while True:
        try:
            time.sleep(3600)  # 60 minutes
            
            # Calculate stats
            with state_lock:
                active_count = len([
                    t for t in active_trades.values()
                    if t.get("status") in ["PENDING", "OPEN"]
                ])
            
            # Calculate win rate
            total_trades = scan_counters.get("Wins", 0) + scan_counters.get("Losses", 0)
            win_rate = (scan_counters.get("Wins", 0) / total_trades * 100) if total_trades > 0 else 0
            
            # Get balance (placeholder - would need exchange API call)
            balance = 0
            try:
                balance = exchange.get_balance() if 'exchange' in dir() else 0
            except:
                balance = 0
            
            # Update Google Sheets
            google_sheet.update_stats(
                balance=balance,
                open_positions=active_count,
                wins=scan_counters.get("Wins", 0),
                losses=scan_counters.get("Losses", 0),
                win_rate=round(win_rate, 2),
                profit_usdt=0,  # Would need to calculate from trades
                current_loss_streak=current_loss_streak
            )
            
            print("[HOURLY_STATS] Updated Google Sheets", flush=True)
            
        except Exception as e:
            print(f"[HOURLY_STATS] Error: {e}", flush=True)
            traceback.print_exc()


# =========================
# MAIN
# =========================

def main():
    global CURRENT_REGIME, LAST_REGIME, LAST_REGIME_CHECK
    global MARKET_MODE, CONTROL_MODE
    global ignore_cooldown_once

    # =========================
    # LOAD CONFIG
    # =========================
    load_config()

    # Feature 7: Load regime storage before anything else
    load_regime_storage()

    # Apply control mode from loaded storage
    CONTROL_MODE = CONTROL_MODE  # Already set by load_regime_storage

    threading.Thread(
        target=telegram_polling,
        daemon=True
    ).start()

    # =========================
    # STARTUP
    # =========================

    # Load persistent state to avoid Amnesia
    load_active_trades()

    threading.Thread(
        target=save_state_loop,
        daemon=True
    ).start()

    # snapshot trades before restore เพื่อ reconcile ที่ปิดระหว่าง downtime
    with state_lock:
        pre_restart_snapshot = dict(active_trades)

    trade_manager.restore_open_positions()

    # Reconcile trades ที่ปิดระหว่าง bot downtime (Bug Fix: restart bug)
    trade_manager.reconcile_closed_trades_on_restart(pre_restart_snapshot)

    # Perform startup cleanup: cancel stale pending limit orders and report open positions
    startup_cleanup()

    threading.Thread(
        target=trade_manager.check_trades,
        daemon=True
    ).start()

    # =========================
    # HEARTBEAT
    # =========================

    threading.Thread(
        target=heartbeat_thread,
        daemon=True
    ).start()

    # =========================
    # HOURLY STATS UPDATE
    # =========================

    threading.Thread(
        target=hourly_stats_update,
        daemon=True
    ).start()

    # =========================
    # FEATURE 1: STARTUP MARKET SCAN
    # =========================
    # Flow: load_regime_storage() → startup_market_scan() → immediate_full_rescan() → send_top_candidates() → bot_ready()
    # startup_market_scan() handles: detect regime, set mode, save storage, rescan, top candidates, bot ready
    
    startup_market_scan()

    with state_lock:
        restored_trades = len(active_trades)

    auto_status = "ON" if AUTO_TRADE else "OFF"
    startup_time = time.strftime(
        "%Y-%m-%d %H:%M:%S UTC",
        time.gmtime()
    )

    market_mode_text = MARKET_MODE

    send_telegram(
        f"🚀 STARTUP REPORT\n\n"
        f"Status: STARTED\n"
        f"Time: {startup_time}\n"
        f"Coins: {len(symbols)}\n"
        f"Active Trades Restored: {restored_trades}\n"
        f"Auto Trade: {auto_status}\n"
        f"Market Mode: {market_mode_text}\n"
        f"Market Regime: {CURRENT_REGIME}"
    )

    # =========================
    # MAIN LOOP
    # =========================
    
    # Register graceful shutdown handler
    import atexit
    atexit.register(google_sheet.shutdown_all)
    
    while True:

        try:

            print(
                "Bot alive - scanning market...",
                flush=True
            )

            # =========================
            # MARKET REGIME CHECK (Feature 2: Auto Switch)
            # =========================

            now = time.time()
            if now - LAST_REGIME_CHECK >= REGIME_CHECK_INTERVAL:
                LAST_REGIME_CHECK = now
                new_regime, btc_adx, btc_atr_pct = detect_market_regime()
                
                if CURRENT_REGIME == "UNKNOWN":
                    # First check — silent initialisation
                    CURRENT_REGIME = new_regime
                    # Set mode based on regime
                    MARKET_MODE = determine_mode_from_regime(new_regime)
                    save_regime_storage()
                    
                elif new_regime != CURRENT_REGIME:
                    print("[REGIME_CHANGE] Market regime changed", flush=True)
                    print(f"[REGIME_CHANGE] {CURRENT_REGIME} → {new_regime}", flush=True)
                    
                    # Feature 2: Auto regime switching
                    # auto_switch_regime() handles: mode switch, cache reset, cooldown bypass,
                    # immediate_full_rescan(), cooldown bypass reset, send_top_candidates(), notification
                    auto_switch_regime(CURRENT_REGIME, new_regime, btc_adx, btc_atr_pct)

            # =========================
            # NORMAL SCAN CYCLE
            # =========================
            
            reset_cycle_counters()

            # SCALPING mode uses dedicated symbol list; other modes use main list
            scan_symbols = SCALPING_SYMBOLS if MARKET_MODE == "SCALPING" else symbols

            for symbol in scan_symbols:

                analyze(symbol)

                time.sleep(2)

            # BACKTEST: ตรวจสอบ signals ที่ครบ 4h แล้ว
            backtest.check_pending()

            # Reset candidate_signals at the end of each normal scan cycle
            # (not after regime-change rescans, which keep them for top candidates)
            candidate_signals.clear()

            # Adaptive scan interval: scalping scans every 60s, others every 300s
            current_scan_interval = STRATEGY_CONFIG.get(MARKET_MODE, {}).get('SCAN_INTERVAL', 300)

            print(
                f"Sleep {current_scan_interval}s ({MARKET_MODE} mode)...",
                flush=True
            )

            time.sleep(
                current_scan_interval
            )

        except Exception:

            print(
                "MAIN LOOP ERROR",
                flush=True
            )

            print(
                traceback.format_exc(),
                flush=True
            )

            time.sleep(30)


if __name__ == "__main__":
    main()