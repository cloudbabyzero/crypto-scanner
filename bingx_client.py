from datetime import time
import time as pytime
import sys
import uuid
import json
import logging

logger = logging.getLogger(__name__)

# Reference main module globals
main_mod = sys.modules["__main__"]

# =========================
# CUSTOM EXCEPTIONS
# =========================

class PositionNotExistError(Exception):
    """Raised when BingX returns code 109420 ('position not exist').
    
    This is a TERMINAL condition - the position has been closed (manually,
    via SL/TP, or liquidation) and protection retries must stop immediately.
    """
    BINGX_CODE = 109420

    def __init__(self, symbol, message="Position does not exist on BingX", original_error=None):
        self.symbol = symbol
        self.original_error = original_error
        super().__init__(f"{symbol}: {message} (BingX code {self.BINGX_CODE})")


# =========================
# ERROR HANDLING HELPERS
# =========================

def extract_bingx_error_code(exception):
    """Extract BingX error code from CCXT exception.
    
    CCXT wraps BingX API errors in the exception response.
    BingX errors are typically in format: {"code": 110406, "msg": "..."}
    
    Args:
        exception: Exception from CCXT exchange call
        
    Returns:
        Tuple (error_code, error_msg) or (None, None) if not a BingX error
    """
    try:
        # CCXT wraps the response in args[0] or response attribute
        if hasattr(exception, 'args') and exception.args:
            response = exception.args[0]
            if isinstance(response, str):
                # Try to parse as JSON
                try:
                    data = json.loads(response)
                    if isinstance(data, dict):
                        code = data.get('code')
                        msg = data.get('msg', '')
                        return code, msg
                except (json.JSONDecodeError, TypeError):
                    pass
        
        # Check exception response attribute
        if hasattr(exception, 'response'):
            response = exception.response
            if isinstance(response, dict):
                code = response.get('code')
                msg = response.get('msg', '')
                return code, msg
        
        # Last resort: check if error message contains code
        error_str = str(exception)
        if '110406' in error_str:
            return 110406, error_str
        if '109420' in error_str:
            return 109420, error_str
    except Exception:
        pass
    
    return None, None

# =========================
# EXCHANGE WRAPPERS
# =========================

def fetch_positions(symbols=None):
    """Fetch current positions from exchange.
    
    Args:
        symbols: List of symbols to fetch, or None for all positions
        
    Returns:
        List of position objects from exchange
    """
    try:
        if symbols:
            return main_mod.exchange.fetch_positions(symbols)
        else:
            return main_mod.exchange.fetch_positions()
    except Exception as e:
        print(f"fetch_positions error: {e}", flush=True)
        raise


def fetch_order(order_id, symbol):
    """Fetch order details from exchange.
    
    Args:
        order_id: Order ID to fetch
        symbol: Trading symbol
        
    Returns:
        Order object from exchange
    """
    try:
        return main_mod.exchange.fetch_order(order_id, symbol)
    except Exception as e:
        print(f"fetch_order error: {e}", flush=True)
        raise


def cancel_order(order_id, symbol):
    """Cancel an order on the exchange.
    
    Args:
        order_id: Order ID to cancel
        symbol: Trading symbol
        
    Returns:
        Cancel result from exchange
    """
    try:
        return main_mod.exchange.cancel_order(order_id, symbol)
    except Exception as e:
        print(f"cancel_order error: {e}", flush=True)
        raise

# =========================
# PROTECTION ORDERS
# =========================

def place_protection_orders(
    symbol,
    side_cfg,
    sl_price,
    tp2_price,
    amount
):
    """Place stop-loss and take-profit orders for a position.
    
    Args:
        symbol: Trading symbol
        side_cfg: Side configuration dict with 'position_side' and 'stop_side'
        sl_price: Stop-loss price
        tp2_price: Take-profit 2 price
        amount: Order amount
        
    Returns:
        (sl_order_id, tp2_order_id) tuple
        
    Raises:
        PositionNotExistError: If BingX returns code 109420 ("position not exist")
        
    Note:
        - If BingX error 110406 ("Position SL order already exists") occurs,
          this is treated as SUCCESS (protection already in place) and returns
          dummy order IDs to prevent retry loops.
        - If BingX error 109420 ("position not exist") occurs, raises PositionNotExistError
          which is a TERMINAL condition - the position no longer exists.
        - Other errors are raised normally.
    """
    # BingX hedge mode rejects reduceOnly on protection orders.
    base_params = {
        'positionSide': side_cfg['position_side'],
        'closePosition': True
    }

    # Place SL order
    sl_order = None
    sl_order_id = None
    try:
        sl_order = main_mod.exchange.create_order(
            symbol=symbol,
            type='STOP_MARKET',
            side=side_cfg['stop_side'],
            amount=amount,
            params={
                **base_params,
                'stopPrice': sl_price
            }
        )
        sl_order_id = sl_order['id']
    except Exception as e:
        # Check if this is error 109420 (position not exist) - TERMINAL condition
        error_code, error_msg = extract_bingx_error_code(e)
        if error_code == PositionNotExistError.BINGX_CODE:
            logger.error(f"Position does not exist for {symbol} (code {error_code}) - protection retry loop will stop")
            raise PositionNotExistError(symbol, error_msg or "Position does not exist", e)
        # Check if this is error 110406 (SL already exists)
        elif error_code == 110406:
            # Protection already in place - treat as success
            print(f"[BINGX] SL already exists for {symbol} (code 110406) - OK", flush=True)
            # Return dummy ID to indicate protection exists (but we don't have order ID)
            sl_order_id = "existing_sl"
        else:
            # Log other errors for debugging
            logger.exception(f"SL order placement failed for {symbol}: {e}")
            # Re-raise for other errors
            raise

    # Place TP order
    tp2_order = None
    tp2_order_id = None
    try:
        tp2_order = main_mod.exchange.create_order(
            symbol=symbol,
            type='TAKE_PROFIT_MARKET',
            side=side_cfg['stop_side'],
            amount=amount,
            params={
                **base_params,
                'stopPrice': tp2_price
            }
        )
        tp2_order_id = tp2_order['id']
    except Exception as e:
        # Check if this is error 109420 (position not exist) - TERMINAL condition
        error_code, error_msg = extract_bingx_error_code(e)
        if error_code == PositionNotExistError.BINGX_CODE:
            logger.error(f"Position does not exist for {symbol} (code {error_code}) - protection retry loop will stop")
            raise PositionNotExistError(symbol, error_msg or "Position does not exist", e)
        # Fallback: try TAKE_PROFIT instead of TAKE_PROFIT_MARKET
        try:
            tp2_order = main_mod.exchange.create_order(
                symbol=symbol,
                type='TAKE_PROFIT',
                side=side_cfg['stop_side'],
                amount=amount,
                params={
                **base_params,
                'stopPrice': tp2_price
                }
            )
            tp2_order_id = tp2_order['id']
        except Exception as e:
            # Check if this is error 109420 (position not exist) - TERMINAL condition
            error_code, error_msg = extract_bingx_error_code(e)
            if error_code == PositionNotExistError.BINGX_CODE:
                logger.error(f"Position does not exist for {symbol} (code {error_code}) - protection retry loop will stop")
                raise PositionNotExistError(symbol, error_msg or "Position does not exist", e)
            # Check if this is error 110406 for TP
            elif error_code == 110406:
                # TP already in place - treat as success
                print(f"[BINGX] TP already exists for {symbol} (code 110406) - OK", flush=True)
                tp2_order_id = "existing_tp"
            else:
                # Log other errors for debugging
                logger.exception(f"TP order placement failed for {symbol}: {e}")
                # Re-raise for other errors
                raise

    return sl_order_id, tp2_order_id


# =========================
# TRADE EXECUTION
# =========================

def execute_trade(symbol, side):
    """Execute a trade entry with protection orders.
    
    Args:
        symbol: Trading symbol (e.g., 'BTC/USDT:USDT')
        side: 'long' or 'short'
    """

    try:

        # =========================
        # FORMAT SYMBOL
        # =========================

        symbol = symbol.upper()

        if ":USDT" not in symbol:
            symbol = f"{symbol}/USDT:USDT"

        # =========================
        # CHECK SYMBOL
        # =========================

        if symbol not in main_mod.symbols:

            main_mod.send_telegram(
                f"❌ {symbol} not supported"
            )

            return

        # =========================
        # PREVENT DUPLICATE
        # =========================

        with main_mod.state_lock:
            trade_items = list(main_mod.active_trades.values())

        for trade in trade_items:

            if (
                trade['symbol'] == symbol
                and trade.get('status') in ["PENDING", "OPEN"]
            ):

                main_mod.send_telegram(
                    f"⚠️ {symbol} already active"
                )

                return

        # =========================
        # CHECK GLOBAL POSITION LIMIT
        # =========================

        if not main_mod.can_open_trade(side.upper()):
            main_mod.send_telegram(
                f"❌ GLOBAL POSITION LIMIT REACHED\n\n"
                f"{symbol}\n\n"
                f"Max {main_mod.MAX_ACTIVE_TRADES} active trades"
            )
            return

        # =========================
        # GET SIGNAL
        # =========================

        signal = main_mod.get_latest_signal(symbol)

        # =========================
        # CHECK SIGNAL BEFORE ENTRY
        # =========================

        if not signal:
            main_mod.send_telegram(
                f"❌ No signal found for {symbol}"
            )
            return

        signal_type = signal.get("signal", "").upper()

        if signal_type != side.upper():
            main_mod.send_telegram(
                f"❌ No {side.upper()} signal for {symbol}\n\n"
                f"Current Signal: {signal_type}"
            )
            return

        # =========================
        # MARKET REGIME SAFETY LOCK
        # =========================

        signal_regime = signal.get("signal_regime", "UNKNOWN")

        if main_mod.CURRENT_REGIME != signal_regime:
            main_mod.send_telegram(
                f"⚠️ Signal Expired\n\n"
                f"Reason: Market Regime Changed\n\n"
                f"Signal Regime:\n{signal_regime}\n\n"
                f"Current Regime:\n{main_mod.CURRENT_REGIME}"
            )
            return

        entry = signal["entry"]
        sl = signal["sl"]
        atr = signal["atr"]
        
        # =========================
        # MARGIN MODE
        # =========================

        try:

            main_mod.exchange.set_margin_mode(
                "isolated",
                symbol
            )

        except Exception:
            pass

        # =========================
        # LEVERAGE
        # =========================

        if side == "long":

            main_mod.exchange.set_leverage(
                main_mod.LEVERAGE,
                symbol,
                {
                     "side": "LONG"
                }
            )

        else:

            main_mod.exchange.set_leverage(
                main_mod.LEVERAGE,
                symbol,
                {
                    "side": "SHORT"
                }
            )

        # =========================
        # AMOUNT
        # =========================

        raw_amount = (
            main_mod.MARGIN_PER_TRADE * main_mod.LEVERAGE
        ) / entry

        amount = main_mod.exchange.amount_to_precision(
            symbol,
            raw_amount
        )

        amount = float(amount)

        # =========================
        # LONG
        # =========================

        if side == "long":
            sl, tp1, tp2, _ = main_mod.calculate_trade_levels(
                entry,
                atr,
                "LONG"
            )

            order = main_mod.exchange.create_order(
                symbol=symbol,
                type='limit',
                side='buy',
                amount=amount,
                price=entry,
                params={
                    'positionSide': 'LONG',
                    'tradeSide': 'OPEN',
                    'marginMode': 'isolated'
                }
            )

        # =========================
        # SHORT
        # =========================

        else:
            sl, tp1, tp2, _ = main_mod.calculate_trade_levels(
                entry,
                atr,
                "SHORT"
            )

            order = main_mod.exchange.create_order(
                symbol=symbol,
                type='limit',
                side='sell',
                amount=amount,
                price=entry,
                params={
                    'positionSide': 'SHORT',
                    'tradeSide': 'OPEN',
                    'marginMode': 'isolated'
                }
            )

        side_cfg = main_mod.get_side_config(
            "LONG"
            if side == "long"
            else "SHORT"
        )

        sl_order_id = None
        tp2_order_id = None

        try:
            sl_order_id, tp2_order_id = place_protection_orders(
                symbol=symbol,
                side_cfg=side_cfg,
                sl_price=sl,
                tp2_price=tp2,
                amount=amount
            )
        except PositionNotExistError as e:
            # Position no longer exists - this shouldn't happen during trade execution
            # but if it does, log it and continue (trade won't be created)
            logger.error(f"Position does not exist during trade execution for {symbol}: {e}")
            return  # Don't create the trade
        except Exception as protect_error:
            main_mod.send_telegram(
                f"⚠️ Protection pre-set failed for {symbol}\n"
                f"{str(protect_error)}\n"
                f"Bot will retry after fill."
            )

        # =========================
        # SAVE TRADE
        # =========================
        trade_id = str(uuid.uuid4())[:8]

        with main_mod.state_lock:
            main_mod.active_trades[trade_id] = {
                "symbol": symbol,
                "side": side.upper(),
                "entry": entry,
                "sl": sl,
                "tp2": tp2,
                "status": "PENDING",
                "order_id": order['id'],
                "amount": amount,
                "sl_order_id": sl_order_id,
                "tp2_order_id": tp2_order_id,
                "created_at": pytime.time()
            }

        # =========================
        # TELEGRAM
        # =========================

        message = f"""
✅ ORDER EXECUTED

{symbol}

Side:
{side.upper()}

Entry:
{entry}

SL:
{sl}

TP2:
{tp2}

Leverage:
x{main_mod.LEVERAGE}

Margin:
{main_mod.MARGIN_PER_TRADE} USDT
"""

        main_mod.send_telegram(message)

    except Exception as e:

        main_mod.send_telegram(
            f"❌ ORDER ERROR\n\n{str(e)}"
        )
