import requests
import time
import os
import traceback
import csv
import threading
import uuid

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
    GRADE_PRIORITY,
    AUTO_TRADE_MIN_ATR,
    AUTO_TRADE_MIN_ADX,
    AUTO_TRADE_HIGH_VOLUME_ONLY,
)

# =========================
# INDICATORS - Import from indicators.py
# =========================

from indicators import get_dataframe, get_btc_trend

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

last_alert = {}

active_trades = {}
state_lock = threading.RLock()

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
    if side == "LONG":
        sl = round(
            entry - atr * 1.5,
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
        entry + atr * 1.5,
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
    
    if side.upper() == "LONG":
        return active_longs < MAX_LONG_TRADES
    else:
        return active_shorts < MAX_SHORT_TRADES

def check_execution_filters(atr_percent, adx, volume_high):
    """Check if trade meets execution filter requirements.
    
    Returns:
        (passes, reason) tuple
        passes: True if all filters pass, False otherwise
        reason: Skip reason string if fails, None if passes
    """
    
    if atr_percent < AUTO_TRADE_MIN_ATR:
        return False, "ATR too low"
    
    if adx < AUTO_TRADE_MIN_ADX:
        return False, "ADX too low"
    
    if AUTO_TRADE_HIGH_VOLUME_ONLY and not volume_high:
        return False, "Volume not high"
    
    return True, None

# =========================
# TRADING FUNCTIONS
# =========================

def get_latest_signal(symbol):

    with state_lock:
        trade_items = list(active_trades.values())

    for trade in trade_items:

        if (
            trade['symbol'] == symbol
            and trade['status'] == "SIGNAL"
        ):

            return {
                "signal": trade['side'],
                "entry": trade['entry'],
                "sl": trade['sl'],
                "tp": trade['tp2'],
                "atr": abs(
                    trade['entry'] - trade['sl']
                ) / 1.5
            }

    return None


# execute_trade moved to bingx_client.py

# =========================
# ANALYZE
# =========================

def analyze(symbol):

    try:

        now = time.time()

        # =========================
        # COOLDOWN
        # =========================

        with state_lock:
            last_time = last_alert.get(symbol)

        if last_time and now - last_time < COOLDOWN:
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

            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # SCORE
        # =========================

        long_score = 0

        short_score = 0

        btc_trend = get_btc_trend()
        signal_id = str(uuid.uuid4())[:8]

        # =========================
        # DAILY TREND
        # =========================

        if d1['ema25'] > d1['ema99']:

            long_score += 10

        else:

            short_score += 10

        # =========================
        # 4H TREND
        # =========================

        if h4['ema25'] > h4['ema99']:

            long_score += 25

        else:

            short_score += 25

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
        # RSI
        # =========================

        if 55 < m15['rsi'] < 70:

            long_score += 15

        elif 30 < m15['rsi'] < 45:

            short_score += 15

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
        # VOLUME
        # =========================

        volume_high = (
            m15['volume']
            >
            m15['vol_avg'] * 1.3
        )

        if volume_high:

            long_score += 15

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

            return {"symbol": symbol, "result": "skipped"}

        # =========================
        # BTC FILTER
        # =========================

        if (
            symbol != 'BTC/USDT:USDT'
            and btc_trend == "bearish"
        ):

            long_score -= 20

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

        long_pullback = (
            m15['ema7']
            +
            (m15['atr'] * 0.2)
        )

        short_pullback = (
            m15['ema7']
            -
            (m15['atr'] * 0.2)
        )

        # =========================
        # LONG SIGNAL
        # =========================

        if (
            long_score >= MIN_SCORE
            and btc_trend == "bullish"
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
            send_telegram(message)
            
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

            with state_lock:
                active_trades[signal_id] = {
                    "symbol": symbol,
                    "status": "SIGNAL",
                    "side": "LONG",
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "created_at": time.time()
                }
                # Update last_alert after storing the signal
                last_alert[symbol] = now

            # =========================
            # AUTO TRADE LOGIC
            # =========================

            if AUTO_TRADE:
                
                skip_reason = None
                
                # Check grade filter
                if not passes_grade_filter(grade):
                    skip_reason = f"Grade: {grade} < {AUTO_TRADE_MIN_GRADE}"
                
                # Check execution filters (only if grade passed)
                elif not skip_reason:
                    passes_exec, exec_reason = check_execution_filters(
                        atr_percent,
                        m15['adx'],
                        volume_high
                    )
                    if not passes_exec:
                        skip_reason = exec_reason
                
                # Check position limit (only if all other filters passed)
                if not skip_reason:
                    if not can_open_trade("LONG"):
                        skip_reason = f"Max {MAX_LONG_TRADES} long positions reached"
                
                # Execute if no skip reason
                if not skip_reason:
                    send_telegram(
                        f"🤖 AUTO TRADE EXECUTED\n\n"
                        f"{symbol}\n"
                        f"LONG\n\n"
                        f"Grade: {grade}\n"
                        f"Entry: {entry}"
                    )
                    
                    threading.Thread(
                        target=lambda: bingx_client.execute_trade(symbol, "long"),
                        daemon=True
                    ).start()
                else:
                    # Send skip reason
                    send_telegram(
                        f"⏭️ AUTO TRADE SKIPPED\n\n"
                        f"{symbol}\n"
                        f"LONG\n\n"
                        f"Reason: {skip_reason}"
                    )

            return {"symbol": symbol, "result": "signal"}
        # =========================
        # SHORT SIGNAL
        # =========================

        elif (
            short_score >= MIN_SCORE
            and btc_trend == "bearish"
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
            send_telegram(message)
            
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

            with state_lock:
                active_trades[signal_id] = {
                    "symbol": symbol,
                    "status": "SIGNAL",
                    "side": "SHORT",
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "created_at": time.time()
                }
                # Update last_alert after storing the signal
                last_alert[symbol] = now

            # =========================
            # AUTO TRADE LOGIC
            # =========================

            if AUTO_TRADE:
                
                skip_reason = None
                
                # Check grade filter
                if not passes_grade_filter(grade):
                    skip_reason = f"Grade: {grade} < {AUTO_TRADE_MIN_GRADE}"
                
                # Check execution filters (only if grade passed)
                elif not skip_reason:
                    passes_exec, exec_reason = check_execution_filters(
                        atr_percent,
                        m15['adx'],
                        volume_high
                    )
                    if not passes_exec:
                        skip_reason = exec_reason
                
                # Check position limit (only if all other filters passed)
                if not skip_reason:
                    if not can_open_trade("SHORT"):
                        skip_reason = f"Max {MAX_SHORT_TRADES} short positions reached"
                
                # Execute if no skip reason
                if not skip_reason:
                    send_telegram(
                        f"🤖 AUTO TRADE EXECUTED\n\n"
                        f"{symbol}\n"
                        f"SHORT\n\n"
                        f"Grade: {grade}\n"
                        f"Entry: {entry}"
                    )
                    
                    threading.Thread(
                        target=lambda: bingx_client.execute_trade(symbol, "short"),
                        daemon=True
                    ).start()
                else:
                    # Send skip reason
                    send_telegram(
                        f"⏭️ AUTO TRADE SKIPPED\n\n"
                        f"{symbol}\n"
                        f"SHORT\n\n"
                        f"Reason: {skip_reason}"
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

        return {"symbol": symbol, "result": "error"}

    # If execution reaches here, no signal was generated for this symbol
        
        return {"symbol": symbol, "result": "skipped"}

# =========================
# TRADE MANAGER
# =========================
# cleanup_closed_trades, check_trades, restore_open_positions moved to trade_manager.py

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


def main():
    threading.Thread(
        target=telegram_polling,
        daemon=True
    ).start()

    # =========================
    # STARTUP
    # =========================

    trade_manager.restore_open_positions()

    threading.Thread(
        target=trade_manager.check_trades,
        daemon=True
    ).start()

    send_telegram(
        "🚀 Railway Scanner Bot Online"
    )

    # =========================
    # SAFE RESTART WARNING
    # =========================

    send_telegram(
        "⚠️ AUTO TRADE DISABLED AFTER RESTART\n\n"
        "Use /autoon to enable auto trading."
    )

    # =========================
    # MAIN LOOP
    # =========================
    while True:

        try:

            print(
                "Bot alive - scanning market...",
                flush=True
            )

            for symbol in symbols:

                analyze(symbol)

                time.sleep(2)

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
