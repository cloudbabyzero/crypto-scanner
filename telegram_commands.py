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
        sl_val = trade.get('sl', 'N/A')
        tp2_val = trade.get('tp2', 'N/A')
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
# COINS COMMAND
# =========================

@bot.message_handler(commands=['coins'])
def coins(message):
    text = "🪙 COINS\n\n"
    for coin in main_mod.symbols:
        text += f"{coin}\n"
    bot.reply_to(message, text)


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

    text = f"""
💓 HEARTBEAT

Status: ONLINE
Uptime: {uptime_str}
Active Trades: {active_count}
Coins: {len(main_mod.symbols)}
Auto Trade: {auto_trade_status}
Market Mode: {market_mode}
Market Regime: {current_regime}
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
    
    lines = [symbol]
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

Market Regime:
{current_regime}
"""

    markup = types.InlineKeyboardMarkup(row_width=1)
    btn_trend = types.InlineKeyboardButton(
        "✅ Enable Trend Mode", callback_data="mode_trending"
    )
    btn_sideways = types.InlineKeyboardButton(
        "🔄 Enable Sideways Mode", callback_data="mode_sideways"
    )
    btn_skip = types.InlineKeyboardButton(
        "⏭ Skip", callback_data="mode_skip"
    )
    markup.add(btn_trend, btn_sideways, btn_skip)

    bot.send_message(message.chat.id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_"))
def market_mode_callback(call):
    action = call.data.replace("mode_", "")

    if action == "trending":
        main_mod.MARKET_MODE = "TRENDING"
        bot.answer_callback_query(call.id, "✅ Trend Mode enabled")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n✅ Market Mode set to TRENDING"
        )

    elif action == "sideways":
        main_mod.MARKET_MODE = "SIDEWAYS"
        bot.answer_callback_query(call.id, "🔄 Sideways Mode enabled")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{call.message.text}\n\n✅ Market Mode set to SIDEWAYS"
        )

    elif action == "skip":
        bot.answer_callback_query(call.id, "⏭ No changes made")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=call.message.text
        )


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

/config
ดู config ปัจจุบัน

/strategy
ดู TREND/SIDEWAYS strategy settings

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

/forcecheck
บังคับสแกนทันที

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
{len([t for t in trade_items if t.get('status') in ['PENDING', 'OPEN']])}
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



