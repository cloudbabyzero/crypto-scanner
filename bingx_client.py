import sys
import uuid

# Reference main module globals
main_mod = sys.modules["__main__"]

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
    """
    # BingX hedge mode rejects reduceOnly on protection orders.
    base_params = {
        'positionSide': side_cfg['position_side'],
        'closePosition': True
    }

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
    except Exception:
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

    return sl_order['id'], tp2_order['id']

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
                "tp2_order_id": tp2_order_id
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
