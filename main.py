import ccxt
import pandas as pd
import ta
import requests
import time
import os
import traceback

# =========================
# CONFIG
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 300
COOLDOWN = 3600

symbols = [
    'BTC/USDT',
    'ETH/USDT',
    'DOGE/USDT',
    'SOL/USDT',
    'XRP/USDT'
    'HYPE/USDT'
    'ZEC/USDT'
    'INJ/USDT'
]

last_alert = {}

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

        print("Telegram Error:", e, flush=True)

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

        df_4h = get_dataframe(symbol, '4h')

        df_1h = get_dataframe(symbol, '1h')

        df_15m = get_dataframe(symbol, '15m')

        # ใช้แท่งปิดแล้ว
        h4 = df_4h.iloc[-2]

        h1 = df_1h.iloc[-2]

        m15 = df_15m.iloc[-2]

        # =========================
        # SCORE
        # =========================

        long_score = 0

        short_score = 0

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
        # MACD
        # =========================

        if h1['macd'] > h1['macd_signal']:

            long_score += 15

        else:

            short_score += 15

        # =========================
        # RSI
        # =========================

        if m15['rsi'] > 50:

            long_score += 15

        else:

            short_score += 15

        # =========================
        # VOLUME
        # =========================

        if m15['volume'] > m15['vol_avg'] * 1.3:

            long_score += 15

            short_score += 15

        # =========================
        # BOLLINGER
        # =========================

        # กันไล่แท่งแรงเกิน

         upper_distance = (
         df_15m['close'].iloc[-2]
         /
         df_15m['bb_upper'].iloc[-2]
         )

         lower_distance = (
         df_15m['close'].iloc[-2]
         /
         df_15m['bb_lower'].iloc[-2]
         )

# LONG
         if (
            m15['close'] > m15['bb_mid']
            and upper_distance < 0.998
            ):

              long_score += 10

# SHORT
          elif (
             m15['close'] < m15['bb_mid']
             and lower_distance > 1.002
             ):

            short_score += 10
          

        # =========================
        # LONG
        # =========================

        if long_score >= 70:

            entry = round(
                m15['close'],
                4
            )

# ATR
           atr = m15['atr']

# หา low ต่ำสุด 5 แท่งล่าสุด
        recent_low =df_15m['low'].tail(5).min()

# SL กัน wick
          sl = round(
          recent_low - (atr * 0.25),
                 4
            )

# TP RR 1:2
          risk = entry - sl

           tp = round(
           entry + (risk * 2),
               4
                )

            message = f"""
🚀 LONG SIGNAL

{symbol}

Score:
{long_score}/100

Entry:
{entry}

SL:
{sl}

TP:
{tp}

RR:
1:{rr}

RSI:
{round(m15['rsi'],2)}

Volume:
HIGH
"""

            print(message, flush=True)

            send_telegram(message)

            last_alert[symbol] = now

        # =========================
        # SHORT
        # =========================

        elif short_score >= 70:

            entry = round(
                m15['close'],
                4
            )

# ATR
atr = m15['atr']

# หา high สูงสุด 5 แท่งล่าสุด
recent_high = df_15m['high'].tail(5).max()

# SL กัน wick
sl = round(
    recent_high + (atr * 0.25),
    4
)

# TP RR 1:2
risk = sl - entry

tp = round(
    entry - (risk * 2),
    4
)

            message = f"""
🔻 SHORT SIGNAL

{symbol}

Score:
{short_score}/100

Entry:
{entry}

SL:
{sl}

TP:
{tp}

RR:
1:{rr}

RSI:
{round(m15['rsi'],2)}

Volume:
HIGH
"""

            print(message, flush=True)

            send_telegram(message)

            last_alert[symbol] = now

    except Exception as e:

        print(f"{symbol} ERROR", flush=True)

        print(traceback.format_exc(), flush=True)

# =========================
# STARTUP
# =========================

send_telegram("🚀 Railway Scanner Bot Online")

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

        time.sleep(SCAN_INTERVAL)

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
