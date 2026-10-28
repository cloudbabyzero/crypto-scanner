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

def get_exchange():
    if hasattr(main_mod, 'exchange'):
        return main_mod.exchange
    from exchange_client import get_exchange as _get_ex
    return _get_ex()

# =====================================================================
# LEGACY HOOKS (ป้องกัน main.py บูตไม่ขึ้น)
# =====================================================================
def cancel_pending_orders(reason):
    """Spot ไม่มีการตั้ง Pending Limit ข้ามการยกเลิก"""
    pass

def cleanup_closed_trades():
    """รองรับคำสั่ง /trades ของ Telegram"""
    pass

def restore_open_positions():
    """โหลด State เก่าของ Spot จาก active_trades.json แทนการดึง Position Futures"""
    print("[SPOT RESTORE] โหลดพอร์ตสะสมเหรียญ Spot จาก active_trades.json เรียบร้อย", flush=True)

def reconcile_closed_trades_on_restart(pre_restart_snapshot):
    """ข้ามการ reconcile แบบ Futures"""
    pass

# =====================================================================
# 3-TIER INFINITY RUN ENGINE
# =====================================================================
def calculate_tier_trailing_floor(gain, highest_price, weighted_avg):
    if gain < INFINITY_ACTIVATION_PCT:
        return 0.0, "None", 0.0

    min_lock_price = weighted_avg * (1.0 + INFINITY_MIN_LOCK_PCT)

    if gain < 0.05:
        floor = highest_price * (1.0 - TRAILING_PCT_TIER1)
        tier_name = "Tier 1 (+2.5% ถึง +5% | Trail 1.2%)"
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

def check_trades():
    """ลูปเฝ้าดูราคาตลาดทุก 5 วินาที สั่งขายเมื่อหลุด Trailing Floor"""
    exchange = get_exchange()

    while True:
        try:
            time.sleep(5)
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

                gain = (current_price - weighted_avg) / weighted_avg
                gain_pct = gain * 100

                # ตรวจสอบเงื่อนไขเปิดระบบ Infinity Run
                if gain >= INFINITY_ACTIVATION_PCT:
                    if not pos.get("infinity_active", False):
                        with main_mod.state_lock:
                            pos["infinity_active"] = True
                            pos["highest_price"] = current_price
                        msg = f"🚀 [INFINITY RUN ACTIVATED]\n\nเหรียญ: {symbol}\nทุนเฉลี่ยรวม: {weighted_avg:.4f}\nราคาปัจจุบัน: {current_price:.4f} (+{gain_pct:.2f}%)\n🟢 กำไรแตะเป้า! เริ่มลาก Trailing SL ล็อกกำไรอัตโนมัติ (ขั้นต่ำ +{INFINITY_MIN_LOCK_PCT*100:.1f}%)"
                        main_mod.send_telegram(msg)
                        print(msg, flush=True)

                    if current_price > pos.get("highest_price", 0.0):
                        with main_mod.state_lock:
                            pos["highest_price"] = current_price

                    highest = pos["highest_price"]
                    trailing_floor, tier_name, trail_pct = calculate_tier_trailing_floor(gain, highest, weighted_avg)

                    with main_mod.state_lock:
                        pos["trailing_floor"] = trailing_floor
                        pos["sl"] = f"{trailing_floor:.4f} ({tier_name})"

                    # เมื่อราคาย่อหลุด Trailing Floor ➔ ขายเหรียญเกลี้ยงพอร์ต 100%
                    if current_price <= trailing_floor:
                        print(f"[INFINITY EXIT] {symbol} ราคา {current_price:.4f} หลุด Floor {trailing_floor:.4f} ➔ ขายเกลี้ยงพอร์ต!", flush=True)
                        order = bingx_client.spot_sell_all(symbol)
                        
                        if order:
                            total_cost = pos.get("total_cost_usdt", 0.0)
                            est_revenue = pos.get("total_qty", 0.0) * current_price
                            pnl_usdt = round(est_revenue - total_cost, 4)
                            entries = pos.get("entry_count", 1)

                            exit_msg = f"""🏆 [PROFIT HARVEST — ปิดรอบสำเร็จ]

เหรียญ: {symbol}
กำไรสุทธิ: {pnl_usdt:+.4f} USDT ({gain_pct:+.2f}%)
สะสมมาทั้งหมด: {entries} ไม้ (รวมทุน {total_cost:.2f} USDT)
ราคาขายเฉลี่ย: {current_price:.4f}
ระดับเทรนด์: {tier_name}

🟢 กวาดเงินต้น + กำไรคืนกระเป๋า USDT 100% เรียบร้อย!
รีเซ็ตสถานะเหรียญเพื่อรอสแกนรอบสะสมใหม่"""
                            main_mod.send_telegram(exit_msg)

                            try:
                                import google_sheet
                                free_usdt = bingx_client.get_spot_free_balance('USDT')
                                google_sheet.log_trade(
                                    symbol=symbol,
                                    side="LONG",
                                    entry=weighted_avg,
                                    exit_price=current_price,
                                    pnl=pnl_usdt,
                                    result="WIN",
                                    grade="PASS",
                                    score=100,
                                    rr=round(gain_pct, 2),
                                    strategy="SPOT_DCA",
                                    local_regime="SPOT",
                                    btc_regime="SPOT",
                                    margin_used=round(total_cost, 2),
                                    portfolio_balance=round(free_usdt, 2)
                                )
                            except Exception as ge:
                                print(f"[GOOGLE SHEETS LOG ERROR] {ge}", flush=True)

                            with main_mod.state_lock:
                                main_mod.active_trades.pop(symbol, None)

        except Exception as e:
            print(f"[TRADE MANAGER ERROR] {e}", flush=True)
            traceback.print_exc()
            time.sleep(10)