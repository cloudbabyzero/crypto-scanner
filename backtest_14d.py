"""
backtest_14d.py — 14-Day Realistic Backtest
จำลองสภาวะตลาดจริงย้อนหลัง 14 วัน สำหรับ FORCE_SCALPING mode

Strategy Config (ตรงกับ config.py):
  - Mode      : FORCE_SCALPING
  - TF Base   : 3m entry / 15m macro filter
  - Leverage  : 25x
  - SL Mult   : 1.2 ATR
  - TP RR     : 2.0 (TP = risk * 2.0)
  - MAX TRADES: 2 concurrent
  - Trailing  : activation 1.0 ATR, buffer 0.7 ATR, step 0.3 ATR
  - Fee       : 0.02% maker x2 (open+close) per BingX standard

Initial Capital : $10 USDT
Margin/Trade    : proportional (max 2 trades = 45% each)
"""

import ccxt
import pandas as pd
import ta
import time
import sys
from datetime import datetime, timedelta, timezone

# CONFIG (mirror config.py SCALPING)
SCALP_CFG = {
    "SL_ATR_MULT":    1.2,
    "TP_RR":          2.0,   # ขึ้นจาก 1.2 → 2.0 (ยังเป็น scalping)
    "MIN_SCORE":      90,   # A+ only (ขึ้นจาก 80)
    "MIN_ADX":        18,
    "MAX_ADX":        35,
    "MIN_ATR_PCT":    0.15,
    "RSI_LONG_MAX":   65,
    "RSI_SHORT_MIN":  25,
}

LEVERAGE          = 15
INITIAL_CAPITAL   = 10.0
MAX_CONCURRENT    = 2
MARGIN_RATIO      = 0.45
TAKER_FEE         = 0.0002   # 0.02% Maker (Limit orders) — BingX rate

TRAILING_ACTIVATION_ATR = 2.0   # ขยับออก: ต้องวิ่ง 2×ATR ก่อน trail จะ active
TRAILING_BUFFER_ATR     = 1.0   # buffer กว้าง = exit กำไร 1.0×ATR (จาก 0.3×ATR)
TRAILING_STEP_ATR       = 0.3
ENABLE_TRAILING         = True   # ON for 15m — ATR ใหญ่พอ ไม่ clip early

DAYS_BACK         = 14
# candle counts per TF (14 days + warmup)
LIMITS = {
    "3m":  14 * 24 * 20 + 50,
    "5m":  14 * 24 * 12 + 50,
    "15m": 14 * 24 * 4  + 50,
    "1h":  14 * 24      + 50,
}
COOLDOWNS = {
    "3m":  300,   # 5 min (5 candles)
    "5m":  600,   # 10 min (2 candles)
    "15m": 900,   # 15 min (1 candle)
}
MACRO_TF = {
    "3m":  "15m",
    "5m":  "15m",
    "15m": "1h",
}

SYMBOLS = [
    "BTC/USDT",   # WR 64-68% ✅ Best performer
    "SOL/USDT",   # WR 47-64% ✅
    "SUI/USDT",   # WR 50-52% ✅
    "AAVE/USDT",  # WR 45-84% ✅
    # LINK WR 29%, ETH WR 35%, AVAX WR 33%, TAO/NEAR ตัดออกทั้งหมด
]

exchange = ccxt.binance({"enableRateLimit": True})

def fetch_ohlcv(symbol, tf, limit):
    try:
        raw = exchange.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.reset_index(drop=True)
    except Exception as e:
        print(f"  [SKIP] {symbol} {tf}: {e}")
        return None

def add_indicators(df):
    df = df.copy()
    df["ema7"]   = ta.trend.ema_indicator(df["close"], window=7)
    df["ema25"]  = ta.trend.ema_indicator(df["close"], window=25)
    df["ema99"]  = ta.trend.ema_indicator(df["close"], window=99)
    df["rsi"]    = ta.momentum.rsi(df["close"], window=14)
    adx_ind      = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
    df["adx"]    = adx_ind.adx()
    df["atr"]    = ta.volatility.average_true_range(df["high"], df["low"], df["close"], window=14)
    df["vol_avg"]= df["volume"].rolling(20).mean()
    stoch        = ta.momentum.StochRSIIndicator(df["close"], window=14, smooth1=3, smooth2=3)
    df["stoch_rsi"] = stoch.stochrsi() * 100
    return df.dropna().reset_index(drop=True)

def score_candle_long(row_3m, row_15m):
    score = 0
    if row_3m["ema7"] > row_3m["ema25"]:     score += 30
    if row_3m["ema25"] > row_3m["ema99"]:    score += 20
    if row_15m["ema7"] > row_15m["ema25"]:   score += 20
    rsi = row_3m["rsi"]
    if 40 <= rsi <= SCALP_CFG["RSI_LONG_MAX"]: score += 15
    adx = row_3m["adx"]
    if SCALP_CFG["MIN_ADX"] <= adx <= SCALP_CFG["MAX_ADX"]: score += 15
    if row_3m["stoch_rsi"] < 40:             score += 10
    stretch = abs(row_3m["close"] - row_3m["ema25"]) / row_3m["ema25"] * 100
    if stretch > 1.5: score -= 20
    if stretch > 2.5: score -= 20
    atr_pct = row_3m["atr"] / row_3m["close"] * 100
    if atr_pct < SCALP_CFG["MIN_ATR_PCT"]: score -= 30
    score = max(0, min(score, 100))
    if score >= 90:    grade = "A+"
    elif score >= 80:  grade = "A"
    elif score >= 70:  grade = "B"
    else:              grade = "C"
    return score, grade

def score_candle_short(row_3m, row_15m):
    score = 0
    if row_3m["ema7"] < row_3m["ema25"]:     score += 30
    if row_3m["ema25"] < row_3m["ema99"]:    score += 20
    if row_15m["ema7"] < row_15m["ema25"]:   score += 20
    rsi = row_3m["rsi"]
    if SCALP_CFG["RSI_SHORT_MIN"] <= rsi <= 60: score += 15
    adx = row_3m["adx"]
    if SCALP_CFG["MIN_ADX"] <= adx <= SCALP_CFG["MAX_ADX"]: score += 15
    if row_3m["stoch_rsi"] > 60:             score += 10
    stretch = abs(row_3m["close"] - row_3m["ema25"]) / row_3m["ema25"] * 100
    if stretch > 1.5: score -= 20
    if stretch > 2.5: score -= 20
    atr_pct = row_3m["atr"] / row_3m["close"] * 100
    if atr_pct < SCALP_CFG["MIN_ATR_PCT"]: score -= 30
    score = max(0, min(score, 100))
    if score >= 90:    grade = "A+"
    elif score >= 80:  grade = "A"
    elif score >= 70:  grade = "B"
    else:              grade = "C"
    return score, grade

COOLDOWN_SECONDS = 300  # default, overridden per test
CANDLE_SEC = 180        # 3 minutes per candle (default)

def backtest_symbol(symbol, df_3m, df_15m, cutoff_ts):
    trades = []
    last_signal_ts = None   # cooldown tracker per symbol
    next_open_candle = 0    # index after last trade exits
    for i in range(100, len(df_3m) - 1):
        row = df_3m.iloc[i]
        if row["ts"] < cutoff_ts:
            continue

        # Skip candles until previous trade exits
        if i < next_open_candle:
            continue

        # Cooldown: skip if last signal was < 300s ago
        if last_signal_ts is not None:
            elapsed = (row["ts"] - last_signal_ts).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                continue

        mask = df_15m["ts"] <= row["ts"]
        if mask.sum() == 0:
            continue
        row_15m = df_15m[mask].iloc[-1]

        score_l, grade_l = score_candle_long(row, row_15m)
        score_s, grade_s = score_candle_short(row, row_15m)

        signal_side = None
        signal_score = 0
        signal_grade = "C"

        if score_l >= SCALP_CFG["MIN_SCORE"] and score_l >= score_s:
            signal_side, signal_score, signal_grade = "LONG", score_l, grade_l
        elif score_s >= SCALP_CFG["MIN_SCORE"]:
            signal_side, signal_score, signal_grade = "SHORT", score_s, grade_s

        if signal_side is None:
            continue

        last_signal_ts = row["ts"]

        entry_price = row["ema25"]
        atr = row["atr"]
        if atr <= 0 or entry_price <= 0:
            continue

        sl_dist = atr * SCALP_CFG["SL_ATR_MULT"]
        tp_dist = sl_dist * SCALP_CFG["TP_RR"]

        if signal_side == "LONG":
            sl_price = entry_price - sl_dist
            tp_price = entry_price + tp_dist
        else:
            sl_price = entry_price + sl_dist
            tp_price = entry_price - tp_dist

        fill_price = None
        result = "PENDING"
        exit_price = None
        candles_held = 0
        trailing_sl = sl_price

        for j in range(i + 1, min(i + 40, len(df_3m) - 1)):
            fut = df_3m.iloc[j]
            candles_held += 1

            if fill_price is None:
                if signal_side == "LONG" and fut["low"] <= entry_price:
                    fill_price = entry_price
                elif signal_side == "SHORT" and fut["high"] >= entry_price:
                    fill_price = entry_price

            if fill_price is None:
                continue

            if signal_side == "LONG":
                if ENABLE_TRAILING:
                    activation = fill_price + atr * TRAILING_ACTIVATION_ATR
                    if fut["high"] >= activation:
                        breakeven = fill_price * 1.0015
                        proposed  = fut["high"] - atr * TRAILING_BUFFER_ATR
                        new_sl    = max(trailing_sl, proposed, breakeven)
                        if new_sl > trailing_sl + atr * TRAILING_STEP_ATR:
                            trailing_sl = new_sl
                if fut["low"] <= trailing_sl:
                    exit_price = trailing_sl
                    result = "WIN" if trailing_sl > fill_price else "LOSS"
                    break
                elif fut["high"] >= tp_price:
                    exit_price = tp_price
                    result = "WIN"
                    break
            else:
                if ENABLE_TRAILING:
                    activation = fill_price - atr * TRAILING_ACTIVATION_ATR
                    if fut["low"] <= activation:
                        breakeven = fill_price * 0.9985
                        proposed  = fut["low"] + atr * TRAILING_BUFFER_ATR
                        new_sl    = min(trailing_sl, proposed, breakeven)
                        if new_sl < trailing_sl - atr * TRAILING_STEP_ATR:
                            trailing_sl = new_sl
                if fut["high"] >= trailing_sl:
                    exit_price = trailing_sl
                    result = "WIN" if trailing_sl < fill_price else "LOSS"
                    break
                elif fut["low"] <= tp_price:
                    exit_price = tp_price
                    result = "WIN"
                    break

        # Determine close candle index
        close_candle_idx = i + candles_held if candles_held > 0 else i + 1

        if fill_price is None:
            result = "UNFILLED"
        elif result == "PENDING":
            exit_price = df_3m.iloc[min(i + 40, len(df_3m) - 1)]["close"]
            pnl_dir = (exit_price - fill_price) if signal_side == "LONG" else (fill_price - exit_price)
            result = "TIMEOUT_WIN" if pnl_dir > 0 else "TIMEOUT_LOSS"

        # Advance next_open_candle so same symbol doesn't re-enter while trade open
        if result != "UNFILLED":
            next_open_candle = close_candle_idx

        # Compute close timestamp for account-level slot management
        close_ts = df_3m.iloc[min(close_candle_idx, len(df_3m) - 1)]["ts"]

        trades.append({
            "symbol":       symbol,
            "ts":           row["ts"],
            "close_ts":     close_ts,
            "side":         signal_side,
            "score":        signal_score,
            "grade":        signal_grade,
            "entry":        round(fill_price, 6) if fill_price else None,
            "sl":           round(sl_price, 6),
            "tp":           round(tp_price, 6),
            "exit":         round(exit_price, 6) if exit_price else None,
            "atr":          round(atr, 6),
            "result":       result,
            "candles_held": candles_held,
        })
    return trades

def simulate_account(all_trades):
    """Time-aware account simulation with proper MAX_CONCURRENT slot tracking."""
    trades_sorted = sorted(
        [t for t in all_trades if t["result"] not in ("UNFILLED", "PENDING") and t["entry"] is not None],
        key=lambda x: x["ts"]
    )

    equity = INITIAL_CAPITAL
    # Track close timestamps of active trades
    open_slots = []   # list of close_ts for currently open trades
    trade_results = []

    for t in trades_sorted:
        open_ts = t["ts"]
        close_ts = t.get("close_ts", open_ts)

        # Free up slots that have already closed before this trade opens
        open_slots = [cts for cts in open_slots if cts > open_ts]

        # Enforce MAX_CONCURRENT
        if len(open_slots) >= MAX_CONCURRENT:
            continue

        if t["exit"] is None:
            continue

        margin   = round(equity * MARGIN_RATIO, 4)
        notional = margin * LEVERAGE
        entry    = t["entry"]
        exit_    = t["exit"]
        contracts= notional / entry

        if t["side"] == "LONG":
            raw_pnl = (exit_ - entry) * contracts
        else:
            raw_pnl = (entry - exit_) * contracts

        fee     = notional * TAKER_FEE * 2
        net_pnl = round(raw_pnl - fee, 4)
        pnl_pct = round(net_pnl / equity * 100, 2)
        equity  = round(equity + net_pnl, 4)
        is_win  = t["result"] in ("WIN", "TIMEOUT_WIN")

        # Register this slot as busy until close_ts
        open_slots.append(close_ts)

        trade_results.append({
            **t,
            "margin":   margin,
            "notional": notional,
            "net_pnl":  net_pnl,
            "pnl_pct":  pnl_pct,
            "equity":   equity,
            "is_win":   is_win,
        })

    return trade_results

def print_report(account_trades, raw_trades):
    print("\n" + "=" * 65)
    print("  📊  14-DAY BACKTEST REPORT — FORCE_SCALPING MODE")
    print("  [OPTIMIZED] Maker Orders (fee 0.02%) + TP_RR 2.0")

    total_signals = len(raw_trades)
    filled        = [t for t in raw_trades if t["result"] != "UNFILLED"]
    unfilled      = total_signals - len(filled)
    fill_rate     = len(filled) / total_signals * 100 if total_signals else 0

    print(f"\n  {'Symbols tested':<30} {len(SYMBOLS)}")
    print(f"  {'Period':<30} Last {DAYS_BACK} days")
    print(f"  {'Total Signals Generated':<30} {total_signals}")
    print(f"  {'Filled (entered)':<30} {len(filled)}")
    print(f"  {'Unfilled (no pullback)':<30} {unfilled}")
    print(f"  {'Fill Rate':<30} {fill_rate:.1f}%")

    if not account_trades:
        print("\n  ⚠️  No trades executed")
        return

    wins     = [t for t in account_trades if t["is_win"]]
    losses   = [t for t in account_trades if not t["is_win"]]
    win_rate = len(wins) / len(account_trades) * 100 if account_trades else 0

    total_pnl    = sum(t["net_pnl"] for t in account_trades)
    final_equity = account_trades[-1]["equity"]
    roi          = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    peak = INITIAL_CAPITAL
    max_dd = 0.0
    for t in account_trades:
        if t["equity"] > peak:
            peak = t["equity"]
        dd = (peak - t["equity"]) / peak * 100
        if dd > max_dd:
            max_dd = dd

    max_consec_loss = cur_consec = 0
    for t in account_trades:
        if not t["is_win"]:
            cur_consec += 1
            max_consec_loss = max(max_consec_loss, cur_consec)
        else:
            cur_consec = 0

    avg_win  = sum(t["net_pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["net_pnl"] for t in losses) / len(losses) if losses else 0
    pf_num   = abs(sum(t["net_pnl"] for t in wins))
    pf_den   = abs(sum(t["net_pnl"] for t in losses)) or 0.0001
    profit_factor = pf_num / pf_den

    print(f"\n  {'─'*61}")
    print(f"  💰  CAPITAL SUMMARY")
    print(f"  {'─'*61}")
    print(f"  {'Initial Capital':<34} $  {INITIAL_CAPITAL:.2f}")
    print(f"  {'Final Equity':<34} $  {final_equity:.2f}")
    print(f"  {'Net PnL':<34} $  {total_pnl:+.2f}")
    print(f"  {'ROI':<34}    {roi:+.2f}%")
    print(f"  {'Max Drawdown':<34}    {max_dd:.2f}%")

    print(f"\n  {'─'*61}")
    print(f"  📈  PERFORMANCE METRICS")
    print(f"  {'─'*61}")
    print(f"  {'Executed Trades':<34} {len(account_trades)}")
    print(f"  {'Wins':<34} {len(wins)}")
    print(f"  {'Losses':<34} {len(losses)}")
    print(f"  {'Win Rate':<34} {win_rate:.1f}%")
    print(f"  {'Avg Win PnL':<34} ${avg_win:+.4f}")
    print(f"  {'Avg Loss PnL':<34} ${avg_loss:+.4f}")
    print(f"  {'Profit Factor':<34} {profit_factor:.2f}x")
    print(f"  {'Max Consecutive Losses':<34} {max_consec_loss}")

    print(f"\n  {'─'*61}")
    print(f"  🏆  PER-SYMBOL BREAKDOWN")
    print(f"  {'─'*61}")
    by_sym = {}
    for t in account_trades:
        s = t["symbol"]
        if s not in by_sym:
            by_sym[s] = {"wins": 0, "losses": 0, "pnl": 0.0}
        by_sym[s]["pnl"] += t["net_pnl"]
        if t["is_win"]:
            by_sym[s]["wins"] += 1
        else:
            by_sym[s]["losses"] += 1
    for sym, data in sorted(by_sym.items(), key=lambda x: -x[1]["pnl"]):
        total_t = data["wins"] + data["losses"]
        wr = data["wins"] / total_t * 100 if total_t else 0
        icon = "🟢" if data["pnl"] > 0 else "🔴"
        print(f"  {icon} {sym:<18} W:{data['wins']} L:{data['losses']} "
              f"WR:{wr:.0f}% PnL:${data['pnl']:+.4f}")

    print(f"\n  {'─'*61}")
    print(f"  📋  LAST 10 TRADES")
    print(f"  {'─'*61}")
    hdr = f"  {'Time':<20} {'Symbol':<12} {'Side':<6} {'Result':<14} {'PnL':>8}  {'Eq':>7}"
    print(hdr)
    for t in account_trades[-10:]:
        ts_str = t["ts"].strftime("%m/%d %H:%M") if hasattr(t["ts"], "strftime") else str(t["ts"])[:16]
        r = "✅ WIN" if t["is_win"] else "❌ LOSS"
        print(f"  {ts_str:<20} {t['symbol']:<12} {t['side']:<6} {r:<14} "
              f"${t['net_pnl']:>+7.4f}  ${t['equity']:>6.2f}")

    print(f"\n{'='*65}")
    print(f"  🎯  $10 USDT → ${final_equity:.2f} in {DAYS_BACK} days ({roi:+.2f}%)")
    print(f"{'='*65}\n")

def run_tf_test(entry_tf: str, cutoff: pd.Timestamp) -> tuple[list, list]:
    """Run full backtest for one entry timeframe."""
    global COOLDOWN_SECONDS
    COOLDOWN_SECONDS = COOLDOWNS[entry_tf]
    macro_tf = MACRO_TF[entry_tf]

    all_raw = []
    for sym in SYMBOLS:
        print(f"    {sym:<14}", end=" ", flush=True)
        df_entry = fetch_ohlcv(sym, entry_tf, LIMITS[entry_tf])
        df_macro  = fetch_ohlcv(sym, macro_tf, LIMITS[macro_tf])

        if df_entry is None or df_macro is None or len(df_entry) < 200:
            print("→ skipped")
            continue

        df_entry = add_indicators(df_entry)
        df_macro  = add_indicators(df_macro)

        sym_trades = backtest_symbol(sym, df_entry, df_macro, cutoff)
        all_raw.extend(sym_trades)

        filled = sum(1 for t in sym_trades if t["result"] != "UNFILLED")
        wins   = sum(1 for t in sym_trades if t["result"] in ("WIN", "TIMEOUT_WIN"))
        wr     = wins / filled * 100 if filled else 0
        print(f"→ sig:{len(sym_trades):3d} filled:{filled:3d} WR:{wr:.0f}%")
        time.sleep(0.3)

    account_trades = simulate_account(all_raw)
    return all_raw, account_trades



def main():
    now_utc   = datetime.now(timezone.utc)
    cutoff_dt = now_utc - timedelta(days=DAYS_BACK)
    cutoff    = pd.Timestamp(cutoff_dt.replace(tzinfo=None)).tz_localize("UTC")

    tf = "15m"
    print(f"\n=== 14-Day Backtest [OPTIMIZED] ===")
    print(f"Period   : {cutoff.strftime('%Y-%m-%d')} to {now_utc.strftime('%Y-%m-%d')}")
    print(f"Entry TF : {tf} (Macro: {MACRO_TF[tf]})")
    print(f"Capital  : ${INITIAL_CAPITAL} @ {LEVERAGE}x leverage")
    print(f"Strategy : SL={SCALP_CFG['SL_ATR_MULT']}xATR | TP RR={SCALP_CFG['TP_RR']} | MIN_SCORE={SCALP_CFG['MIN_SCORE']} (A+ only)")
    print(f"Trailing : {'ON (activation=1.0xATR, buffer=0.7xATR)' if ENABLE_TRAILING else 'OFF'}")
    print(f"Fee      : {TAKER_FEE*100:.3f}% per side (maker/limit orders)")
    print()

    raw, acc = run_tf_test(tf, cutoff)

    if not acc:
        print("No trades executed.")
        return

    print_report(acc, raw)


if __name__ == "__main__":
    main()
