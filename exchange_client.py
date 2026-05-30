"""
Shared exchange client for Crypto Scanner Bot.
Provides singleton exchange instance with lazy-loaded markets.
"""

import ccxt
import os
import threading

# Singleton exchange instance
_exchange_instance = None
_markets_loaded = False
_exchange_lock = threading.RLock()

def get_exchange():
    """Get or create the shared exchange instance."""
    global _exchange_instance
    
    with _exchange_lock:
        if _exchange_instance is None:
            _exchange_instance = ccxt.bingx({
                'apiKey': os.getenv("BINGX_API_KEY"),
                'secret': os.getenv("BINGX_SECRET_KEY"),
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'swap',
                    'defaultSubType': 'linear',
                    'adjustForTimeDifference': True
                }
            })
            
            _exchange_instance.set_sandbox_mode(False)
            _exchange_instance.options['defaultType'] = 'swap'
            _exchange_instance.options['defaultSubType'] = 'linear'
    
    return _exchange_instance

def load_markets_if_needed():
    """Load markets if they haven't been loaded yet."""
    global _markets_loaded
    
    with _exchange_lock:
        if not _markets_loaded:
            exchange = get_exchange()
            exchange.load_markets()
            _markets_loaded = True
            print("✅ FUTURES MARKETS LOADED")