import ccxt
import pandas as pd
import ta
import time

def fetch_historical_data(symbol, tf, limit=100):
    exchange = ccxt.binance()
    ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def calculate_indicators(df):
    df['ema7'] = ta.trend.EMAIndicator(df['close'], window=7).ema_indicator()
    df['ema25'] = ta.trend.EMAIndicator(df['close'], window=25).ema_indicator()
    df['ema99'] = ta.trend.EMAIndicator(df['close'], window=99).ema_indicator()
    df['adx'] = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx()
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    return df

symbols = ['BTC/USDT', 'SUI/USDT']

print("=== LIVE DATA CHECK ===")
for sym in symbols:
    print(f"\n--- {sym} ---")
    df_1h = fetch_historical_data(sym, '1h')
    df_15m = fetch_historical_data(sym, '15m')
    
    df_1h = calculate_indicators(df_1h)
    df_15m = calculate_indicators(df_15m)
    
    h1 = df_1h.iloc[-2] # Last closed candle
    m15 = df_15m.iloc[-2] # Last closed candle
    
    # 1H Data
    h1_adx = h1['adx']
    h1_atr_pct = (h1['atr'] / h1['close']) * 100
    is_uptrend = h1['ema7'] > h1['ema25'] > h1['ema99']
    is_downtrend = h1['ema7'] < h1['ema25'] < h1['ema99']
    
    print(f"[1H MACRO CHART]")
    print(f"ADX: {h1_adx:.2f} | ATR%: {h1_atr_pct:.2f}%")
    print(f"Uptrend: {is_uptrend} | Downtrend: {is_downtrend}")
    
    # 15M Data
    m15_adx = m15['adx']
    m15_atr_pct = (m15['atr'] / m15['close']) * 100
    print(f"[15M MICRO CHART]")
    print(f"ADX: {m15_adx:.2f} | ATR%: {m15_atr_pct:.2f}%")
