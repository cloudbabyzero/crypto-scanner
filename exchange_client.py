"""
Shared exchange client for Crypto Scanner Bot (Spot Mode).
Provides singleton CCXT exchange instance with spot configuration.
"""

import ccxt
import os
import threading

_exchange_instance = None
_markets_loaded = False
_exchange_lock = threading.RLock()

def get_exchange():
    """Get or create the shared Spot exchange instance."""
    global _exchange_instance
    
    with _exchange_lock:
        if _exchange_instance is None:
            _exchange_instance = ccxt.bingx({
                'apiKey': os.getenv("BINGX_API_KEY"),
                'secret': os.getenv("BINGX_SECRET_KEY"),
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'spot',  # เชื่อมต่อกระดาน Spot 100%
                    'adjustForTimeDifference': True
                }
            })
            _exchange_instance.set_sandbox_mode(False)
    
    return _exchange_instance

def load_markets_if_needed():
    """โหลดข้อมูลเหรียญและกฎการเทรดของกระดาน Spot"""
    global _markets_loaded
    with _exchange_lock:
        if not _markets_loaded:
            exchange = get_exchange()
            exchange.load_markets()
            _markets_loaded = True
            print("✅ BINGX SPOT MARKETS LOADED", flush=True)