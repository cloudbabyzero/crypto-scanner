"""
ทดสอบตั้ง/ยกเลิก Stop-Loss บน BingX Spot ด้วยเหรียญที่ถืออยู่จริง (ตั้งต่ำกว่าราคาตลาด 30% แล้วยกเลิกทันที)
ใช้: python test_stop_order.py LINK/USDT
ต้องมี BINGX_API_KEY / BINGX_SECRET_KEY ใน environment และมีเหรียญนั้นว่างในกระเป๋า (มูลค่า >= ~5 USDT)
"""
import sys
import time
from exchange_client import get_exchange, load_markets_if_needed
import bingx_client

symbol = sys.argv[1] if len(sys.argv) > 1 else "LINK/USDT"
load_markets_if_needed()
ex = get_exchange()

price = float(ex.fetch_ticker(symbol)["last"])
stop = price * 0.70
print(f"ราคาตลาด {price} ➔ ตั้ง SL ทดสอบที่ {stop:.4f} (-30%)")

res = bingx_client.spot_place_stop_sell(symbol, stop)
if not res:
    print("❌ ตั้งไม่สำเร็จ:", bingx_client.last_stop_error)
    sys.exit(1)

print("✅ ตั้งสำเร็จ:", res)
try:
    time.sleep(2)
    order = ex.fetch_order(res["id"], symbol)
    print("สถานะ:", order.get("status"), "| type:", (order.get("info") or {}).get("type"), "| stopPrice:", (order.get("info") or {}).get("stopPrice"))
finally:
    ex.cancel_order(res["id"], symbol)
    print("ยกเลิกออเดอร์ทดสอบแล้ว")
