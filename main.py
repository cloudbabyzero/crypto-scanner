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
MARGIN_PER_TRADE = 0.6

ADX_FILTER = 20
MIN_SCORE = 85
ATR_FILTER = 0.4

symbols = [
    'BTC/USDT',
    'ETH/USDT',
    'DOGE/USDT',
    'SOL/USDT',
    'XRP/USDT',
    'HYPE/USDT',
    'ZEC/USDT',
    'INJ/USDT'
]

last_alert = {}

active_trades = {}

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

    if not active_trades:

        bot.reply_to(
            message,
            "No active trades"
        )

        return

    text = "📊 ACTIVE TRADES\n\n"

    for trade_id in active_trades:

        trade = active_trades[trade_id]

        text += f"""
{trade['symbol']}
{trade['side']}

Entry:
{trade['entry']}

SL:
{trade['sl']}

TP2:
{trade['tp2']}

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

    threading.Thread(
        target=lambda: [
            analyze(symbol)
            for symbol in symbols
        ],
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

@bot.message_handler(commands=['adx'])
def set_adx(message):

    global ADX_FILTER

    try:

        value = int(
            message.text.split()[1]
        )

        ADX_FILTER = value

        bot.reply_to(
            message,
            f"✅ ADX Filter updated to {value}"
        )

    except:

        bot.reply_to(
            message,
            "Usage: /adx 18"
        )

# =========================

@bot.message_handler(commands=['score'])
def set_score(message):

    global MIN_SCORE

    try:

        value = int(
            message.text.split()[1]
        )

        MIN_SCORE = value

        bot.reply_to(
            message,
            f"✅ MIN_SCORE updated to {value}"
        )

    except:

        bot.reply_to(
            message,
            "Usage: /score 85"
        )

# =========================

@bot.message_handler(commands=['atr'])
def set_atr(message):

    global ATR_FILTER

    try:

        value = float(
            message.text.split()[1]
        )

        ATR_FILTER = value

        bot.reply_to(
            message,
            f"✅ ATR Filter updated to {value}"
        )

    except:

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

        symbol = f"{coin}/USDT"

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

        symbol = f"{coin}/USDT"

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

/help
ดูคำสั่งทั้งหมด
"""

    bot.reply_to(
        message,
        text
    )
    

# =========================
# TELEGRAM COMMANDS
# =========================

def get_latest_signal(symbol):

    df_15m = get_dataframe(symbol, '15m')

    m15 = df_15m.iloc[-2]

    entry = float(m15['close'])

    atr = float(m15['atr'])

    return {
        "entry": entry,
        "atr": atr
    }

def execute_trade(symbol, side):

    try:

        # =========================
        # CHECK SYMBOL
        # =========================

        if symbol not in symbols:

            send_telegram(
                f"❌ {symbol} not supported"
            )

            return

        # =========================
        # PREVENT DUPLICATE TRADE
        # =========================

        for trade_id in active_trades:

            trade = active_trades[trade_id]

            if (
               trade['symbol'] == symbol
               and trade.get('status') in ["PENDING", "OPEN"]
           ):

               send_telegram(
                   f"⚠️ {symbol} already active"
               )

               return

        
        signal = get_latest_signal(symbol)

        entry = signal['entry']

        atr = signal['atr']

        try:
            exchange.set_margin_mode(
                "isolated",
                symbol
            )
        except:

            pass

        exchange.set_leverage(
            LEVERAGE,
            symbol,
            params={
                "side": side.upper()
            }
        )

        amount = round(
            (MARGIN_PER_TRADE * LEVERAGE)
            / entry,
            3
        )

        # =========================
        # MARKET ORDER
        # =========================

        if side == "long":

            order = exchange.create_limit_buy_order(
                symbol,
                amount,
                entry
           )

            sl = round(
                entry - atr * 1.5,
                4
            )

            tp = round(
                entry + ((entry - sl) * 2),
                4
            )
            
            order = exchange.create_order(
                    symbol,
                    'limit',
                    'buy',
                    amount,
                    entry,
                    params={
                        'positionSide': 'LONG',
                        'marginMode': 'isolated'
                    }
                )

        else:

            order = exchange.create_order(
                    symbol,
                    'limit',
                    'sell',
                    amount,
                    entry,
                    params={
                        'positionSide': 'SHORT',
                        'marginMode': 'isolated'
                    }
                )

        message = f"""
        ✅ ORDER EXECUTED

        {symbol}

        Side:
        {side.upper()}

        Entry:
        {entry}

        SL:
        {sl}

        TP:
        {tp}

        Leverage:
        x{LEVERAGE}

        Margin:
        {MARGIN_PER_TRADE} USDT
        """

        send_telegram(message)

        trade_id = str(uuid.uuid4())[:8]

        active_trades[trade_id] = {
            "symbol": symbol,
            "side": side.upper(),
            "entry": entry,
            "sl": sl,
            "tp1": tp,
            "tp2": tp,
            "tp1_hit": False,
            "status": "PENDING",
            "order_id": order['id']
        }

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
        'defaultType': 'swap'
    }
})

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
        'BTC/USDT',
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

        if symbol in last_alert:

            if now - last_alert[symbol] < COOLDOWN:

                return

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

            return

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

            return

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

            return

        # =========================
        # BTC FILTER
        # =========================

        if (
            symbol != 'BTC/USDT'
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

            message = f"""
🚀 LONG SIGNAL

{symbol}

Grade:
{grade}

Score:
{long_score}/100

Pullback Entry:
{entry}

SL:
{sl}

TP1:
{tp1}

TP2:
{tp2}

RR:
1:{rr}

RSI:
{round(m15['rsi'],2)}

ADX:
{round(m15['adx'],2)}

ATR %:
{round(atr_percent,2)}

Volume:
{"HIGH" if volume_high else "NORMAL"}

BTC Trend:
{btc_trend}

Plan:
- TP1 = ปิด 50%
- Move SL -> BE
- TP2 = ปล่อยรัน
"""

            print(
                message,
                flush=True
            )

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

            active_trades[signal_id] = {
                "symbol": symbol,
                "status": "SIGNAL",
                "side": "LONG",
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "tp1_hit": False
            }
            last_alert[symbol] = now

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

            message = f"""
🔻 SHORT SIGNAL

{symbol}

Grade:
{grade}

Score:
{short_score}/100

Pullback Entry:
{entry}

SL:
{sl}

TP1:
{tp1}

TP2:
{tp2}

RR:
1:{rr}

RSI:
{round(m15['rsi'],2)}

ADX:
{round(m15['adx'],2)}

ATR %:
{round(atr_percent,2)}

Volume:
{"HIGH" if volume_high else "NORMAL"}

BTC Trend:
{btc_trend}

Plan:
- TP1 = ปิด 50%
- Move SL -> BE
- TP2 = ปล่อยรัน
"""

            print(
                message,
                flush=True
            )

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

            active_trades[signal_id] = {
                "symbol": symbol,
                "status": "SIGNAL",
                "side": "SHORT",
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "tp1_hit": False
            }
            last_alert[symbol] = now

    except Exception as e:

        print(
            f"{symbol} ERROR",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

# =========================
# TRADE CHECKER
# =========================

def check_trades():

    while True:

        try:

            for signal_id in list(active_trades.keys()):

                trade = active_trades[signal_id]

                # =========================
                # WAIT FOR LIMIT FILL
                # =========================

                if trade.get('status') == "PENDING":

                    order_info = exchange.fetch_order(
                        trade['order_id'],
                        trade['symbol']
                    )

                    if order_info['status'] == "closed":

                        trade['status'] = "OPEN"

                        send_telegram(
                            f"✅ ORDER FILLED\n\n{trade['symbol']}"
                        )

                    else:

                        continue

                ticker = exchange.fetch_ticker(
                    trade['symbol']
                )

                price = ticker['last']

                # =========================
                # LONG
                # =========================

                if trade['side'] == "LONG":

                    if (
                        not trade['tp1_hit']
                        and price >= trade['tp1']
                    ):

                        trade['tp1_hit'] = True

                        send_telegram(
                            f"✅ TP1 HIT\n\n{trade['symbol']}\n\nMove SL -> BE"
                        )

                    if price >= trade['tp2']:

                        send_telegram(
                            f"🏆 WIN\n\n{trade['symbol']}"
                        )

                        update_signal_result(
                            signal_id,
                            "WIN"
                        )

                        del active_trades[signal_id]

                    elif price <= trade['sl']:

                        result = (
                            "BE"
                            if trade['tp1_hit']
                            else "LOSS"
                        )

                        send_telegram(
                            f"❌ {result}\n\n{trade['symbol']}"
                        )

                        update_signal_result(
                            signal_id,
                            result
                        )

                        del active_trades[signal_id]

                # =========================
                # SHORT
                # =========================

                elif trade['side'] == "SHORT":

                    if (
                        not trade['tp1_hit']
                        and price <= trade['tp1']
                    ):

                        trade['tp1_hit'] = True

                        send_telegram(
                            f"✅ TP1 HIT\n\n{trade['symbol']}\n\nMove SL -> BE"
                        )

                    if price <= trade['tp2']:

                        send_telegram(
                            f"🏆 WIN\n\n{trade['symbol']}"
                        )

                        update_signal_result(
                            signal_id,
                            "WIN"
                        )

                        del active_trades[signal_id]

                    elif price >= trade['sl']:

                        result = (
                            "BE"
                            if trade['tp1_hit']
                            else "LOSS"
                        )

                        send_telegram(
                            f"❌ {result}\n\n{trade['symbol']}"
                        )

                        update_signal_result(
                            signal_id,
                            result
                        )

                        del active_trades[signal_id]

            time.sleep(60)

        except Exception as e:

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

        except Exception as e:

            print(
                "Telegram polling error",
                flush=True
            )

            print(
                traceback.format_exc(),
                flush=True
            )

            time.sleep(10)

threading.Thread(
    target=telegram_polling,
    daemon=True
).start()

# =========================
# STARTUP
# =========================

threading.Thread(
    target=check_trades,
    daemon=True
).start()

send_telegram(
    "🚀 Railway Scanner Bot Online"
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

    except Exception as e:

        print(
            "MAIN LOOP ERROR",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

        time.sleep(30)
