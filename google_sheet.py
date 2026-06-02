"""
Google Sheets Sync System for Crypto Scanner Bot - IMPROVED VERSION.

This module provides integration with Google Sheets for:
- Storing signals, trades, and debug information with full traceability
- Remote configuration management with auto-reload
- Performance analytics and fill rate analysis
- Health monitoring and diagnostic logging

IMPROVEMENTS:
1. SignalID support for tracking signals end-to-end
2. Batch writes for efficiency
3. Graceful shutdown with final flush to prevent data loss
4. Dashboard sheet for future analytics
5. Enhanced FillAnalysis with ATR/ADX/BTC trend analysis
6. Config auto-reload every 300 seconds
7. Health check logging every 30 minutes
8. Better debug information for rejection analysis
9. Thread-safe operations with proper lock usage
10. Isolated error handling to prevent trading interruptions
"""

import os
import time
import threading
import traceback
import uuid
import json
from datetime import datetime
from collections import defaultdict, deque

import gspread
from google.oauth2.service_account import Credentials

# ==================================================
# CONFIGURATION
# ==================================================

SPREADSHEET_NAME = "Crypto Scanner Dashboard"
FLUSH_INTERVAL = 30  # seconds - buffer flush interval
CONFIG_RELOAD_INTERVAL = 300  # seconds - config auto-reload interval
HEALTH_CHECK_INTERVAL = 1800  # seconds - 30 minutes

# Sheet names
SHEET_SIGNALS = "Signals"
SHEET_TRADES = "Trades"
SHEET_STATS = "Stats"
SHEET_CONFIG = "Config"
SHEET_DEBUG = "Debug"
SHEET_FILL_ANALYSIS = "FillAnalysis"
SHEET_DASHBOARD = "Dashboard"

# ==================================================
# GLOBAL STATE
# ==================================================

_client = None
_spreadsheet = None
_buffer = deque()
_buffer_lock = threading.Lock()
_flush_thread = None
_config_thread = None
_health_thread = None
_stop_flush = threading.Event()
_stop_config = threading.Event()
_stop_health = threading.Event()

# Config caching
_config_cache = {}
_config_lock = threading.Lock()
_last_health_check = time.time()

# ==================================================
# INITIALIZATION
# ==================================================

def get_sheet():
    """
    Get or create Google Sheets client connection.
    Returns the spreadsheet object or None if connection fails.
    """
    global _client, _spreadsheet
    
    if _client is not None and _spreadsheet is not None:
        return _spreadsheet
    
    try:
        credentials_json = os.getenv("GOOGLE_CREDENTIALS")
        if not credentials_json:
            print("[GOOGLE_SHEETS] GOOGLE_CREDENTIALS not found", flush=True)
            return None
        
        # Parse credentials
        import json
        creds_dict = json.loads(credentials_json)
        
        # Set up scopes
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        _client = gspread.service_account_from_dict(creds_dict, scopes=scopes)
        _spreadsheet = _client.open(SPREADSHEET_NAME)
        
        print("[GOOGLE_SHEETS] Connected successfully", flush=True)
        return _spreadsheet
        
    except Exception as e:
        print(f"[GOOGLE_SHEETS] Connection error: {e}", flush=True)
        traceback.print_exc()
        return None


def _ensure_sheets_exist():
    """Create sheets if they don't exist with proper headers."""
    spreadsheet = get_sheet()
    if not spreadsheet:
        return False
    
    try:
        existing_tabs = [sheet.title for sheet in spreadsheet.worksheets()]
        
        sheet_configs = [
            (SHEET_SIGNALS, [
                "SignalID", "Timestamp", "Symbol", "Side", "Grade", "Score", 
                "Entry", "SL", "TP", "ATR", "ADX", "Volume", "BTCTrend", "Status"
            ]),
            (SHEET_TRADES, [
                "Timestamp", "Symbol", "Side", "Entry", "Exit", "PnL", 
                "Result", "Grade", "Score", "RR"
            ]),
            (SHEET_STATS, [
                "Timestamp", "Balance", "OpenPositions", "Wins", "Losses", 
                "WinRate", "ProfitUSDT", "CurrentLossStreak"
            ]),
            (SHEET_CONFIG, ["Key", "Value"]),
            (SHEET_DEBUG, [
                "Timestamp", "Symbol", "Reason", "Score", "ADX", "ATR", "ExtraData"
            ]),
            (SHEET_FILL_ANALYSIS, [
                "Timestamp", "Symbol", "Side", "CurrentPrice", "EntryPrice", 
                "DistancePercent", "Grade", "Score", "ATR", "ADX", "BTCTrend", "FillStatus"
            ]),
            (SHEET_DASHBOARD, ["Metric", "Value"]),
        ]
        
        for sheet_name, headers in sheet_configs:
            if sheet_name not in existing_tabs:
                spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
                worksheet = spreadsheet.worksheet(sheet_name)
                worksheet.append_row(headers)
                print(f"[GOOGLE_SHEETS] Created sheet: {sheet_name}", flush=True)
        
        return True
    except Exception as e:
        print(f"[GOOGLE_SHEETS] Error ensuring sheets: {e}", flush=True)
        return False


# ==================================================
# BUFFER MANAGEMENT
# ==================================================

def _add_to_buffer(sheet_name, row_data):
    """Add a row to the write buffer."""
    with _buffer_lock:
        _buffer.append((sheet_name, row_data))


def _flush_buffer():
    """Flush all buffered writes to Google Sheets using batch writes."""
    spreadsheet = get_sheet()
    if not spreadsheet:
        return
    
    with _buffer_lock:
        if not _buffer:
            return
        items = list(_buffer)
        _buffer.clear()
    
    try:
        # Group by sheet
        sheet_rows = defaultdict(list)
        for sheet_name, row_data in items:
            sheet_rows[sheet_name].append(row_data)
        
        # Write each sheet using batch writes (append_rows)
        for sheet_name, rows in sheet_rows.items():
            if not rows:
                continue
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
                # Use append_rows for batch insert (more efficient than individual appends)
                worksheet.append_rows(rows)
                print(f"[GOOGLE_SHEETS] Flushed {len(rows)} rows to {sheet_name}", flush=True)
            except Exception as e:
                print(f"[GOOGLE_SHEETS] Error writing to {sheet_name}: {e}", flush=True)
                # Re-add items to buffer on failure (don't lose data)
                with _buffer_lock:
                    for row in rows:
                        _buffer.appendleft((sheet_name, row))
                
    except Exception as e:
        print(f"[GOOGLE_SHEETS] Buffer flush error: {e}", flush=True)


def _buffer_flush_loop():
    """Background thread for periodic buffer flushing."""
    while not _stop_flush.is_set():
        _stop_flush.wait(FLUSH_INTERVAL)
        if not _stop_flush.is_set():
            try:
                _flush_buffer()
            except Exception as e:
                print(f"[GOOGLE_SHEETS] Flush loop error: {e}", flush=True)


def _config_reload_loop():
    """Background thread for periodic config reloading."""
    while not _stop_config.is_set():
        _stop_config.wait(CONFIG_RELOAD_INTERVAL)
        if not _stop_config.is_set():
            try:
                load_config()
            except Exception as e:
                print(f"[GOOGLE_SHEETS] Config reload error: {e}", flush=True)


def _health_check_loop():
    """Background thread for periodic health check logging."""
    while not _stop_health.is_set():
        _stop_health.wait(HEALTH_CHECK_INTERVAL)
        if not _stop_health.is_set():
            try:
                _log_health_check()
            except Exception as e:
                print(f"[GOOGLE_SHEETS] Health check error: {e}", flush=True)


def _log_health_check():
    """Log a health check entry every 30 minutes."""
    try:
        with _buffer_lock:
            buffer_size = len(_buffer)
        
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        reason = "HEALTH_CHECK"
        extra_data = f"BufferSize={buffer_size},Connected=True"
        
        row = [timestamp, "", reason, "", "", "", extra_data]
        _add_to_buffer(SHEET_DEBUG, row)
        print(f"[GOOGLE_SHEETS] Health check logged (buffer={buffer_size})", flush=True)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] _log_health_check error: {e}", flush=True)


def start_buffer_flush():
    """Start the background flush thread."""
    global _flush_thread
    if _flush_thread is None:
        _stop_flush.clear()
        _flush_thread = threading.Thread(target=_buffer_flush_loop, daemon=True)
        _flush_thread.start()
        print("[GOOGLE_SHEETS] Buffer flush thread started", flush=True)


def start_config_reload():
    """Start the background config reload thread."""
    global _config_thread
    if _config_thread is None:
        _stop_config.clear()
        _config_thread = threading.Thread(target=_config_reload_loop, daemon=True)
        _config_thread.start()
        print("[GOOGLE_SHEETS] Config reload thread started", flush=True)


def start_health_check():
    """Start the background health check thread."""
    global _health_thread
    if _health_thread is None:
        _stop_health.clear()
        _health_thread = threading.Thread(target=_health_check_loop, daemon=True)
        _health_thread.start()
        print("[GOOGLE_SHEETS] Health check thread started", flush=True)


def stop_buffer_flush(timeout=5):
    """
    Stop the background flush thread with graceful shutdown.
    Performs a final flush before stopping to prevent data loss.
    """
    global _flush_thread
    if _flush_thread is not None:
        try:
            # Force a final flush before stopping
            _flush_buffer()
            print("[GOOGLE_SHEETS] Final buffer flush completed", flush=True)
        except Exception as e:
            print(f"[GOOGLE_SHEETS] Error in final flush: {e}", flush=True)
        
        _stop_flush.set()
        try:
            _flush_thread.join(timeout=timeout)
        except Exception as e:
            print(f"[GOOGLE_SHEETS] Error joining flush thread: {e}", flush=True)
        _flush_thread = None
        print("[GOOGLE_SHEETS] Buffer flush thread stopped", flush=True)


def stop_config_reload(timeout=3):
    """Stop the background config reload thread."""
    global _config_thread
    if _config_thread is not None:
        _stop_config.set()
        try:
            _config_thread.join(timeout=timeout)
        except Exception as e:
            print(f"[GOOGLE_SHEETS] Error joining config thread: {e}", flush=True)
        _config_thread = None
        print("[GOOGLE_SHEETS] Config reload thread stopped", flush=True)


def stop_health_check(timeout=3):
    """Stop the background health check thread."""
    global _health_thread
    if _health_thread is not None:
        _stop_health.set()
        try:
            _health_thread.join(timeout=timeout)
        except Exception as e:
            print(f"[GOOGLE_SHEETS] Error joining health thread: {e}", flush=True)
        _health_thread = None
        print("[GOOGLE_SHEETS] Health check thread stopped", flush=True)


def shutdown_all(flush_timeout=10, other_timeout=5):
    """
    Gracefully shutdown all background threads.
    Performs final flush before stopping to prevent data loss.
    """
    print("[GOOGLE_SHEETS] Starting graceful shutdown...", flush=True)
    stop_buffer_flush(timeout=flush_timeout)
    stop_health_check(timeout=other_timeout)
    stop_config_reload(timeout=other_timeout)
    print("[GOOGLE_SHEETS] All threads stopped", flush=True)


# ==================================================
# PUBLIC API FUNCTIONS
# ==================================================

def log_signal(symbol, side, grade, score, entry, sl, tp, atr, adx, volume, btc_trend, status="SIGNAL"):
    """
    Log a signal to the Signals sheet with unique SignalID.
    
    Args:
        symbol: Trading pair symbol
        side: LONG or SHORT
        grade: Signal grade (A+, A, B, C)
        score: Signal score (0-100)
        entry: Entry price
        sl: Stop loss price
        tp: Take profit price
        atr: ATR value
        adx: ADX value
        volume: Volume status (HIGH/NORMAL)
        btc_trend: BTC trend direction
        status: Signal status (SIGNAL, FILLED, EXPIRED, SKIPPED)
    
    Returns:
        SignalID for tracking
    """
    try:
        signal_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        row = [signal_id, timestamp, symbol, side, grade, score, entry, sl, tp, atr, adx, volume, btc_trend, status]
        _add_to_buffer(SHEET_SIGNALS, row)
        return signal_id
    except Exception as e:
        print(f"[GOOGLE_SHEETS] log_signal error: {e}", flush=True)
        return None


def log_trade(symbol, side, entry, exit_price, pnl, result, grade, score, rr):
    """
    Log a trade result to the Trades sheet.
    
    Args:
        symbol: Trading pair symbol
        side: LONG or SHORT
        entry: Entry price
        exit_price: Exit/fill price
        pnl: Profit/Loss in USDT
        result: WIN, LOSS, or MANUAL_CLOSE
        grade: Signal grade
        score: Signal score
        rr: Risk-reward ratio
    """
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        row = [timestamp, symbol, side, entry, exit_price, pnl, result, grade, score, rr]
        _add_to_buffer(SHEET_TRADES, row)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] log_trade error: {e}", flush=True)


def update_signal_status(signal_id, status):
    """
    Update the status of a signal in the Signals sheet.
    
    Args:
        signal_id: The SignalID from log_signal()
        status: New status (FILLED, EXPIRED, SKIPPED)
    """
    try:
        if not signal_id:
            return
        
        spreadsheet = get_sheet()
        if not spreadsheet:
            return
        
        worksheet = spreadsheet.worksheet(SHEET_SIGNALS)
        # Find the row with matching signal_id (column A)
        cell = worksheet.find(str(signal_id))
        if cell:
            # Column 14 is Status (after SignalID at column 1)
            worksheet.update_cell(cell.row, 14, status)
            print(f"[GOOGLE_SHEETS] Updated signal {signal_id} to {status}", flush=True)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] update_signal_status error: {e}", flush=True)


def log_debug(symbol, reason, score=0, adx=0, atr=0, extra_data=""):
    """
    Log a debug/rejection reason to the Debug sheet.
    
    Args:
        symbol: Trading pair symbol
        reason: Rejection reason
        score: Signal score at rejection
        adx: ADX value
        atr: ATR value
        extra_data: Additional debug information
    """
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        row = [timestamp, symbol, reason, score, adx, atr, extra_data]
        _add_to_buffer(SHEET_DEBUG, row)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] log_debug error: {e}", flush=True)


def log_fill_analysis(symbol, side, current_price, entry_price, grade, score, atr, adx, btc_trend, fill_status):
    """
    Log fill analysis data to the FillAnalysis sheet.
    
    Args:
        symbol: Trading pair symbol
        side: LONG or SHORT
        current_price: Current market price
        entry_price: Entry price
        grade: Signal grade
        score: Signal score
        atr: ATR value
        adx: ADX value
        btc_trend: BTC trend direction
        fill_status: OPEN, FILLED, or EXPIRED
    """
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        distance_percent = abs(entry_price - current_price) / current_price * 100
        row = [timestamp, symbol, side, current_price, entry_price, round(distance_percent, 2), 
               grade, score, atr, adx, btc_trend, fill_status]
        _add_to_buffer(SHEET_FILL_ANALYSIS, row)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] log_fill_analysis error: {e}", flush=True)


def update_stats(balance, open_positions, wins, losses, win_rate, profit_usdt, current_loss_streak):
    """
    Update stats to the Stats sheet.
    
    Args:
        balance: Current account balance
        open_positions: Number of open positions
        wins: Total wins
        losses: Total losses
        win_rate: Win rate percentage
        profit_usdt: Profit in USDT
        current_loss_streak: Current consecutive loss streak
    """
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        row = [timestamp, balance, open_positions, wins, losses, win_rate, profit_usdt, current_loss_streak]
        _add_to_buffer(SHEET_STATS, row)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] update_stats error: {e}", flush=True)


def load_config():
    """
    Load configuration from the Config sheet.
    Updates internal cache and returns dictionary.
    
    Returns:
        Dictionary of configuration key-value pairs
    """
    config = {}
    try:
        spreadsheet = get_sheet()
        if not spreadsheet:
            return config
        
        worksheet = spreadsheet.worksheet(SHEET_CONFIG)
        records = worksheet.get_all_records()
        
        for record in records:
            key = record.get("Key", "")
            value = record.get("Value", "")
            if key:
                config[key] = value
        
        # Update cache
        with _config_lock:
            _config_cache.clear()
            _config_cache.update(config)
        
        print(f"[GOOGLE_SHEETS] Loaded {len(config)} config values", flush=True)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] load_config error: {e}", flush=True)
    
    return config


def get_config_value(key, default=None):
    """
    Get a configuration value from cache.
    
    Args:
        key: Configuration key
        default: Default value if key not found
    
    Returns:
        Configuration value or default
    """
    with _config_lock:
        return _config_cache.get(key, default)


def set_config_value(key, value):
    """
    Update a configuration value in the Config sheet.
    
    Args:
        key: Configuration key
        value: Configuration value
    """
    try:
        spreadsheet = get_sheet()
        if not spreadsheet:
            return
        
        worksheet = spreadsheet.worksheet(SHEET_CONFIG)
        
        # Try to find existing key and update, or append new row
        try:
            cell = worksheet.find(str(key))
            if cell:
                # Update existing row
                worksheet.update_cell(cell.row, 2, value)  # Column 2 is Value
            else:
                # Append new row
                worksheet.append_row([key, value])
        except gspread.exceptions.CellNotFound:
            # Key not found, append new row
            worksheet.append_row([key, value])
        
        # Update cache
        with _config_lock:
            _config_cache[key] = value
        
        print(f"[GOOGLE_SHEETS] Updated config: {key} = {value}", flush=True)
    except Exception as e:
        print(f"[GOOGLE_SHEETS] set_config_value error: {e}", flush=True)


# ==================================================
# INITIALIZATION
# ==================================================

# Ensure sheets exist on module load
_ensure_sheets_exist()

# Start all background threads
start_buffer_flush()
start_config_reload()
start_health_check()

# Load initial config
load_config()

print("[GOOGLE_SHEETS] Module initialized successfully", flush=True)