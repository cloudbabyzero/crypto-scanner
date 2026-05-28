import ccxt
import pandas as pd
import ta
import requests
import time
import os
import traceback
import csv
import threading
import telebot
import uuid

# =========================
# CONFIG
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = telebot.TeleBot(
    TELEGRAM_TOKEN
)

SCAN_INTERVAL = 300
COOLDOWN = 3600

LEVERAGE = 25
MARGIN_PER_TRADE = 0.84

ADX_FILTER = 20
MIN_SCORE = 85
ATR_FILTER = 0.4

symbols = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'DOGE/USDT:USDT',
    'SOL/USDT:USDT',
    'XRP/USDT:USDT',
    'HYPE/USDT:USDT',
    'ZEC/USDT:USDT',
    'INJ/USDT:USDT'
]

# =========================
# AUTO TRADE CONFIG
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
# TELEGRAM COMMANDS
# =========================

@bot.message_handler(commands=['ping'])
def ping(message):

    bot.reply_to(
        message,
        "✅ Bot Online"
    )

# =========================

@bot.message_handler(commands=['status'])
def status(message):

    text = f"""
🤖 BOT STATUS

Coins:
{len(symbols)}

Active Trades:
{len(active_trades)}

Cooldown:
{COOLDOWN}s

Scan Interval:
{SCAN_INTERVAL}s
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['trades'])
def trades(message):

    cleanup_closed_trades()

    with state_lock:
        trade_items = [
            t for t in active_trades.values()
            if t.get("status") in ["PENDING", "OPEN"]
        ]

    if not trade_items:

        bot.reply_to(
            message,
            "No active trades"
        )

        return

    text = "📊 ACTIVE TRADES\n\n"

    for trade in trade_items:

        sl_val = trade.get('sl', 'N/A')
        tp2_val = trade.get('tp2', 'N/A')

        text += f"""
{trade['symbol']}
{trade['side']}

Entry:
{trade['entry']}

SL:
{sl_val}

TP2:
{tp2_val}

----------------
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['coins'])
def coins(message):

    text = "🪙 COINS\n\n"

    for coin in symbols:

        text += f"{coin}\n"

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['stats'])
def stats(message):

    wins = 0
    losses = 0
    be = 0

    if not os.path.exists(
        'signals.csv'
    ):

        bot.reply_to(
            message,
            "No stats yet"
        )

        return

    with open(
        'signals.csv',
        'r'
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            result = row['result']

            if result == "WIN":

                wins += 1

            elif result == "LOSS":

                losses += 1

            elif result == "BE":

                be += 1

    total = wins + losses

    winrate = 0

    if total > 0:

        winrate = round(
            (wins / total) * 100,
            2
        )

    text = f"""
📈 STATS

WIN:
{wins}

LOSS:
{losses}

BE:
{be}

WINRATE:
{winrate}%
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['forcecheck'])
def forcecheck(message):
    bot.reply_to(
        message,
        "🔍 Force scanning..."
    )

    def _forcecheck_runner(chat_id):
        scanned = 0
        signals = 0
        skipped = 0
        errors = 0

        for symbol in symbols:
            scanned += 1

            try:
                res = analyze(symbol)
            except Exception:
                # In case analyze raises unexpectedly
                res = {"symbol": symbol, "result": "error"}

            if not isinstance(res, dict):
                # Normalize older non-dict returns as skipped
                res = {"symbol": symbol, "result": "skipped"}

            r = res.get("result")

            if r == "signal":
                signals += 1
            elif r == "error":
                errors += 1
            else:
                # treat cooldown/no-signal/explicit skips as skipped
                skipped += 1

        # Build summary message
        if signals > 0:
            summary = f"""
✅ FORCE SCAN COMPLETE

Scanned:
{scanned} coins

Signals:
{signals}

Skipped:
{skipped}

Errors:
{errors}
"""
        else:
            summary = f"""
⚠️ FORCE SCAN COMPLETE

No valid signals found.

Scanned:
{scanned} coins

Skipped:
{skipped}

Errors:
{errors}
"""

        try:
            bot.send_message(chat_id, summary)
        except Exception:
            # Fallback to global send_telegram
            send_telegram(summary)

    threading.Thread(
        target=lambda: _forcecheck_runner(message.chat.id),
        daemon=True
    ).start()
    
# =========================

@bot.message_handler(commands=['csv'])
def csv_file(message):

    if not os.path.exists(
        'signals.csv'
    ):

        bot.reply_to(
            message,
            "No CSV file yet"
        )

        return

    with open(
        'signals.csv',
        'rb'
    ) as file:

        bot.send_document(
            message.chat.id,
            file
        )

# =========================

def parse_command_value(
    message_text,
    cast_fn
):
    parts = message_text.split()
    if len(parts) < 2:
        raise ValueError("missing value")
    return cast_fn(parts[1])

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

def place_protection_orders(
    symbol,
    side_cfg,
    sl_price,
    tp2_price,
    amount
):
    # BingX hedge mode rejects reduceOnly on protection orders.
    base_params = {
        'positionSide': side_cfg['position_side'],
        'closePosition': True
    }

    sl_order = exchange.create_order(
        symbol=symbol,
        type='STOP_MARKET',
        side=side_cfg['stop_side'],
        amount=amount,
        params={
            **base_params,
            'stopPrice': sl_price
        }
    )

    try:
        tp2_order = exchange.create_order(
            symbol=symbol,
            type='TAKE_PROFIT_MARKET',
            side=side_cfg['stop_side'],
            amount=amount,
            params={
                **base_params,
                'stopPrice': tp2_price
            }
        )
    except Exception:
        tp2_order = exchange.create_order(
            symbol=symbol,
            type='TAKE_PROFIT',
            side=side_cfg['stop_side'],
            amount=amount,
            params={
                **base_params,
                'stopPrice': tp2_price
            }
        )

    return sl_order['id'], tp2_order['id']

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

@bot.message_handler(commands=['adx'])
def set_adx(message):

    global ADX_FILTER

    try:

        value = parse_command_value(
            message.text,
            int
        )

        ADX_FILTER = value

        bot.reply_to(
            message,
            f"✅ ADX Filter updated to {value}"
        )

    except Exception:

        bot.reply_to(
            message,
            "Usage: /adx 18"
        )

# =========================

@bot.message_handler(commands=['score'])
def set_score(message):

    global MIN_SCORE

    try:

        value = parse_command_value(
            message.text,
            int
        )

        MIN_SCORE = value

        bot.reply_to(
            message,
            f"✅ MIN_SCORE updated to {value}"
        )

    except Exception:

        bot.reply_to(
            message,
            "Usage: /score 85"
        )

# =========================

@bot.message_handler(commands=['atr'])
def set_atr(message):

    global ATR_FILTER

    try:

        value = parse_command_value(
            message.text,
            float
        )

        ATR_FILTER = value

        bot.reply_to(
            message,
            f"✅ ATR Filter updated to {value}"
        )

    except Exception:

        bot.reply_to(
            message,
            "Usage: /atr 0.4"
        )

# =========================

@bot.message_handler(commands=['config'])
def config(message):

    text = f"""
⚙️ CURRENT CONFIG

ADX FILTER:
{ADX_FILTER}

MIN SCORE:
{MIN_SCORE}

ATR FILTER:
{ATR_FILTER}

COOLDOWN:
{COOLDOWN}

SCAN INTERVAL:
{SCAN_INTERVAL}
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['long'])
def long_order(message):

    try:

        parts = message.text.split()

        if len(parts) < 2:

            bot.reply_to(
                message,
                "Usage: /long xrp"
            )

            return

        coin = parts[1].upper()

        symbol = f"{coin}/USDT:USDT"
        
        execute_trade(
            symbol,
            "long"
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"ERROR: {str(e)}"
        )

# =========================

@bot.message_handler(commands=['short'])
def short_order(message):

    try:

        parts = message.text.split()

        if len(parts) < 2:

            bot.reply_to(
                message,
                "Usage: /short xrp"
            )

            return

        coin = parts[1].upper()

        symbol = f"{coin}/USDT:USDT"

        execute_trade(
            symbol,
            "short"
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"ERROR: {str(e)}"
        )

# =========================

@bot.message_handler(commands=['help'])
def help_command(message):

    text = """
🤖 AVAILABLE COMMANDS

/ping
เช็คว่าบอทยังออนไลน์ไหม

/status
ดูสถานะบอท

/stats
ดู winrate

/trades
ดู active trades

/coins
ดูเหรียญที่สแกน

/forcecheck
บังคับสแกนทันที

/csv
ดาวน์โหลด signals.csv

/adx 18
เปลี่ยน ADX filter

/score 85
เปลี่ยน minimum score

/atr 0.4
เปลี่ยน ATR filter

/config
ดู config ปัจจุบัน

/long xrp
เปิด LONG

/short xrp
เปิด SHORT

---

🤖 AUTO TRADE COMMANDS

/autoon
เปิด AUTO TRADE

/autooff
ปิด AUTO TRADE

/autostatus
ดู status AUTO TRADE

/grade A
เปลี่ยน grade filter (A+, A, B, C)

/autoatr 0.5
เปลี่ยน ATR filter

/autoadx 22
เปลี่ยน ADX filter

/autovol on
เปิด High Volume Only

/autovol off
ปิด High Volume Only

---

/help
ดูคำสั่งทั้งหมด
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['autoon'])
def autoon(message):

    global AUTO_TRADE
    AUTO_TRADE = True

    text = f"""
✅ AUTO TRADE ENABLED

Grade Filter:
{AUTO_TRADE_MIN_GRADE}

Max Longs:
{MAX_LONG_TRADES}

Max Shorts:
{MAX_SHORT_TRADES}
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['autooff'])
def autooff(message):

    global AUTO_TRADE
    AUTO_TRADE = False

    bot.reply_to(
        message,
        "❌ AUTO TRADE DISABLED"
    )

# =========================

@bot.message_handler(commands=['autostatus'])
def autostatus(message):

    cleanup_closed_trades()

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

    status_text = "🔴 OFF" if not AUTO_TRADE else "🟢 ON"
    vol_text = "🟢 ON" if AUTO_TRADE_HIGH_VOLUME_ONLY else "🔴 OFF"

    text = f"""
🤖 AUTO TRADE STATUS

Status:
{status_text}

Grade Filter:
{AUTO_TRADE_MIN_GRADE}

ATR Filter:
{AUTO_TRADE_MIN_ATR}%

ADX Filter:
{AUTO_TRADE_MIN_ADX}

High Volume Only:
{vol_text}

Active Longs:
{active_longs} / {MAX_LONG_TRADES}

Active Shorts:
{active_shorts} / {MAX_SHORT_TRADES}

Total Active:
{len([t for t in trade_items if t.get('status') in ['PENDING', 'OPEN']])}
"""

    bot.reply_to(
        message,
        text
    )

# =========================

@bot.message_handler(commands=['grade'])
def set_grade(message):

    global AUTO_TRADE_MIN_GRADE

    try:

        parts = message.text.split()

        if len(parts) < 2:
            bot.reply_to(
                message,
                "Usage: /grade A\n\nAllowed: A+ A B C"
            )
            return

        grade = parts[1].upper()

        if grade not in GRADE_PRIORITY:
            bot.reply_to(
                message,
                f"❌ Invalid grade: {grade}\n\nAllowed: A+ A B C"
            )
            return

        AUTO_TRADE_MIN_GRADE = grade

        bot.reply_to(
            message,
            f"✅ Auto Trade Grade Filter updated to {grade}"
        )

    except Exception as e:

        bot.reply_to(
            message,
            f"ERROR: {str(e)}"
        )

# =========================

@bot.message_handler(commands=['autoatr'])
def set_autoatr(message):

    global AUTO_TRADE_MIN_ATR

    try:

        value = parse_command_value(
            message.text,
            float
        )

        AUTO_TRADE_MIN_ATR = value

        bot.reply_to(
            message,
            f"✅ Auto Trade ATR Filter updated to {value}%"
        )

    except Exception:

        bot.reply_to(
            message,
            "Usage: /autoatr 0.5"
        )

# =========================

@bot.message_handler(commands=['autoadx'])
def set_autoadx(message):

    global AUTO_TRADE_MIN_ADX

    try:

        value = parse_command_value(
            message.text,
            int
        )

        AUTO_TRADE_MIN_ADX = value

        bot.reply_to(
            message,
            f"✅ Auto Trade ADX Filter updated to {value}"
        )

    except Exception:

        bot.reply_to(
            message,
            "Usage: /autoadx 22"
        )

# =========================

@bot.message_handler(commands=['autovol'])
def set_autovol(message):

    global AUTO_TRADE_HIGH_VOLUME_ONLY

    try:

        parts = message.text.split()

        if len(parts) < 2:
            bot.reply_to(
                message,
                "Usage: /autovol on\nUsage: /autovol off"
            )
            return

        setting = parts[1].lower()

        if setting == "on":
            AUTO_TRADE_HIGH_VOLUME_ONLY = True
            bot.reply_to(
                message,
                "✅ Auto Trade High Volume Only: ON"
            )
        elif setting == "off":
            AUTO_TRADE_HIGH_VOLUME_ONLY = False
            bot.reply_to(
                message,
                "✅ Auto Trade High Volume Only: OFF"
            )
        else:
            bot.reply_to(
                message,
                "❌ Invalid setting. Use: on or off"
            )

    except Exception as e:

        bot.reply_to(
            message,
            f"ERROR: {str(e)}"
        )

# =========================
# TELEGRAM COMMANDS
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


def execute_trade(symbol, side):

    try:

        # =========================
        # FORMAT SYMBOL
        # =========================

        symbol = symbol.upper()

        if ":USDT" not in symbol:
            symbol = f"{symbol}/USDT:USDT"

        # =========================
        # CHECK SYMBOL
        # =========================

        if symbol not in symbols:

            send_telegram(
                f"❌ {symbol} not supported"
            )

            return

        # =========================
        # PREVENT DUPLICATE
        # =========================

        with state_lock:
            trade_items = list(active_trades.values())

        for trade in trade_items:

            if (
                trade['symbol'] == symbol
                and trade.get('status') in ["PENDING", "OPEN"]
            ):

                send_telegram(
                    f"⚠️ {symbol} already active"
                )

                return

        # =========================
        # GET SIGNAL
        # =========================

        signal = get_latest_signal(symbol)

        # =========================
        # CHECK SIGNAL BEFORE ENTRY
        # =========================

        if not signal:
            send_telegram(
                f"❌ No signal found for {symbol}"
            )
            return

        signal_type = signal.get("signal", "").upper()

        if signal_type != side.upper():
            send_telegram(
                f"❌ No {side.upper()} signal for {symbol}\n\n"
                f"Current Signal: {signal_type}"
            )
            return

        entry = signal["entry"]
        sl = signal["sl"]
        atr = signal["atr"]
        
        # =========================
        # MARGIN MODE
        # =========================

        try:

            exchange.set_margin_mode(
                "isolated",
                symbol
            )

        except Exception:
            pass

        # =========================
        # LEVERAGE
        # =========================

        if side == "long":

            exchange.set_leverage(
                LEVERAGE,
                symbol,
                {
                     "side": "LONG"
                }
            )

        else:

            exchange.set_leverage(
                LEVERAGE,
                symbol,
                {
                    "side": "SHORT"
                }
            )

        # =========================
        # AMOUNT
        # =========================

        raw_amount = (
            MARGIN_PER_TRADE * LEVERAGE
        ) / entry

        amount = exchange.amount_to_precision(
            symbol,
            raw_amount
        )

        amount = float(amount)

        # =========================
        # LONG
        # =========================

        if side == "long":
            sl, tp1, tp2, _ = calculate_trade_levels(
                entry,
                atr,
                "LONG"
            )

            order = exchange.create_order(
                symbol=symbol,
                type='limit',
                side='buy',
                amount=amount,
                price=entry,
                params={
                    'positionSide': 'LONG',
                    'tradeSide': 'OPEN',
                    'marginMode': 'isolated'
                }
            )

        # =========================
        # SHORT
        # =========================

        else:
            sl, tp1, tp2, _ = calculate_trade_levels(
                entry,
                atr,
                "SHORT"
            )

            order = exchange.create_order(
                symbol=symbol,
                type='limit',
                side='sell',
                amount=amount,
                price=entry,
                params={
                    'positionSide': 'SHORT',
                    'tradeSide': 'OPEN',
                    'marginMode': 'isolated'
                }
            )

        side_cfg = get_side_config(
            "LONG"
            if side == "long"
            else "SHORT"
        )

        sl_order_id = None
        tp2_order_id = None

        try:
            sl_order_id, tp2_order_id = place_protection_orders(
                symbol=symbol,
                side_cfg=side_cfg,
                sl_price=sl,
                tp2_price=tp2,
                amount=amount
            )
        except Exception as protect_error:
            send_telegram(
                f"⚠️ Protection pre-set failed for {symbol}\n"
                f"{str(protect_error)}\n"
                f"Bot will retry after fill."
            )

        # =========================
        # SAVE TRADE
        # =========================
        trade_id = str(uuid.uuid4())[:8]

        with state_lock:
            active_trades[trade_id] = {
                "symbol": symbol,
                "side": side.upper(),
                "entry": entry,
                "sl": sl,
                "tp2": tp2,
                "status": "PENDING",
                "order_id": order['id'],
                "amount": amount,
                "sl_order_id": sl_order_id,
                "tp2_order_id": tp2_order_id
            }

        # =========================
        # TELEGRAM
        # =========================

        message = f"""
✅ ORDER EXECUTED

{symbol}

Side:
{side.upper()}

Entry:
{entry}

SL:
{sl}

TP2:
{tp2}

Leverage:
x{LEVERAGE}

Margin:
{MARGIN_PER_TRADE} USDT
"""

        send_telegram(message)

    except Exception as e:

        send_telegram(
            f"❌ ORDER ERROR\n\n{str(e)}"
        )


# =========================
# BINGX
# =========================

exchange = ccxt.bingx({

    'apiKey': os.getenv("BINGX_API_KEY"),

    'secret': os.getenv("BINGX_SECRET_KEY"),

    'enableRateLimit': True,

    'options': {

        'defaultType': 'swap',

        'defaultSubType': 'linear',

        'adjustForTimeDifference': True

    }

})

exchange.set_sandbox_mode(False)

exchange.options['defaultType'] = 'swap'
exchange.options['defaultSubType'] = 'linear'

markets = exchange.load_markets()

print("✅ FUTURES MARKETS LOADED")

# =========================
# DATAFRAME
# =========================

def get_dataframe(symbol, timeframe):

    ohlcv = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=200
    )

    df = pd.DataFrame(
        ohlcv,
        columns=[
            'time',
            'open',
            'high',
            'low',
            'close',
            'volume'
        ]
    )

    # =========================
    # EMA
    # =========================

    df['ema7'] = ta.trend.ema_indicator(
        df['close'],
        window=7
    )

    df['ema25'] = ta.trend.ema_indicator(
        df['close'],
        window=25
    )

    df['ema99'] = ta.trend.ema_indicator(
        df['close'],
        window=99
    )

    # =========================
    # RSI
    # =========================

    df['rsi'] = ta.momentum.rsi(
        df['close'],
        window=14
    )

    # =========================
    # MACD
    # =========================

    macd = ta.trend.MACD(df['close'])

    df['macd'] = macd.macd()

    df['macd_signal'] = macd.macd_signal()

    # =========================
    # ADX
    # =========================

    adx = ta.trend.ADXIndicator(
        df['high'],
        df['low'],
        df['close'],
        window=14
    )

    df['adx'] = adx.adx()

    # =========================
    # BOLLINGER
    # =========================

    bb = ta.volatility.BollingerBands(
        close=df['close'],
        window=20,
        window_dev=2
    )

    df['bb_mid'] = bb.bollinger_mavg()

    df['bb_upper'] = bb.bollinger_hband()

    df['bb_lower'] = bb.bollinger_lband()

    # =========================
    # ATR
    # =========================

    df['atr'] = ta.volatility.average_true_range(
        df['high'],
        df['low'],
        df['close'],
        window=14
    )

    # =========================
    # VOLUME
    # =========================

    df['vol_avg'] = (
        df['volume']
        .rolling(20)
        .mean()
    )

    return df

# =========================
# BTC TREND
# =========================

def get_btc_trend():

    btc_df = get_dataframe(
        'BTC/USDT:USDT',
        '4h'
    )

    btc = btc_df.iloc[-2]

    if btc['ema25'] > btc['ema99']:

        return "bullish"

    return "bearish"

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
                        target=lambda: execute_trade(symbol, "long"),
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
                        target=lambda: execute_trade(symbol, "short"),
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
# CLEANUP CLOSED TRADES
# =========================

def cleanup_closed_trades():

    try:

        all_positions = exchange.fetch_positions()

        open_symbols = set()

        for pos in all_positions:

            try:

                contracts = float(pos.get('contracts') or 0)

            except (TypeError, ValueError):

                contracts = 0

            if contracts > 0:

                open_symbols.add(pos['symbol'])

    except Exception:

        print(
            "cleanup_closed_trades: fetch_positions failed",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

        return

    now = time.time()

    with state_lock:

        seen_symbols = set()

        to_remove = []

        for trade_id, trade in active_trades.items():

            status = trade.get("status")

            if status == "SIGNAL":

                age = now - trade.get("created_at", now)

                if age > 3600:

                    to_remove.append(trade_id)

                continue

            if status == "PENDING":

                continue

            symbol = trade['symbol']

            if symbol not in open_symbols or symbol in seen_symbols:

                to_remove.append(trade_id)

            else:

                seen_symbols.add(symbol)

        for trade_id in to_remove:

            active_trades.pop(trade_id, None)

# =========================
# TRADE CHECKER
# =========================

def check_trades():

    while True:

        try:

            with state_lock:
                trades_snapshot = list(active_trades.items())

            for signal_id, trade in trades_snapshot:

                # =========================
                # WAIT FOR LIMIT FILL
                # =========================

                if trade.get('status') == "PENDING":

                    order_info = exchange.fetch_order(
                        trade['order_id'],
                        trade['symbol']
                    )

                    if order_info['status'] == "closed":

                        amount = trade['amount']
                        side_cfg = get_side_config(trade['side'])

                        # =========================
                        # ENSURE PROTECTION
                        # =========================

                        sl_order_id = trade.get('sl_order_id')
                        tp2_order_id = trade.get('tp2_order_id')

                        if not sl_order_id or not tp2_order_id:

                            sl_order_id, tp2_order_id = place_protection_orders(
                                symbol=trade['symbol'],
                                side_cfg=side_cfg,
                                sl_price=trade['sl'],
                                tp2_price=trade['tp2'],
                                amount=amount
                            )

                        with state_lock:

                            trade['status'] = "OPEN"
                            trade['sl_order_id'] = sl_order_id
                            trade['tp2_order_id'] = tp2_order_id

                        send_telegram(
                            f"✅ ORDER FILLED\n\n"
                            f"{trade['symbol']}"
                        )

                    elif order_info['status'] in [
                        'canceled', 'expired', 'rejected'
                    ]:

                        send_telegram(
                            f"⚠️ ORDER {order_info['status'].upper()}\n\n"
                            f"{trade['symbol']}"
                        )

                        with state_lock:

                            active_trades.pop(signal_id, None)

                        continue

                    else:

                        continue

                # =========================
                # SKIP SIGNAL
                # =========================

                if trade.get('status') == "SIGNAL":

                    continue

                # =========================
                # CHECK REAL POSITION
                # =========================

                try:

                    positions = exchange.fetch_positions(
                        [trade['symbol']]
                    )

                    contracts = 0

                    for pos in positions:

                        try:

                            contracts += float(
                                pos.get('contracts') or 0
                            )

                        except (TypeError, ValueError):

                            pass

                except Exception:

                    contracts = 0

                # =========================
                # POSITION CLOSED
                # =========================

                if (
                    contracts <= 0
                    and not trade.get('closed')
                ):

                    tp2_filled = False

                    if trade.get('tp2_order_id'):

                        try:

                            tp2_info = exchange.fetch_order(
                                trade['tp2_order_id'],
                                trade['symbol']
                            )

                            tp2_filled = (
                                tp2_info.get('status') == "closed"
                            )

                        except Exception:

                            tp2_filled = False

                    if tp2_filled:

                        send_telegram(
                            f"🏆 WIN\n\n"
                            f"{trade['symbol']}"
                        )

                        update_signal_result(
                            signal_id,
                            "WIN"
                        )

                    else:

                        send_telegram(
                            f"❌ LOSS\n\n"
                            f"{trade['symbol']}"
                        )

                        update_signal_result(
                            signal_id,
                            "LOSS"
                        )

                    with state_lock:

                        trade['closed'] = True

                        active_trades.pop(signal_id, None)

                    continue

            time.sleep(60)

        except Exception:

            print(
                "Trade checker error",
                flush=True
            )

            print(
                traceback.format_exc(),
                flush=True
            )

            time.sleep(30)

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

def restore_open_positions():

    try:

        positions = exchange.fetch_positions()

    except Exception:

        print(
            "restore_open_positions: fetch_positions failed",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

        return

    restored_count = 0

    for pos in positions:

        try:

            contracts = abs(float(pos.get('contracts') or 0))

        except (TypeError, ValueError):

            continue

        if contracts <= 0:

            continue

        symbol = pos.get('symbol')

        if not symbol:

            continue

        position_side = (
            pos.get('side') or
            pos.get('positionSide') or
            ''
        ).upper()

        if position_side in ['LONG', 'BUY']:

            side = 'LONG'

        elif position_side in ['SHORT', 'SELL']:

            side = 'SHORT'

        else:

            side = 'LONG'

        entry_price = pos.get('entryPrice') or pos.get('markPrice') or 0

        trade_id = f"restored_{str(uuid.uuid4())[:8]}"

        with state_lock:

            already_tracked = any(
                t['symbol'] == symbol
                and t.get('status') in ['PENDING', 'OPEN']
                for t in active_trades.values()
            )

            if already_tracked:

                continue

            active_trades[trade_id] = {
                "symbol": symbol,
                "status": "OPEN",
                "side": side,
                "entry": entry_price,
                "amount": contracts,
                "restored": True
            }

        restored_count += 1

        print(
            f"Restored: {symbol} {side} {contracts}",
            flush=True
        )

    if restored_count > 0:

        send_telegram(
            f"🔄 Restored {restored_count} open position(s) from BingX"
        )

    else:

        print(
            "restore_open_positions: no open positions found",
            flush=True
        )


def main():
    threading.Thread(
        target=telegram_polling,
        daemon=True
    ).start()

    # =========================
    # STARTUP
    # =========================

    restore_open_positions()

    threading.Thread(
        target=check_trades,
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
