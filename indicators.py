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

    df['rsi_7'] = ta.momentum.rsi(
        df['close'],
        window=7
    )



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

    # =========================
    # VWAP
    # =========================

    vwap_ind = ta.volume.VolumeWeightedAveragePrice(
        high=df['high'],
        low=df['low'],
        close=df['close'],
        volume=df['volume'],
        window=14
    )
    df['vwap'] = vwap_ind.volume_weighted_average_price()

    # =========================
    # STOCH RSI
    # =========================

    stoch_rsi_ind = ta.momentum.StochRSIIndicator(
        close=df['close'],
        window=14,
        smooth1=3,
        smooth2=3
    )
    df['stoch_rsi'] = stoch_rsi_ind.stochrsi() * 100

    return df

# =========================
# PER-COIN REGIME DETECTION
# =========================

def detect_symbol_regime(df_1h):
    """Detect local market regime for a specific symbol based on its 1h dataframe."""
    if df_1h is None or len(df_1h) < 2:
        return "PAUSE"
        
    h1 = df_1h.iloc[-2]
    adx = h1['adx']
    atr_pct = (h1['atr'] / h1['close']) * 100
    
    is_uptrend = h1['ema7'] > h1['ema25'] > h1['ema99']
    is_downtrend = h1['ema7'] < h1['ema25'] < h1['ema99']
    has_trend_alignment = is_uptrend or is_downtrend
    
    # 1. Momentum: very strong push, far from EMA
    price_distance_pct = abs(h1['close'] - h1['ema7']) / h1['close'] * 100
    if adx > 30 and price_distance_pct >= 1.5:
        return "MOMENTUM"
        
    # 2. Trending: strong trend, EMA aligned
    if adx >= 20:
        if has_trend_alignment:
            return "TRENDING"
        elif atr_pct > 0.15:
            # If ADX is high but EMAs aren't perfectly aligned, fallback to SCALPING if volatile
            return "SCALPING"
            
    # 3. Sideways: low ADX
    if adx < 25:
        return "SIDEWAYS"
        
    # 4. Scalping: high volatility but no clear trend/momentum
    if atr_pct > 0.15:
        return "SCALPING"
        
    return "PAUSE"

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
        STRATEGY_CONFIG,
        MOMENTUM_MIN_PRICE_DISTANCE,
        MOMENTUM_MIN_CANDLES,
    )
    MOMENTUM_MIN_ADX = STRATEGY_CONFIG['MOMENTUM']['FILTERS']['MIN_ADX']

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
    """Determine BTC trend using Hybrid Macro-Micro scoring system.

    Scoring System (Max 6 points):
    - Macro (4H): EMA25 > EMA99 → +2 pts  (structural)
    - Macro (4H): EMA7  > EMA25 → +1 pt   (4h momentum)
    - Micro (1H): EMA7  > EMA25 → +1 pt   (1h momentum)
    - Micro (1H): Close > EMA25 → +1 pt   (price vs 1h structure)
    - Micro (1H): EMA25 > EMA99 → +1 pt   (1h structural alignment)

    FIX: was 4-point system, "neutral" zone was too wide.
    Old system: 4h macro still bullish but 1h fully bearish → score=2 → neutral
    → allowed SHORT entries during bearish 1h when 4h EMA25 > EMA99 barely
    New 6-point system catches reversal faster: need score>=4 for bullish,
    <=2 for bearish. Full 1h bearish alignment reduces score to 2 → bearish
    even if 4h EMA25 still above EMA99.

    Result:
    - Score >= 4 → "bullish"
    - Score <= 2 → "bearish"
    - Score == 3 → "neutral"
    """

    df_4h = get_dataframe('BTC/USDT:USDT', '4h')
    btc_4h = df_4h.iloc[-2]

    df_1h = get_dataframe('BTC/USDT:USDT', '1h')
    btc_1h = df_1h.iloc[-2]

    score = 0

    # === Macro (4H) — 3 pts ===
    if btc_4h['ema25'] > btc_4h['ema99']:
        score += 2  # Structural bullish
    if btc_4h['ema7'] > btc_4h['ema25']:
        score += 1  # 4h short-term momentum

    # === Micro (1H) — 3 pts ===
    if btc_1h['ema7'] > btc_1h['ema25']:
        score += 1  # 1h momentum
    if btc_1h['close'] > btc_1h['ema25']:
        score += 1  # Price above 1h structure
    if btc_1h['ema25'] > btc_1h['ema99']:
        score += 1  # 1h structural alignment

    if score >= 4:
        return "bullish"
    elif score <= 2:
        return "bearish"
    else:
        return "neutral"