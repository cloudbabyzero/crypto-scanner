"""Telegram command handlers module.

All handlers are registered here and access shared state/functions from main.py
through the main_mod reference to avoid circular imports.
"""

import sys
import threading
import bingx_client

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
        signals = 0
        skipped = 0
        errors = 0
        
        for symbol in main_mod.symbols:
            scanned += 1
            try:
                res = main_mod.analyze(symbol)
            except Exception:
                res = {"symbol": symbol, "result": "error"}
            
            if not isinstance(res, dict):
                res = {"symbol": symbol, "result": "skipped"}
            
            r = res.get("result")
            if r == "signal":
                signals += 1
            elif r == "error":
                errors += 1
            else:
                skipped += 1
        
        if signals > 0:
            summary = f"""
✅ FORCE SCAN COMPLETE

Scanned:
{scanned} coins

Signals:
{signals}

Skipped:
{skipped}

Errors:
{errors}
"""
        else:
            summary = f"""
⚠️ FORCE SCAN COMPLETE

No valid signals found.

Scanned:
{scanned} coins

Skipped:
{skipped}

Errors:
{errors}
"""
        
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
⚙️ CURRENT CONFIG

ADX FILTER:
{main_mod.ADX_FILTER}

MIN SCORE:
{main_mod.MIN_SCORE}

ATR FILTER:
{main_mod.ATR_FILTER}

COOLDOWN:
{main_mod.COOLDOWN}

SCAN INTERVAL:
{main_mod.SCAN_INTERVAL}
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

    text = f"""
💓 HEARTBEAT

Status: ONLINE
Uptime: {uptime_str}
Active Trades: {active_count}
Coins: {len(main_mod.symbols)}
Auto Trade: {auto_trade_status}
Time: {current_time}
"""
    bot.reply_to(message, text)


# =========================
# SCAN REPORT COMMAND
# =========================

@bot.message_handler(commands=['scanreport'])
def scanreport(message):
    results = getattr(main_mod, 'scan_results', {})
    
    if not results:
        bot.reply_to(message, "📋 SCAN REPORT\n\nNo scan data yet.")
        return
    
    text = "📋 SCAN REPORT\n\n"
    for symbol in main_mod.symbols:
        reason = results.get(symbol, "Not Scanned")
        text += f"{symbol} - {reason}\n"
    
    bot.reply_to(message, text)


# =========================
# HELP COMMAND
# =========================

@bot.message_handler(commands=['help'])
def help_command(message):
    text = """
🤖 AVAILABLE COMMANDS

/ping
เช็คว่าบอทยังออนไลน์ไหม

/status
ดูสถานะบอท

/heartbeat
ดูสถานะแบบละเอียด

/scanreport
ดูผลสแกนล่าสุด

/stats
ดู winrate

/trades
ดู active trades

/coins
ดูเหรียญที่สแกน

/forcecheck
บังคับสแกนทันที

/csv
ดาวน์โหลด signals.csv

/adx 18
เปลี่ยน ADX filter

/score 85
เปลี่ยน minimum score

/atr 0.4
เปลี่ยน ATR filter

/config
ดู config ปัจจุบัน

/long xrp
เปิด LONG

/short xrp
เปิด SHORT

---

🤖 AUTO TRADE COMMANDS

/autoon
เปิด AUTO TRADE

/autooff
ปิด AUTO TRADE

/autostatus
ดู status AUTO TRADE

/grade A
เปลี่ยน grade filter (A+, A, B, C)

/autoatr 0.5
เปลี่ยน ATR filter

/autoadx 22
เปลี่ยน ADX filter

/autovol on
เปิด High Volume Only

/autovol off
ปิด High Volume Only

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
    vol_text = "🟢 ON" if main_mod.AUTO_TRADE_HIGH_VOLUME_ONLY else "🔴 OFF"
    
    text = f"""
🤖 AUTO TRADE STATUS

Status:
{status_text}

Grade Filter:
{main_mod.AUTO_TRADE_MIN_GRADE}

ATR Filter:
{main_mod.AUTO_TRADE_MIN_ATR}%

ADX Filter:
{main_mod.AUTO_TRADE_MIN_ADX}

High Volume Only:
{vol_text}

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


# =========================
# AUTO TRADE MIN ATR COMMAND
# =========================

@bot.message_handler(commands=['autoatr'])
def set_autoatr(message):
    try:
        value = parse_command_value(message.text, float)
        main_mod.AUTO_TRADE_MIN_ATR = value
        bot.reply_to(
            message,
            f"✅ Auto Trade ATR Filter updated to {value}%"
        )
    except Exception:
        bot.reply_to(message, "Usage: /autoatr 0.5")


# =========================
# AUTO TRADE MIN ADX COMMAND
# =========================

@bot.message_handler(commands=['autoadx'])
def set_autoadx(message):
    try:
        value = parse_command_value(message.text, int)
        main_mod.AUTO_TRADE_MIN_ADX = value
        bot.reply_to(
            message,
            f"✅ Auto Trade ADX Filter updated to {value}"
        )
    except Exception:
        bot.reply_to(message, "Usage: /autoadx 22")


# =========================
# AUTO TRADE VOLUME ONLY COMMAND
# =========================

@bot.message_handler(commands=['autovol'])
def set_autovol(message):
    try:
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(
                message,
                "Usage: /autovol on\nUsage: /autovol off"
            )
            return
        
        setting = parts[1].lower()
        
        if setting == "on":
            main_mod.AUTO_TRADE_HIGH_VOLUME_ONLY = True
            bot.reply_to(
                message,
                "✅ Auto Trade High Volume Only: ON"
            )
        elif setting == "off":
            main_mod.AUTO_TRADE_HIGH_VOLUME_ONLY = False
            bot.reply_to(
                message,
                "✅ Auto Trade High Volume Only: OFF"
            )
        else:
            bot.reply_to(
                message,
                "❌ Invalid setting. Use: on or off"
            )
    except Exception as e:
        bot.reply_to(message, f"ERROR: {str(e)}")
