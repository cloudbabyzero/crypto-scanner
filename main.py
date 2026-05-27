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
    'XRP/USDT',
    'HYPE/USDT',
    'ZEC/USDT',
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
    
    def get_btc_trend():

        btc_df = get_dataframe('BTC/USDT', '4h')

        btc = btc_df.iloc[-2]

    if btc['ema25'] > btc['ema99']:

        return "bullish"

    return "bearish"

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

        df_15m = get_dataframe(symbol, '1d')
        
        df_4h = get_dataframe(symbol, '4h')

        df_1h = get_dataframe(symbol, '1h')

        df_15m = get_dataframe(symbol, '15m')

        # =========================
        # CLOSED CANDLES
        # =========================

        d1 = df_1d.iloc[-2]
        
        h4 = df_4h.iloc[-2]

        h1 = df_1h.iloc[-2]

        m15 = df_15m.iloc[-2]

        # =========================
        # SCORE
        # =========================

        long_score = 0

        short_score = 0

        btc_trend = get_btc_trend()
        
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
        # MACD
        # =========================

        if h1['macd'] > h1['macd_signal']:

            long_score += 15

        else:

            short_score += 15

        # =========================
        # RSI
        # =========================

        if m15['rsi'] > 55:

            long_score += 15

        elif m15['rsi'] < 45:

            short_score += 15
        
        # =========================
        # ADX Trend Strength
        # =========================


        if m15['adx'] > 20:

            long_score += 10

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
        # LONG SIGNAL
        # =========================

        if long_score >= 70 and btc_trend == "bullish":

            entry = round(
                m15['close'],
                4
            )

            atr = m15['atr']

            # กัน wick
            sl = round(
                entry - atr * 1.8,
                4
            )

            tp = round(
                entry + atr * 3.6,
                4
            )

            rr = round(
                (tp - entry)
                /
                (entry - sl),
                2
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
{"HIGH" if volume_high else "NORMAL"}
"""

            print(message, flush=True)

            send_telegram(message)

            last_alert[symbol] = now

        # =========================
        # SHORT SIGNAL
        # =========================

        elif short_score >= 70 and btc_trend == "bearish":

            entry = round(
                m15['close'],
                4
            )

            atr = m15['atr']

            # กัน wick
            sl = round(
                entry + atr * 1.8,
                4
            )

            tp = round(
                entry - atr * 3.6,
                4
            )

            rr = round(
                (entry - tp)
                /
                (sl - entry),
                2
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
{"HIGH" if volume_high else "NORMAL"}
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
