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
# TRAILING STOP
# =========================

def _process_trailing_stop(trade, current_price):
    from config import TRAILING_ACTIVATION_ATR, TRAILING_BUFFER_ATR, TRAILING_STEP_ATR
    import bingx_client
    import google_sheet
    
    # Only trail in TRENDING strategies
    regime = trade.get('strategy') or trade.get('signal_regime')
    if regime not in ["TRENDING", "TREND"]:
        return

    side = trade.get('side', 'LONG')
    entry_price = trade.get('entry', 0)
    current_sl = trade.get('sl', 0)
    current_atr = trade.get('atr', 0)
    tp_price = trade.get('tp2') or trade.get('tp')
    phase = trade.get('trailing_phase', 1)
    
    if current_atr <= 0 or current_price <= 0 or entry_price <= 0:
        return

    activation_dist = current_atr * TRAILING_ACTIVATION_ATR
    trailing_buffer = current_atr * TRAILING_BUFFER_ATR
    step_size = current_atr * TRAILING_STEP_ATR
    
    new_sl = current_sl
    
    if side == "LONG":
        # Check Phase 2 transition
        if phase == 1 and tp_price and current_price >= tp_price:
            # Enter Phase 2
            if trade.get('tp2_order_id'):
                try:
                    bingx_client.cancel_order(trade['tp2_order_id'], trade['symbol'])
                except Exception as e:
                    print(f"[TRAILING] Failed to cancel TP for Phase 2: {e}", flush=True)
                with main_mod.state_lock:
                    trade['tp2_order_id'] = None
            
            with main_mod.state_lock:
                trade['trailing_phase'] = 2
            phase = 2
            
            msg = (f"🚀 [INFINITY RUN] ชนเป้า TP แล้ว บังคับยึดกำไรเป้าหมาย และปล่อยรันเทรนด์!\n\n"
                   f"{trade['symbol']}\n"
                   f"Side: LONG\n"
                   f"Price: {current_price}")
            main_mod.send_telegram(msg)
            google_sheet.log_event(trade['symbol'], side, "TRAILING_PHASE_2", f"Price {current_price} >= TP {tp_price}. Phase 2 activated.")
            print(f"[TRAILING] {trade['symbol']} ENTERED PHASE 2", flush=True)

        if phase == 2:
            phase_2_buffer = current_atr * 1.5  # Sweet spot
            proposed_sl = current_price - phase_2_buffer
            min_lock = tp_price * 0.9985 if tp_price else entry_price * 1.0015
            new_sl = max(current_sl, proposed_sl, min_lock)
        else:
            if current_price >= (entry_price + activation_dist):
                breakeven = entry_price * 1.0015
                proposed_sl = current_price - trailing_buffer
                new_sl = max(current_sl, proposed_sl, breakeven)
            
        if new_sl > current_sl + step_size or (phase == 2 and new_sl > current_sl):
            side_cfg = main_mod.get_side_config(side)
            amount = trade.get('amount')
            new_id = bingx_client.update_sl_order(trade['symbol'], side_cfg, trade.get('sl_order_id'), new_sl, amount)
            if new_id:
                with main_mod.state_lock:
                    trade['sl'] = new_sl
                    trade['sl_order_id'] = new_id
                
                prefix = "🚀 [INFINITY]" if phase == 2 else "🛡️ [TRAILING STOP]"
                msg = (f"{prefix} ขยับบังทุน\n\n"
                       f"{trade['symbol']}\n"
                       f"Side: {side}\n"
                       f"New SL: {new_sl}")
                main_mod.send_telegram(msg)
                print(f"[TRAILING] {trade['symbol']} LONG SL updated to {new_sl} (Phase {phase})", flush=True)

    elif side == "SHORT":
        # Check Phase 2 transition
        if phase == 1 and tp_price and current_price <= tp_price:
            # Enter Phase 2
            if trade.get('tp2_order_id'):
                try:
                    bingx_client.cancel_order(trade['tp2_order_id'], trade['symbol'])
                except Exception as e:
                    print(f"[TRAILING] Failed to cancel TP for Phase 2: {e}", flush=True)
                with main_mod.state_lock:
                    trade['tp2_order_id'] = None
            
            with main_mod.state_lock:
                trade['trailing_phase'] = 2
            phase = 2
            
            msg = (f"🚀 [INFINITY RUN] ชนเป้า TP แล้ว บังคับยึดกำไรเป้าหมาย และปล่อยรันเทรนด์!\n\n"
                   f"{trade['symbol']}\n"
                   f"Side: SHORT\n"
                   f"Price: {current_price}")
            main_mod.send_telegram(msg)
            google_sheet.log_event(trade['symbol'], side, "TRAILING_PHASE_2", f"Price {current_price} <= TP {tp_price}. Phase 2 activated.")
            print(f"[TRAILING] {trade['symbol']} ENTERED PHASE 2", flush=True)

        if phase == 2:
            phase_2_buffer = current_atr * 1.5  # Sweet spot
            proposed_sl = current_price + phase_2_buffer
            min_lock = tp_price * 1.0015 if tp_price else entry_price * 0.9985
            new_sl = min(current_sl, proposed_sl, min_lock)
        else:
            if current_price <= (entry_price - activation_dist):
                breakeven = entry_price * 0.9985
                proposed_sl = current_price + trailing_buffer
                new_sl = min(current_sl, proposed_sl, breakeven)
            
        if new_sl < current_sl - step_size or (phase == 2 and new_sl < current_sl):
            side_cfg = main_mod.get_side_config(side)
            amount = trade.get('amount')
            new_id = bingx_client.update_sl_order(trade['symbol'], side_cfg, trade.get('sl_order_id'), new_sl, amount)
            if new_id:
                with main_mod.state_lock:
                    trade['sl'] = new_sl
                    trade['sl_order_id'] = new_id
                
                prefix = "🚀 [INFINITY]" if phase == 2 else "🛡️ [TRAILING STOP]"
                msg = (f"{prefix} ขยับบังทุน\n\n"
                       f"{trade['symbol']}\n"
                       f"Side: {side}\n"
                       f"New SL: {new_sl}")
                main_mod.send_telegram(msg)
                print(f"[TRAILING] {trade['symbol']} SHORT SL updated to {new_sl} (Phase {phase})", flush=True)


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

                        # 2. Calculate the slippage delta (difference between filled and original entry)
                        #    We use this to simply shift the original SL and TP by the exact same amount,
                        #    preserving the original risk, reward, and RR multipliers perfectly across ALL strategies.
                        delta = filled_entry - trade['entry']

                        # 3. Shift SL and TP1/TP2 by the delta
                        new_sl = round(trade['sl'] + delta, 4)
                        new_tp1 = round(trade.get('tp1', trade['tp2']) + delta, 4)
                        new_tp2 = round(trade['tp2'] + delta, 4)

                        # 4. Update trade dict with the actual entry and new protection levels.
                        trade['entry'] = filled_entry
                        trade['sl'] = new_sl
                        trade['tp1'] = new_tp1
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

                            # Bug Fix: log fill status = FILLED
                            try:
                                ticker = main_mod.exchange.fetch_ticker(trade['symbol'])
                                current_price = ticker['last']
                                created_at = trade.get('created_at', time.time())
                                pending_minutes = round((time.time() - created_at) / 60, 1)
                                google_sheet.log_fill_analysis(
                                    symbol=trade['symbol'],
                                    side=trade.get('side', ''),
                                    current_price=current_price,
                                    entry_price=trade.get('entry', 0),
                                    grade=trade.get('grade', 'C'),
                                    score=trade.get('score', 0),
                                    atr=0,
                                    adx=0,
                                    btc_trend='',
                                    fill_status='FILLED',
                                    pending_minutes=pending_minutes,
                                    expired_reason='Filled'
                                )
                            except Exception as fe:
                                print(f"[FILL_LOG] FILLED log error: {fe}", flush=True)
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
                        except bingx_client.StopLossBreachedError as e:
                            # Stop Loss has already been breached by current price
                            main_mod.send_telegram(
                                f"🚨 STOP LOSS BREACHED BEFORE PLACEMENT\n\n"
                                f"{trade['symbol']} {trade['side']}\n\n"
                                f"Closing position at market to prevent further loss."
                            )
                            print(f"[TRADE_MANAGER] Stop loss breached for {trade['symbol']}, market closing", flush=True)
                            
                            try:
                                bingx_client.close_position_market(
                                    symbol=trade['symbol'],
                                    side=trade['side'],
                                    amount=amount
                                )
                            except Exception as close_err:
                                print(f"[TRADE_MANAGER] Failed to emergency close {trade['symbol']}: {close_err}", flush=True)
                                
                            with main_mod.state_lock:
                                trade['closed'] = True
                                trade['status'] = "CLOSED"
                                main_mod.active_trades.pop(signal_id, None)
                                
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
                        # Pending order still open – check expiration
                        # Scalping trades expire after 5 min; others after 60 min
                        now = time.time()
                        created_at = trade.get('created_at', now)
                        age = now - created_at

                        strategy = trade.get('strategy', '')
                        if strategy == 'SCALPING':
                            from config import SCALPING_PENDING_EXPIRY
                            expiry_seconds = SCALPING_PENDING_EXPIRY  # 300s (5 min)
                            expiry_label = f"{SCALPING_PENDING_EXPIRY // 60} minutes (SCALPING)"
                        elif strategy == 'MOMENTUM':
                            expiry_seconds = 300  # 5 minutes
                            expiry_label = "5 minutes (MOMENTUM)"
                        else:
                            expiry_seconds = 3600  # 60 minutes
                            expiry_label = "60 minutes"

                        if age > expiry_seconds:
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
                                f"Pending more than {expiry_label}"
                            )

                            # Bug Fix: log fill status = EXPIRED
                            try:
                                ticker = main_mod.exchange.fetch_ticker(trade['symbol'])
                                current_price = ticker['last']
                                google_sheet.log_fill_analysis(
                                    symbol=trade['symbol'],
                                    side=trade.get('side', ''),
                                    current_price=current_price,
                                    entry_price=trade.get('entry', 0),
                                    grade=trade.get('grade', 'C'),
                                    score=trade.get('score', 0),
                                    atr=0,
                                    adx=0,
                                    btc_trend='',
                                    fill_status='EXPIRED',
                                    pending_minutes=round(age / 60, 1),
                                    expired_reason=f'Timeout {expiry_label}'
                                )
                            except Exception as fe:
                                print(f"[FILL_LOG] EXPIRED log error: {fe}", flush=True)

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
                # CHECK REAL POSITION & TRAILING STOP
                # =========================

                try:

                    positions = main_mod.exchange.fetch_positions(
                        [trade['symbol']]
                    )

                    contracts = 0
                    current_price = 0

                    for pos in positions:

                        try:

                            c = float(pos.get('contracts') or 0)
                            if c > 0:
                                contracts += c
                                current_price = float(pos.get('markPrice') or pos.get('entryPrice') or 0)

                        except (TypeError, ValueError):

                            pass

                except Exception:

                    contracts = 0
                    current_price = 0

                # =========================
                # TRAILING STOP (If position is open)
                # =========================
                if contracts > 0 and trade.get('status') == "OPEN":
                    if current_price == 0:
                        try:
                            ticker = main_mod.exchange.fetch_ticker(trade['symbol'])
                            current_price = float(ticker['last'])
                        except Exception:
                            pass
                    if current_price > 0:
                        _process_trailing_stop(trade, current_price)

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

                    # ============================================================
                    # Check if LOSS was actually a TRAILED stop
                    # ============================================================
                    if result == "LOSS":
                        entry_price = trade.get('entry', 0)
                        current_sl = trade.get('sl', 0)
                        side = trade.get('side', 'LONG')
                        
                        is_trailed = False
                        if side == 'LONG' and current_sl > entry_price:
                            is_trailed = True
                        elif side == 'SHORT' and 0 < current_sl < entry_price:
                            is_trailed = True
                            
                        if is_trailed:
                            result = "TRAILED"

                    if result == "WIN" or result == "TRAILED":

                        msg_title = "🏆 WIN" if result == "WIN" else "🛡️ TRAILED (PROFIT)"
                        main_mod.send_telegram(
                            f"{msg_title}\n\n"
                            f"{trade['symbol']}"
                        )

                        main_mod.update_signal_result(
                            signal_id,
                            "WIN" if result == "WIN" else "TRAILED"
                        )
                        
                        # Google Sheets logging for WIN/TRAILED
                        try:
                            entry_price = trade.get('entry', 0)
                            # Try to get actual exit price from exchange
                            actual_exit = None
                            try:
                                recent_trades = main_mod.exchange.fetch_my_trades(trade['symbol'], limit=5)
                                if recent_trades:
                                    actual_exit = float(recent_trades[-1].get('price', 0))
                            except Exception:
                                pass
                            if actual_exit and actual_exit > 0:
                                exit_price = actual_exit
                            else:
                                exit_price = trade.get('tp2', entry_price) if result == "WIN" else trade.get('sl', entry_price)
                            amount = trade.get('amount', 0)
                            if trade['side'] == 'LONG':
                                pnl = round((exit_price - entry_price) * amount, 4)
                            else:
                                pnl = round((entry_price - exit_price) * amount, 4)
                            rr = 1.0
                            grade = trade.get('grade', 'C')
                            score = trade.get('score', 0)
                            strategy = trade.get('strategy', '')
                            
                            google_sheet.log_trade(
                                symbol=trade['symbol'],
                                side=trade['side'],
                                entry=entry_price,
                                exit_price=exit_price,
                                pnl=pnl,
                                result="WIN" if result == "WIN" else "TRAILED",
                                grade=grade,
                                score=score,
                                rr=rr,
                                strategy=strategy
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
                            # Try to get actual exit price from exchange
                            actual_exit = None
                            try:
                                recent_trades = main_mod.exchange.fetch_my_trades(trade['symbol'], limit=5)
                                if recent_trades:
                                    actual_exit = float(recent_trades[-1].get('price', 0))
                            except Exception:
                                pass
                            if actual_exit and actual_exit > 0:
                                exit_price = actual_exit
                            else:
                                exit_price = trade.get('sl', entry_price)
                            amount = trade.get('amount', 0)
                            if trade['side'] == 'LONG':
                                pnl = round((exit_price - entry_price) * amount, 4)
                            else:
                                pnl = round((entry_price - exit_price) * amount, 4)
                            rr = 1.0
                            grade = trade.get('grade', 'C')
                            score = trade.get('score', 0)
                            strategy = trade.get('strategy', '')
                            
                            google_sheet.log_trade(
                                symbol=trade['symbol'],
                                side=trade['side'],
                                entry=entry_price,
                                exit_price=exit_price,
                                pnl=pnl,
                                result="LOSS",
                                grade=grade,
                                score=score,
                                rr=rr,
                                strategy=strategy
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
        # Fetch all regular open limit orders for the symbol
        orders = main_mod.exchange.fetch_open_orders(symbol)
        
        # BingX and other exchanges hide trigger orders. We must fetch them explicitly.
        try:
            stop_orders = main_mod.exchange.fetch_open_orders(symbol, params={'stop': True})
            if stop_orders:
                orders.extend(stop_orders)
        except Exception as e:
            # Silently ignore if exchange doesn't support this parameter
            pass
            
        try:
            # Some CCXT versions use type=STOP_MARKET for BingX triggers
            stop_market_orders = main_mod.exchange.fetch_open_orders(symbol, params={'type': 'STOP_MARKET'})
            if stop_market_orders:
                orders.extend(stop_market_orders)
        except Exception as e:
            pass
            
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
            
            # AUTO-FIX: Recreate missing protection orders
            if protection['sl_order_id'] is None and protection['tp_order_id'] is None:
                print(f"[RESTORE] Protection orders not found for {symbol}. Attempting AUTO-FIX...", flush=True)
                try:
                    # 1. Fetch 15m ATR for fallback calculation
                    df = main_mod.indicators.fetch_ohlcv_with_retry(symbol, "15m", limit=50)
                    if df is not None and not df.empty:
                        atr_series = main_mod.indicators.calculate_atr(df, 14)
                        atr = atr_series.iloc[-1]
                    else:
                        atr = entry_price * 0.02 # fallback to 2%
                        
                    # 2. Calculate trade levels based on restored entry
                    sl, tp1, tp2, _ = main_mod.calculate_trade_levels(entry_price, atr, side.upper())
                    
                    # 3. Cancel existing trigger orders just in case they exist but are hidden
                    try:
                        main_mod.exchange.cancel_all_orders(symbol)
                    except Exception:
                        pass
                        
                    # 4. Re-place protection orders
                    side_cfg = main_mod.get_side_config(side.upper())
                    import bingx_client
                    sl_id, tp_id = bingx_client.place_protection_orders(
                        symbol=symbol,
                        side_cfg=side_cfg,
                        sl_price=sl,
                        tp2_price=tp2,
                        amount=contracts
                    )
                    
                    # 5. Overwrite the missing protection data
                    protection['sl_order_id'] = sl_id
                    protection['tp_order_id'] = tp_id
                    protection['sl_price'] = sl
                    protection['tp_price'] = tp2
                    print(f"[RESTORE] AUTO-FIX SUCCESS! Recreated protection orders. SL_ID={sl_id}, TP_ID={tp_id}", flush=True)
                except Exception as e:
                    print(f"[RESTORE] AUTO-FIX FAILED: {e}", flush=True)

            # Determine SL and TP prices
            # Use None if protection orders not found (displayed as N/A in /trades)
            sl_price = protection['sl_price']
            tp_price = protection['tp_price']
            
            trailing_phase = 1
            
            # Log when protection orders are not found
            if sl_price is None and tp_price is None:
                print(f"[RESTORE] Protection orders not found for {symbol}", flush=True)
            elif sl_price is None:
                print(f"[RESTORE] SL order not found for {symbol} (TP found: {tp_price})", flush=True)
            elif tp_price is None:
                print(f"[RESTORE] TP order not found for {symbol} (SL found: {sl_price}). Assuming Phase 2 Infinity Run.", flush=True)
                trailing_phase = 2

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
                "trailing_phase": trailing_phase,
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
    
    # Log position limit status after restore for debugging
    with main_mod.state_lock:
        trade_items = list(main_mod.active_trades.values())
    active_longs = sum(1 for t in trade_items if t.get("status") in ["PENDING", "OPEN"] and t.get("side") == "LONG")
    active_shorts = sum(1 for t in trade_items if t.get("status") in ["PENDING", "OPEN"] and t.get("side") == "SHORT")
    total_active = active_longs + active_shorts
    print(f"[POSITION_LIMIT] Startup: Restored positions counted: {restored_count}", flush=True)
    print(f"[POSITION_LIMIT] Startup: Active positions: {total_active}/{main_mod.MAX_ACTIVE_TRADES} (LONG: {active_longs}, SHORT: {active_shorts})", flush=True)


# =========================
# RECONCILE CLOSED TRADES ON RESTART (Bug Fix: restart bug)
# =========================

def reconcile_closed_trades_on_restart(pre_restart_trades):
    """ตรวจสอบ trades ที่ปิดระหว่าง bot downtime แล้ว log ผลที่ถูกต้อง
    
    เรียกหลัง restore_open_positions() เพื่อหา trades ที่:
    1. มีอยู่ใน active_trades ก่อน restart (จาก persistent storage)
    2. แต่ตอนนี้ position ปิดไปแล้วใน BingX
    3. ยังไม่มีผล WIN/LOSS ใน google sheet
    """
    if not pre_restart_trades:
        return

    try:
        for trade_id, trade in pre_restart_trades.items():
            symbol = trade.get('symbol')
            if not symbol:
                continue

            # ถ้า trade นี้ถูก restore เป็น OPEN แล้ว = ยังเปิดอยู่ ข้ามไป
            with main_mod.state_lock:
                still_active = trade_id in main_mod.active_trades

            if still_active:
                continue

            # trade หายไปจาก active_trades = ปิดระหว่าง downtime
            # หาผล WIN/LOSS จาก exchange
            try:
                result = _determine_trade_result(trade, symbol)
                entry_price = trade.get('entry', 0)
                exit_price = trade.get('tp2', entry_price) if result == "WIN" else trade.get('sl', entry_price)
                amount = trade.get('amount', 0)
                side = trade.get('side', '')
                if side == 'LONG':
                    pnl = round((exit_price - entry_price) * amount, 4)
                else:
                    pnl = round((entry_price - exit_price) * amount, 4)

                print(
                    f"[RECONCILE] {symbol} closed during downtime → {result}",
                    flush=True
                )

                main_mod.send_telegram(
                    f"🔄 RECONCILE\\n\\n"
                    f"{symbol}\\n"
                    f"ปิดระหว่าง downtime\\n"
                    f"Result: {result}"
                )

                google_sheet.log_trade(
                    symbol=symbol,
                    side=trade.get('side', ''),
                    entry=entry_price,
                    exit_price=exit_price,
                    pnl=pnl,
                    result=result,
                    grade=trade.get('grade', 'C'),
                    score=trade.get('score', 0),
                    rr=1.0,
                    strategy=trade.get('strategy', '')
                )

            except Exception as e:
                print(f"[RECONCILE] Error for {symbol}: {e}", flush=True)

    except Exception as e:
        print(f"[RECONCILE] reconcile_closed_trades_on_restart error: {e}", flush=True)


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