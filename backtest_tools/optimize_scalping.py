import os
import pandas as pd
import ta
import time
os.environ['TELEGRAM_TOKEN'] = '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'
from config import STRATEGY_CONFIG
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

def simulate_scalping_param(df_3m, df_15m, btc_df_1h, entry_type, rsi_max, sl_atr, tp_rr):
    trades = []
    for i in range(100, len(df_3m) - 20):
        m3 = df_3m.iloc[i]
        btc_1h = btc_df_1h[btc_df_1h['timestamp'] <= m3['timestamp']].iloc[-1]
        regime = get_market_regime(btc_1h)
        if regime != "SCALPING": continue

        m15_slice = df_15m[df_15m['timestamp'] <= m3['timestamp']]
        if m15_slice.empty: continue
        m15 = m15_slice.iloc[-1]
        
        long_score, short_score = 0, 0
        if m3['ema7'] > m3['ema25']: long_score += 50
        if m15['ema7'] > m15['ema25']: long_score += 30
        if m3['rsi'] <= rsi_max: long_score += 25
        if m3['volume'] > m3['vol_avg'] * 1.5: long_score += 10
        
        if m3['ema7'] < m3['ema25']: short_score += 50
        if m15['ema7'] < m15['ema25']: short_score += 30
        if m3['rsi'] >= (100 - rsi_max): short_score += 25
        if m3['volume'] > m3['vol_avg'] * 1.5: short_score += 10
        
        stoch_rsi = m3.get('stoch_rsi', 50)
        stretch_pct = abs(m3['close'] - m3['ema25']) / m3['ema25'] * 100
        if stoch_rsi > 80: long_score -= 30
        if m3['close'] > m3['ema25'] and stretch_pct > 1.0: long_score -= 20
        if stoch_rsi < 20: short_score -= 30
        if m3['close'] < m3['ema25'] and stretch_pct > 1.0: short_score -= 20
        
        # Assume BTC macro filter is active
        long_score = min(long_score, 100); short_score = min(short_score, 100)
        score = max(long_score, short_score)
        
        if score >= 70 and m3['adx'] > 20:
            side = "LONG" if long_score > short_score else "SHORT"
            
            # Entry logic
            if entry_type == "ema25":
                entry_price = m3['ema25']
            elif entry_type == "ema_mid":
                entry_price = (m3['ema25'] + m3['ema99']) / 2
            elif entry_type == "bb_edge":
                entry_price = m3['bb_lower'] if side == "LONG" else m3['bb_upper']
            else:
                entry_price = m3['ema99']
                
            sl_dist = m3['atr'] * sl_atr
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
            trades.append({"result": result, "rr": tp_rr if result == "WIN" else -1 if result == "LOSS" else 0, "filled": fill_status})
    return trades

if __name__ == "__main__":
    df_3m = fetch_historical_data(SYMBOL, '3m', limit=3000)
    df_15m = fetch_historical_data(SYMBOL, '15m', limit=1000)
    btc_df_1h = fetch_historical_data('BTC/USDT', '1h', limit=1000)
    
    btc_df_1h = calculate_indicators(btc_df_1h)
    df_3m = calculate_indicators(df_3m)
    df_15m = calculate_indicators(df_15m)
    
    entry_types = ["ema25", "ema_mid", "bb_edge"]
    rsi_maxs = [45, 40]
    sl_atrs = [1.5, 2.0, 2.5]
    tp_rrs = [1.0, 1.2, 1.5]
    
    best_wr = 0
    best_pnl = -999
    best_params = None
    
    print(f"Testing Combinations for Scalping Optimization...")
    print(f"Original Config: entry=ema25, rsi=45, sl=1.5, tp=1.0")
    
    results = []
    
    for et in entry_types:
        for rsi in rsi_maxs:
            for sl in sl_atrs:
                for tp in tp_rrs:
                    trades = simulate_scalping_param(df_3m, df_15m, btc_df_1h, et, rsi, sl, tp)
                    filled = [t for t in trades if t['filled']]
                    if not filled: continue
                    wins = len([t for t in filled if t['result'] == 'WIN'])
                    losses = len([t for t in filled if t['result'] == 'LOSS'])
                    if wins + losses == 0: continue
                    wr = wins / (wins + losses) * 100
                    pnl = sum(t['rr'] for t in filled)
                    
                    res_str = f"Entry: {et:8} | RSI<={rsi} | SL={sl} ATR | TP={tp} RR -> WR: {wr:.2f}% | PnL: {pnl:.2f} | Fills: {len(filled)}"
                    results.append({"pnl": pnl, "wr": wr, "str": res_str})
                    
    results.sort(key=lambda x: x['pnl'], reverse=True)
    
    print("\n--- TOP 5 CONFIGS BY PNL ---")
    for r in results[:5]:
        print(r['str'])
        
    results.sort(key=lambda x: x['wr'], reverse=True)
    print("\n--- TOP 5 CONFIGS BY WINRATE ---")
    for r in results[:5]:
        print(r['str'])
