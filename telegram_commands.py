"""Telegram command handlers module.

All handlers are registered here and access shared state/functions from main.py
through the main_mod reference to avoid circular imports.
"""

import sys
import threading
import bingx_client
from telebot import types

# Reference to main.py's globals
main_mod = sys.modules["__main__"]

# Get bot reference for decorators
bot = main_mod.bot

# =========================
# HELPER FUNCTIONS
# =========================

def parse_command_value(message_text, cast_fn):
    """Parse a command value from message text."""
    parts = message_text.split()
    if len(parts) < 2:
        raise ValueError("missing value")
    return cast_fn(parts[1])


# =========================
# PING COMMAND
# =========================

@bot.message_handler(commands=['ping'])
def ping(message):
    bot.reply_to(message, "✅ Bot Online")


# =========================
# STATUS COMMAND
# =========================

@bot.message_handler(commands=['status'])
def status(message):
    text = f"""
🤖 BOT STATUS

Coins:
{len(main_mod.symbols)}

Active Trades:
{len(main_mod.active_trades)}

Cooldown:
{main_mod.COOLDOWN}s

Scan Interval:
{main_mod.SCAN_INTERVAL}s
"""
    bot.reply_to(message, text)


# =========================
# TRADES COMMAND
# =========================

@bot.message_handler(commands=['trades'])
def trades(message):
    main_mod.trade_manager.cleanup_closed_trades()
    
    with main_mod.state_lock:
        trade_items = [
            t for t in main_mod.active_trades.values()
            if t.get("status") in ["PENDING", "OPEN"]
        ]
    
    if not trade_items:
        bot.reply_to(message, "No active trades")
        return
    
    text = "📊 ACTIVE TRADES\n\n"
    for trade in trade_items:
        def _fmt(val):
            if val is None:
                return "N/A"
            # Format floats cleanly
            try:
                fval = float(val)
                if fval == 0:
                    return "N/A"
                return str(fval)
            except (TypeError, ValueError):
                return str(val)
        
        sl_val = _fmt(trade.get('sl'))
        tp2_val = _fmt(trade.get('tp2'))
        text += f"""
{trade['symbol']}
{trade['side']}

Entry:
{trade['entry']}

SL:
{sl_val}

TP2:
{tp2_val}

----------------
"""
    bot.reply_to(message, text)


# =========================
# CLEAR TRADES COMMAND
# =========================

@bot.message_handler(commands=['cleartrades'])
def cleartrades(message):
    with main_mod.state_lock:
        count = len(main_mod.active_trades)
        main_mod.active_trades.clear()
        
    try:
        import os
        if os.path.exists(main_mod.STATE_STORAGE_FILE):
            os.remove(main_mod.STATE_STORAGE_FILE)
    except Exception as e:
        pass
        
    bot.reply_to(message, f"✅ Cleared {count} ghost trades from memory and volume!\n\nNote: Any actually open positions on BingX will be re-detected on next bot restart.")
# =========================
# COINS COMMAND
# =========================

@bot.message_handler(commands=['modes'])
def coin_modes_report(message):
    bot.reply_to(message, "⏳ Scanning regimes for all coins... (This may take a minute)")
    
    def run_report():
        modes_count = {"TRENDING": 0, "MOMENTUM": 0, "SCALPING": 0, "SIDEWAYS": 0, "PAUSE": 0}
        try:
            for symbol in main_mod.symbols:
                df_1h = main_mod.get_dataframe(symbol, '1h')
                if df_1h is not None and len(df_1h) >= 2:
                    from indicators import detect_symbol_regime
                    regime = detect_symbol_regime(df_1h)
                    if regime in modes_count:
                        modes_count[regime] += 1
                    else:
                        modes_count["PAUSE"] += 1
                        
            total = sum(modes_count.values())
            text = f"📊 COIN MODES REPORT\n\n"
            text += f"Total Coins: {total}\n\n"
            text += f"🚀 MOMENTUM: {modes_count['MOMENTUM']}\n"
            text += f"📈 TRENDING: {modes_count['TRENDING']}\n"
            text += f"⚡ SCALPING: {modes_count['SCALPING']}\n"
            text += f"📉 SIDEWAYS: {modes_count['SIDEWAYS']}\n"
            text += f"⏸️ PAUSE: {modes_count['PAUSE']}\n"
            
            bot.send_message(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error generating report: {e}")
            
    threading.Thread(target=run_report).start()

@bot.message_handler(commands=['coins'])
def coins(message):
    text = "🪙 COINS\n\n"
    for coin in main_mod.symbols:
        text += f"{coin}\n"
    bot.reply_to(message, text)


# =========================
# MODES COMMAND
# =========================

@bot.message_handler(commands=['modes'])
def modes(message):
    text = "🔍 COIN MODES REPORT\n\n"
    results = getattr(main_mod, 'scan_results', {})
    for coin in main_mod.symbols:
        mode = "UNKNOWN"
        if coin in results and isinstance(results[coin], dict):
            mode = results[coin].get("mode", "UNKNOWN")
        text += f"{coin}\nMode: {mode}\n\n"
    
    bot.reply_to(message, text.strip())


# =========================
# STATS COMMAND
# =========================

@bot.message_handler(commands=['stats'])
def stats(message):
    wins = 0
    losses = 0
    be = 0
    
    if not main_mod.os.path.exists('signals.csv'):
        bot.reply_to(message, "No stats yet")
        return
    
    with open('signals.csv', 'r') as file:
        reader = main_mod.csv.DictReader(file)
        for row in reader:
            result = row['result']
            if result == "WIN":
                wins += 1
            elif result == "LOSS":
                losses += 1
            elif result == "BE":
                be += 1
    
    total = wins + losses
    winrate = 0
    if total > 0:
        winrate = round((wins / total) * 100, 2)
    
    text = f"""
📈 STATS

WIN:
{wins}

LOSS:
{losses}

BE:
{be}

WINRATE:
{winrate}%
"""
    bot.reply_to(message, text)


# =========================
# FORCECHECK COMMAND
# =========================

@bot.message_handler(commands=['forcecheck'])
def forcecheck(message):
    bot.reply_to(message, "🔍 Force scanning...")
    
    def _forcecheck_runner(chat_id):
        scanned = 0
        
        for symbol in main_mod.symbols:
            scanned += 1
            try:
                main_mod.analyze(symbol, bypass_cooldown=True)
            except Exception:
                pass
        
        # Build summary counters from scan_results
        results = getattr(main_mod, 'scan_results', {})
        counters = {
            "Signal Generated": 0,
            "Score Below MIN_SCORE": 0,
            "Sideways Market": 0,
            "Candle Too Big": 0,
            "Cooldown": 0,
            "Too Close EMA99": 0,
            "Error": 0,
        }
        
        for data in results.values():
            if isinstance(data, dict):
                status = data.get("status", "Unknown")
                if status in counters:
                    counters[status] += 1
            else:
                counters["Error"] += 1
        
        summary = f"""📊 FORCE SCAN SUMMARY

Scanned: {scanned}

Signal Generated: {counters["Signal Generated"]}
Score Below MIN_SCORE: {counters["Score Below MIN_SCORE"]}
Sideways Market: {counters["Sideways Market"]}
Candle Too Big: {counters["Candle Too Big"]}
Cooldown: {counters["Cooldown"]}
Too Close EMA99: {counters["Too Close EMA99"]}
Errors: {counters["Error"]}"""
        
        try:
            bot.send_message(chat_id, summary)
        except Exception:
            main_mod.send_telegram(summary)
    
    threading.Thread(
        target=lambda: _forcecheck_runner(message.chat.id),
        daemon=True
    ).start()


# =========================
# CSV COMMAND
# =========================

@bot.message_handler(commands=['csv'])
def csv_file(message):
    if not main_mod.os.path.exists('signals.csv'):
        bot.reply_to(message, "No CSV file yet")
        return
    
    with open('signals.csv', 'rb') as file:
        bot.send_document(message.chat.id, file)


# =========================
# ADX FILTER COMMAND
# =========================

@bot.message_handler(commands=['adx'])
def set_adx(message):
    try:
        value = parse_command_value(message.text, int)
        main_mod.ADX_FILTER = value
        bot.reply_to(message, f"✅ ADX Filter updated to {value}")
    except Exception:
        bot.reply_to(message, "Usage: /adx 18")


# =========================
# SCORE FILTER COMMAND
# =========================

@bot.message_handler(commands=['score'])
def set_score(message):
    try:
        value = parse_command_value(message.text, int)
        main_mod.MIN_SCORE = value
        bot.reply_to(message, f"✅ MIN_SCORE updated to {value}")
    except Exception:
        bot.reply_to(message, "Usage: /score 85")


# =========================
# ATR FILTER COMMAND
# =========================

@bot.message_handler(commands=['atr'])
def set_atr(message):
    try:
        value = parse_command_value(message.text, float)
        main_mod.ATR_FILTER = value
        bot.reply_to(message, f"✅ ATR Filter updated to {value}")
    except Exception:
        bot.reply_to(message, "Usage: /atr 0.4")


# =========================
# CONFIG COMMAND
# =========================

@bot.message_handler(commands=['config'])
def config(message):
    from config import STRATEGY_CONFIG, PAUSE_MAX_ADX, PAUSE_MAX_ATR

    scalp_symbols_str = ", ".join([s.split('/')[0] for s in SCALPING_SYMBOLS])

    text = f"""
📈 TREND STRATEGY

TREND_MIN_ADX:
{main_mod.TREND_MIN_ADX}

TREND_MIN_ATR:
{main_mod.TREND_MIN_ATR}%

TREND_HIGH_VOLUME_ONLY:
{main_mod.TREND_HIGH_VOLUME_ONLY}

📉 SIDEWAYS STRATEGY

SIDEWAYS_MAX_ADX:
{main_mod.SIDEWAYS_MAX_ADX}

SIDEWAYS_MIN_ATR:
{main_mod.SIDEWAYS_MIN_ATR}%

SIDEWAYS_HIGH_VOLUME_ONLY:
{main_mod.SIDEWAYS_HIGH_VOLUME_ONLY}

🤖 AUTO TRADE

AUTO_TRADE:
{main_mod.AUTO_TRADE}

AUTO_TRADE_MIN_GRADE:
{main_mod.AUTO_TRADE_MIN_GRADE}

🚀 MOMENTUM STRATEGY

MOMENTUM_MIN_ADX:
{STRATEGY_CONFIG['MOMENTUM']['FILTERS']['MIN_ADX']}

MOMENTUM_MIN_PRICE_DISTANCE:
0.5%

MOMENTUM_ENTRY_ATR_MULT:
0.0

MOMENTUM_SL_ATR_MULT:
{STRATEGY_CONFIG['MOMENTUM']['SL_ATR_MULT']}

MOMENTUM_TP_RR:
{STRATEGY_CONFIG['MOMENTUM']['TP_RR']}

MOMENTUM_AUTO_TRADE:
{"ON" if main_mod.MOMENTUM_AUTO_TRADE else "OFF"}

MOMENTUM_MIN_GRADE:
{main_mod.MOMENTUM_MIN_GRADE}

MOMENTUM_MIN_SCORE:
{main_mod.MOMENTUM_MIN_SCORE}

MOMENTUM_MAX_TRADES:
{main_mod.MOMENTUM_MAX_TRADES}

⚡ SCALPING STRATEGY

SCALPING_SYMBOLS:
{scalp_symbols_str}

SCALPING_SCAN_INTERVAL:
{STRATEGY_CONFIG['SCALPING']['SCAN_INTERVAL']}s

SCALPING_COOLDOWN:
{STRATEGY_CONFIG['SCALPING']['COOLDOWN']}s

SCALPING_SL_ATR_MULT:
{STRATEGY_CONFIG['SCALPING']['SL_ATR_MULT']}

SCALPING_TP_RR:
{STRATEGY_CONFIG['SCALPING']['TP_RR']}

SCALPING_LEVERAGE:
x{STRATEGY_CONFIG['SCALPING']['LEVERAGE']}

SCALPING_MARGIN:
{STRATEGY_CONFIG['SCALPING']['MARGIN_PER_TRADE']} USDT

SCALPING_AUTO_TRADE:
{"ON" if SCALPING_AUTO_TRADE else "OFF"}

SCALPING_MIN_GRADE:
{STRATEGY_CONFIG['SCALPING']['MIN_GRADE']}

SCALPING_MIN_SCORE:
{STRATEGY_CONFIG['SCALPING']['MIN_SCORE']}

SCALPING_MAX_TRADES:
{STRATEGY_CONFIG['SCALPING']['MAX_TRADES']}

⏸ PAUSE PROTECTION

PAUSE_MAX_ADX:
{PAUSE_MAX_ADX}

PAUSE_MAX_ATR:
{PAUSE_MAX_ATR}%

🎮 CONTROL MODE

CONTROL_MODE:
{main_mod.CONTROL_MODE}

⚙️ GENERAL

SCAN_INTERVAL:
{main_mod.SCAN_INTERVAL}s

COOLDOWN:
{main_mod.COOLDOWN}s
"""
    bot.reply_to(message, text)


# =========================
# LONG ORDER COMMAND
# =========================

@bot.message_handler(commands=['long'])
def long_order(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Usage: /long xrp")
            return
        
        coin = parts[1].upper()
        symbol = f"{coin}/USDT:USDT"
        bingx_client.execute_trade(symbol, "long")
    except Exception as e:
        bot.reply_to(message, f"ERROR: {str(e)}")


# =========================
# SHORT ORDER COMMAND
# =========================

@bot.message_handler(commands=['short'])
def short_order(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Usage: /short xrp")
            return
        
        coin = parts[1].upper()
        symbol = f"{coin}/USDT:USDT"
        bingx_client.execute_trade(symbol, "short")
    except Exception as e:
        bot.reply_to(message, f"ERROR: {str(e)}")


# =========================
# HEARTBEAT COMMAND
# =========================

@bot.message_handler(commands=['heartbeat'])
def heartbeat(message):
    uptime_seconds = int(
        main_mod.time.time() - main_mod.BOT_START_TIME
    )
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60
    uptime_str = f"{uptime_hours}h {uptime_minutes}m"

    with main_mod.state_lock:
        active_count = len(
            [
                t
                for t in main_mod.active_trades.values()
                if t.get("status")
                in ["PENDING", "OPEN"]
            ]
        )

    auto_trade_status = (
        "ON" if main_mod.AUTO_TRADE else "OFF"
    )

    current_time = main_mod.time.strftime(
        "%Y-%m-%d %H:%M:%S UTC",
        main_mod.time.gmtime()
    )

    market_mode = getattr(main_mod, 'MARKET_MODE', 'TRENDING')
    current_regime = getattr(main_mod, 'CURRENT_REGIME', 'UNKNOWN')
    control_mode = getattr(main_mod, 'CONTROL_MODE', 'AUTO')

    text = f"""
💓 HEARTBEAT

Status: ONLINE
Uptime: {uptime_str}
Active Trades: {active_count}
Coins: {len(main_mod.symbols)}
Auto Trade: {auto_trade_status}
Market Mode: {market_mode}
Market Regime: {current_regime}
Control Mode: {control_mode}
Time: {current_time}
"""
    bot.reply_to(message, text)


# =========================
# SCAN REPORT COMMAND
# =========================

def format_scan_row(symbol, data):
    """Format a single scan result row for /scanreport output."""
    if not isinstance(data, dict):
        return f"{symbol}\nStatus: {data}\n"
    
    status = data.get("status", "Unknown")
    score = data.get("score", 0)
    adx = data.get("adx", 0)
    atr = data.get("atr", 0)
    volume = data.get("volume", "N/A")
    min_score = getattr(main_mod, 'MIN_SCORE', 85)
    mode = data.get("mode", "UNKNOWN")
    
    lines = [symbol]
    lines.append(f"Mode: {mode}")
    lines.append(f"Status: {status}")
    
    if status == "Signal Generated":
        strategy = data.get("strategy", "TREND")
        lines.append(f"Strategy: {strategy}")
        lines.append(f"Score: {score}/{min_score}")
        lines.append(f"ADX: {adx}")
        lines.append(f"ATR: {atr}")
        lines.append(f"Volume: {volume}")
    elif status == "Score Below MIN_SCORE":
        long_score = data.get("long_score", 0)
        short_score = data.get("short_score", 0)
        missing_points = data.get("missing_points", 0)
        lines.append(f"Long Score: {long_score}")
        lines.append(f"Short Score: {short_score}")
        lines.append(f"Need: +{missing_points} points")
        lines.append(f"ATR: {atr}")
        lines.append(f"ADX: {adx}")
        lines.append(f"Volume: {volume}")
    elif status == "Sideways Market":
        lines.append(f"ADX: {adx}")
    elif status == "Too Close EMA99":
        lines.append(f"ADX: {adx}")
        lines.append(f"ATR: {atr}")
    elif status == "Candle Too Big":
        lines.append(f"ADX: {adx}")
        lines.append(f"ATR: {atr}%")
        lines.append(f"Volume: {volume}")
    elif status == "Cooldown":
        pass
    elif status == "Error":
        pass
    
    return "\n".join(lines)


@bot.message_handler(commands=['scanreport'])
def scanreport(message):
    results = getattr(main_mod, 'scan_results', {})
    
    if not results:
        bot.reply_to(message, "📋 SCAN REPORT\n\nNo scan data yet.")
        return
    
    text = "📋 SCAN REPORT\n\n"
    for symbol in main_mod.symbols:
        data = results.get(symbol, "Not Scanned")
        text += format_scan_row(symbol, data) + "\n\n"
    
    bot.reply_to(message, text.strip())


# =========================
# DASHBOARD COMMAND
# =========================

@bot.message_handler(commands=['dashboard'])
def dashboard(message):
    counters = getattr(main_mod, 'scan_counters', {})
    total = counters.get("Total Scans", 0)
    signals = counters.get("Signal Generated", 0)
    sideways = counters.get("Sideways Market", 0)
    score_low = counters.get("Score Below MIN_SCORE", 0)
    cooldown = counters.get("Cooldown", 0)
    candle = counters.get("Candle Too Big", 0)
    ema99 = counters.get("Too Close EMA99", 0)
    errors = counters.get("Error", 0)

    uptime_seconds = int(
        main_mod.time.time() - main_mod.BOT_START_TIME
    )
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60
    uptime_str = f"{uptime_hours}h {uptime_minutes}m"

    def pct(val):
        if total == 0:
            return 0
        return round((val / total) * 100)

    sideways_pct = pct(sideways)
    score_low_pct = pct(score_low)
    cooldown_pct = pct(cooldown)
    candle_pct = pct(candle)
    ema99_pct = pct(ema99)
    errors_pct = pct(errors)

    market_mode = getattr(main_mod, 'MARKET_MODE', 'TRENDING')
    current_regime = getattr(main_mod, 'CURRENT_REGIME', 'UNKNOWN')
    control_mode = getattr(main_mod, 'CONTROL_MODE', 'AUTO')

    text = f"""
📊 DASHBOARD

Uptime:
{uptime_str}

Scans:
{total}

Signals:
{signals}

Sideways:
{sideways_pct}%

Score Low:
{score_low_pct}%

Cooldown:
{cooldown_pct}%

Candle Big:
{candle_pct}%

EMA99:
{ema99_pct}%

Errors:
{errors}

Current Regime:
{current_regime}

Market Mode:
{market_mode}

Control Mode:
{control_mode}
"""
    bot.reply_to(message, text)


# =========================
# MARKET COMMAND
# =========================

def get_regime_recommendation(regime):
    recommendations = {
        "TRENDING": "Trend Following",
        "SIDEWAYS": "Mean Reversion",
        "VOLATILE": "Volatility Breakout",
    }
    return recommendations.get(regime, "Trend Following")


@bot.message_handler(commands=['market'])
def market(message):
    try:
        regime, btc_adx, btc_atr_pct = main_mod.detect_market_regime()
    except Exception:
        bot.reply_to(message, "❌ Failed to detect market regime.")
        return

    market_mode = getattr(main_mod, 'MARKET_MODE', 'TRENDING')
    current_regime = getattr(main_mod, 'CURRENT_REGIME', 'UNKNOWN')
    control_mode = getattr(main_mod, 'CONTROL_MODE', 'AUTO')
    recommendation = get_regime_recommendation(regime)

    text = f"""
📊 MARKET REPORT

Current Regime:
{regime}

BTC ADX: {btc_adx}

Recommended Strategy:
{recommendation}

Current Market Mode:
{market_mode}

Market Regime (Active):
{current_regime}

Control Mode:
{control_mode}
"""

    # Keep inline buttons for manual override (Feature 8)
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_trend = types.InlineKeyboardButton(
        "✅ Switch to Trend Mode", callback_data="mode_trending"
    )
    btn_sideways = types.InlineKeyboardButton(
        "🔄 Switch to Sideways Mode", callback_data="mode_sideways"
    )
    btn_momentum = types.InlineKeyboardButton(
        "🚀 Switch to Momentum Mode", callback_data="mode_momentum"
    )
    btn_scalping = types.InlineKeyboardButton(
        "⚡ Switch to Scalping Mode", callback_data="mode_scalping"
    )
    btn_pause = types.InlineKeyboardButton(
        "⏸ Pause Bot", callback_data="mode_pause"
    )
    btn_auto = types.InlineKeyboardButton(
        "🤖 Auto Mode", callback_data="mode_auto"
    )
    markup.add(btn_trend, btn_sideways, btn_momentum, btn_scalping, btn_pause, btn_auto)

    bot.send_message(message.chat.id, text, reply_markup=markup)


# =========================
# CALLBACK: MODE BUTTONS
# =========================
# Feature 8: Keep manual controls as optional override.
# These buttons let the user switch modes manually.
# Use caution: if user sets FORCE_TREND or FORCE_SIDEWAY,
# auto regime switching will be disabled.

@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_"))
def market_mode_callback(call):
    action = call.data.replace("mode_", "")

    if action == "trending":
        # Feature 8: Set FORCE_TREND override
        main_mod.CONTROL_MODE = "FORCE_TREND"
        main_mod.MARKET_MODE = "TRENDING"
        # Save to persistent storage
        main_mod.save_regime_storage()
        
        bot.answer_callback_query(call.id, "✅ FORCE_TREND Override Active")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n🔒 FORCE_TREND Override Active\nAuto switching disabled"
        )
        
        # Send confirmation to main channel
        main_mod.send_telegram(
            "🔒 CONTROL MODE CHANGED\n\n"
            "Mode: FORCE_TREND\n"
            "Auto regime switching disabled.\n"
            "Use /setauto to re-enable."
        )

    elif action == "sideways":
        # Feature 8: Set FORCE_SIDEWAY override
        main_mod.CONTROL_MODE = "FORCE_SIDEWAY"
        main_mod.MARKET_MODE = "SIDEWAYS"
        # Save to persistent storage
        main_mod.save_regime_storage()
        
        bot.answer_callback_query(call.id, "🔄 FORCE_SIDEWAY Override Active")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n🔒 FORCE_SIDEWAY Override Active\nAuto switching disabled"
        )
        
        # Send confirmation to main channel
        main_mod.send_telegram(
            "🔒 CONTROL MODE CHANGED\n\n"
            "Mode: FORCE_SIDEWAY\n"
            "Auto regime switching disabled.\n"
            "Use /setauto to re-enable."
        )

    elif action == "momentum":
        main_mod.CONTROL_MODE = "FORCE_MOMENTUM"
        main_mod.MARKET_MODE = "MOMENTUM"
        main_mod.save_regime_storage()

        bot.answer_callback_query(call.id, "🚀 FORCE_MOMENTUM Override Active")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n🔒 FORCE_MOMENTUM Override Active\nAuto switching disabled"
        )

        main_mod.send_telegram(
            "🔒 CONTROL MODE CHANGED\n\n"
            "Mode: FORCE_MOMENTUM\n"
            "Auto regime switching disabled.\n"
            "Use /setauto to re-enable."
        )

    elif action == "scalping":
        main_mod.CONTROL_MODE = "FORCE_SCALPING"
        main_mod.MARKET_MODE = "SCALPING"
        main_mod.save_regime_storage()

        bot.answer_callback_query(call.id, "⚡ FORCE_SCALPING Override Active")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n🔒 FORCE_SCALPING Override Active\nAuto switching disabled\nScan interval: 60s"
        )

        main_mod.send_telegram(
            "🔒 CONTROL MODE CHANGED\n\n"
            "Mode: FORCE_SCALPING\n"
            "Auto regime switching disabled.\n"
            "Scan interval: 60s\n"
            "Use /setauto to re-enable."
        )

    elif action == "pause":
        main_mod.CONTROL_MODE = "FORCE_PAUSE"
        main_mod.MARKET_MODE = "PAUSE"
        main_mod.save_regime_storage()

        bot.answer_callback_query(call.id, "⏸ FORCE_PAUSE Override Active")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n🔒 FORCE_PAUSE Override Active\nBot is now paused and will not scan."
        )

        main_mod.send_telegram(
            "🔒 CONTROL MODE CHANGED\n\n"
            "Mode: FORCE_PAUSE\n"
            "Trading is paused.\n"
            "Use /setauto to re-enable."
        )

    elif action == "auto":
        # Feature 8: Re-enable AUTO mode
        old_control = main_mod.CONTROL_MODE
        main_mod.CONTROL_MODE = "AUTO"
        
        # When switching back to auto, re-detect regime and set mode
        try:
            new_regime, btc_adx, btc_atr_pct = main_mod.detect_market_regime()
            new_mode = main_mod.determine_mode_from_regime(new_regime)
            main_mod.CURRENT_REGIME = new_regime
            main_mod.MARKET_MODE = new_mode
        except Exception:
            pass
        
        # Save to persistent storage
        main_mod.save_regime_storage()
        
        bot.answer_callback_query(call.id, "🤖 Auto Mode Enabled")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n🤖 Auto Mode Enabled\nAuto regime switching active"
        )
        
        # Send confirmation to main channel
        main_mod.send_telegram(
            "🤖 CONTROL MODE CHANGED\n\n"
            "Mode: AUTO\n"
            "Auto regime switching enabled.\n"
            f"Market Mode set to: {main_mod.MARKET_MODE}"
        )
    
    # After any mode change, save config
    try:
        main_mod.save_config()
    except Exception:
        pass


# =========================
# SET AUTO MODE COMMAND
# =========================

@bot.message_handler(commands=['setauto'])
def set_auto_mode(message):
    """Manually set control mode via Telegram command.
    
    Feature 8: Allows users to switch between AUTO, FORCE_TREND, and FORCE_SIDEWAY.
    
    Usage:
    /setauto auto           - Enable auto regime switching
    /setauto trend          - Force trend mode always
    /setauto sideways       - Force sideways mode always
    """
    try:
        parts = message.text.split()
        if len(parts) < 2:
            text = (
                f"🎮 CONTROL MODE\n\n"
                f"Current: {main_mod.CONTROL_MODE}\n\n"
                f"Usage:\n"
                f"/setauto auto      - Auto regime switching\n"
                f"/setauto trend     - Force trend mode\n"
                f"/setauto sideways  - Force sideways mode"
            )
            bot.reply_to(message, text)
            return
        
        mode_arg = parts[1].lower()
        
        if mode_arg == "auto":
            old = main_mod.CONTROL_MODE
            main_mod.CONTROL_MODE = "AUTO"
            # Re-detect regime to set mode
            try:
                new_regime, _, _ = main_mod.detect_market_regime()
                main_mod.CURRENT_REGIME = new_regime
                main_mod.MARKET_MODE = main_mod.determine_mode_from_regime(new_regime)
            except Exception:
                pass
            main_mod.save_regime_storage()
            
            bot.reply_to(
                message,
                f"🤖 Control Mode: AUTO\n\n"
                f"Old: {old}\n"
                f"Auto regime switching enabled."
            )
            
        elif mode_arg == "trend":
            old = main_mod.CONTROL_MODE
            main_mod.CONTROL_MODE = "FORCE_TREND"
            main_mod.MARKET_MODE = "TRENDING"
            main_mod.save_regime_storage()
            
            bot.reply_to(
                message,
                f"🔒 Control Mode: FORCE_TREND\n\n"
                f"Old: {old}\n"
                f"Auto regime switching disabled."
            )
            
        elif mode_arg == "sideways":
            old = main_mod.CONTROL_MODE
            main_mod.CONTROL_MODE = "FORCE_SIDEWAY"
            main_mod.MARKET_MODE = "SIDEWAYS"
            main_mod.save_regime_storage()
            
            bot.reply_to(
                message,
                f"🔒 Control Mode: FORCE_SIDEWAY\n\n"
                f"Old: {old}\n"
                f"Auto regime switching disabled."
            )

        elif mode_arg == "momentum":
            old = main_mod.CONTROL_MODE
            main_mod.CONTROL_MODE = "FORCE_MOMENTUM"
            main_mod.MARKET_MODE = "MOMENTUM"
            main_mod.save_regime_storage()

            bot.reply_to(
                message,
                f"🚀 Control Mode: FORCE_MOMENTUM\n\n"
                f"Old: {old}\n"
                f"Auto regime switching disabled."
            )
            
        else:
            bot.reply_to(
                message,
                f"❌ Unknown mode: {mode_arg}\n\n"
                f"Usage:\n"
                f"/setauto auto      - Auto regime switching\n"
                f"/setauto trend     - Force trend mode\n"
                f"/setauto sideways  - Force sideways mode\n"
                f"/setauto momentum  - Force momentum mode"
            )
            
    except Exception as e:
        bot.reply_to(message, f"ERROR: {str(e)}")


# =========================
# TREND ADX FILTER COMMAND
# =========================

@bot.message_handler(commands=['trendadx'])
def set_trendadx(message):
    try:
        value = parse_command_value(message.text, int)
        old_value = main_mod.TREND_MIN_ADX
        main_mod.TREND_MIN_ADX = value
        main_mod.save_config()
        bot.reply_to(
            message,
            f"""✅ TREND_MIN_ADX updated

Old: {old_value}
New: {value}"""
        )
    except Exception:
        bot.reply_to(message, "Usage: /trendadx 25")


# =========================
# TREND ATR FILTER COMMAND
# =========================

@bot.message_handler(commands=['trendatr'])
def set_trendatr(message):
    try:
        value = parse_command_value(message.text, float)
        old_value = main_mod.TREND_MIN_ATR
        main_mod.TREND_MIN_ATR = value
        main_mod.save_config()
        bot.reply_to(
            message,
            f"""✅ TREND_MIN_ATR updated

Old: {old_value}
New: {value}"""
        )
    except Exception:
        bot.reply_to(message, "Usage: /trendatr 0.50")


# =========================
# SIDEWAYS ADX FILTER COMMAND
# =========================

@bot.message_handler(commands=['sideadx'])
def set_sideadx(message):
    try:
        value = parse_command_value(message.text, int)
        old_value = main_mod.SIDEWAYS_MAX_ADX
        main_mod.SIDEWAYS_MAX_ADX = value
        main_mod.save_config()
        bot.reply_to(
            message,
            f"""✅ SIDEWAYS_MAX_ADX updated

Old: {old_value}
New: {value}"""
        )
    except Exception:
        bot.reply_to(message, "Usage: /sideadx 18")


# =========================
# SIDEWAYS ATR FILTER COMMAND
# =========================

@bot.message_handler(commands=['sideatr'])
def set_sideatr(message):
    try:
        value = parse_command_value(message.text, float)
        old_value = main_mod.SIDEWAYS_MIN_ATR
        main_mod.SIDEWAYS_MIN_ATR = value
        main_mod.save_config()
        bot.reply_to(
            message,
            f"""✅ SIDEWAYS_MIN_ATR updated

Old: {old_value}
New: {value}"""
        )
    except Exception:
        bot.reply_to(message, "Usage: /sideatr 0.20")


# =========================
# STRATEGY COMMAND
# =========================

@bot.message_handler(commands=['strategy'])
def strategy(message):
    text = f"""
📈 TREND

MIN ADX: {main_mod.TREND_MIN_ADX}
MIN ATR: {main_mod.TREND_MIN_ATR}%

📉 SIDEWAYS

MAX ADX: {main_mod.SIDEWAYS_MAX_ADX}
MIN ATR: {main_mod.SIDEWAYS_MIN_ATR}%

🚀 MOMENTUM

MIN ADX: {STRATEGY_CONFIG['MOMENTUM']['FILTERS']['MIN_ADX']}
MIN Price Distance: 0.5%
Min Consecutive Candles: 3
Entry ATR Mult: 0.0
SL ATR Mult: {STRATEGY_CONFIG['MOMENTUM']['SL_ATR_MULT']}
TP RR: {STRATEGY_CONFIG['MOMENTUM']['TP_RR']}
Min Grade: {main_mod.MOMENTUM_MIN_GRADE}
Min Score: {main_mod.MOMENTUM_MIN_SCORE}
Max Trades: {main_mod.MOMENTUM_MAX_TRADES}
Auto Trade: {"ON" if main_mod.MOMENTUM_AUTO_TRADE else "OFF"}

⚡ SCALPING

MIN ADX: {main_mod.config.SCALPING_MIN_ADX}
MIN ATR: {main_mod.config.SCALPING_MIN_ATR_PCT}%
MAX ADX: {main_mod.config.SCALPING_MAX_ADX}
SL ATR Mult: {STRATEGY_CONFIG['SCALPING']['SL_ATR_MULT']}
TP RR: {STRATEGY_CONFIG['SCALPING']['TP_RR']}
Min Grade: {main_mod.config.SCALPING_MIN_GRADE}
Min Score: {main_mod.config.SCALPING_MIN_SCORE}
Max Trades: {main_mod.config.SCALPING_MAX_TRADES}
Auto Trade: {"ON" if main_mod.config.SCALPING_AUTO_TRADE else "OFF"}
"""
    bot.reply_to(message, text)


# =========================
# HELP COMMAND
# =========================

@bot.message_handler(commands=['help'])
def help_command(message):
    text = """
🤖 AVAILABLE COMMANDS

📊 Market

/market
ดู market regime และเลือกโหมด

/modes
ดึงรายงานจำนวนเหรียญในแต่ละ Mode

/cleartrades
ล้างออเดอร์ค้างในหน่วยความจำของบอท

/config
ดู config ปัจจุบัน

/strategy
ดู TREND/SIDEWAYS strategy settings

🎮 Control Mode (Feature 8)

/setauto
ดู control mode ปัจจุบัน

/setauto auto
เปิด AUTO mode (bot ควบคุมอัตโนมัติ)

/setauto trend
บังคับ TREND mode ตลอด

/setauto sideways
บังคับ SIDEWAYS mode ตลอด

📈 Trend Strategy

/trendadx <value>
เปลี่ยน TREND MIN ADX

Example: /trendadx 25

/trendatr <value>
เปลี่ยน TREND MIN ATR

Example: /trendatr 0.50

📉 Sideways Strategy

/sideadx <value>
เปลี่ยน SIDEWAYS MAX ADX

Example: /sideadx 18

/sideatr <value>
เปลี่ยน SIDEWAYS MIN ATR

Example: /sideatr 0.20

🤖 Auto Trade

/autoon
เปิด AUTO TRADE

/autooff
ปิด AUTO TRADE

/autostatus
ดู status AUTO TRADE

/grade A
เปลี่ยน grade filter (A+, A, B, C)

📋 Reports

/scanreport
ดูผลสแกนล่าสุด

/status
ดูสถานะบอท

/heartbeat
ดูสถานะแบบละเอียด

📊 Other

/ping
เช็คว่าบอทยังออนไลน์ไหม

/stats
ดู winrate

/trades
ดู active trades

/coins
ดูเหรียญที่สแกน

/modes
ดู Market Regime (สภาพตลาด) ของแต่ละเหรียญ

/forcecheck
บังคับสแกนทันที

/cleartrades
ล้าง active trades ที่ค้างในระบบ

/long xrp
เปิด LONG

/short xrp
เปิด SHORT

/csv
ดาวน์โหลด signals.csv

/dashboard
ดู dashboard สถิติทั้งหมด

---

/help
ดูคำสั่งทั้งหมด
"""
    bot.reply_to(message, text)


# =========================
# AUTO TRADE ON COMMAND
# =========================

@bot.message_handler(commands=['autoon'])
def autoon(message):
    main_mod.AUTO_TRADE = True
    text = f"""
✅ AUTO TRADE ENABLED

Grade Filter:
{main_mod.AUTO_TRADE_MIN_GRADE}

Max Longs:
{main_mod.MAX_LONG_TRADES}

Max Shorts:
{main_mod.MAX_SHORT_TRADES}

Max Total:
{main_mod.MAX_ACTIVE_TRADES}
"""
    bot.reply_to(message, text)


# =========================
# AUTO TRADE OFF COMMAND
# =========================

@bot.message_handler(commands=['autooff'])
def autooff(message):
    main_mod.AUTO_TRADE = False
    bot.reply_to(message, "❌ AUTO TRADE DISABLED")


# =========================
# AUTO TRADE STATUS COMMAND
# =========================

@bot.message_handler(commands=['autostatus'])
def autostatus(message):
    main_mod.trade_manager.cleanup_closed_trades()
    
    with main_mod.state_lock:
        trade_items = list(main_mod.active_trades.values())
    
    active_longs = sum(
        1 for t in trade_items
        if t.get("status") in ["PENDING", "OPEN"]
        and t.get("side") == "LONG"
    )
    
    active_shorts = sum(
        1 for t in trade_items
        if t.get("status") in ["PENDING", "OPEN"]
        and t.get("side") == "SHORT"
    )
    
    status_text = "🔴 OFF" if not main_mod.AUTO_TRADE else "🟢 ON"
    
    text = f"""
🤖 AUTO TRADE STATUS

Status:
{status_text}

Grade Filter:
{main_mod.AUTO_TRADE_MIN_GRADE}

📈 TREND STRATEGY

TREND_MIN_ADX:
{main_mod.TREND_MIN_ADX}

TREND_MIN_ATR:
{main_mod.TREND_MIN_ATR}%

📉 SIDEWAYS STRATEGY

SIDEWAYS_MAX_ADX:
{main_mod.SIDEWAYS_MAX_ADX}

SIDEWAYS_MIN_ATR:
{main_mod.SIDEWAYS_MIN_ATR}%

Active Longs:
{active_longs} / {main_mod.MAX_LONG_TRADES}

Active Shorts:
{active_shorts} / {main_mod.MAX_SHORT_TRADES}

Total Active:
{active_longs + active_shorts} / {main_mod.MAX_ACTIVE_TRADES}
"""
    bot.reply_to(message, text)


# =========================
# AUTO GRADE FILTER COMMAND
# =========================

@bot.message_handler(commands=['grade'])
def set_grade(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Usage: /grade A\n\nAllowed: A+ A B C")
            return
        
        grade = parts[1].upper()
        
        if grade not in main_mod.GRADE_PRIORITY:
            bot.reply_to(
                message,
                f"❌ Invalid grade: {grade}\n\nAllowed: A+ A B C"
            )
            return
        
        main_mod.AUTO_TRADE_MIN_GRADE = grade
        bot.reply_to(
            message,
            f"✅ Auto Trade Grade Filter updated to {grade}"
        )
    except Exception as e:
        bot.reply_to(message, f"ERROR: {str(e)}")