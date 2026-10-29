"""
Trade Manager Module (Spot DCA & 3-Tier Infinity Run).
Monitors position state, weighted average cost, and executes Trailing profit exits.
Backward compatible with legacy main.py hooks.
"""

import sys
import time
import json
import os
import traceback
import bingx_client
import config
from config import (
    INFINITY_ACTIVATION_PCT,
    INFINITY_MIN_LOCK_PCT,
    TRAILING_PCT_TIER1,
    TRAILING_PCT_TIER2,
    TRAILING_PCT_TIER3
)

main_mod = sys.modules.get("__main__")
STATE_STORAGE_FILE = "active_trades.json"

# ตัวแปรที่ main.py เรียกหา ป้องกัน AttributeError
last_closed_trade = {}
last_loop_ts = time.time()   # watchdog ใน main.py ใช้ตรวจว่าลูปเฝ้าราคายังทำงานอยู่

def get_exchange():
    if hasattr(main_mod, 'exchange'):
        return main_mod.exchange
    from exchange_client import get_exchange as _get_ex
    return _get_ex()

# =====================================================================
# LEGACY HOOKS (ป้องกัน main.py บูตไม่ขึ้น)
# =====================================================================
def restore_open_positions():
    """หลังรีสตาร์ต: ย้อนดูราคาช่วงที่บอทดับ แล้วอัปเดต highest_price / เปิด Infinity Run ย้อนหลังถ้าราคาเคยแตะเป้า"""
    print("[SPOT RESTORE] โหลดพอร์ตสะสมเหรียญ Spot จาก active_trades.json เรียบร้อย", flush=True)
    try:
        check_exchange_stops_on_restart()
    except Exception as e:
        print(f"[SPOT RESTORE] stop check error: {e}", flush=True)
        traceback.print_exc()
    try:
        backfill_peaks_after_restart()
    except Exception as e:
        print(f"[SPOT RESTORE] backfill error: {e}", flush=True)
        traceback.print_exc()

def _pick_timeframe(gap_seconds):
    """เลือก timeframe ให้ 1,000 แท่งครอบคลุมช่วงที่บอทดับ"""
    if gap_seconds <= 15 * 3600:
        return "1m", 60
    if gap_seconds <= 9 * 86400:
        return "15m", 900
    return "1h", 3600

def backfill_peaks_after_restart():
    exchange = get_exchange()
    now = time.time()
    with main_mod.state_lock:
        items = list(main_mod.active_trades.items())

    for symbol, pos in items:
        qty = float(pos.get("total_qty", 0.0) or 0.0)
        avg = float(pos.get("weighted_avg_price", 0.0) or 0.0)
        if qty <= 0 or avg <= 0:
            continue

        last_seen = pos.get("last_seen_ts") or pos.get("updated_at")
        if not last_seen:
            continue
        gap = now - float(last_seen)
        if gap < 120:
            continue

        try:
            tf, tf_sec = _pick_timeframe(gap)
            candles = exchange.fetch_ohlcv(symbol, tf, since=int((float(last_seen) - tf_sec) * 1000), limit=1000)
        except Exception as e:
            print(f"[BACKFILL] {symbol} ดึงแท่งเทียนย้อนหลังไม่ได้: {e}", flush=True)
            continue
        if not candles:
            continue

        peak = max(float(c[2]) for c in candles)
        peak_gain = (peak - avg) / avg
        old_high = float(pos.get("highest_price", 0.0) or 0.0)

        if pos.get("infinity_active", False):
            if peak > old_high:
                with main_mod.state_lock:
                    pos["highest_price"] = peak
                print(f"[BACKFILL] {symbol} อัปเดต highest_price {old_high:.4f} ➔ {peak:.4f}", flush=True)
        elif peak_gain >= INFINITY_ACTIVATION_PCT:
            with main_mod.state_lock:
                pos["infinity_active"] = True
                pos["highest_price"] = max(peak, old_high)
            main_mod.send_telegram(
                f"🚀 [INFINITY RUN ACTIVATED — ย้อนหลัง]\n\n"
                f"เหรียญ: {symbol}\n"
                f"ช่วงที่บอทดับ ({gap / 60:.0f} นาที) ราคาเคยขึ้นถึง {peak:.4f} (+{peak_gain * 100:.2f}% เหนือทุนเฉลี่ย {avg:.4f})\n"
                f"🟢 เปิดโหมด Trailing ย้อนหลังด้วยจุดสูงสุดนี้ ถ้าราคาปัจจุบันหลุด Floor บอทจะขายให้ทันที"
            )

def cleanup_closed_trades():
    """รองรับคำสั่ง /trades ของ Telegram"""
    pass

def cancel_pending_orders(reason):
    """Spot ไม่มีการตั้ง Pending Limit ข้ามการยกเลิก"""
    pass

def reconcile_closed_trades_on_restart(pre_restart_snapshot):
    """เทียบยอดเหรียญจริงบน BingX กับไฟล์พอร์ต แจ้งเตือนอย่างเดียว ไม่แก้ไขอัตโนมัติ"""
    try:
        exchange = get_exchange()
        totals = exchange.fetch_balance().get("total", {})
    except Exception as e:
        print(f"[RECONCILE] เช็คยอดจริงไม่ได้: {e}", flush=True)
        return

    issues = []
    for symbol in config.symbols:
        base = symbol.split("/")[0]
        actual = float(totals.get(base, 0.0) or 0.0)
        with main_mod.state_lock:
            pos = main_mod.active_trades.get(symbol)
        file_qty = float(pos.get("total_qty", 0.0)) if pos else 0.0

        try:
            price = float(exchange.fetch_ticker(symbol)["last"])
        except Exception:
            price = 0.0

        if file_qty > 0:
            if actual < file_qty * 0.97:
                issues.append(f"• {symbol}: ไฟล์บอกถือ {file_qty:.6f} แต่ในกระเป๋ามี {actual:.6f} (น้อยกว่า — อาจขายไปแล้ว/ขายไม่ได้)")
            elif actual > file_qty * 1.03 and (actual - file_qty) * price >= 5:
                issues.append(f"• {symbol}: ไฟล์บอกถือ {file_qty:.6f} แต่ในกระเป๋ามี {actual:.6f} (ส่วนเกินอาจเป็นของที่ซื้อเอง — บอทจะขายทั้งหมดตอนปิดรอบ)")
        elif price > 0 and actual * price >= 5:
            issues.append(f"• {symbol}: ในกระเป๋ามี {actual:.6f} (~{actual * price:.2f} USDT) แต่ไม่มีในไฟล์พอร์ต — บอทจะไม่ดูแลเหรียญนี้")

    if issues:
        main_mod.send_telegram(
            "⚠️ [STATE MISMATCH] ยอดเหรียญจริงบน BingX ไม่ตรงกับไฟล์พอร์ต\n\n"
            + "\n".join(issues)
            + "\n\n👉 บอทไม่แก้ไขอัตโนมัติ กรุณาตรวจสอบก่อน"
        )
    else:
        print("[RECONCILE] ยอดเหรียญจริงตรงกับไฟล์พอร์ต", flush=True)

# =====================================================================
# 3-TIER INFINITY RUN ENGINE
# =====================================================================
def calculate_tier_trailing_floor(gain, highest_price, weighted_avg):
    if gain < INFINITY_ACTIVATION_PCT:
        return 0.0, "None", 0.0

    min_lock_price = weighted_avg * (1.0 + INFINITY_MIN_LOCK_PCT)

    if gain < 0.05:
        floor = highest_price * (1.0 - TRAILING_PCT_TIER1)
        tier_name = f"Tier 1 (+{INFINITY_ACTIVATION_PCT*100:.1f}% ถึง +5% | Trail {TRAILING_PCT_TIER1*100:.1f}%)"
        trail_pct = TRAILING_PCT_TIER1
    elif gain < 0.15:
        floor = highest_price * (1.0 - TRAILING_PCT_TIER2)
        tier_name = "Tier 2 (+5% ถึง +15% | Trail 2.5%)"
        trail_pct = TRAILING_PCT_TIER2
    else:
        floor = highest_price * (1.0 - TRAILING_PCT_TIER3)
        tier_name = "Tier 3 (> +15% | Trail 4.0%)"
        trail_pct = TRAILING_PCT_TIER3

    final_floor = max(floor, min_lock_price)
    return final_floor, tier_name, trail_pct

# =====================================================================
# EXCHANGE-SIDE STOP LOSS MANAGEMENT
# =====================================================================
STOP_MIN_STEP_PCT = 0.002      # ขยับ SL บนกระดานเมื่อ Floor ขึ้นอย่างน้อย 0.2%
STOP_SYNC_INTERVAL = 30        # เช็กสถานะออเดอร์ SL ทุก 30 วินาที
_stop_retry_ts = {}
_stop_check_ts = {}
_stop_fail_alert_ts = {}

def _clear_stop(pos):
    with main_mod.state_lock:
        pos.pop("stop_order_id", None)
        pos.pop("stop_price", None)

def _report_harvest(symbol, pos, exit_price, revenue, tier_name, via_exchange=False, downtime=False):
    """ส่ง [PROFIT HARVEST] + บันทึก Google Sheets (ใช้ร่วมกันทั้งขายโดยบอทและขายโดย SL บนกระดาน)"""
    total_cost = float(pos.get("total_cost_usdt", 0.0))
    weighted_avg = float(pos.get("weighted_avg_price", 0.0))
    pnl_usdt = round(revenue - total_cost, 4)
    gain_pct = ((exit_price - weighted_avg) / weighted_avg * 100) if weighted_avg > 0 else 0.0
    entries = int(pos.get("entry_count", 1))

    note = ""
    if via_exchange:
        note = "\n🛡️ ขายโดย Stop Loss บนกระดาน BingX" + (" ระหว่างที่บอทออฟไลน์" if downtime else "")

    exit_msg = f"""🏆 [PROFIT HARVEST — ปิดรอบสำเร็จ]

เหรียญ: {symbol}
กำไรสุทธิ: {pnl_usdt:+.4f} USDT ({gain_pct:+.2f}%)
สะสมมาทั้งหมด: {entries} ไม้ (รวมทุน {total_cost:.2f} USDT)
ราคาขายเฉลี่ย: {exit_price:.4f}
ระดับเทรนด์: {tier_name}{note}

🟢 กวาดเงินต้น + กำไรคืนกระเป๋า USDT 100% เรียบร้อย!
รีเซ็ตสถานะเหรียญเพื่อรอสแกนรอบสะสมใหม่"""
    main_mod.send_telegram(exit_msg)

    try:
        import google_sheet
        free_usdt = bingx_client.get_spot_free_balance('USDT')
        google_sheet.log_trade(
            symbol=symbol, side="LONG", entry=weighted_avg, exit_price=exit_price, pnl=pnl_usdt,
            result="WIN" if pnl_usdt >= 0 else "LOSS", grade="PASS", score=100, rr=round(gain_pct, 2),
            strategy="SPOT_DCA", local_regime="SPOT", btc_regime="SPOT",
            margin_used=round(total_cost, 2), portfolio_balance=round(free_usdt, 2)
        )
    except Exception as ge:
        print(f"[GOOGLE SHEETS LOG ERROR] {ge}", flush=True)

def _tier_name_for(pos):
    avg = float(pos.get("weighted_avg_price", 0.0) or 0.0)
    highest = float(pos.get("highest_price", 0.0) or 0.0)
    if avg <= 0:
        return "N/A"
    return calculate_tier_trailing_floor((highest - avg) / avg, highest, avg)[1]

def _finalize_exchange_sell(symbol, pos, order, downtime=False):
    """ออเดอร์ SL บนกระดานถูก fill แล้ว ➔ ปิดรอบ"""
    filled = float(order.get("filled") or 0.0)
    cost = float(order.get("cost") or 0.0)
    px = float(order.get("average") or order.get("price") or 0.0)
    if cost <= 0 and filled > 0 and px > 0:
        cost = filled * px
    if px <= 0 and filled > 0 and cost > 0:
        px = cost / filled
    if cost <= 0 or px <= 0:  # ไม่มีรายละเอียด fill ➔ ประมาณจากราคา SL
        px = float(pos.get("stop_price") or pos.get("trailing_floor") or 0.0)
        cost = float(pos.get("total_qty", 0.0)) * px
    _report_harvest(symbol, pos, px, cost, _tier_name_for(pos), via_exchange=True, downtime=downtime)
    with main_mod.state_lock:
        main_mod.active_trades.pop(symbol, None)

def _cancel_stop(exchange, symbol, pos):
    """คืน 'CANCELLED' | 'FILLED' (ปิดรอบให้แล้ว) | 'FAILED'"""
    sid = pos.get("stop_order_id")
    if not sid:
        return "CANCELLED"
    try:
        exchange.cancel_order(sid, symbol)
        _clear_stop(pos)
        return "CANCELLED"
    except Exception as e:
        print(f"[STOP] ยกเลิก SL {symbol} ไม่สำเร็จ: {e}", flush=True)
    try:
        order = exchange.fetch_order(sid, symbol)
    except Exception:
        return "FAILED"
    status = (order.get("status") or "").lower()
    if status == "closed":
        _finalize_exchange_sell(symbol, pos, order)
        return "FILLED"
    if status in ("canceled", "cancelled", "expired", "rejected"):
        _clear_stop(pos)
        return "CANCELLED"
    return "FAILED"

def _sync_stop_status(exchange, symbol, pos):
    """เช็กสถานะออเดอร์ SL บนกระดานเป็นระยะ คืน True ถ้าถูกขายไปแล้ว (ปิดรอบแล้ว)"""
    sid = pos.get("stop_order_id")
    if not sid:
        return False
    now = time.time()
    if now - _stop_check_ts.get(symbol, 0) < STOP_SYNC_INTERVAL:
        return False
    _stop_check_ts[symbol] = now
    try:
        order = exchange.fetch_order(sid, symbol)
    except Exception as e:
        print(f"[STOP] เช็กสถานะ SL {symbol} ไม่ได้: {e}", flush=True)
        return False
    status = (order.get("status") or "").lower()
    if status == "closed":
        _finalize_exchange_sell(symbol, pos, order)
        return True
    if status in ("canceled", "cancelled", "expired", "rejected"):
        _clear_stop(pos)
        main_mod.send_telegram(f"⚠️ [SL ถูกยกเลิก] {symbol}\nออเดอร์ Stop Loss บน BingX ถูกยกเลิก/หมดอายุ ({status}) — บอทจะตั้งใหม่ให้อัตโนมัติ")
    return False

def _ensure_exchange_stop(exchange, symbol, pos, floor, current_price):
    """ตั้ง/ขยับ SL บนกระดานให้ตรงกับ Trailing Floor (cancel → place ใหม่เมื่อ Floor ขึ้น ≥ 0.2%)"""
    if floor <= 0 or floor >= current_price * 0.998:
        return
    sid = pos.get("stop_order_id")
    placed = float(pos.get("stop_price") or 0.0)
    if sid and floor < placed * (1.0 + STOP_MIN_STEP_PCT):
        return
    now = time.time()
    if now - _stop_retry_ts.get(symbol, 0) < 20:
        return
    _stop_retry_ts[symbol] = now

    if sid:
        result = _cancel_stop(exchange, symbol, pos)
        if result != "CANCELLED":
            return  # FILLED = ปิดรอบแล้ว / FAILED = ลองใหม่รอบหน้า

    res = bingx_client.spot_place_stop_sell(symbol, floor)
    if res:
        first_time = not pos.get("stop_notified", False)
        with main_mod.state_lock:
            pos["stop_order_id"] = res["id"]
            pos["stop_price"] = res["stop_price"]
            pos["stop_notified"] = True
        if first_time:
            avg = float(pos.get("weighted_avg_price", 0.0))
            pct = (res["stop_price"] - avg) / avg * 100 if avg > 0 else 0.0
            main_mod.send_telegram(
                f"🛡️ [SL ON EXCHANGE] {symbol}\n\n"
                f"ตั้ง Stop Loss บน BingX แล้วที่ {res['stop_price']:.4f} ({pct:+.2f}% จากทุนเฉลี่ย)\n"
                f"ถ้าบอท/เซิร์ฟเวอร์ล่ม ออเดอร์นี้ยังทำงานเอง และบอทจะขยับตามราคาที่ขึ้นอัตโนมัติ"
            )
    else:
        if now - _stop_fail_alert_ts.get(symbol, 0) > 3600:
            _stop_fail_alert_ts[symbol] = now
            main_mod.send_telegram(
                f"⚠️ [SL ON EXCHANGE ล้มเหลว] {symbol}\n{bingx_client.last_stop_error}\n"
                f"บอทยังเฝ้าราคาและขายเองตามเดิม แต่ตอนนี้ยังไม่มี SL บนกระดานคุ้มครอง"
            )

def check_exchange_stops_on_restart():
    """หลังรีสตาร์ต: ถ้า SL บนกระดานถูก fill ระหว่างบอทดับ ➔ ปิดรอบ/แจ้งเตือน ถ้ายังเปิดอยู่ ➔ คงไว้"""
    exchange = get_exchange()
    with main_mod.state_lock:
        items = list(main_mod.active_trades.items())
    for symbol, pos in items:
        sid = pos.get("stop_order_id")
        if not sid:
            continue
        try:
            order = exchange.fetch_order(sid, symbol)
        except Exception as e:
            print(f"[STOP RESTORE] เช็ก SL {symbol} ไม่ได้: {e}", flush=True)
            continue
        status = (order.get("status") or "").lower()
        if status == "closed":
            _finalize_exchange_sell(symbol, pos, order, downtime=True)
        elif status in ("canceled", "cancelled", "expired", "rejected"):
            _clear_stop(pos)
            print(f"[STOP RESTORE] SL ของ {symbol} ไม่อยู่แล้ว ({status}) ➔ จะตั้งใหม่", flush=True)
        else:
            print(f"[STOP RESTORE] SL ของ {symbol} ยังทำงานอยู่บนกระดาน", flush=True)

# =====================================================================
# MAIN WATCH LOOP
# =====================================================================
def check_trades():
    """ลูปเฝ้าราคาทุก 5 วินาที: คำนวณ Trailing Floor (ขึ้นอย่างเดียว) ➔ ตั้ง/ขยับ SL บน BingX
    และขายเองเป็นตาข่ายสำรองเมื่อราคาหลุด Floor"""
    global last_loop_ts
    exchange = get_exchange()

    while True:
        try:
            time.sleep(5)
            last_loop_ts = time.time()
            with main_mod.state_lock:
                trades_snapshot = list(main_mod.active_trades.items())

            for symbol, pos in trades_snapshot:
                if pos.get("total_qty", 0.0) <= 0:
                    continue

                weighted_avg = pos.get("weighted_avg_price", 0.0)
                if weighted_avg <= 0:
                    continue

                try:
                    ticker = exchange.fetch_ticker(symbol)
                    current_price = float(ticker['last'])
                except Exception:
                    continue

                last_loop_ts = time.time()
                with main_mod.state_lock:
                    pos["last_seen_ts"] = time.time()

                gain = (current_price - weighted_avg) / weighted_avg
                gain_pct = gain * 100

                if not (gain >= INFINITY_ACTIVATION_PCT or pos.get("infinity_active", False)):
                    continue

                if not pos.get("infinity_active", False):
                    with main_mod.state_lock:
                        pos["infinity_active"] = True
                        pos["highest_price"] = current_price
                    msg = (f"🚀 [INFINITY RUN ACTIVATED]\n\nเหรียญ: {symbol}\nทุนเฉลี่ยรวม: {weighted_avg:.4f}\n"
                           f"ราคาปัจจุบัน: {current_price:.4f} (+{gain_pct:.2f}%)\n"
                           f"🟢 กำไรแตะเป้า! เริ่มลาก Trailing SL ล็อกกำไรอัตโนมัติ (ขั้นต่ำ +{INFINITY_MIN_LOCK_PCT*100:.1f}%)")
                    main_mod.send_telegram(msg)
                    print(msg, flush=True)

                if current_price > pos.get("highest_price", 0.0):
                    with main_mod.state_lock:
                        pos["highest_price"] = current_price

                highest = pos["highest_price"]
                calc_floor, tier_name, trail_pct = calculate_tier_trailing_floor(
                    (highest - weighted_avg) / weighted_avg, highest, weighted_avg)

                # Floor ขึ้นอย่างเดียว ไม่ลดลงแม้เปลี่ยน Tier
                trailing_floor = max(calc_floor, float(pos.get("trailing_floor", 0.0) or 0.0))
                with main_mod.state_lock:
                    pos["trailing_floor"] = trailing_floor
                    pos["sl"] = f"{trailing_floor:.4f} ({tier_name})"

                # 1) ออเดอร์ SL บนกระดานถูก fill ไปแล้วหรือยัง
                if _sync_stop_status(exchange, symbol, pos):
                    continue

                # 2) ราคาหลุด Floor ➔ ยกเลิก SL บนกระดาน (ถ้ามี) แล้วขายเกลี้ยงเอง
                if current_price <= trailing_floor:
                    if pos.get("stop_order_id"):
                        result = _cancel_stop(exchange, symbol, pos)
                        if result != "CANCELLED":
                            continue  # FILLED = ปิดรอบแล้ว / FAILED = ลองใหม่รอบหน้า

                    print(f"[INFINITY EXIT] {symbol} ราคา {current_price:.4f} หลุด Floor {trailing_floor:.4f} ➔ ขายเกลี้ยงพอร์ต!", flush=True)
                    order = bingx_client.spot_sell_all(symbol)
                    if order:
                        est_revenue = pos.get("total_qty", 0.0) * current_price
                        _report_harvest(symbol, pos, current_price, est_revenue, tier_name)
                        with main_mod.state_lock:
                            main_mod.active_trades.pop(symbol, None)
                    continue

                # 3) ยังไม่หลุด ➔ ตั้ง/ขยับ SL บนกระดานให้ตรงกับ Floor
                _ensure_exchange_stop(exchange, symbol, pos, trailing_floor, current_price)

        except Exception as e:
            print(f"[TRADE MANAGER ERROR] {e}", flush=True)
            traceback.print_exc()
            time.sleep(10)
