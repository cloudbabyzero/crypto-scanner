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

class StopLossBreachedError(Exception):
    """Raised when BingX returns code 110412.
    
    This indicates that the Stop Loss price provided has already been breached
    by the current market price, so the protection order is invalid.
    The position should be closed at MARKET immediately.
    """
    BINGX_CODE = 110412

    def __init__(self, symbol, message="Stop Loss price breached", original_error=None):
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
        error_str = str(exception)
        
        # CCXT wraps the response in args[0] or response attribute
        if hasattr(exception, 'args') and exception.args:
            response = exception.args[0]
            if isinstance(response, str):
                # CCXT sometimes prepends the exchange name: 'bingx {"code":110412,...}'
                if '{' in response:
                    json_str = response[response.find('{'):]
                    try:
                        data = json.loads(json_str)
                        if isinstance(data, dict) and 'code' in data:
                            return data.get('code'), data.get('msg', '')
                    except (json.JSONDecodeError, TypeError):
                        pass
        
        # Check exception response attribute
        if hasattr(exception, 'response'):
            response = exception.response
            if isinstance(response, dict) and 'code' in response:
                return response.get('code'), response.get('msg', '')
        
        # Last resort: check if error message contains code
        if '110406' in error_str:
            return 110406, error_str
        if '109420' in error_str:
            return 109420, error_str
        if '110412' in error_str:
            return 110412, error_str
            
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
    """Cancel a specific order.
    
    Args:
        order_id: Order ID to cancel
        symbol: Trading symbol
        
    Returns:
        Response from exchange
    """
    try:
        return main_mod.exchange.cancel_order(order_id, symbol)
    except Exception as e:
        print(f"cancel_order error: {e}", flush=True)
        raise

def close_position_market(symbol, side, amount):
    """Close an open position at market price.
    
    Args:
        symbol: Trading symbol
        side: Position side ('LONG' or 'SHORT')
        amount: Position size to close
        
    Returns:
        Response from exchange
    """
    position_side = side.upper()
    order_side = 'sell' if position_side == 'LONG' else 'buy'
    
    print(f"[BINGX] Emergency Market Close for {symbol} {position_side}", flush=True)
    try:
        return main_mod.exchange.create_order(
            symbol=symbol,
            type='market',
            side=order_side,
            amount=amount,
            params={
                'positionSide': position_side,
                'tradeSide': 'CLOSE',
                'closePosition': True
            }
        )
    except Exception as e:
        print(f"close_position_market error: {e}", flush=True)
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
        elif error_code == 110406:
            # Protection already in place - treat as success
            print(f"[BINGX] SL already exists for {symbol} (code 110406) - OK", flush=True)
            # Return dummy ID to indicate protection exists (but we don't have order ID)
            sl_order_id = "existing_sl"
        elif error_code == 110412:
            # SL price breached
            logger.error(f"Stop Loss breached for {symbol} (code 110412) - SL price {sl_price} invalid")
            raise StopLossBreachedError(symbol, error_msg or "Stop Loss breached", e)
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
            # Check if this is error 110406 or 110407 for TP
            elif error_code in [110406, 110407]:
                # TP already in place - treat as success
                print(f"[BINGX] TP already exists for {symbol} (code {error_code}) - OK", flush=True)
                tp2_order_id = "existing_tp"
            else:
                # Log other errors for debugging
                logger.exception(f"TP order placement failed for {symbol}: {e}")
                # Re-raise for other errors
                raise

    return sl_order_id, tp2_order_id


def update_sl_order(symbol, side_cfg, old_sl_id, new_sl_price, amount):
    """Update stop-loss order by cancelling old and creating new."""
    if old_sl_id and old_sl_id not in ["existing_sl", None]:
        try:
            cancel_order(old_sl_id, symbol)
        except Exception as e:
            logger.warning(f"Failed to cancel old SL {old_sl_id} for {symbol}: {e}")
            pass

    base_params = {
        'positionSide': side_cfg['position_side'],
        'closePosition': True
    }

    try:
        sl_order = main_mod.exchange.create_order(
            symbol=symbol,
            type='STOP_MARKET',
            side=side_cfg['stop_side'],
            amount=amount,
            params={
                **base_params,
                'stopPrice': new_sl_price
            }
        )
        return sl_order['id']
    except Exception as e:
        logger.error(f"Failed to create new SL for {symbol}: {e}")
        return None


# =========================
# TRADE EXECUTION
# =========================

def execute_trade(symbol, side, skip_pullback_check=False):
    """Execute a trade entry with protection orders.
    
    Args:
        symbol: Trading symbol (e.g., 'BTC/USDT:USDT')
        side: 'long' or 'short'
        skip_pullback_check: If True, bypass pullback distance validation.
                             Use for Momentum mode where entry is intentionally
                             close to current price.
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

        signal = main_mod.get_latest_signal(symbol, side=side)

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
        tp2 = signal["tp"]
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

            cfg = main_mod.get_strategy_config(signal.get('signal_regime', 'TRENDING'))

            main_mod.exchange.set_leverage(
                cfg['LEVERAGE'],
                symbol,
                {
                     "side": "LONG"
                }
            )

        else:

            cfg = main_mod.get_strategy_config(signal.get('signal_regime', 'TRENDING'))

            main_mod.exchange.set_leverage(
                cfg['LEVERAGE'],
                symbol,
                {
                    "side": "SHORT"
                }
            )

        # =========================
        # AMOUNT
        # =========================

        raw_amount = (
            cfg['MARGIN_PER_TRADE'] * cfg['LEVERAGE']
        ) / entry

        amount = main_mod.exchange.amount_to_precision(
            symbol,
            raw_amount
        )

        amount = float(amount)

        # =========================
        # CURRENT PRICE
        # =========================

        ticker = main_mod.exchange.fetch_ticker(symbol)
        current_price = ticker['last']

        # =========================
        # ENTRY_TYPE LOGIC
        # =========================
        
        entry_type = cfg.get('ENTRY_TYPE', 'LIMIT_PULLBACK')
        
        if entry_type == 'MARKET':
            # =========================
            # MARKET ENTRY
            # =========================
            order = main_mod.exchange.create_order(
                symbol=symbol,
                type='market',
                side='buy' if side == 'long' else 'sell',
                amount=amount,
                params={
                    'positionSide': 'LONG' if side == 'long' else 'SHORT',
                    'tradeSide': 'OPEN',
                    'marginMode': 'isolated'
                }
            )
            
            # Market fill is immediate, recalculate actual entry and SL/TP
            ticker = main_mod.exchange.fetch_ticker(symbol)
            actual_entry = ticker['last']
            entry = actual_entry  # Update for state save
            
            atr_val = signal['atr']
            if side == 'long':
                sl  = round(actual_entry - atr_val * cfg['SL_ATR_MULT'], 4)
                tp2 = round(actual_entry + (actual_entry - sl) * cfg['TP_RR'], 4)
            else:
                sl  = round(actual_entry + atr_val * cfg['SL_ATR_MULT'], 4)
                tp2 = round(actual_entry - (sl - actual_entry) * cfg['TP_RR'], 4)

        else:
            # =========================
            # PULLBACK VALIDATION (LIMIT ORDERS)
            # =========================

            # 1) Price has already passed the entry (no pullback opportunity)
            if (
                (side == "long" and current_price <= entry)
                or
                (side != "long" and current_price >= entry)
            ):
                logger.info(
                    "Pullback passed %s side=%s current=%.4f entry=%.4f atr=%.4f",
                    symbol, side, current_price, entry, atr
                )
                main_mod.send_telegram(
                    f"⚠️ Pullback already passed – skipping order\n\n"
                    f"{symbol}\n"
                    f"Side: {side.upper()}\n"
                    f"Current: {current_price}\n"
                    f"Entry: {entry}\n"
                    f"ATR: {atr:.4f}"
                )
                return

            # 2) Distance too small
            distance_pct = abs(current_price - entry) / current_price * 100
            min_dist = main_mod.PULLBACK_MIN_DISTANCE_PCT
            if not skip_pullback_check and distance_pct < min_dist:
                logger.info(
                    "Pullback shallow %s side=%s dist=%.2f%% min=%.2f%% atr=%.4f",
                    symbol, side, distance_pct, min_dist, atr
                )
                main_mod.send_telegram(
                    f"⚠️ Pullback too shallow – skipping order\n\n"
                    f"{symbol}\n"
                    f"Side: {side.upper()}\n"
                    f"Current: {current_price}\n"
                    f"Entry: {entry}\n"
                    f"Distance: {distance_pct:.2f}% (min: {min_dist}%)\n"
                    f"ATR: {atr:.4f}"
                )
                return

            # =========================
            # LIMIT ORDER EXECUTION
            # =========================

            order = main_mod.exchange.create_order(
                symbol=symbol,
                type='limit',
                side='buy' if side == 'long' else 'sell',
                amount=amount,
                price=entry,
                params={
                    'positionSide': 'LONG' if side == 'long' else 'SHORT',
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
            # Position does not exist yet because the limit order has not been
            # filled. This is EXPECTED — BingX rejects protection orders when
            # there is no open position. Save as PENDING; protection will be
            # placed after fill detection in check_trades().
            logger.warning(f"Position not exist for {symbol}: {e} (expected for limit orders before fill)")
            main_mod.send_telegram(
                f"⚠️ Protection pre-set delayed for {symbol}\n"
                f"Limit order pending — protection will be applied after fill."
            )
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

        # Fetch grade/score/strategy from the signal so A+ override logic can compare later
        _signal_for_grade = main_mod.get_latest_signal(symbol)
        _signal_grade = "C"
        _signal_score = 0
        _signal_strategy = "UNKNOWN"
        if _signal_for_grade:
            _signal_grade = _signal_for_grade.get("grade", "C")
            _signal_score = _signal_for_grade.get("score", 0)
            _signal_strategy = _signal_for_grade.get("strategy", "UNKNOWN")

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
                "created_at": pytime.time(),
                "grade": _signal_grade,
                "score": _signal_score,
                "strategy": _signal_strategy,
                "atr": atr
            }

        # =========================
        # TELEGRAM
        # =========================

        message = f"""
✅ ORDER EXECUTED

{symbol}

Mode:
{_signal_strategy}

Side:
{side.upper()}

Entry:
{entry}

SL:
{sl}

TP2:
{tp2}

Leverage:
x{cfg['LEVERAGE']}

Margin:
{cfg['MARGIN_PER_TRADE']} USDT
"""

        main_mod.send_telegram(message)

    except Exception as e:

        main_mod.send_telegram(
            f"❌ ORDER ERROR\n\n{str(e)}"
        )


# =========================
# SCALPING TRADE EXECUTION
# =========================

def execute_scalp_trade(symbol, side):
    """Execute scalping trade with market order + immediate protection.

    Key differences from execute_trade():
    - Market order (instant fill, no pullback wait)
    - Uses SCALPING-specific leverage and margin
    - Places SL/TP immediately after market fill
    - Shorter pending expiry (5 min vs 60 min)

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
        # CHECK SYMBOL (must be in scalping list)
        # =========================

        from config import SCALPING_SYMBOLS
        if symbol not in SCALPING_SYMBOLS:
            main_mod.send_telegram(f"❌ {symbol} not in SCALPING_SYMBOLS")
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
                and trade.get('strategy') == "SCALPING"
            ):
                main_mod.send_telegram(f"⚠️ {symbol} scalp already active")
                return

        # =========================
        # CHECK SCALPING POSITION LIMIT
        # =========================

        from config import STRATEGY_CONFIG
        max_scalp_trades = STRATEGY_CONFIG['SCALPING']['MAX_TRADES']
        with main_mod.state_lock:
            scalp_count = sum(
                1 for t in main_mod.active_trades.values()
                if t.get("status") in ["PENDING", "OPEN"]
                and t.get("strategy") == "SCALPING"
            )
        if scalp_count >= max_scalp_trades:
            main_mod.send_telegram(
                f"❌ SCALPING POSITION LIMIT\n\n"
                f"{symbol}\n"
                f"Max {max_scalp_trades} scalping trades"
            )
            return

        # =========================
        # GET SIGNAL
        # =========================

        signal = main_mod.get_latest_signal(symbol, side=side)

        if not signal:
            main_mod.send_telegram(f"❌ No scalping signal found for {symbol}")
            return

        signal_type = signal.get("signal", "").upper()
        if signal_type != side.upper():
            main_mod.send_telegram(
                f"❌ No {side.upper()} scalp signal for {symbol}\n"
                f"Current Signal: {signal_type}"
            )
            return

        # =========================
        # MARKET REGIME SAFETY
        # =========================

        signal_regime = signal.get("signal_regime", "UNKNOWN")
        if main_mod.CURRENT_REGIME != signal_regime:
            main_mod.send_telegram(
                f"⚠️ Scalp Signal Expired\n\n"
                f"Reason: Market Regime Changed\n"
                f"Signal: {signal_regime}\n"
                f"Current: {main_mod.CURRENT_REGIME}"
            )
            return

        entry = signal["entry"]
        atr   = signal["atr"]

        # =========================
        # MARGIN MODE + LEVERAGE (scalping-specific)
        # =========================

        cfg = main_mod.get_strategy_config('SCALPING')

        try:
            main_mod.exchange.set_margin_mode("isolated", symbol)
        except Exception:
            pass

        leverage_side = "LONG" if side == "long" else "SHORT"
        main_mod.exchange.set_leverage(
            cfg['LEVERAGE'],
            symbol,
            {"side": leverage_side}
        )

        # =========================
        # DYNAMIC RISK SIZING (Auto Snowball)
        # =========================

        # ดึงยอดเงินจริงจาก Exchange
        try:
            balance = main_mod.exchange.fetch_balance()
            current_usdt = float(balance['total'].get('USDT', 0.0))
        except Exception:
            current_usdt = cfg['MARGIN_PER_TRADE']  # fallback ถ้าดึงไม่ได้

        # Tiered Risk — SAFE MODE (ป้องกันพอร์ตแตก)
        if current_usdt < 10:
            risk_percent = 0.20   # Micro: ใช้ 20% ของพอร์ต (Max)
        elif current_usdt < 50:
            risk_percent = 0.15   # Small: ใช้ 15%
        else:
            risk_percent = 0.10   # Medium+: ใช้ 10% (institutional standard)

        calculated_margin = current_usdt * risk_percent

        # Safety: ขั้นต่ำ 0.5 USDT (exchange minimum)
        # ไม่มี cap สูงสุด — ให้ risk_percent ควบคุมขนาดเอง
        MIN_MARGIN = 0.5
        margin_to_use = max(MIN_MARGIN, calculated_margin)

        # =========================
        # AMOUNT
        # =========================

        raw_amount = (margin_to_use * cfg['LEVERAGE']) / entry
        amount = main_mod.exchange.amount_to_precision(symbol, raw_amount)
        amount = float(amount)

        # =========================
        # SL / TP
        # =========================

        

        if side == "long":
            sl   = round(entry - atr * cfg['SL_ATR_MULT'], 4)
            risk = entry - sl
            tp2  = round(entry + risk * cfg['TP_RR'], 4)
        else:
            sl   = round(entry + atr * cfg['SL_ATR_MULT'], 4)
            risk = sl - entry
            tp2  = round(entry - risk * cfg['TP_RR'], 4)

        # =========================
        # MARKET ORDER (instant fill)
        # =========================

        position_side = "LONG" if side == "long" else "SHORT"
        order_side    = "buy"  if side == "long" else "sell"

        order = main_mod.exchange.create_order(
            symbol=symbol,
            type='market',
            side=order_side,
            amount=amount,
            params={
                'positionSide': position_side,
                'tradeSide': 'OPEN',
                'marginMode': 'isolated'
            }
        )

        # =========================
        # Get actual fill price from market order
        # =========================

        filled_entry = float(
            order.get('average')
            or order.get('price')
            or entry
        )

        # Recalculate SL/TP based on actual fill price
        # Add spread buffer to TP to compensate for round-trip execution cost
        spread_buffer = filled_entry * 0.0002  # 0.02% spread compensation
        if side == "long":
            sl   = round(filled_entry - atr * cfg['SL_ATR_MULT'], 4)
            risk = filled_entry - sl
            tp2  = round(filled_entry + risk * cfg['TP_RR'] + spread_buffer, 4)
        else:
            sl   = round(filled_entry + atr * cfg['SL_ATR_MULT'], 4)
            risk = sl - filled_entry
            tp2  = round(filled_entry - risk * cfg['TP_RR'] - spread_buffer, 4)

        # =========================
        # PLACE PROTECTION ORDERS IMMEDIATELY
        # (market order = position already exists)
        # =========================

        side_cfg = main_mod.get_side_config(
            "LONG" if side == "long" else "SHORT"
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
            logger.error(f"Scalp position not exist for {symbol}: {e}")
            main_mod.send_telegram(
                f"🚨 SCALP POSITION NOT EXIST\n\n"
                f"{symbol}\n"
                f"Market order may have failed"
            )
            return
        except Exception as protect_error:
            main_mod.send_telegram(
                f"⚠️ SCALP PROTECTION FAILED\n\n"
                f"{symbol}\n"
                f"{str(protect_error)}\n"
                f"Bot will retry via check_trades."
            )

        # =========================
        # SAVE TRADE
        # =========================

        trade_id = str(uuid.uuid4())[:8]

        _signal_for_grade = main_mod.get_latest_signal(symbol)
        _signal_grade = "C"
        _signal_score = 0
        if _signal_for_grade:
            _signal_grade = _signal_for_grade.get("grade", "C")
            _signal_score = _signal_for_grade.get("score", 0)

        with main_mod.state_lock:
            main_mod.active_trades[trade_id] = {
                "symbol": symbol,
                "side": side.upper(),
                "entry": filled_entry,
                "sl": sl,
                "tp2": tp2,
                "status": "OPEN",  # Market order = already filled = OPEN
                "order_id": order['id'],
                "amount": amount,
                "sl_order_id": sl_order_id,
                "tp2_order_id": tp2_order_id,
                "created_at": pytime.time(),
                "grade": _signal_grade,
                "score": _signal_score,
                "strategy": "SCALPING",
                "margin_used": round(margin_to_use, 4),
                "portfolio_balance": round(current_usdt, 4),
            }

        # =========================
        # TELEGRAM
        # =========================

        message = f"""
⚡ SCALP ORDER FILLED

{symbol}

Mode:
SCALPING

Side:
{side.upper()}

Entry (Fill):
{filled_entry}

SL:
{sl}

TP:
{tp2}

Leverage:
x{cfg['LEVERAGE']}

💰 Portfolio:
{round(current_usdt, 2)} USDT

📊 Risk:
{round(risk_percent * 100, 1)}%

Margin Used:
{round(margin_to_use, 2)} USDT

Position Size:
{round(margin_to_use * cfg['LEVERAGE'], 2)} USDT
"""

        main_mod.send_telegram(message)

    except Exception as e:
        main_mod.send_telegram(
            f"❌ SCALP ORDER ERROR\n\n{str(e)}"
        )

