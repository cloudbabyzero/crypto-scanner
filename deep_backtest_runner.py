import ccxt
import pandas as pd
import ta
import time

SYMBOL = 'SOL/USDT'
LIMIT = 1500

def fetch_data(tf='1m'):
    exchange = ccxt.binance()
    ohlcv = exchange.fetch_ohlcv(SYMBOL, tf, limit=LIMIT)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

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
    return df.dropna().reset_index(drop=True)

def simulate_trend_logic_15m(df_15m):
    # Trend 15m (SL 2.0, TP 3.0 => RR 1.5:1) with Trailing Stop
    trades = []
    for i in range(50, len(df_15m) - 10):
        m15 = df_15m.iloc[i]
        long_score = 0
        if m15['ema7'] > m15['ema25']: long_score += 35
        if m15['ema25'] > m15['ema99']: long_score += 25
        if m15['close'] > m15['ema7']: long_score += 10
        if m15['volume'] > m15['vol_avg'] * 1.3: long_score += 15
        if m15['adx'] > 25: long_score += 15
        
        if long_score >= 85:
            # Pullback entry at EMA7
            entry_price = m15['ema7']
            original_sl = entry_price - (m15['atr'] * 2.0)
            sl_price = original_sl
            tp_price = entry_price + (m15['atr'] * 3.0) # RR 1.5:1
            activation_price = entry_price + (m15['atr'] * 1.5)
            trailing_buffer = m15['atr'] * 1.0
            
            fill_status, trade_result = False, "PENDING"
            actual_rr = 0.0
            for j in range(i+1, min(i+40, len(df_15m))): # Lookahead 10 hours
                future_bar = df_15m.iloc[j]
                if not fill_status and future_bar['low'] <= entry_price:
                    fill_status = True
                if fill_status:
                    # Check SL First (Did it hit our stop this candle?)
                    if future_bar['low'] <= sl_price:
                        if sl_price > entry_price:
                            trade_result = "BREAKEVEN/TRAILED"
                            # Calculate profit obtained before getting stopped out
                            profit = sl_price - entry_price
                            risk = entry_price - original_sl
                            actual_rr = profit / risk
                        else:
                            trade_result = "LOSS"
                            actual_rr = -1.0
                        break
                        
                    # Check TP
                    elif future_bar['high'] >= tp_price:
                        trade_result = "WIN"
                        actual_rr = 1.5
                        break
                        
                    # Process Trailing Stop based on candle High
                    if future_bar['high'] >= activation_price:
                        breakeven = entry_price * 1.0015
                        proposed_sl = future_bar['high'] - trailing_buffer
                        new_sl = max(sl_price, proposed_sl, breakeven)
                        if new_sl > sl_price:
                            sl_price = new_sl
                            
            if trade_result == "PENDING" and fill_status:
                # Time expired, close at last price
                last_price = df_15m.iloc[min(i+40, len(df_15m))-1]['close']
                if last_price > entry_price:
                    trade_result = "BREAKEVEN/TRAILED"
                else:
                    trade_result = "LOSS"
                actual_rr = (last_price - entry_price) / (entry_price - original_sl)
                
            trades.append({'time': m15['timestamp'], 'filled': fill_status, 'result': trade_result, 'rr': actual_rr})
    return trades

def simulate_momentum_logic_3m(df_3m):
    # Momentum 3m (SL 1.2, TP 2.0 => RR 1.66:1)
    trades = []
    for i in range(50, len(df_3m) - 10):
        m3 = df_3m.iloc[i]
        volume_high = m3['volume'] > m3['vol_avg'] * 1.3
        if not volume_high or m3['adx'] < 20: continue
            
        long_score = 0
        if m3['ema7'] > m3['ema25']: long_score += 50
        if m3['ema25'] > m3['ema99']: long_score += 35
        long_score += 10
        if m3['adx'] > 25: long_score += 10
        if 40 <= m3['rsi'] <= 60: long_score += 15
            
        if m3['stoch_rsi'] > 80: long_score -= 30
        if abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100 > 1.0: long_score -= 20
        long_score = min(long_score, 100)
        
        if long_score >= 85:
            entry_price = m3['close']
            sl_price = entry_price - (m3['atr'] * 1.2)
            tp_price = entry_price + (m3['atr'] * 2.0)
            fill_status, trade_result = True, "PENDING"
            for j in range(i+1, min(i+120, len(df_3m))):
                future_bar = df_3m.iloc[j]
                if future_bar['low'] <= sl_price:
                    trade_result = "LOSS"
                    break
                elif future_bar['high'] >= tp_price:
                    trade_result = "WIN"
                    break
            trades.append({'time': m3['timestamp'], 'filled': fill_status, 'result': trade_result, 'rr': 1.66})
    return trades

def simulate_scalping_logic_3m(df_3m):
    # Scalping 3m (SL 1.5, TP 1.5 => RR 1:1)
    trades = []
    # approximate 15m EMAs using 3m (multiply windows by 5)
    df_3m['ema_15m_7'] = ta.trend.EMAIndicator(df_3m['close'], window=35).ema_indicator()
    df_3m['ema_15m_25'] = ta.trend.EMAIndicator(df_3m['close'], window=125).ema_indicator()
    
    for i in range(150, len(df_3m) - 10):
        m3 = df_3m.iloc[i]
        long_score = 0
        if m3['ema7'] > m3['ema25']: long_score += 50
        if m3['ema_15m_7'] > m3['ema_15m_25']: long_score += 30
        if 40 <= m3['rsi'] <= 60: long_score += 15
        if m3['volume'] > m3['vol_avg'] * 1.5: long_score += 10
            
        stoch_rsi = m3['stoch_rsi']
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100
        if stoch_rsi > 80: long_score -= 30
        if stretch_pct > 1.0: long_score -= 20
        
        if long_score >= 85:
            entry_price = m3['close'] # Market order
            sl_price = entry_price - (m3['atr'] * 1.5)
            tp_price = entry_price + (m3['atr'] * 1.5)
            fill_status, trade_result = True, "PENDING"
            for j in range(i+1, min(i+40, len(df_3m))): # Lookahead 2 hours
                future_bar = df_3m.iloc[j]
                if future_bar['low'] <= sl_price:
                    trade_result = "LOSS"
                    break
                elif future_bar['high'] >= tp_price:
                    trade_result = "WIN"
                    break
            trades.append({'time': m3['timestamp'], 'filled': fill_status, 'result': trade_result, 'rr': 1.0})
    return trades

def simulate_sideways_logic_3m(df_3m):
    # Sideways 3m (SL 1.2, TP BB_Mid)
    trades = []
    for i in range(50, len(df_3m) - 10):
        m3 = df_3m.iloc[i]
        
        long_trend_ok = m3['ema7'] >= m3['ema25'] * 0.99
        if (m3['rsi'] < 45 and m3['low'] <= m3['bb_lower'] and m3['adx'] < 28 and long_trend_ok):
            entry_price = m3['close']
            sl_price = entry_price - (m3['atr'] * 1.2)
            tp_price = m3['bb_mid']
            
            fill_status, trade_result = True, "PENDING"
            if tp_price <= entry_price: continue # Invalid TP
            
            expected_rr = (tp_price - entry_price) / (entry_price - sl_price)
            if expected_rr < 0.5: continue # Filter out trades with very poor RR
            
            for j in range(i+1, min(i+40, len(df_3m))):
                future_bar = df_3m.iloc[j]
                if future_bar['low'] <= sl_price:
                    trade_result = "LOSS"
                    break
                elif future_bar['high'] >= tp_price:
                    trade_result = "WIN"
                    break
            trades.append({'time': m3['timestamp'], 'filled': fill_status, 'result': trade_result, 'rr': expected_rr})
    return trades

def print_results(name, trades):
    total = len(trades)
    filled = sum(1 for t in trades if t['filled'])
    wins = sum(1 for t in trades if t['result'] == 'WIN')
    losses = sum(1 for t in trades if t['result'] == 'LOSS')
    breakeven = sum(1 for t in trades if t['result'] == 'BREAKEVEN/TRAILED')
    
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    safe_rate = ((wins + breakeven) / filled * 100) if filled > 0 else 0
    
    # Calculate Net PnL (Risk = 1 Unit)
    net_pnl = sum(t['rr'] for t in trades if t['filled'])
            
    print(f"\n--- {name} ---")
    print(f"Total Signals: {total}")
    print(f"Filled: {filled}")
    print(f"Wins: {wins} | Losses: {losses} | Trailed/Breakeven: {breakeven}")
    print(f"Win Rate (Strict): {win_rate:.2f}% | Win+Safe Rate: {safe_rate:.2f}%")
    print(f"Net PnL (Risk=1): {net_pnl:.2f} Units")

if __name__ == "__main__":
    print(f"Deep Backtesting {SYMBOL} across 4 Modes...")
    
    print("\nFetching 15m data...")
    df_15m = fetch_data('15m')
    df_15m = calculate_indicators(df_15m)
    
    print("Fetching 3m data...")
    df_3m = fetch_data('3m')
    df_3m = calculate_indicators(df_3m)
    
    print_results("1. TREND MODE (15m | SL 2.0 / TP 3.0 | RR 1.5:1)", simulate_trend_logic_15m(df_15m))
    print_results("2. MOMENTUM MODE (3m | SL 1.2 / TP 2.0 | RR 1.66:1)", simulate_momentum_logic_3m(df_3m))
    print_results("3. SCALPING MODE (3m | SL 1.5 / TP 1.5 | RR 1:1)", simulate_scalping_logic_3m(df_3m))
    print_results("4. SIDEWAYS MODE (3m | SL 1.2 / TP BB Mid)", simulate_sideways_logic_3m(df_3m))
