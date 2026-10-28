"""
BingX Spot Client Module.
Handles Spot Market Buy (quoteOrderQty) and Full Market Sell with precision formatting.
"""

import sys
import logging
import traceback

logger = logging.getLogger(__name__)
main_mod = sys.modules.get("__main__")

def get_exchange():
    if hasattr(main_mod, 'exchange'):
        return main_mod.exchange
    from exchange_client import get_exchange as _get_ex
    return _get_ex()

def get_spot_free_balance(currency='USDT'):
    """เช็คยอดเงินสดคงเหลือจริงในกระเป๋า Spot"""
    try:
        exchange = get_exchange()
        balance = exchange.fetch_balance()
        return float(balance['free'].get(currency, 0.0))
    except Exception as e:
        logger.error(f"[BALANCE ERROR] เช็คยอด {currency} ไม่ได้: {e}")
        return 0.0

def spot_market_buy(symbol, usdt_amount):
    """
    ซื้อ Spot Market ตามจำนวนเงิน USDT (quoteOrderQty)
    พร้อมระบบ Soft Balance Check ถ้าเงินไม่พอซื้อ จะไม่แครช
    """
    exchange = get_exchange()
    free_usdt = get_spot_free_balance('USDT')

    if free_usdt < usdt_amount:
        msg = f"⚠️ [SPOT BUY SKIPPED] {symbol}\nUSDT ไม่พอซื้อ (ต้องการ: {usdt_amount} USDT | มีจริง: {free_usdt:.2f} USDT)\nปล่อยข้ามรอบนี้ไปก่อนเพื่อรอเงินเติมสัปดาห์ถัดไป"
        # ข้อความ Telegram ย้ายไปแจ้งเป็น [SPOT DCA PAUSED] ที่ run_priority_dca_engine (มี cooldown กันสแปม)
        print(f"[SPOT BUY] {msg}", flush=True)
        return None

    try:
        print(f"[BINGX SPOT] สั่งซื้อ Market Buy: {symbol} มูลค่า {usdt_amount} USDT", flush=True)
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='buy',
            amount=None,
            params={'quoteOrderQty': usdt_amount}
        )
        
        filled_qty = float(order.get('filled') or order.get('amount') or 0.0)
        cost = float(order.get('cost') or usdt_amount)
        avg_price = cost / filled_qty if filled_qty > 0 else 0.0

        if avg_price == 0.0:
            ticker = exchange.fetch_ticker(symbol)
            avg_price = float(ticker['last'])
            filled_qty = cost / avg_price

        return {
            "order_id": order.get('id', 'local_id'),
            "filled_qty": filled_qty,
            "cost": cost,
            "avg_price": avg_price
        }

    except Exception as e:
        err_msg = f"🚨 [SPOT BUY ERROR] {symbol} ล้มเหลว: {e}"
        if hasattr(main_mod, 'send_telegram'):
            main_mod.send_telegram(err_msg)
        print(err_msg, flush=True)
        traceback.print_exc()
        return None

def spot_sell_all(symbol):
    """
    สั่งขายเหรียญทั้งหมด 100% ในกระเป๋า Spot คืนเป็น USDT
    พร้อมระบบปัดเศษทศนิยมตรงตามกฎ BingX ป้องกัน Order Reject
    """
    exchange = get_exchange()
    base_coin = symbol.split('/')[0]

    try:
        actual_qty = get_spot_free_balance(base_coin)
        if actual_qty <= 0:
            print(f"[SPOT SELL] {symbol} ไม่มียอดเหรียญ {base_coin} คงเหลือให้ขาย", flush=True)
            return None

        # ปัดเศษทศนิยมตามกฎของคู่เหรียญนั้นๆ บน BingX
        sell_qty = float(exchange.amount_to_precision(symbol, actual_qty))

        ticker = exchange.fetch_ticker(symbol)
        est_val = sell_qty * float(ticker['last'])
        if est_val < 4.8:
            print(f"[SPOT SELL] {symbol} มูลค่าต่ำกว่าเกณฑ์ขั้นต่ำ ({est_val:.2f} USDT) ข้ามการขายเศษเหรียญ", flush=True)
            return None

        print(f"[BINGX SPOT] สั่งขายเกลี้ยงพอร์ต: {symbol} จำนวน {sell_qty} {base_coin}", flush=True)
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='sell',
            amount=sell_qty
        )
        return order

    except Exception as e:
        err_msg = f"🚨 [SPOT SELL ERROR] ขาย {symbol} ล้มเหลว: {e}"
        if hasattr(main_mod, 'send_telegram'):
            main_mod.send_telegram(err_msg)
        print(err_msg, flush=True)
        traceback.print_exc()
        return None


# =====================================================================
# EXCHANGE-SIDE STOP LOSS (TAKE_STOP_MARKET) — ส่งคำสั่งตรงเข้า BingX Spot API
# ไม่ผ่าน create_order ของ ccxt เพราะถ้า ccxt ไม่รู้จักพารามิเตอร์ SL มันจะกลายเป็น Market Sell ขายทิ้งทันที
# =====================================================================
last_stop_error = ""

def spot_place_stop_sell(symbol, stop_price):
    """ตั้ง Stop-Loss ฝั่งขาย (TAKE_STOP_MARKET) บน BingX สำหรับเหรียญว่างทั้งหมดในกระเป๋า
    คืน {'id','stop_price','qty'} หรือ None (เหตุผลอยู่ใน last_stop_error)"""
    global last_stop_error
    last_stop_error = ""
    exchange = get_exchange()
    base_coin = symbol.split('/')[0]
    try:
        free_qty = get_spot_free_balance(base_coin)
        if free_qty <= 0:
            last_stop_error = f"ไม่มียอด {base_coin} ว่างให้ตั้ง SL (อาจถูกล็อกในออเดอร์เดิม)"
            return None

        qty = float(exchange.amount_to_precision(symbol, free_qty))
        sp = float(exchange.price_to_precision(symbol, stop_price))
        if qty * sp < 4.8:
            last_stop_error = f"มูลค่าต่ำกว่าขั้นต่ำ ({qty * sp:.2f} USDT)"
            return None

        fn = getattr(exchange, "spotV1PrivatePostTradeOrder", None)
        if fn is None:
            last_stop_error = "ccxt เวอร์ชันนี้ไม่มี endpoint spot trade/order (ไม่ได้ส่งคำสั่งใดๆ)"
            return None

        resp = fn({
            "symbol": exchange.market(symbol)["id"],
            "side": "SELL",
            "type": "TAKE_STOP_MARKET",
            "quantity": qty,
            "stopPrice": sp,
        })

        if isinstance(resp, dict) and str(resp.get("code", 0)) != "0":
            last_stop_error = f"BingX ปฏิเสธ: {resp.get('msg') or resp}"
            return None

        data = (resp or {}).get("data") or {}
        if isinstance(data.get("order"), dict):
            data = data["order"]
        order_id = data.get("orderId") or data.get("orderID") or data.get("id")
        if not order_id:
            last_stop_error = f"ไม่พบ orderId ในผลลัพธ์: {resp}"
            return None

        print(f"[BINGX SPOT] ตั้ง SL บนกระดาน {symbol} qty={qty} stop={sp} id={order_id}", flush=True)
        return {"id": str(order_id), "stop_price": sp, "qty": qty}

    except Exception as e:
        last_stop_error = str(e)
        print(f"[BINGX SPOT] ตั้ง SL ไม่สำเร็จ {symbol}: {e}", flush=True)
        traceback.print_exc()
        return None
