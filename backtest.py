"""
backtest.py — Signal Quality Backtester

วิธีทำงาน:
1. ดักจับทุก signal ที่ analyze_trend/sideways ส่งออกมา
2. บันทึก entry price, SL, TP ณ เวลาที่ signal ออก
3. ตรวจสอบผลหลัง 4 ชั่วโมง ว่าราคาชน TP หรือ SL ก่อน
4. บันทึกผลลง BacktestResults sheet ใน Google Sheet

ใช้งาน:
    import backtest
    backtest.record_signal(signal_id, symbol, side, entry, sl, tp2, grade, score, strategy)
    
    # เรียกทุก scan cycle
    backtest.check_pending()
"""

import time
import threading
import json
import os
from datetime import datetime, timedelta
import pandas as pd

# ===========================
# STATE
# ===========================

_pending = {}   # signal_id → signal_data
_lock = threading.Lock()
SAVE_FILE = "backtest_pending.json"

def _save_pending():
    # Helper to save _pending dictionary. Assumes _lock is held or called safely.
    try:
        with open(SAVE_FILE, 'w') as f:
            json.dump(_pending, f)
    except Exception as e:
        print(f"[BACKTEST] Failed to save pending: {e}", flush=True)

def _load_pending():
    global _pending
    if os.path.exists(SAVE_FILE):
        try:
            with open(SAVE_FILE, 'r') as f:
                _pending = json.load(f)
            print(f"[BACKTEST] Loaded {len(_pending)} pending signals from {SAVE_FILE}", flush=True)
        except Exception as e:
            print(f"[BACKTEST] Failed to load pending: {e}", flush=True)
            _pending = {}

# Load on startup
_load_pending()


EVAL_HOURS = 4           # ประเมินผลหลัง 4 ชั่วโมง
EVAL_SECONDS = EVAL_HOURS * 3600
SHEET_BACKTEST = "BacktestResults"

# ===========================
# RECORD SIGNAL
# ===========================

def record_signal(signal_id, symbol, side, entry, sl, tp, grade, score, strategy,
                  rr=0, local_regime="", btc_regime="",
                  adx=0, atr_pct=0, vol_status="", btc_trend="",
                  vwap_pos="", stoch_rsi=0, stretch_pct=0, candle_color=""):
    """บันทึก signal สำหรับ backtest
    
    เรียกทันทีหลัง signal ออก ก่อน place order
    """
    with _lock:
        _pending[signal_id] = {
            "signal_id":  signal_id,
            "symbol":     symbol,
            "side":       side.upper(),
            "entry":      entry,
            "sl":         sl,
            "tp":         tp,
            "grade":      grade,
            "score":      score,
            "strategy":   strategy,
            "rr":         rr,
            "local_regime": local_regime,
            "btc_regime": btc_regime,
            "adx":        adx,
            "atr_pct":    atr_pct,
            "vol_status": vol_status,
            "btc_trend":  btc_trend,
            "vwap_pos":   vwap_pos,
            "stoch_rsi":  stoch_rsi,
            "stretch_pct":stretch_pct,
            "candle_color":candle_color,
            "recorded_at": time.time(),
            "eval_at":    time.time() + EVAL_SECONDS,
        }
        _save_pending()
    print(f"[BACKTEST] Recorded {symbol} {side} entry={entry} sl={sl} tp={tp}", flush=True)


# ===========================
# CHECK PENDING SIGNALS
# ===========================

def check_pending():
    """ตรวจสอบ signals ที่ครบเวลา eval แล้ว
    
    เรียกใน scan loop ทุก cycle
    """
    try:
        import main as main_mod
        import google_sheet
    except ImportError:
        return

    now = time.time()

    with _lock:
        due = {k: v for k, v in _pending.items() if v["eval_at"] <= now}

    for signal_id, sig in due.items():
        try:
            result = _evaluate(sig, main_mod)
            _log_result(sig, result, google_sheet)

            with _lock:
                _pending.pop(signal_id, None)
                _save_pending()

        except Exception as e:
            print(f"[BACKTEST] Error evaluating {sig['symbol']}: {e}", flush=True)


# ===========================
# EVALUATE RESULT
# ===========================

def _evaluate(sig, main_mod):
    """ดูว่าหลัง 4h ราคาชน TP หรือ SL ก่อน
    
    Logic:
    - ดึง OHLCV 15m ย้อนหลัง 4h (16 candles)
    - scan ว่า high/low ชน TP หรือ SL candle ไหนก่อน
    - ถ้าไม่ชนทั้งคู่ = OPEN (ยังไม่มีผล)
    """
    symbol  = sig["symbol"]
    side    = sig["side"]
    entry   = sig["entry"]
    sl      = sig["sl"]
    tp      = sig["tp"]

    try:
        df = main_mod.get_dataframe(symbol, "3m")
        recorded_at = sig.get('recorded_at', 0)
        
        # Ensure time column is datetime for filtering
        df['time_dt'] = pd.to_datetime(df['time'], unit='ms')
        
        # Filter candles that occurred AFTER the signal was generated
        df_after = df[df['time'] > recorded_at * 1000]
        
        # เอาแค่ 16 candles หลัง signal = 4h
        candles = df_after.head(80)

        tp_hit_idx  = None
        sl_hit_idx  = None

        for i, row in enumerate(candles.itertuples()):
            if side == "SHORT":
                # SHORT: TP = ราคาลง, SL = ราคาขึ้น
                if tp_hit_idx is None and row.low <= tp:
                    tp_hit_idx = i
                if sl_hit_idx is None and row.high >= sl:
                    sl_hit_idx = i
            else:
                # LONG: TP = ราคาขึ้น, SL = ราคาลง
                if tp_hit_idx is None and row.high >= tp:
                    tp_hit_idx = i
                if sl_hit_idx is None and row.low <= sl:
                    sl_hit_idx = i

        if tp_hit_idx is None and sl_hit_idx is None:
            # ราคายังไม่ชนทั้งคู่ใน 4h
            current_price = candles.iloc[-1]["close"]
            if side == "SHORT":
                pnl_pct = round((entry - current_price) / entry * 100, 2)
            else:
                pnl_pct = round((current_price - entry) / entry * 100, 2)
            return {"result": "OPEN", "pnl_pct": pnl_pct, "note": "Neither TP nor SL hit in 4h"}

        elif tp_hit_idx is not None and (sl_hit_idx is None or tp_hit_idx < sl_hit_idx):
            # TP ถูกชนก่อน = WIN
            rr = round(abs(tp - entry) / abs(sl - entry), 2)
            pnl_pct = round(abs(tp - entry) / entry * 100, 2)
            return {"result": "WIN", "pnl_pct": pnl_pct, "rr": rr,
                    "candle_idx": tp_hit_idx, "note": f"TP hit at candle {tp_hit_idx}"}

        else:
            # SL ถูกชนก่อน = LOSS
            pnl_pct = round(-abs(sl - entry) / entry * 100, 2)
            return {"result": "LOSS", "pnl_pct": pnl_pct, "rr": 0,
                    "candle_idx": sl_hit_idx, "note": f"SL hit at candle {sl_hit_idx}"}

    except Exception as e:
        return {"result": "ERROR", "pnl_pct": 0, "note": str(e)}


# ===========================
# LOG RESULT TO GOOGLE SHEET
# ===========================

def _log_result(sig, result, google_sheet):
    """บันทึกผล backtest ลง BacktestResults sheet"""
    try:
        # ใช้เวลาตอนเกิด signal จริง (recorded_at) จะได้ตรงกับ sheet Signals
        recorded_at_ts = sig.get("recorded_at", time.time() - EVAL_SECONDS)
        timestamp = (datetime.utcfromtimestamp(recorded_at_ts) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
        row = [
            timestamp,
            sig["signal_id"],
            sig["symbol"],
            sig["side"],
            sig["strategy"],
            sig["grade"],
            sig["score"],
            sig["entry"],
            sig["sl"],
            sig["tp"],
            result.get("result", "ERROR"),
            result.get("pnl_pct", 0),
            result.get("rr", 0),
            result.get("note", ""),
            sig.get("adx", 0),
            sig.get("atr_pct", 0),
            sig.get("vol_status", ""),
            sig.get("btc_trend", ""),
            sig.get("vwap_pos", ""),
            sig.get("stoch_rsi", 0),
            sig.get("stretch_pct", 0),
            sig.get("candle_color", ""),
        ]
        google_sheet._add_to_buffer(SHEET_BACKTEST, row)

        print(
            f"[BACKTEST] {sig['symbol']} {sig['side']} "
            f"{sig['strategy']} Grade={sig['grade']} "
            f"→ {result.get('result')} pnl={result.get('pnl_pct')}%",
            flush=True
        )

    except Exception as e:
        print(f"[BACKTEST] log_result error: {e}", flush=True)


# ===========================
# STATS SUMMARY
# ===========================

def get_stats():
    """สรุป win rate จาก pending signals ที่ evaluate แล้ว (in-memory)"""
    # Note: stats จริงควรดูจาก Google Sheet
    with _lock:
        total = len(_pending)
    return {
        "pending_signals": total,
        "eval_after_hours": EVAL_HOURS,
    }
