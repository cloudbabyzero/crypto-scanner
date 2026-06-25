import os
import pandas as pd
import ta
import time
os.environ['TELEGRAM_TOKEN'] = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
import ccxt

SYMBOL = 'NEAR/USDT'
LIMIT = 5000

def fetch_historical_data(symbol, tf, limit=5000):
    exchange = ccxt.binance()
    all_ohlcv = []
    since = exchange.milliseconds() - limit * exchange.parse_timeframe(tf) * 1000
    while len(all_ohlcv) < limit:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, tf, since=since, limit=1000)
            if not ohlcv: break
            since = ohlcv[-1][0] + 1
            all_ohlcv.extend(ohlcv)
            time.sleep(0.5)
        except: break
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

def get_market_regime(btc_1h_bar):
    btc_adx = btc_1h_bar['adx']
    btc_atr_percent = (btc_1h_bar['atr'] / btc_1h_bar['close']) * 100
    is_uptrend = btc_1h_bar['ema7'] > btc_1h_bar['ema25'] > btc_1h_bar['ema99']
    is_downtrend = btc_1h_bar['ema7'] < btc_1h_bar['ema25'] < btc_1h_bar['ema99']
    has_trend_alignment = is_uptrend or is_downtrend
    if btc_atr_percent >= 0.7: return "TREND"
    if btc_adx >= 25:
        if has_trend_alignment: return "TREND"
        elif btc_atr_percent >= 0.15: return "SCALPING"
    if (20 <= btc_adx < 35 and 0.15 <= btc_atr_percent <= 0.6): return "SCALPING"
    return "SIDEWAYS"

if __name__ == "__main__":
    df_3m = fetch_historical_data(SYMBOL, '3m', limit=3000)
    df_15m = fetch_historical_data(SYMBOL, '15m', limit=1000)
    btc_df_1h = fetch_historical_data('BTC/USDT', '1h', limit=1000)
    
    btc_df_1h = calculate_indicators(btc_df_1h)
    df_3m = calculate_indicators(df_3m)
    df_15m = calculate_indicators(df_15m)
    
    entry_types = ["ema25", "ema_mid"]
    rsi_maxs = [45, 38]
    sl_atrs = [1.5, 2.0, 2.5]
    tp_rrs = [1.0, 1.5]
    
    # Pre-calculate combinations
    combos = []
    for et in entry_types:
        for r in rsi_maxs:
            for s in sl_atrs:
                for t in tp_rrs:
                    combos.append({"et": et, "rsi": r, "sl": s, "tp": t, "trades": [], "wins": 0, "losses": 0, "rr": 0})
                    
    print(f"Testing {len(combos)} Combinations for Scalping Optimization...")
    
    for i in range(100, len(df_3m) - 40):
        m3 = df_3m.iloc[i]
        btc_1h = btc_df_1h[btc_df_1h['timestamp'] <= m3['timestamp']].iloc[-1]
        regime = get_market_regime(btc_1h)
        if regime != "SCALPING": continue

        m15_slice = df_15m[df_15m['timestamp'] <= m3['timestamp']]
        if m15_slice.empty: continue
        m15 = m15_slice.iloc[-1]
        
        # Base scores
        l_base, s_base = 0, 0
        if m3['ema7'] > m3['ema25']: l_base += 50
        if m15['ema7'] > m15['ema25']: l_base += 30
        if m3['volume'] > m3['vol_avg'] * 1.5: l_base += 10
        
        if m3['ema7'] < m3['ema25']: s_base += 50
        if m15['ema7'] < m15['ema25']: s_base += 30
        if m3['volume'] > m3['vol_avg'] * 1.5: s_base += 10
        
        stoch_rsi = m3.get('stoch_rsi', 50)
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100
        if stoch_rsi > 80: l_base -= 30
        if m3['close'] > m3['ema25'] and stretch_pct > 1.0: l_base -= 20
        if stoch_rsi < 20: s_base -= 30
        if m3['close'] < m3['ema25'] and stretch_pct > 1.0: s_base -= 20
        
        for c in combos:
            l_score, s_score = l_base, s_base
            if m3['rsi'] <= c['rsi']: l_score += 25
            if m3['rsi'] >= (100 - c['rsi']): s_score += 25
            
            l_score = min(l_score, 100); s_score = min(s_score, 100)
            score = max(l_score, s_score)
            
            if score >= 70 and m3['adx'] > 20:
                side = "LONG" if l_score > s_score else "SHORT"
                if c['et'] == "ema25": entry_price = m3['ema25']
                else: entry_price = (m3['ema25'] + m3['ema99']) / 2
                
                sl_dist = m3['atr'] * c['sl']
                sl_price = entry_price - sl_dist if side == "LONG" else entry_price + sl_dist
                tp_price = entry_price + (sl_dist * c['tp']) if side == "LONG" else entry_price - (sl_dist * c['tp'])
                
                fill_status, result = False, "PENDING"
                for j in range(i+1, i+40):
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
                
                if fill_status and result != "PENDING":
                    c['trades'].append(result)
                    if result == "WIN":
                        c['wins'] += 1
                        c['rr'] += c['tp']
                    else:
                        c['losses'] += 1
                        c['rr'] -= 1

    results = []
    for c in combos:
        total = c['wins'] + c['losses']
        if total > 0:
            wr = c['wins'] / total * 100
            res_str = f"Entry: {c['et']:8} | RSI<={c['rsi']} | SL={c['sl']} ATR | TP={c['tp']} RR -> WR: {wr:.2f}% | PnL: {c['rr']:.2f} | Fills: {total}"
            results.append({"pnl": c['rr'], "wr": wr, "str": res_str})
            
    results.sort(key=lambda x: x['pnl'], reverse=True)
    print("\n--- TOP 5 CONFIGS BY PNL ---")
    for r in results[:5]: print(r['str'])
        
    results.sort(key=lambda x: x['wr'], reverse=True)
    print("\n--- TOP 5 CONFIGS BY WINRATE ---")
    for r in results[:5]: print(r['str'])
