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
    SCAN_INTERVAL,
    COOLDOWN,
    LEVERAGE,
    MARGIN_PER_TRADE,
    ADX_FILTER,
    MIN_SCORE,
    ATR_FILTER,
    symbols,
    AUTO_TRADE,
    AUTO_TRADE_MIN_GRADE,
    MAX_LONG_TRADES,
    MAX_SHORT_TRADES,
    MAX_ACTIVE_TRADES,
    GRADE_PRIORITY,
    TREND_MIN_ADX,
    TREND_MIN_ATR,
    TREND_HIGH_VOLUME_ONLY,
    SIDEWAYS_MAX_ADX,
    SIDEWAYS_MIN_ATR,
    SIDEWAYS_HIGH_VOLUME_ONLY,
    HEARTBEAT_INTERVAL,
    MARKET_REGIME_ADX_TRENDING,
    MARKET_REGIME_ADX_SIDEWAYS,
    MARKET_REGIME_ATR_VOLATILE,
    MODE,
    SIGNAL_COOLDOWN,
    TOP_CANDIDATES_COUNT,
    MAX_CONSECUTIVE_LOSSES,
    LOSS_STREAK_RESET_ON_WIN,
    PULLBACK_MIN_DISTANCE_PCT,
    MOMENTUM_MIN_ADX,
    MOMENTUM_MIN_PRICE_DISTANCE,
    MOMENTUM_MIN_CANDLES,
    MOMENTUM_ENTRY_ATR_MULT,
    MOMENTUM_SL_ATR_MULT,
    MOMENTUM_TP_RR,
    MOMENTUM_AUTO_TRADE,
    MOMENTUM_MIN_GRADE,
    MOMENTUM_MIN_SCORE,
    MOMENTUM_MAX_TRADES,
    ALLOW_PENDING_OVERRIDE,
    MIN_SCORE_GAP_TO_OVERRIDE,
    ADX_CEILING_LIMIT,
    STRETCH_MAX_DISTANCE_PCT,
)
import config

# =========================
# INDICATORS - Import from indicators.py
# =========================

from indicators import get_dataframe, get_btc_trend, detect_momentum

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


def calculate_sideways_levels(entry, atr, bb_mid, side):
    """Calculate SL and TP for sideways mean reversion trades.

    SL: ATR * 1.2
    TP: Bollinger Middle Band
    """
    if side == "LONG":
        sl = round(entry - atr * 1.2, 4)
        tp = round(bb_mid, 4)
        risk = entry - sl
        reward = tp - entry
    else:
        sl = round(entry + atr * 1.2, 4)
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
        
        # Send regime info
        send_telegram(
            f"Regime: {startup_regime}\n"
            f"BTC ADX: {btc_adx}\n"
            f"BTC ATR: {btc_atr_pct}%"
        )
        
        print(
            f"[STARTUP_SCAN] Detected regime: {startup_regime}, "
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
    """
    if regime == "SIDEWAYS":
        return "SIDEWAYS"
    if regime == "MOMENTUM":
        return "MOMENTUM"
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
            "TREND_MIN_ADX": TREND_MIN_ADX,
            "TREND_MIN_ATR": TREND_MIN_ATR,
            "TREND_HIGH_VOLUME_ONLY": TREND_HIGH_VOLUME_ONLY,
            "SIDEWAYS_MAX_ADX": SIDEWAYS_MAX_ADX,
            "SIDEWAYS_MIN_ATR": SIDEWAYS_MIN_ATR,
            "SIDEWAYS_HIGH_VOLUME_ONLY": SIDEWAYS_HIGH_VOLUME_ONLY,
        }
        with open('config.json', 'w') as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}", flush=True)

def load_config():
    """Load strategy filter configuration from config.json"""
    global TREND_MIN_ADX, TREND_MIN_ATR, TREND_HIGH_VOLUME_ONLY
    global SIDEWAYS_MAX_ADX, SIDEWAYS_MIN_ATR, SIDEWAYS_HIGH_VOLUME_ONLY
    
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r') as f:
                config_data = json.load(f)
                
            TREND_MIN_ADX = config_data.get('TREND_MIN_ADX', TREND_MIN_ADX)
            TREND_MIN_ATR = config_data.get('TREND_MIN_ATR', TREND_MIN_ATR)
            TREND_HIGH_VOLUME_ONLY = config_data.get('TREND_HIGH_VOLUME_ONLY', TREND_HIGH_VOLUME_ONLY)
            SIDEWAYS_MAX_ADX = config_data.get('SIDEWAYS_MAX_ADX', SIDEWAYS_MAX_ADX)
            SIDEWAYS_MIN_ATR = config_data.get('SIDEWAYS_MIN_ATR', SIDEWAYS_MIN_ATR)
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
    side
):
    # Dynamic volatility SL: minimum 2.0% distance or 2.5 ATR (was 1.5%/2.0)
    sl_dist = max(atr * 2.5, entry * 0.020)

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
            entry + (risk * 2),
            4
        )
        rr = round(
            (tp2 - entry)
            /
            (entry - sl),
            2
        )
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
        entry - (risk * 2),
        4
    )
    rr = round(
        (entry - tp2)
        /
        (sl - entry),
        2
    )
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
    btc_trend
):
    icon = "🚀" if side == "LONG" else "🔻"
    return f"""
{icon} {side} SIGNAL

{symbol}

Strategy:
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

def passes_grade_filter(grade):
    """Check if grade meets minimum auto trade grade."""
    if grade not in GRADE_PRIORITY:
        return False
    return GRADE_PRIORITY[grade] >= GRADE_PRIORITY[AUTO_TRADE_MIN_GRADE]

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
    
    if atr_percent < TREND_MIN_ATR:
        return False, "ATR too low"
    
    if adx < TREND_MIN_ADX:
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
    
    if atr_percent < SIDEWAYS_MIN_ATR:
        return False, "ATR too low"
    
    if adx > SIDEWAYS_MAX_ADX:
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
                "grade": trade.get("grade", "C"),
                "score": trade.get("score", 0)
            }

    return None


# execute_trade moved to bingx_client.py

# =========================
# MARKET REGIME DETECTION
# =========================

def detect_market_regime():
    """Detect current BTC market regime: MOMENTUM, TRENDING, SIDEWAYS, or VOLATILE.

    Priority: MOMENTUM > VOLATILE > TRENDING > SIDEWAYS

    Returns:
        (regime, btc_adx, btc_atr_percent) tuple
    """
    try:
        df_15m = get_dataframe('BTC/USDT:USDT', '15m')
        btc = df_15m.iloc[-2]

        btc_adx = round(btc['adx'], 2)
        btc_atr_percent = round((btc['atr'] / btc['close']) * 100, 2)

        # MOMENTUM: strongest trend, price far from EMA7, consecutive candles
        momentum_info = detect_momentum('BTC/USDT:USDT')
        if momentum_info['is_momentum']:
            return "MOMENTUM", btc_adx, btc_atr_percent

        # VOLATILE has next priority
        if btc_atr_percent >= MARKET_REGIME_ATR_VOLATILE:
            return "VOLATILE", btc_adx, btc_atr_percent

        if btc_adx >= MARKET_REGIME_ADX_TRENDING:
            return "TRENDING", btc_adx, btc_atr_percent

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
    """Route to the correct analysis strategy based on MARKET_MODE.
    
    Feature 8: Respect CONTROL_MODE override.
    - If FORCE_TREND: always use trend analysis
    - If FORCE_SIDEWAY: always use sideways analysis
    - If AUTO: use MARKET_MODE (which follows detected regime)
    
    Args:
        symbol: Trading symbol to analyze.
        bypass_cooldown: If True, ignore cooldown timers.
        silent_mode: If True, do not send Telegram signals (for rescans).
        signal_only: If True, do not execute auto trades (for startup rescans).
    """
    # Determine effective mode
    effective_mode = MARKET_MODE
    if CONTROL_MODE == "FORCE_TREND":
        effective_mode = "TRENDING"
    elif CONTROL_MODE == "FORCE_SIDEWAY":
        effective_mode = "SIDEWAYS"

    if effective_mode == "SIDEWAYS":
        return analyze_sideways(symbol, bypass_cooldown=bypass_cooldown, silent_mode=silent_mode, signal_only=signal_only)
    if effective_mode == "MOMENTUM":
        return analyze_momentum(symbol, bypass_cooldown=bypass_cooldown, silent_mode=silent_mode, signal_only=signal_only)
    return analyze_trend(symbol, bypass_cooldown=bypass_cooldown, silent_mode=silent_mode, signal_only=signal_only)


# =========================
# ANALYZE MOMENTUM
# =========================

def analyze_momentum(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False):
    """Momentum regime analysis — entry near current price, no pullback wait."""

    global pause_trading

    if pause_trading:
        return {"symbol": symbol, "result": "paused"}

    try:
        now = time.time()

        # =========================
        # COOLDOWN
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "MOMENTUM"))
            if last_time and now - last_time < COOLDOWN:
                set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                google_sheet.log_debug(symbol, "Cooldown", score=0, adx=0, atr=0)
                return {"symbol": symbol, "result": "skipped"}

        # =========================
        # GET DATA
        # =========================

        df_4h = get_dataframe(symbol, '4h')
        df_1h = get_dataframe(symbol, '1h')
        df_15m = get_dataframe(symbol, '15m')

        h4  = df_4h.iloc[-2]
        h1  = df_1h.iloc[-2]
        m15 = df_15m.iloc[-2]

        now_ts = time.time()
        signal_id = str(uuid.uuid4())[:8]

        atr_percent = (m15['atr'] / m15['close']) * 100
        volume_high = m15['volume'] > m15['vol_avg'] * 1.3
        vol_status  = "HIGH" if volume_high else "NORMAL"
        adx_val     = round(m15['adx'], 2)
        atr_val     = round(atr_percent, 2)

        # =========================
        # SCORE
        # =========================

        long_score  = 0
        short_score = 0
        btc_trend   = get_btc_trend()

        # 4H EMA alignment (35pts)
        if h4['ema25'] > h4['ema99']:
            long_score += 35
        else:
            short_score += 35

        # 1H EMA alignment (25pts)
        if h1['ema7'] > h1['ema25']:
            long_score += 25
        else:
            short_score += 25

        # MACD 1H (20pts)
        if h1['macd'] > h1['macd_signal'] and h1['macd'] > 0:
            long_score += 20
        elif h1['macd'] < h1['macd_signal'] and h1['macd'] < 0:
            short_score += 20

        # ADX strength (10pts)
        if m15['adx'] >= MOMENTUM_MIN_ADX:
            if h1['ema7'] > h1['ema25']:
                long_score += 10
            else:
                short_score += 10

        # Volume confirmation (10pts)
        if volume_high:
            long_score  += 10
            short_score += 10

        # BTC filter
        if symbol != 'BTC/USDT:USDT' and btc_trend == "bearish":
            long_score -= 20

        long_score  = min(long_score, 100)
        short_score = min(short_score, 100)

        # =========================
        # GRADE
        # =========================

        score = max(long_score, short_score)
        grade = "C"
        if score >= 95:
            grade = "A+"
        elif score >= 85:
            grade = "A"
        elif score >= 75:
            grade = "B"

        # =========================
        # SCORE FILTER
        # =========================

        if score < MOMENTUM_MIN_SCORE:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            google_sheet.log_debug(symbol, "Score Below MIN_SCORE", score=score, adx=adx_val, atr=atr_val)
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # DETERMINE SIDE
        # =========================

        rsi_val = m15['rsi']
        if long_score >= short_score and long_score >= MOMENTUM_MIN_SCORE and btc_trend == "bullish":
            if rsi_val > config.MOMENTUM_LONG_MAX_RSI:
                set_scan_result(symbol, {"status": "RSI Too High", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, f"RSI Too High ({round(rsi_val, 2)} > {config.MOMENTUM_LONG_MAX_RSI})", score=score, adx=adx_val, atr=atr_val)
                return {"symbol": symbol, "result": "skipped"}
            side  = "LONG"
            entry = round(m15['close'] - (m15['atr'] * MOMENTUM_ENTRY_ATR_MULT), 4)
        elif short_score > long_score and short_score >= MOMENTUM_MIN_SCORE:
            if rsi_val < config.MOMENTUM_SHORT_MIN_RSI:
                set_scan_result(symbol, {"status": "RSI Too Low", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
                google_sheet.log_debug(symbol, f"RSI Too Low ({round(rsi_val, 2)} < {config.MOMENTUM_SHORT_MIN_RSI})", score=score, adx=adx_val, atr=atr_val)
                return {"symbol": symbol, "result": "skipped"}
            side  = "SHORT"
            entry = round(m15['close'] + (m15['atr'] * MOMENTUM_ENTRY_ATR_MULT), 4)
        else:
            set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": adx_val, "atr": atr_val, "volume": vol_status, "timestamp": now_ts})
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # SL / TP
        # =========================

        atr = m15['atr']
        if side == "LONG":
            sl   = round(entry - atr * MOMENTUM_SL_ATR_MULT, 4)
            risk = entry - sl
            tp2  = round(entry + risk * MOMENTUM_TP_RR, 4)
            tp1  = round(entry + risk, 4)
            rr   = round((tp2 - entry) / (entry - sl), 2)
        else:
            sl   = round(entry + atr * MOMENTUM_SL_ATR_MULT, 4)
            risk = sl - entry
            tp2  = round(entry - risk * MOMENTUM_TP_RR, 4)
            tp1  = round(entry - risk, 4)
            rr   = round((entry - tp2) / (sl - entry), 2)

        # =========================
        # DISTANCE LOG
        # =========================

        current_price = m15['close']
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
{round(m15['adx'], 2)}

ATR %:
{round(atr_percent, 2)}

Volume:
{vol_status}

BTC Trend:
{btc_trend}

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
                    "created_at": time.time()
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

        # =========================
        # AUTO TRADE
        # =========================

        if MOMENTUM_AUTO_TRADE and not signal_only:

            skip_reason = None

            # Grade must be A+
            if grade != MOMENTUM_MIN_GRADE:
                skip_reason = f"Momentum Grade: {grade} < {MOMENTUM_MIN_GRADE}"

            # Max 1 active position for momentum
            if not skip_reason:
                with state_lock:
                    active_count = sum(
                        1 for t in active_trades.values()
                        if t.get("status") in ["PENDING", "OPEN"]
                    )
                if active_count >= MOMENTUM_MAX_TRADES:
                    skip_reason = f"Momentum max {MOMENTUM_MAX_TRADES} position reached"

            # Regime still valid
            if not skip_reason and CURRENT_REGIME != signal_regime:
                skip_reason = "MARKET_REGIME_CHANGED"

            if not skip_reason:
                print(f"[MOMENTUM_AUTO_TRADE] {symbol} {side} — executing", flush=True)
                try:
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
        google_sheet.log_debug(symbol, "Error", score=0, adx=0, atr=0)
        return {"symbol": symbol, "result": "error"}


# =========================
# ANALYZE TREND
# =========================

def analyze_trend(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False):

    # =========================
    # PAUSE TRADING CHECK
    # =========================
    
    global pause_trading

    if pause_trading:
        return {"symbol": symbol, "result": "paused"}

    try:

        now = time.time()

        # =========================
        # COOLDOWN (Feature 4: Bypass if ignore_cooldown_once is set)
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "TREND"))

            if last_time and now - last_time < COOLDOWN:
                set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                # Google Sheets debug logging
                google_sheet.log_debug(symbol, "Cooldown", score=0, adx=0, atr=0)
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

        df_1h = get_dataframe(
            symbol,
            '1h'
        )

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
            # Google Sheets debug logging
            google_sheet.log_debug(symbol, "Candle Too Big", score=0, adx=adx_val, atr=atr_val)
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
            google_sheet.log_debug(symbol, "Sideways Market", score=0, adx=adx_val, atr=atr_val)
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # ADX CEILING & STRETCH LIMIT FILTERS
        # Prevent chasing overextended trends and buying at absolute top
        # =========================

        adx_val = round(m15['adx'], 2)
        current_price = m15['close']
        entry_ema = m15['ema25']  # Entry EMA reference

        # ADX Ceiling Check - apply penalty if ADX is overextended
        adx_ceiling_penalty = False
        if adx_val > ADX_CEILING_LIMIT:
            print(
                f"[SKIP] ADX is overextended ({adx_val:.2f} > {ADX_CEILING_LIMIT}). Applying -50 penalty.",
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
        # SCORE
        # =========================

        long_score = 0

        short_score = 0

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
        # DAILY TREND (ลด weight เพราะ lagging มาก)
        # =========================

        if d1['ema25'] > d1['ema99']:

            long_score += 5   # เดิม 10

        else:

            short_score += 5  # เดิม 10

        # =========================
        # 4H TREND (ลด weight เพราะ lagging)
        # =========================

        if h4['ema25'] > h4['ema99']:

            long_score += 15  # เดิม 25

        else:

            short_score += 15  # เดิม 25

        # =========================
        # 1H EMA
        # =========================

        if h1['ema7'] > h1['ema25']:

            long_score += 20

        else:

            short_score += 20

        # =========================
        # MACD MOMENTUM
        # =========================

        if (
            h1['macd'] > h1['macd_signal']
            and h1['macd'] > 0
        ):

            long_score += 15

        elif (
            h1['macd'] < h1['macd_signal']
            and h1['macd'] < 0
        ):

            short_score += 15

        # =========================
        # ANTI-FOMO SCORING ENGINE
        # หลักการ: ยิ่งวิ่งไปไกล ยิ่งต้องเกรดตก
        # Golden Zone = momentum เพิ่งเริ่ม (ต้นน้ำ)
        # Danger Zone = momentum extended ไปแล้ว (ปลายน้ำ/ดอย)
        # =========================
        rsi_val = m15['rsi']

        # คำนวณระยะห่างของราคากับเส้นฐาน EMA25
        long_stretch_pct  = ((m15['close'] - m15['ema25']) / m15['ema25']) * 100
        short_stretch_pct = ((m15['ema25'] - m15['close']) / m15['ema25']) * 100

        if CURRENT_REGIME == "TRENDING":

            # --- TREND LONG ---
            if m15['close'] > m15['ema25']:
                if rsi_val > 70:
                    long_score -= 40   # ดอยจัด ไล่ราคาสูงมาก
                elif 65 <= rsi_val <= 70:
                    long_score -= 20   # เริ่มตึง
                elif 53 <= rsi_val < 65:
                    long_score += 5    # Neutral/high
                elif 42 <= rsi_val <= 52:
                    long_score += 20   # GOLDEN ZONE - pullback สวยงามในแนวโน้มขาขึ้น
                elif 35 <= rsi_val <= 41:
                    long_score += 10   # PRE-GOLDEN - pullback ลึก

                # Price stretch: ใช้ 3.5% แทน 2.5% เพราะ crypto volatile สูง
                if long_stretch_pct > 3.5:
                    long_score -= 25   # บินหนีเส้นฐานมากเกินไป
                elif long_stretch_pct <= 1.2:
                    long_score += 10   # เกาะเส้นฐานสวย ปลอดภัย

            # --- TREND SHORT ---
            if m15['close'] < m15['ema25']:
                if rsi_val < 40:
                    short_score -= 40  # ต่ำเกินไป เสี่ยงดีดกลับ (Squeeze)
                elif 40 <= rsi_val < 45:
                    short_score -= 20  # ค่อนข้างต่ำ เสี่ยงสะสม
                elif 45 <= rsi_val < 48:
                    short_score += 5    # Neutral/low
                elif 48 <= rsi_val <= 58:
                    short_score += 20  # GOLDEN ZONE - pullback ดีดกลับมาที่แนวต้านกลางของขาลง
                elif 59 <= rsi_val <= 65:
                    short_score += 10  # PRE-GOLDEN - pullback สูงขึ้น ปลอดภัยขึ้น

                if short_stretch_pct > 3.5:
                    short_score -= 25  # ดิ่งลึกเกินไป เสี่ยงดีดกลับ
                elif short_stretch_pct <= 1.2:
                    short_score += 10  # เพิ่งหลุดเส้นฐาน ปลอดภัย

        elif CURRENT_REGIME == "SIDEWAYS":

            # --- SIDEWAYS LONG (ซื้อขอบล่างกรอบ) ---
            if 40 <= rsi_val <= 49:
                long_score += 25       # GOLDEN ZONE ช้อนซื้อแนวรับล่าง
            elif rsi_val >= 58:
                long_score -= 30       # อันตราย ราคาจ่อขอบบนแล้ว

            if long_stretch_pct > 1.5:
                long_score -= 20       # ดีดห่างเส้นฐานในไซด์เวย์ = ใกล้จบรอบ

            # --- SIDEWAYS SHORT (ขายขอบบนกรอบ) ---
            if 51 <= rsi_val <= 60:
                short_score += 25      # GOLDEN ZONE เปิด SHORT ที่แนวต้านบน
            elif rsi_val <= 42:
                short_score -= 30      # อันตราย ราคาจ่อแนวรับล่างแล้ว

            if short_stretch_pct > 1.5:
                short_score -= 20      # ลงลึกเกินในไซด์เวย์ เสี่ยงดีดกลับ

        # =========================
        # ADX FILTER
        # =========================

        if m15['adx'] > ADX_FILTER:
            
            if h1['ema7'] > h1['ema25']:

                long_score += 10

            else:

                short_score += 10

        # =========================
        # ATR VOLATILITY FILTER
        # =========================

        atr_percent = (
            m15['atr']
            /
            m15['close']
        ) * 100

        if atr_percent > ATR_FILTER:

            if h1['ema7'] > h1['ema25']:

                long_score += 10

            else:

                short_score += 10
        
        # =========================
        # CANDLE PA, VOLUME TRAP, AND BTC REGIME
        # =========================
        is_green = m15['close'] > m15['open']
        is_red = m15['close'] < m15['open']

        volume_high = (
            m15['volume']
            >
            m15['vol_avg'] * 1.3
        )

        # BTC Regime Filter (reduced from -25 — was too dominant)
        if btc_trend == "bullish":
            short_score -= 15
        elif btc_trend == "bearish":
            long_score -= 15

        # PA & Volume Logic (reduced penalties — single candle shouldn't dominate)
        if is_green:
            short_score -= 15  # was -30
            if volume_high:
                short_score -= 20  # was -40
            
            if h1['ema7'] > h1['ema25']:
                # Aligns with LONG
                if volume_high:
                    long_score += 15
        
        if is_red:
            long_score -= 15   # was -30
            if volume_high:
                long_score -= 20   # was -40
                
            if h1['ema7'] <= h1['ema25']:
                # Aligns with SHORT
                if volume_high:
                    short_score += 15

        # =========================
        # BOLLINGER FILTER
        # =========================

        upper_distance = (
            m15['close']
            /
            m15['bb_upper']
        )

        lower_distance = (
            m15['close']
            /
            m15['bb_lower']
        )

        # LONG FILTER
        if (
            m15['close'] > m15['bb_mid']
            and upper_distance < 0.998
        ):

            long_score += 10

        # SHORT FILTER
        elif (
            m15['close'] < m15['bb_mid']
            and lower_distance > 1.002
        ):

            short_score += 10

        # =========================
        # SUPPORT / RESISTANCE FILTER
        # =========================

        recent_low = min(
            df_15m['low'].tail(20)
        )

        recent_high = max(
            df_15m['high'].tail(20)
        )

        distance_to_low = (
            m15['close'] - recent_low
        ) / m15['close']

        distance_to_high = (
            recent_high - m15['close']
        ) / m15['close']

        # SHORT ใกล้ low มากไป
        if distance_to_low < 0.003:

            short_score -= 15

        # LONG ใกล้ high มากไป
        if distance_to_high < 0.003:

            long_score -= 15

        # =========================
        # EMA99 FILTER
        # =========================

        distance_ema99 = abs(
            m15['close'] - m15['ema99']
        )

        if distance_ema99 < m15['atr'] * 0.3:
            print(
                f"{symbol} skipped - too close EMA99",
                flush=True
            )

            score = max(long_score, short_score)
            vol_status = "HIGH" if volume_high else "NORMAL"
            set_scan_result(symbol, {"status": "Too Close EMA99", "score": score, "adx": round(m15['adx'], 2), "atr": round(atr_percent, 2), "volume": vol_status, "timestamp": now})
            # Track rejected signal (Feature 3)
            rejected_signals.add(symbol)
            # Google Sheets debug logging
            google_sheet.log_debug(symbol, "Too Close EMA99", score=score, adx=round(m15['adx'], 2), atr=round(atr_percent, 2))
            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # BTC FILTER
        # =========================
        # bullish  → หัก short_score 20 (ไม่ควร short ตอน BTC ขึ้น)
        # bearish  → หัก long_score  20 (ไม่ควร long ตอน BTC ลง)
        # neutral  → หัก ทั้ง long และ short 20 (market reversal zone — strict penalty)
        if symbol != 'BTC/USDT:USDT':

            if btc_trend == "bullish":
                # BTC bullish: penalize SHORT only (reduced from -20)
                short_score -= 10

            elif btc_trend == "bearish":
                # BTC bearish: penalize LONG only (reduced from -20)
                long_score -= 10

            elif btc_trend == "neutral":
                # BTC neutral: market reversal / conflicting signals
                # Reduced from -20 to -10 — neutral shouldn't block both sides
                print(
                    "BTC Trend is neutral. Applying -10 score penalty.",
                    flush=True
                )
                long_score  -= 10
                short_score -= 10

        # =========================
        # LIMIT SCORE
        # =========================

        long_score = min(
            long_score,
            100
        )

        short_score = min(
            short_score,
            100
        )

        # =========================
        # GRADE
        # =========================

        grade = "C"

        if (
            long_score >= 95
            or short_score >= 95
        ):

            grade = "A+"

        elif (
            long_score >= 85
            or short_score >= 85
        ):

            grade = "A"

        elif (
            long_score >= 75
            or short_score >= 75
        ):

            grade = "B"

        # =========================
        # PULLBACK ENTRY
        # =========================

        # =========================
        # ADAPTIVE ENTRY
        # คำนวณ entry 2 แบบแล้วเลือกแบบที่ใกล้ current price มากกว่า
        # เพื่อเพิ่ม fill rate โดยยังรอ pullback นิดนึง
        # =========================

        # =========================
        # ADAPTIVE ENTRY
        # LONG:  entry ต้องต่ำกว่า current price = รอราคาลงมา (pullback)
        # SHORT: entry ต้องสูงกว่า current price = รอราคาขึ้นมา (pullback)
        # =========================

        # Deep entry — รอ pullback มาที่ ema7 (reduced from ATR*0.3 to ATR*0.15)
        long_pullback_deep   = round(m15['ema7'] - (m15['atr'] * 0.15), 4)
        short_pullback_deep  = round(m15['ema7'] + (m15['atr'] * 0.15), 4)

        # Shallow entry — ใกล้ราคาปัจจุบันมากขึ้น (ATR*0.05 instead of 0.1)
        long_pullback_shallow  = round(m15['close'] - (m15['atr'] * 0.05), 4)
        short_pullback_shallow = round(m15['close'] + (m15['atr'] * 0.05), 4)

        # LONG: เลือก entry ที่สูงกว่า (ใกล้ close มากกว่า) เพื่อเพิ่ม fill rate
        long_pullback = max(long_pullback_deep, long_pullback_shallow)

        # SHORT: เลือก entry ที่ต่ำกว่า (ใกล้ close มากกว่า) เพื่อเพิ่ม fill rate
        short_pullback = min(short_pullback_deep, short_pullback_shallow)

        # =========================
        # LONG SIGNAL
        # =========================

        if (
            long_score >= MIN_SCORE
            and btc_trend == "bullish"
            and m15['rsi'] <= config.TREND_LONG_MAX_RSI
        ):

            entry = round(
                long_pullback,
                4
            )

            atr = m15['atr']
            sl, tp1, tp2, rr = calculate_trade_levels(
                entry,
                atr,
                "LONG"
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
                btc_trend=btc_trend
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
                    "score": long_score
                }
                # Update last_alert after storing the signal
                last_alert[(symbol, "TREND")] = now

            # =========================
            # AUTO TRADE LOGIC
            # =========================

            if AUTO_TRADE and not signal_only:
                
                skip_reason = None
                
                # Check grade filter
                if not passes_grade_filter(grade):
                    skip_reason = f"Grade: {grade} < {AUTO_TRADE_MIN_GRADE}"
                
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
                skip_reason=""
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
                    strategy="TREND"
                )

            return {"symbol": symbol, "result": "signal"}
        
        # =========================
        # SHORT SIGNAL
        # =========================

        elif (
            short_score >= MIN_SCORE
            and btc_trend == "bearish"
            and m15['rsi'] >= config.TREND_SHORT_MIN_RSI
        ):

            entry = round(
                short_pullback,
                4
            )

            atr = m15['atr']
            sl, tp1, tp2, rr = calculate_trade_levels(
                entry,
                atr,
                "SHORT"
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
                btc_trend=btc_trend
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
                    "score": short_score
                }
                # Update last_alert after storing the signal
                last_alert[(symbol, "TREND")] = now

            # =========================
            # AUTO TRADE LOGIC
            # =========================

            if AUTO_TRADE and not signal_only:
                
                skip_reason = None
                
                # Check grade filter
                if not passes_grade_filter(grade):
                    skip_reason = f"Grade: {grade} < {AUTO_TRADE_MIN_GRADE}"
                
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
                skip_reason=""
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
                    strategy="TREND"
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
    missing_points = max(MIN_SCORE - score, 0)
    set_scan_result(symbol, {"status": "Score Below MIN_SCORE", "score": score, "adx": round(m15['adx'], 2), "atr": round(atr_percent, 2), "volume": vol_status, "timestamp": now, "long_score": long_score, "short_score": short_score, "missing_points": missing_points})
    # Track rejected signal (Feature 3)
    if score > 0:
        rejected_signals.add(symbol)
    # Google Sheets debug logging
    google_sheet.log_debug(symbol, f"Score Below MIN_SCORE ({missing_points} points needed)", score=score, adx=round(m15['adx'], 2), atr=round(atr_percent, 2))
    return {"symbol": symbol, "result": "skipped"}


# =========================
# ANALYZE SIDEWAYS
# =========================

def build_sideways_message(symbol, grade, score, side, entry, sl, tp, rr, rsi, adx, atr_percent, volume_high):
    icon = "🚀" if side == "LONG" else "🔻"
    return f"""
{icon} {side} SIGNAL
{symbol}

Strategy:
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


def analyze_sideways(symbol, bypass_cooldown=False, silent_mode=False, signal_only=False):

    # =========================
    # PAUSE TRADING CHECK
    # =========================
    
    global pause_trading

    if pause_trading:
        return {"symbol": symbol, "result": "paused"}

    try:

        now = time.time()

        # =========================
        # COOLDOWN (Feature 4: Bypass if ignore_cooldown_once is set)
        # =========================

        if not bypass_cooldown and not ignore_cooldown_once:
            with state_lock:
                last_time = last_alert.get((symbol, "SIDEWAYS"))

                if last_time and now - last_time < COOLDOWN:
                    set_scan_result(symbol, {"status": "Cooldown", "score": 0, "adx": 0, "atr": 0, "volume": "N/A", "timestamp": now})
                    return {"symbol": symbol, "result": "skipped"}

        # =========================
        # GET DATA
        # =========================

        df_15m = get_dataframe(
            symbol,
            '15m'
        )

        m15 = df_15m.iloc[-2]

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

        rsi = m15['rsi']
        close = m15['close']
        bb_lower = m15['bb_lower']
        bb_upper = m15['bb_upper']
        bb_mid = m15['bb_mid']
        adx = m15['adx']
        atr = m15['atr']

        atr_percent = round((atr / close) * 100, 2)
        volume_high = m15['volume'] > m15['vol_avg'] * 1.3
        signal_id = str(uuid.uuid4())[:8]

        # =========================
        # SIDEWAYS CONDITION CHECK
        # =========================

        # =========================
        # TREND FILTER (Bug Fix: ป้องกัน LONG ในตลาดที่ downtrend ชัดเจน)
        # =========================
        ema7 = m15['ema7']
        ema25 = m15['ema25']

        # LONG: RSI < 30, Close <= BB Lower, ADX < 28
        # + ต้องไม่ downtrend ชัด: ema7 ต้องไม่ต่ำกว่า ema25 มากเกิน 1%
        long_trend_ok = ema7 >= ema25 * 0.99  # ยอมให้ต่ำกว่าได้นิดหน่อย แต่ไม่ downtrend ชัด
        long_condition = (
            rsi < 30
            and close <= bb_lower
            and adx < 28
            and long_trend_ok
        )

        # SHORT: RSI > 70, Close >= BB Upper, ADX < 28
        # + ต้องไม่ uptrend ชัด: ema7 ต้องไม่สูงกว่า ema25 มากเกิน 1%
        short_trend_ok = ema7 <= ema25 * 1.01
        short_condition = (
            rsi > 70
            and close >= bb_upper
            and adx < 28
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
            side = "LONG" if abs(30 - rsi) > abs(70 - rsi) else "SHORT"
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
        # SHORT: RSI 65=0pts, 70=30pts, 75+=60pts(max)
        # LONG:  RSI 35=0pts, 30=30pts, 25-=60pts(max)
        # =========================
        if side == "LONG":
            rsi_score = max(0, min(60, int((30 - rsi) * 6)))  # RSI 30→0pts, 20→60pts
        else:
            rsi_score = max(0, min(60, int((rsi - 70) * 6)))  # RSI 70→0pts, 80→60pts

        # RR component: max 40 pts
        rr_score = max(0, min(40, int((rr - 1.0) * 20)))

        sideways_score = rsi_score + rr_score

        # =========================
        # GRADE (ขึ้นกับ score รวม)
        # A+ = RSI extreme มาก + RR ดี (score 70+)
        # A  = RSI overbought/oversold ชัด + RR ดี (score 50+)
        # B  = RSI พอใช้ หรือ RR ดีมาก (score 35+)
        # C  = RSI แค่แตะ threshold
        # =========================
        if sideways_score >= 70:
            grade = "A+"
        elif sideways_score >= 50:
            grade = "A"
        elif sideways_score >= 35:
            grade = "B"
        else:
            grade = "C"

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
            volume_high=volume_high
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
                "grade": grade
            }
            # Update last_alert after storing the signal
            last_alert[(symbol, "SIDEWAYS")] = now

        # =========================
        # AUTO TRADE LOGIC
        # =========================

        if AUTO_TRADE and not signal_only:

            skip_reason = None

            # Check grade filter FIRST (Bug Fix: sideways ไม่เคย check grade filter)
            if not passes_grade_filter(grade):
                skip_reason = f"Grade: {grade} < {AUTO_TRADE_MIN_GRADE}"

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

        # BACKTEST: บันทึก signal สำหรับ evaluate ผล 4h ทีหลัง
        backtest.record_signal(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
            grade=grade,
            score=sideways_score,
            strategy="SIDEWAYS"
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
            for symbol in symbols:

                analyze(symbol)

                time.sleep(2)

            # BACKTEST: ตรวจสอบ signals ที่ครบ 4h แล้ว
            backtest.check_pending()

            # Reset candidate_signals at the end of each normal scan cycle
            # (not after regime-change rescans, which keep them for top candidates)
            candidate_signals.clear()

            print(
                "Sleep 5 minutes...",
                flush=True
            )

            time.sleep(
                SCAN_INTERVAL
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