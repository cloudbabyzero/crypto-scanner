"""
Technical indicators module for Crypto Scanner Bot.
Contains all indicator calculations and data fetching functions.
"""

import pandas as pd
import ta
from exchange_client import get_exchange, load_markets_if_needed

# =========================
# DATAFRAME
# =========================

def get_dataframe(symbol, timeframe):
    """Fetch OHLCV data and calculate all technical indicators."""
    
    # Ensure markets are loaded before fetching data
    load_markets_if_needed()
    
    exchange = get_exchange()
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
# MOMENTUM DETECTION
# =========================

def detect_momentum(symbol='BTC/USDT:USDT'):
    """Detect if a symbol is in Momentum regime (strong directional move, no pullback).

    Returns:
        dict with keys:
            is_momentum (bool)
            direction   ('LONG' | 'SHORT' | None)
            adx         (float)
            price_distance_pct (float)  -- % distance of close from EMA7
            consecutive_candles (int)
    """
    from config import (
        MOMENTUM_MIN_ADX,
        MOMENTUM_MIN_PRICE_DISTANCE,
        MOMENTUM_MIN_CANDLES,
    )

    df = get_dataframe(symbol, '4h')

    # Use confirmed (closed) candles only — skip last (forming) candle
    recent = df.iloc[-MOMENTUM_MIN_CANDLES - 1 : -1]
    last   = df.iloc[-2]

    adx = last['adx']

    # --- Condition 1: ADX strong enough ---
    if adx < MOMENTUM_MIN_ADX:
        return {
            'is_momentum': False,
            'direction': None,
            'adx': round(adx, 2),
            'price_distance_pct': 0,
            'consecutive_candles': 0,
        }

    # --- Condition 2: Price far from EMA7 ---
    price_distance_pct = abs(last['close'] - last['ema7']) / last['close'] * 100
    if price_distance_pct < MOMENTUM_MIN_PRICE_DISTANCE:
        return {
            'is_momentum': False,
            'direction': None,
            'adx': round(adx, 2),
            'price_distance_pct': round(price_distance_pct, 3),
            'consecutive_candles': 0,
        }

    # --- Condition 3: Consecutive candles in same direction ---
    bull_count = sum(1 for _, row in recent.iterrows() if row['close'] > row['open'])
    bear_count = sum(1 for _, row in recent.iterrows() if row['close'] < row['open'])

    if bull_count >= MOMENTUM_MIN_CANDLES and last['close'] > last['ema7']:
        direction = 'LONG'
        consecutive = bull_count
    elif bear_count >= MOMENTUM_MIN_CANDLES and last['close'] < last['ema7']:
        direction = 'SHORT'
        consecutive = bear_count
    else:
        return {
            'is_momentum': False,
            'direction': None,
            'adx': round(adx, 2),
            'price_distance_pct': round(price_distance_pct, 3),
            'consecutive_candles': max(bull_count, bear_count),
        }

    return {
        'is_momentum': True,
        'direction': direction,
        'adx': round(adx, 2),
        'price_distance_pct': round(price_distance_pct, 3),
        'consecutive_candles': consecutive,
    }


# =========================
# BTC TREND
# =========================

def get_btc_trend():
    """Determine BTC trend using multi-timeframe confirmation.
    
    Bug Fix: เดิมใช้แค่ EMA25 > EMA99 บน 4h ซึ่ง lagging มาก
    ทำให้ยังบอกว่า bearish ทั้งที่ราคาขึ้นไปแล้วหลายวัน
    
    แก้เป็น multi-confirmation:
    1. Price vs EMA25 (1h) — fast, responsive
    2. EMA7 vs EMA25 (1h) — medium
    3. EMA25 vs EMA99 (4h) — slow, structural
    
    ต้องผ่านอย่างน้อย 2/3 ถึงจะนับเป็น bullish/bearish
    ถ้าผ่านแค่ 1/3 = neutral
    """

    # 4h for structural trend
    df_4h = get_dataframe('BTC/USDT:USDT', '4h')
    btc_4h = df_4h.iloc[-2]

    # 1h for faster response
    df_1h = get_dataframe('BTC/USDT:USDT', '1h')
    btc_1h = df_1h.iloc[-2]

    # === Signals ===
    # 1. Structural: EMA25 vs EMA99 on 4h (lagging แต่ reliable)
    structural_bull = btc_4h['ema25'] > btc_4h['ema99']

    # 2. Medium: EMA7 vs EMA25 on 1h (faster)
    medium_bull = btc_1h['ema7'] > btc_1h['ema25']

    # 3. Fast: close vs EMA25 on 1h (most responsive)
    fast_bull = btc_1h['close'] > btc_1h['ema25']

    # 4. Momentum: RSI on 1h (> 50 = bullish momentum)
    rsi_bull = btc_1h['rsi'] > 50

    bull_count = sum([structural_bull, medium_bull, fast_bull, rsi_bull])

    if bull_count >= 3:
        return "bullish"
    elif bull_count <= 1:
        return "bearish"
    else:
        # bull_count == 2 = neutral (mixed signals)
        return "neutral"