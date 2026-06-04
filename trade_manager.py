import sys
import time
import traceback
import uuid
import bingx_client
from telegram_commands import status
import google_sheet
from config import DEBUG_ORDER_STATUS

# Import custom exception
from bingx_client import PositionNotExistError

# Reference main module globals
main_mod = sys.modules["__main__"]

# =========================
# CLEANUP CLOSED TRADES
# =========================

def cleanup_closed_trades():

    try:

        all_positions = main_mod.exchange.fetch_positions()

        open_symbols = set()

        for pos in all_positions:

            try:

                contracts = float(pos.get('contracts') or 0)

            except (TypeError, ValueError):

                contracts = 0

            if contracts > 0:

                open_symbols.add(pos['symbol'])

    except Exception:

        print(
            "cleanup_closed_trades: fetch_positions failed",
            flush=True
        )

        print(
            traceback.format_exc(),
            flush=True
        )

        return

    now = time.time()

    with main_mod.state_lock:

        seen_symbols = set()

        to_remove = []

        for trade_id, trade in main_mod.active_trades.items():

            status = trade.get("status")

            if status == "SIGNAL":

                age = now - trade.get("created_at", now)

                if age > 3600:

                    to_remove.append(trade_id)

                continue

            if status == "PENDING":

                continue

            symbol = trade['symbol']

            if symbol not in open_symbols or symbol in seen_symbols:

                to_remove.append(trade_id)

            else:

                seen_symbols.add(symbol)

        for trade_id in to_remove:

            main_mod.active_trades.pop(trade_id, None)

# =========================
# DETERMINE TRADE RESULT (WIN/LOSS)
# =========================

def _determine_trade_result(trade, symbol):
    """Determine WIN or LOSS for a closed position using multiple sources of truth.
    
    This function uses a fallback hierarchy instead of relying solely on TP order status:
    1. Check if SL order was filled → LOSS
    2. Check if TP order was filled → WIN
    3. Fetch recent closed orders to find which order closed the position
    4. Compare close price to TP/SL thresholds
    5. Last resort: check realized PnL or default conservative LOSS
    
    Args:
        trade: Trade dict with entry, sl, tp2, sl_order_id, tp2_order_id
        symbol: Trading symbol
        
    Returns:
        "WIN" or "LOSS"
    """
    try:
        # ============================================================
        # PRIORITY 1: Check SL order status - highest confidence LOSS
        # ============================================================
        sl_order_id = trade.get('sl_order_id')
        if sl_order_id and sl_order_id not in ["existing_sl", None]:
            try:
                sl_info = main_mod.exchange.fetch_order(sl_order_id, symbol)
                if sl_info.get('status') == 'closed':
                    print(f"[WIN/LOSS] {symbol}: SL order filled → LOSS (order {sl_order_id})", flush=True)
                    return "LOSS"
            except Exception:
                # Order not found or fetch failed - continue to next check
                pass

        # ============================================================
        # PRIORITY 2: Check TP order status - highest confidence WIN
        # ============================================================
        tp_order_id = trade.get('tp2_order_id')
        if tp_order_id and tp_order_id not in ["existing_tp", None]:
            try:
                tp_info = main_mod.exchange.fetch_order(tp_order_id, symbol)
                if tp_info.get('status') == 'closed':
                    print(f"[WIN/LOSS] {symbol}: TP order filled → WIN (order {tp_order_id})", flush=True)
                    return "WIN"
            except Exception:
                # Order not found or fetch failed - continue to next check
                pass

        # ============================================================
        # PRIORITY 3: Fetch closed orders to infer close mechanism
        # ============================================================
        try:
            closed_orders = main_mod.exchange.fetch_closed_orders(symbol, limit=50)
            
            if closed_orders:
                # Look for recent SL/TP orders (most recent first)
                for order in reversed(closed_orders[-20:]):  # Check last 20 closed orders
                    if order.get('status') != 'closed':
                        continue
                    
                    order_type = str(order.get('type', '')).upper()
                    order_side = str(order.get('side', '')).upper()
                    trade_side = trade.get('side', 'LONG').upper()
                    
                    # Position closing orders have opposite side
                    if trade_side == 'LONG':
                        expected_close_side = 'SELL'
                    else:
                        expected_close_side = 'BUY'
                    
                    # Match side for closing order
                    if order_side != expected_close_side:
                        continue
                    
                    # Check for SL order (STOP_MARKET, STOP, etc.)
                    if 'STOP' in order_type or 'SL' in order_type:
                        print(f"[WIN/LOSS] {symbol}: Recent SL order found in closed orders → LOSS ({order.get('id')})", flush=True)
                        return "LOSS"
                    
                    # Check for TP order (TAKE_PROFIT, TP, etc.)
                    if 'TAKE' in order_type or 'PROFIT' in order_type or 'TP' in order_type:
                        print(f"[WIN/LOSS] {symbol}: Recent TP order found in closed orders → WIN ({order.get('id')})", flush=True)
                        return "WIN"
        except Exception as e:
            # fetch_closed_orders not available or failed
            pass

        # ============================================================
        # PRIORITY 4: Compare position close price to TP/SL levels
        # ============================================================
        try:
            # Fetch recent trades to see at what price position was closed
            recent_trades = main_mod.exchange.fetch_my_trades(symbol, limit=20)
            
            if recent_trades:
                # Most recent trade is likely the position close
                latest_trade = recent_trades[-1]
                close_price = latest_trade.get('average') or latest_trade.get('price')
                
                if close_price:
                    entry_price = trade.get('entry')
                    tp_price = trade.get('tp2')
                    sl_price = trade.get('sl')
                    side = trade.get('side', 'LONG')
                    
                    if tp_price and sl_price and entry_price:
                        print(f"[WIN/LOSS] {symbol}: Position closed at {close_price} "
                              f"(Entry={entry_price}, SL={sl_price}, TP={tp_price})", flush=True)
                        
                        if side == 'LONG':
                            # For LONG: close_price >= tp_price → WIN, close_price <= sl_price → LOSS
                            if close_price >= tp_price * 0.99:  # Allow 1% slippage
                                print(f"[WIN/LOSS] {symbol}: Close price >= TP threshold → WIN", flush=True)
                                return "WIN"
                            elif close_price <= sl_price * 1.01:  # Allow 1% slippage
                                print(f"[WIN/LOSS] {symbol}: Close price <= SL threshold → LOSS", flush=True)
                                return "LOSS"
                        else:  # SHORT
                            # For SHORT: close_price <= tp_price → WIN, close_price >= sl_price → LOSS
                            if close_price <= tp_price * 1.01:  # Allow 1% slippage
                                print(f"[WIN/LOSS] {symbol}: Close price <= TP threshold (SHORT) → WIN", flush=True)
                                return "WIN"
                            elif close_price >= sl_price * 0.99:  # Allow 1% slippage
                                print(f"[WIN/LOSS] {symbol}: Close price >= SL threshold (SHORT) → LOSS", flush=True)
                                return "LOSS"
        except Exception as e:
            # fetch_my_trades not available or failed
            pass

        # ============================================================
        # FALLBACK: Conservative default to LOSS
        # ============================================================
        # If we can't determine, default to LOSS (conservative)
        # This avoids false WINS when data is unavailable
        print(f"[WIN/LOSS] {symbol}: Unable to determine result, defaulting to LOSS (conservative)", flush=True)
        return "LOSS"
        
    except Exception as e:
        print(f"[WIN/LOSS] Error determining result for {symbol}: {e}", flush=True)
        return "LOSS"

# =========================
# TRADE CHECKER
# =========================

def check_trades():

    while True:

        try:

            with main_mod.state_lock:
                trades_snapshot = list(main_mod.active_trades.items())

            for signal_id, trade in trades_snapshot:

                # =========================
                # WAIT FOR LIMIT FILL
                # =========================

                if trade.get('status') == "PENDING":

                    order_info = main_mod.exchange.fetch_order(
                        trade['order_id'],
                        trade['symbol']
                    )

                    status = str(order_info.get('status', '')).lower()
                    # Temporary debug: report order status via Telegram
                    
                    if DEBUG_ORDER_STATUS:
                    
                        main_mod.send_telegram(
                        f"🔍 ORDER STATUS\n\n"
                        f"{trade['symbol']}\n"
                        f"status={status}"
                    )
                    if status in ['closed', 'filled']:
                    
                        amount = trade['amount']
                        side_cfg = main_mod.get_side_config(trade['side'])

                        # =========================
                        # ENSURE PROTECTION
                        # =========================

                        sl_order_id = trade.get('sl_order_id')
                        tp2_order_id = trade.get('tp2_order_id')

                        # -------------------------------------------------
                        # The limit order has been filled. The original
                        # entry price used for signal generation may differ
                        # from the actual fill price (e.g., due to slippage).
                        # For SHORT positions this caused the stop‑loss to be
                        # placed below the filled entry price, which BingX
                        # rejects. We now recalculate SL and TP2 based on the
                        # real fill price while preserving the original ATR
                        # distance.
                        # -------------------------------------------------

                        # 1. Determine the actual filled entry price.
                        #    ccxt returns the average fill price under the
                        #    "average" key for most exchanges; fall back to
                        #    "price" if unavailable.
                        filled_entry = float(
                            order_info.get('average')
                            or order_info.get('price')
                            or trade['entry']
                        )

                        # 2. Derive the original ATR distance from the
                        #    signal‑based entry and SL (ATR = |entry‑SL| / 1.5).
                        original_atr = abs(trade['entry'] - trade['sl']) / 1.5

                        # 3. Re‑calculate SL and TP2 using the filled entry.
                        new_sl, _, new_tp2, _ = main_mod.calculate_trade_levels(
                            filled_entry,
                            original_atr,
                            trade['side']
                        )

                        # 4. Update trade dict with the actual entry and new
                        #    protection levels.
                        trade['entry'] = filled_entry
                        trade['sl'] = new_sl
                        trade['tp2'] = new_tp2

                        # 5. (Re)place protection orders using the updated
                        #    prices. We always place them here because the
                        #    previous attempt may have failed or used stale
                        #    values.
                        try:
                            sl_order_id, tp2_order_id = bingx_client.place_protection_orders(
                                symbol=trade['symbol'],
                                side_cfg=side_cfg,
                                sl_price=trade['sl'],
                                tp2_price=trade['tp2'],
                                amount=amount
                            )
                            
                            # Protection orders placed successfully (or already existed - code 110406)
                            with main_mod.state_lock:
                                trade['status'] = "OPEN"
                                trade['sl_order_id'] = sl_order_id
                                trade['tp2_order_id'] = tp2_order_id

                            main_mod.send_telegram(
                                f"✅ ORDER FILLED\n\n"
                                f"{trade['symbol']}"
                            )
                        except PositionNotExistError as e:
                            # Position no longer exists - TERMINAL condition
                            main_mod.send_telegram(
                                f"🚨 POSITION CLOSED MANUALLY\n\n"
                                f"{trade['symbol']}\n\n"
                                f"Position no longer exists - removing from active trades"
                            )
                            
                            # Mark trade as closed and remove from active_trades
                            with main_mod.state_lock:
                                trade['closed'] = True
                                trade['status'] = "CLOSED"
                                main_mod.active_trades.pop(signal_id, None)
                            
                            # Continue to next trade - no retry
                            continue
                        except Exception as e:
                            # Protection order placement failed (real error, not 110406)
                            main_mod.send_telegram(
                                f"🚨 PROTECTION FAILED AFTER FILL\n\n"
                                f"{trade['symbol']}\n\n"
                                f"{str(e)}"
                            )
                            # Log the actual exception for Railway debugging
                            print(f"[TRADE_MANAGER] Protection error for {trade['symbol']}: {e}", flush=True)
                            print(f"[TRADE_MANAGER] Traceback: {traceback.format_exc()}", flush=True)
                            # Don't continue - will retry on next check_trades iteration
                            continue


                    elif status in [
                        'canceled', 'expired', 'rejected'
                    ]:

                        main_mod.send_telegram(
                            f"⚠️ ORDER {order_info['status'].upper()}\n\n"
                            f"{trade['symbol']}"
                        )

                        # Remove the trade and continue loop for this signal
                        with main_mod.state_lock:
                            main_mod.active_trades.pop(signal_id, None)

                        continue

                    else:
                        # Pending order still open – check expiration (>30 minutes)
                        now = time.time()
                        created_at = trade.get('created_at', now)
                        age = now - created_at

                        if age > 1800:  # 30 minutes = 1800 seconds
                            # Cancel the stale pending order
                            try:
                                main_mod.exchange.cancel_order(
                                    trade['order_id'],
                                    trade['symbol']
                                )
                            except Exception:
                                pass

                            main_mod.send_telegram(
                                f"⚠️ PENDING ORDER EXPIRED\n\n"
                                f"{trade['symbol']}\n\n"
                                f"Reason:\n"
                                f"Pending more than 30 minutes"
                            )

                            # Remove the trade only when it has been expired
                            with main_mod.state_lock:
                                main_mod.active_trades.pop(signal_id, None)

                        # If not expired, keep the pending trade in active_trades
                        continue

                # =========================
                # SKIP SIGNAL
                # =========================

                if trade.get('status') == "SIGNAL":

                    continue

                # =========================
                # CHECK REAL POSITION
                # =========================

                try:

                    positions = main_mod.exchange.fetch_positions(
                        [trade['symbol']]
                    )

                    contracts = 0

                    for pos in positions:

                        try:

                            contracts += float(
                                pos.get('contracts') or 0
                            )

                        except (TypeError, ValueError):

                            pass

                except Exception:

                    contracts = 0

                # =========================
                # POSITION CLOSED
                # =========================

                if (
                    contracts <= 0
                    and not trade.get('closed')
                ):

                    # ============================================================
                    # DETERMINE WIN/LOSS using robust multi-source logic
                    # ============================================================
                    result = _determine_trade_result(trade, trade['symbol'])

                    if result == "WIN":

                        main_mod.send_telegram(
                            f"🏆 WIN\n\n"
                            f"{trade['symbol']}"
                        )

                        main_mod.update_signal_result(
                            signal_id,
                            "WIN"
                        )
                        
                        # Google Sheets logging for WIN
                        try:
                            entry_price = trade.get('entry', 0)
                            exit_price = trade.get('tp2', entry_price)
                            pnl = 0
                            rr = 1.0
                            grade = trade.get('grade', 'C')
                            score = trade.get('score', 0)
                            
                            google_sheet.log_trade(
                                symbol=trade['symbol'],
                                side=trade['side'],
                                entry=entry_price,
                                exit_price=exit_price,
                                pnl=pnl,
                                result="WIN",
                                grade=grade,
                                score=score,
                                rr=rr
                            )
                        except Exception as e:
                            print(f"[GOOGLE_SHEETS] Trade log error: {e}", flush=True)

                    else:  # result == "LOSS"

                        main_mod.send_telegram(
                            f"❌ LOSS\n\n"
                            f"{trade['symbol']}"
                        )

                        main_mod.update_signal_result(
                            signal_id,
                            "LOSS"
                        )
                        
                        # Google Sheets logging for LOSS
                        try:
                            entry_price = trade.get('entry', 0)
                            exit_price = trade.get('sl', entry_price)
                            pnl = 0
                            rr = 1.0
                            grade = trade.get('grade', 'C')
                            score = trade.get('score', 0)
                            
                            google_sheet.log_trade(
                                symbol=trade['symbol'],
                                side=trade['side'],
                                entry=entry_price,
                                exit_price=exit_price,
                                pnl=pnl,
                                result="LOSS",
                                grade=grade,
                                score=score,
                                rr=rr
                            )
                        except Exception as e:
                            print(f"[GOOGLE_SHEETS] Trade log error: {e}", flush=True)

                    with main_mod.state_lock:

                        trade['closed'] = True

                        main_mod.active_trades.pop(signal_id, None)

                    continue

            time.sleep(60)

        except Exception:

            print(
                "Trade checker error",
                flush=True
            )

            print(
                traceback.format_exc(),
                flush=True
            )

            time.sleep(30)

# =========================
# RESTORE OPEN POSITIONS
# =========================

def _fetch_protection_orders_for_position(symbol, position_side):
    """Fetch SL/TP orders for a restored position.
    
    Args:
        symbol: Trading symbol (e.g., 'BTC/USDT:USDT')
        position_side: Position side ('LONG' or 'SHORT')
        
    Returns:
        Dict with keys: sl_price, tp_price, sl_order_id, tp_order_id
        Returns None values if orders not found
    """
    try:
        # Fetch all open orders for the symbol
        orders = main_mod.exchange.fetch_open_orders(symbol)
        
        sl_order_id = None
        tp_order_id = None
        sl_price = None
        tp_price = None
        
        for order in orders:
            try:
                # Check if this order belongs to our position
                order_side = (order.get('side') or '').upper()
                order_type = (order.get('type') or '').upper()
                order_info = order.get('info', {})
                
                # For a LONG position, SL is a SELL, TP is a SELL
                # For a SHORT position, SL is a BUY, TP is a BUY
                if position_side == 'LONG':
                    target_side = 'SELL'
                else:
                    target_side = 'BUY'
                
                if order_side != target_side:
                    continue
                
                # Match STOP_LOSS or SL orders
                if order_type in ['STOP_MARKET', 'STOP', 'STOP_LOSS'] or \
                   'stop' in str(order_type).lower():
                    if sl_order_id is None:  # Take first SL found
                        sl_order_id = order.get('id')
                        sl_price = order.get('stopPrice') or order.get('info', {}).get('stopPrice')
                
                # Match TAKE_PROFIT or TP orders
                elif order_type in ['TAKE_PROFIT_MARKET', 'TAKE_PROFIT', 'TP'] or \
                     'profit' in str(order_type).lower():
                    if tp_order_id is None:  # Take first TP found
                        tp_order_id = order.get('id')
                        tp_price = order.get('stopPrice') or order.get('info', {}).get('stopPrice')
            except Exception:
                continue
        
        return {
            'sl_price': sl_price,
            'tp_price': tp_price,
            'sl_order_id': sl_order_id,
            'tp_order_id': tp_order_id
        }
    except Exception as e:
        print(f"[RESTORE] fetch_protection_orders error for {symbol}: {e}", flush=True)
        return {
            'sl_price': None,
            'tp_price': None,
            'sl_order_id': None,
            'tp_order_id': None
        }


def restore_open_positions():
    """Restore open positions from exchange after bot restart.
    
    This function:
    1. Fetches all open positions from BingX
    2. Creates internal trade objects to track them
    3. Restores protection order information (SL/TP prices and order IDs)
    4. Ensures restored trades behave identically to non-restored trades
    """
    try:
        positions = main_mod.exchange.fetch_positions()
    except Exception:
        print(
            "restore_open_positions: fetch_positions failed",
            flush=True
        )
        print(
            traceback.format_exc(),
            flush=True
        )
        return

    restored_count = 0

    for pos in positions:
        try:
            contracts = abs(float(pos.get('contracts') or 0))
        except (TypeError, ValueError):
            continue

        if contracts <= 0:
            continue

        symbol = pos.get('symbol')
        if not symbol:
            continue

        position_side = (
            pos.get('side') or
            pos.get('positionSide') or
            ''
        ).upper()

        if position_side in ['LONG', 'BUY']:
            side = 'LONG'
        elif position_side in ['SHORT', 'SELL']:
            side = 'SHORT'
        else:
            side = 'LONG'

        entry_price = pos.get('entryPrice') or pos.get('markPrice') or 0

        trade_id = f"restored_{str(uuid.uuid4())[:8]}"

        with main_mod.state_lock:
            already_tracked = any(
                t['symbol'] == symbol
                and t.get('status') in ['PENDING', 'OPEN']
                for t in main_mod.active_trades.values()
            )

            if already_tracked:
                continue

            # ============================================================
            # RESTORE PROTECTION ORDERS
            # ============================================================
            # Fetch SL/TP orders for this position to restore protection state
            protection = _fetch_protection_orders_for_position(symbol, side)
            
            # Determine SL and TP prices
            # Preference: use fetched prices if available, otherwise entry_price as fallback
            sl_price = protection['sl_price'] if protection['sl_price'] is not None else entry_price
            tp_price = protection['tp_price'] if protection['tp_price'] is not None else entry_price

            # Create trade object with all required fields for consistency
            # This ensures restored trades have the same structure as non-restored trades
            main_mod.active_trades[trade_id] = {
                "symbol": symbol,
                "status": "OPEN",
                "side": side,
                "entry": entry_price,
                "amount": contracts,
                "sl": sl_price,
                "tp2": tp_price,
                "sl_order_id": protection['sl_order_id'],
                "tp2_order_id": protection['tp_order_id'],
                # Set default grade/score for consistency with non-restored trades
                # These may be refined later if metadata is available
                "grade": "C",
                "score": 50,
                "restored": True
            }

        restored_count += 1

        print(
            f"Restored: {symbol} {side} {contracts} "
            f"(SL_ID={protection['sl_order_id']}, TP_ID={protection['tp_order_id']})",
            flush=True
        )

    if restored_count > 0:
        main_mod.send_telegram(
            f"🔄 Restored {restored_count} open position(s) from BingX"
        )
    else:
        print(
            "restore_open_positions: no open positions found",
            flush=True
        )


# =========================
# CANCEL PENDING ORDERS
# =========================

def cancel_pending_orders(reason):
    """Cancel all pending orders with a given reason."""
    # Collect pending trades under lock to avoid holding the lock while
    # performing network operations.
    with main_mod.state_lock:
        pending_trades = [
            (trade_id, trade)
            for trade_id, trade in main_mod.active_trades.items()
            if trade.get('status') == "PENDING"
        ]

    for trade_id, trade in pending_trades:
        try:
            main_mod.exchange.cancel_order(
                trade['order_id'],
                trade['symbol']
            )
        except Exception:
            # Ignore cancel errors; we still remove local tracking
            pass

        main_mod.send_telegram(
            f"⚠️ PENDING ORDER CANCELLED\n\n"
            f"{trade['symbol']}\n\n"
            f"Reason:\n"
            f"{reason}"
        )

        with main_mod.state_lock:
            main_mod.active_trades.pop(trade_id, None)