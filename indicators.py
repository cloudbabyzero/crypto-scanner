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
    """Determine BTC trend based on EMA25 vs EMA99 on 4h timeframe."""
    
    btc_df = get_dataframe(
        'BTC/USDT:USDT',
        '4h'
    )

    btc = btc_df.iloc[-2]

    if btc['ema25'] > btc['ema99']:
        return "bullish"

    return "bearish"