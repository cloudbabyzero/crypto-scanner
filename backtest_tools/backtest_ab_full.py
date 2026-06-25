import os
import pandas as pd
import ta
import time
from datetime import datetime, timedelta
os.environ['TELEGRAM_TOKEN'] = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
import config
from config import STRATEGY_CONFIG
import ccxt

SYMBOL = 'NEAR/USDT'
LIMIT = 5000

def fetch_historical_data(symbol, tf, limit=5000):
    exchange = ccxt.binance()
    all_ohlcv = []
    since = exchange.milliseconds() - limit * exchange.parse_timeframe(tf) * 1000
    
    print(f"Fetching {limit} candles for {symbol} ({tf})...")
    while len(all_ohlcv) < limit:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, tf, since=since, limit=1000)
            if not ohlcv:
                break
            since = ohlcv[-1][0] + 1
            all_ohlcv.extend(ohlcv)
            time.sleep(0.5)
        except Exception as e:
            print(f"Error fetching data: {e}")
            break
            
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df.tail(limit)

def calculate_indicators(df):
    df['ema7'] = ta.trend.EMAIndicator(df['close'], window=7).ema_indicator()
    df['ema25'] = ta.trend.EMAIndicator(df['close'], window=25).ema_indicator()
    df['ema99'] = ta.trend.EMAIndicator(df['close'], window=99).ema_indicator()
    
    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    df['adx'] = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx()
    df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    
    stoch_rsi_ind = ta.momentum.StochRSIIndicator(close=df['close'], window=14, smooth1=3, smooth2=3)
    df['stoch_rsi'] = stoch_rsi_ind.stochrsi() * 100
    
    bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
    df['bb_mid'] = bb.bollinger_mavg()
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    
    df['vol_avg'] = df['volume'].rolling(window=20).mean()
    return df

def _get_btc_trend(btc_df_4h, timestamp):
    btc_trend = "neutral"
    if btc_df_4h is not None:
        btc_bar = btc_df_4h[btc_df_4h['timestamp'] <= timestamp].iloc[-1]
        if btc_bar['ema25'] > btc_bar['ema99']: btc_trend = "bullish"
        elif btc_bar['ema25'] < btc_bar['ema99']: btc_trend = "bearish"
    return btc_trend

def simulate_trend(df_15m, btc_df_4h=None, use_btc_filter=True):
    trades = []
    for i in range(100, len(df_15m) - 20):
        m15 = df_15m.iloc[i]
        long_score, short_score = 0, 0
        vol_high = m15['volume'] > m15['vol_avg'] * 1.3
        adx_val = m15['adx']
        
        if m15['ema7'] > m15['ema25']: long_score += 35
        if m15['ema25'] > m15['ema99']: long_score += 25
        if m15['close'] > m15['ema7']: long_score += 10
        if vol_high: long_score += 15
        if adx_val > STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX']: long_score += 15

        if m15['ema7'] < m15['ema25']: short_score += 35
        if m15['ema25'] < m15['ema99']: short_score += 25
        if m15['close'] < m15['ema7']: short_score += 10
        if vol_high: short_score += 15
        if adx_val > STRATEGY_CONFIG['TRENDING']['FILTERS']['MIN_ADX']: short_score += 15
        
        btc_trend = _get_btc_trend(btc_df_4h, m15['timestamp']) if use_btc_filter else "neutral"
        if btc_trend == "bullish": short_score = 0
        elif btc_trend == "bearish": long_score = 0
        elif btc_trend == "neutral" and use_btc_filter:
            long_score -= 20; short_score -= 20
        
        long_score = min(long_score, 100); short_score = min(short_score, 100)
        score = max(long_score, short_score)
        
        min_score = STRATEGY_CONFIG['TRENDING']['MIN_SCORE']
        grade = "C"
        if score >= min_score + 10 and adx_val > 25: grade = "A+"
        elif score >= min_score: grade = "A"
        
        if grade in ["A", "A+"]:
            side = "LONG" if long_score > short_score else "SHORT"
            entry_price = m15['ema25']
            if side == "LONG" and m15['close'] < entry_price: continue
            if side == "SHORT" and m15['close'] > entry_price: continue
            sl_dist = m15['atr'] * STRATEGY_CONFIG['TRENDING']['SL_ATR_MULT']
            tp_rr = STRATEGY_CONFIG['TRENDING']['TP_RR']
            sl_price = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
            tp_price = entry_price + (sl_dist * tp_rr) if side == "LONG" else entry_price - (sl_dist * tp_rr)
            
            fill_status, result = False, "PENDING"
            for j in range(i+1, min(i+40, len(df_15m))):
                f_bar = df_15m.iloc[j]
                if not fill_status:
                    if side == "LONG" and f_bar['low'] <= entry_price: fill_status = True
                    if side == "SHORT" and f_bar['high'] >= entry_price: fill_status = True
                if fill_status:
                    if side == "LONG":
                        if f_bar['low'] <= sl_price: result = "LOSS"; break
                        elif f_bar['high'] >= tp_price: result = "WIN"; break
                    else:
                        if f_bar['high'] >= sl_price: result = "LOSS"; break
                        elif f_bar['low'] <= tp_price: result = "WIN"; break
            trades.append({"mode": "TREND", "grade": grade, "side": side, "filled": fill_status, "result": result, "rr": tp_rr if result == "WIN" else -1 if result == "LOSS" else 0})
    return trades

def simulate_momentum(df_3m, df_15m, btc_df_4h=None, use_btc_filter=True):
    trades = []
    for i in range(100, len(df_3m) - 20):
        m3 = df_3m.iloc[i]
        m15_slice = df_15m[df_15m['timestamp'] <= m3['timestamp']]
        if m15_slice.empty: continue
        m15 = m15_slice.iloc[-1]
        
        long_score, short_score = 0, 0
        if m3['ema7'] > m3['ema25']: long_score += 50
        if m3['ema25'] > m3['ema99']: long_score += 35
        if m3['adx'] > 25: long_score += 10
        if m3['rsi'] <= 45: long_score += 25

        if m3['ema7'] < m3['ema25']: short_score += 50
        if m3['ema25'] < m3['ema99']: short_score += 35
        if m3['adx'] > 25: short_score += 10
        if m3['rsi'] >= 55: short_score += 25
        
        stoch_rsi = m3.get('stoch_rsi', 50)
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100

        if stoch_rsi > 80: long_score -= 30
        if m3['close'] > m3['ema25'] and stretch_pct > 1.0: long_score -= 20
        if stoch_rsi < 20: short_score -= 30
        if m3['close'] < m3['ema25'] and stretch_pct > 1.0: short_score -= 20
        
        vol_high = m3['volume'] > m3['vol_avg'] * 1.3
        if vol_high:
            long_score += 10; short_score += 10
            
        btc_trend = _get_btc_trend(btc_df_4h, m3['timestamp']) if use_btc_filter else "neutral"
        if btc_trend == "bullish": short_score = 0
        elif btc_trend == "bearish": long_score = 0
        elif btc_trend == "neutral" and use_btc_filter:
            long_score -= 20; short_score -= 20
                
        long_score = min(long_score, 100); short_score = min(short_score, 100)
        score = max(long_score, short_score)
        
        min_score = STRATEGY_CONFIG['MOMENTUM']['MIN_SCORE']
        grade = "C"
        adx_val = m3['adx']
        if score >= min_score + 10 and adx_val > 35 and vol_high: grade = "A+"
        elif score >= min_score and adx_val > 25: grade = "A"
        
        if grade in ["A", "A+"]:
            side = "LONG" if long_score > short_score else "SHORT"
            entry_price = m3['ema7']
            sl_dist = m3['atr'] * STRATEGY_CONFIG['MOMENTUM']['SL_ATR_MULT']
            tp_rr = STRATEGY_CONFIG['MOMENTUM']['TP_RR']
            sl_price = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
            tp_price = entry_price + (sl_dist * tp_rr) if side == "LONG" else entry_price - (sl_dist * tp_rr)
            
            fill_status, result = False, "PENDING"
            for j in range(i+1, min(i+40, len(df_3m))):
                f_bar = df_3m.iloc[j]
                if not fill_status:
                    if side == "LONG" and f_bar['low'] <= entry_price: fill_status = True
                    if side == "SHORT" and f_bar['high'] >= entry_price: fill_status = True
                if fill_status:
                    if side == "LONG":
                        if f_bar['low'] <= sl_price: result = "LOSS"; break
                        elif f_bar['high'] >= tp_price: result = "WIN"; break
                    else:
                        if f_bar['high'] >= sl_price: result = "LOSS"; break
                        elif f_bar['low'] <= tp_price: result = "WIN"; break
            trades.append({"mode": "MOMENTUM", "grade": grade, "side": side, "filled": fill_status, "result": result, "rr": tp_rr if result == "WIN" else -1 if result == "LOSS" else 0})
    return trades

def simulate_scalping(df_3m, df_15m, btc_df_4h=None, use_btc_filter=True):
    trades = []
    for i in range(100, len(df_3m) - 20):
        m3 = df_3m.iloc[i]
        m15_slice = df_15m[df_15m['timestamp'] <= m3['timestamp']]
        if m15_slice.empty: continue
        m15 = m15_slice.iloc[-1]
        
        long_score, short_score = 0, 0
        if m3['ema7'] > m3['ema25']: long_score += 50
        if m15['ema7'] > m15['ema25']: long_score += 30
        if m3['rsi'] <= 45: long_score += 25
        if m3['volume'] > m3['vol_avg'] * 1.5: long_score += 10

        if m3['ema7'] < m3['ema25']: short_score += 50
        if m15['ema7'] < m15['ema25']: short_score += 30
        if m3['rsi'] >= 55: short_score += 25
        if m3['volume'] > m3['vol_avg'] * 1.5: short_score += 10

        stoch_rsi = m3.get('stoch_rsi', 50)
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100

        if stoch_rsi > 80: long_score -= 30
        if m3['close'] > m3['ema25'] and stretch_pct > 1.0: long_score -= 20
        if stoch_rsi < 20: short_score -= 30
        if m3['close'] < m3['ema25'] and stretch_pct > 1.0: short_score -= 20
        
        btc_trend = _get_btc_trend(btc_df_4h, m3['timestamp']) if use_btc_filter else "neutral"
        if btc_trend == "bullish": short_score = 0
        elif btc_trend == "bearish": long_score = 0
        elif btc_trend == "neutral" and use_btc_filter:
            long_score -= 20; short_score -= 20
                
        long_score = min(long_score, 100); short_score = min(short_score, 100)
        score = max(long_score, short_score)
        
        min_score = STRATEGY_CONFIG['SCALPING']['MIN_SCORE']
        grade = "C"
        adx_val = m3['adx']
        if score >= min_score + 10 and adx_val > 30: grade = "A+"
        elif score >= min_score and adx_val > 20: grade = "A"
        
        if grade in ["A", "A+"]:
            side = "LONG" if long_score > short_score else "SHORT"
            entry_price = m3['ema25']  # Scalping typically buys the dip
            sl_dist = m3['atr'] * STRATEGY_CONFIG['SCALPING']['SL_ATR_MULT']
            tp_rr = STRATEGY_CONFIG['SCALPING']['TP_RR']
            sl_price = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
            tp_price = entry_price + (sl_dist * tp_rr) if side == "LONG" else entry_price - (sl_dist * tp_rr)
            
            fill_status, result = False, "PENDING"
            for j in range(i+1, min(i+30, len(df_3m))):
                f_bar = df_3m.iloc[j]
                if not fill_status:
                    if side == "LONG" and f_bar['low'] <= entry_price: fill_status = True
                    if side == "SHORT" and f_bar['high'] >= entry_price: fill_status = True
                if fill_status:
                    if side == "LONG":
                        if f_bar['low'] <= sl_price: result = "LOSS"; break
                        elif f_bar['high'] >= tp_price: result = "WIN"; break
                    else:
                        if f_bar['high'] >= sl_price: result = "LOSS"; break
                        elif f_bar['low'] <= tp_price: result = "WIN"; break
            trades.append({"mode": "SCALPING", "grade": grade, "side": side, "filled": fill_status, "result": result, "rr": tp_rr if result == "WIN" else -1 if result == "LOSS" else 0})
    return trades

def simulate_sideways(df_3m, btc_df_4h=None, use_btc_filter=True):
    trades = []
    for i in range(100, len(df_3m) - 20):
        m3 = df_3m.iloc[i]
        
        long_trend_ok = m3['ema7'] >= m3['ema25'] * 0.99
        long_condition = (m3['rsi'] < 45 and m3['low'] <= m3['bb_lower'] and m3['adx'] <= STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX'] and long_trend_ok)
        short_trend_ok = m3['ema7'] <= m3['ema25'] * 1.01
        short_condition = (m3['rsi'] > 55 and m3['high'] >= m3['bb_upper'] and m3['adx'] <= STRATEGY_CONFIG['SIDEWAYS']['FILTERS']['MAX_ADX'] and short_trend_ok)
        
        if not long_condition and not short_condition: continue
        
        if long_condition and short_condition: side = "LONG" if abs(40 - m3['rsi']) > abs(60 - m3['rsi']) else "SHORT"
        elif long_condition: side = "LONG"
        else: side = "SHORT"
        
        base_score = 20 if (side == "LONG" and m3['low'] <= m3['bb_lower']) or (side == "SHORT" and m3['high'] >= m3['bb_upper']) else 0
        rsi = m3['rsi']
        rsi_score = max(0, min(60, int((40 - rsi) * 4))) if side == "LONG" else max(0, min(60, int((rsi - 60) * 4)))
        
        # approximate rr
        sl_dist = m3['atr'] * 2.0
        tp_dist = abs(m3['bb_mid'] - m3['close'])
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        rr_score = max(0, min(40, int((rr - 1.0) * 20)))
        
        score = base_score + rsi_score + rr_score
        
        btc_trend = _get_btc_trend(btc_df_4h, m3['timestamp']) if use_btc_filter else "neutral"
        if btc_trend == "bullish" and side == "SHORT": score = 0
        elif btc_trend == "bearish" and side == "LONG": score = 0
        elif btc_trend == "neutral" and use_btc_filter: score -= 20
        
        min_score = STRATEGY_CONFIG['SIDEWAYS']['MIN_SCORE']
        grade = "C"
        adx_val = m3['adx']
        if score >= min_score + 10 and adx_val < 20: grade = "A+"
        elif score >= min_score: grade = "A"
        
        if grade in ["A", "A+"]:
            entry_price = m3['close']
            tp_rr = STRATEGY_CONFIG['SIDEWAYS']['TP_RR']
            sl_price = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
            tp_price = entry_price + tp_dist if side == "LONG" else entry_price - tp_dist
            # Normalize rr for logging
            tp_rr = rr
            
            fill_status, result = True, "PENDING"  # Market execution
            for j in range(i+1, min(i+40, len(df_3m))):
                f_bar = df_3m.iloc[j]
                if side == "LONG":
                    if f_bar['low'] <= sl_price: result = "LOSS"; break
                    elif f_bar['high'] >= tp_price: result = "WIN"; break
                else:
                    if f_bar['high'] >= sl_price: result = "LOSS"; break
                    elif f_bar['low'] <= tp_price: result = "WIN"; break
            trades.append({"mode": "SIDEWAYS", "grade": grade, "side": side, "filled": fill_status, "result": result, "rr": tp_rr if result == "WIN" else -1 if result == "LOSS" else 0})
    return trades

def print_summary(test_name, all_trades):
    print(f"\n{'='*40}")
    print(f"RESULTS: {test_name}")
    print(f"{'='*40}")
    
    total_signals = len(all_trades)
    filled_trades = [t for t in all_trades if t['filled']]
    total_filled = len(filled_trades)
    
    wins = len([t for t in filled_trades if t['result'] == "WIN"])
    losses = len([t for t in filled_trades if t['result'] == "LOSS"])
    winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    total_rr = sum(t['rr'] for t in filled_trades)
    
    print(f"Total A/A+ Signals: {total_signals}")
    print(f"Filled Orders:      {total_filled}")
    print(f"Wins: {wins} | Losses: {losses}")
    print(f"Winrate:            {winrate:.2f}%")
    print(f"Net PnL (RR):       {total_rr:.2f} RR")
    
    print("\n--- Breakdown by Mode ---")
    modes = ['TREND', 'MOMENTUM', 'SCALPING', 'SIDEWAYS']
    for m in modes:
        m_trades = [t for t in filled_trades if t['mode'] == m]
        m_wins = len([t for t in m_trades if t['result'] == "WIN"])
        m_loss = len([t for t in m_trades if t['result'] == "LOSS"])
        m_wr = (m_wins / (m_wins + m_loss) * 100) if (m_wins + m_loss) > 0 else 0
        m_rr = sum(t['rr'] for t in m_trades)
        print(f"[{m}] Fills: {len(m_trades)} | Winrate: {m_wr:.2f}% | PnL: {m_rr:.2f} RR")


if __name__ == "__main__":
    print(f"Fetching data for A/B Test...")
    df_3m = fetch_historical_data(SYMBOL, '3m', limit=3000)
    df_15m = fetch_historical_data(SYMBOL, '15m', limit=1000)
    
    if df_3m is None or df_3m.empty or df_15m is None or df_15m.empty:
        print("Data fetch failed. Exiting.")
        exit(1)
        
    btc_df_4h = fetch_historical_data('BTC/USDT', '4h', limit=300)
    
    if btc_df_4h is not None and not btc_df_4h.empty:
        btc_df_4h['ema25'] = ta.trend.EMAIndicator(btc_df_4h['close'], window=25).ema_indicator()
        btc_df_4h['ema99'] = ta.trend.EMAIndicator(btc_df_4h['close'], window=99).ema_indicator()
        
    df_3m = calculate_indicators(df_3m)
    df_15m = calculate_indicators(df_15m)
    
    print("\nRunning Test A (No BTC Filter)...")
    t_trend_a = simulate_trend(df_15m, btc_df_4h, False)
    t_mom_a = simulate_momentum(df_3m, df_15m, btc_df_4h, False)
    t_scalp_a = simulate_scalping(df_3m, df_15m, btc_df_4h, False)
    t_side_a = simulate_sideways(df_3m, btc_df_4h, False)
    all_A = t_trend_a + t_mom_a + t_scalp_a + t_side_a
    
    print("Running Test B (With BTC Filter)...")
    t_trend_b = simulate_trend(df_15m, btc_df_4h, True)
    t_mom_b = simulate_momentum(df_3m, df_15m, btc_df_4h, True)
    t_scalp_b = simulate_scalping(df_3m, df_15m, btc_df_4h, True)
    t_side_b = simulate_sideways(df_3m, btc_df_4h, True)
    all_B = t_trend_b + t_mom_b + t_scalp_b + t_side_b
    
    print_summary("TEST A: WITHOUT BTC FILTER", all_A)
    print_summary("TEST B: WITH STRICT BTC FILTER", all_B)
