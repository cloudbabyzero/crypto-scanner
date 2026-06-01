import sys
import time
import traceback
import uuid
import bingx_client
from telegram_commands import status

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
                        # Temporary debug: announce attempt to place protection orders
                        main_mod.send_telegram(
                            f"🔍 PROTECTION ATTEMPT\n\n"
                            f"{trade['symbol']}\n"
                            f"SL={trade['sl']}\n"
                            f"TP={trade['tp2']}"
                        )
                        try:
                            sl_order_id, tp2_order_id = bingx_client.place_protection_orders(
                                symbol=trade['symbol'],
                                side_cfg=side_cfg,
                                sl_price=trade['sl'],
                                tp2_price=trade['tp2'],
                                amount=amount
                            )
                        except Exception as e:
                            main_mod.send_telegram(
                                f"🚨 PROTECTION FAILED AFTER FILL\n\n"
                                f"{trade['symbol']}\n\n"
                                f"{str(e)}"
                            )

                            continue

                        with main_mod.state_lock:
                            trade['status'] = "OPEN"
                            trade['sl_order_id'] = sl_order_id
                            trade['tp2_order_id'] = tp2_order_id

                        main_mod.send_telegram(
                            f"✅ ORDER FILLED\n\n"
                            f"{trade['symbol']}"
                        )

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

                    tp2_filled = False

                    if trade.get('tp2_order_id'):

                        try:

                            tp2_info = main_mod.exchange.fetch_order(
                                trade['tp2_order_id'],
                                trade['symbol']
                            )

                            tp2_filled = (
                                tp2_info.get('status') == "closed"
                            )

                        except Exception:

                            tp2_filled = False

                    if tp2_filled:

                        main_mod.send_telegram(
                            f"🏆 WIN\n\n"
                            f"{trade['symbol']}"
                        )

                        main_mod.update_signal_result(
                            signal_id,
                            "WIN"
                        )

                    else:

                        main_mod.send_telegram(
                            f"❌ LOSS\n\n"
                            f"{trade['symbol']}"
                        )

                        main_mod.update_signal_result(
                            signal_id,
                            "LOSS"
                        )

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

def restore_open_positions():

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

            main_mod.active_trades[trade_id] = {
                "symbol": symbol,
                "status": "OPEN",
                "side": side,
                "entry": entry_price,
                "amount": contracts,
                "restored": True
            }

            # TODO: Restore protection orders here if trade metadata
            # is available from BingX or external state caching.
            # This preserves restart-safe behavior without duplicating
            # protection recovery logic elsewhere.

        restored_count += 1

        print(
            f"Restored: {symbol} {side} {contracts}",
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