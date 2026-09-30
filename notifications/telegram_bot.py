from datetime import datetime, timezone

import requests

from config import Config
from logs.logger import logger


class TelegramNotifier:
    def __init__(self):
        self.token = (Config.TELEGRAM_TOKEN or "").strip()
        self.chat_id = (Config.TELEGRAM_CHAT_ID or "").strip()
        placeholders = {"your-telegram-bot-token", "your-chat-id"}
        self.enabled = bool(self.token and self.chat_id) and not (
            self.token.lower() in placeholders or self.chat_id.lower() in placeholders
        )
        if (self.token or self.chat_id) and not self.enabled:
            logger.warning("Telegram notifications disabled: replace the placeholder token/chat ID")

    def send(self, message):
        if not self.enabled:
            return False
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.error(f"Telegram error: {exc}")
            return False

    def send_bot_status(self, message):
        return self.send(f"🤖 <b>BOT STATUS:</b> {message}")

    def send_warning(self, message):
        return self.send(f"⚠️ <b>WARNING</b>\n{message}")

    def send_signal(self, signal, lot_size):
        emoji = "🟢" if signal["type"] == "BUY" else "🔴"
        signal_details = (
            f"Chandelier ATR distance: {signal['chandelier_atr']}\n"
            f"Chandelier stop: {signal['chandelier_stop']}"
        )
        levels = "\n".join(
            f"{kind.title()}: {level:.3f}" for kind, level in signal.get("levels", [])[-4:]
        ) or "No recent swing levels"
        message = (
            f"{emoji} <b>{Config.SYMBOL} {Config.TIMEFRAME} {signal['type']} SIGNAL</b>\n\n"
            f"📍 <b>Session:</b> {signal['session']}\n"
            f"⏰ <b>Time:</b> {signal['timestamp'].strftime('%H:%M:%S UTC')}\n"
            f"🧭 <b>Signal:</b> {signal['signal_method']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n<b>TRADE LEVELS</b>\n"
            f"📌 Entry: <b>{signal['entry']}</b>\n"
            f"🛑 Stop Loss: <b>{signal['stop_loss']}</b> ({signal['sl_pips']})\n"
            f"✅ Take Profit: <b>{signal['take_profit']}</b> ({signal['tp_pips']})\n"
            f"🔄 Breakeven: <b>{signal['breakeven']}</b>\n"
            f"📊 Lots: <b>{lot_size}</b>\n"
            f"⚖️ Risk/Reward: <b>1:{signal['rr']}</b>\n\n"
            f"📈 <b>INDICATORS</b>\n"
            f"RSI: {signal['rsi']} | ATR: {signal['atr']} | Trend: {signal['trend']}\n"
            f"MACD: {signal['macd']} | EMA9: {signal['ema9']} | EMA21: {signal['ema21']}\n"
            f"{signal_details}\n\n"
            f"📐 <b>RECENT LEVELS</b>\n{levels}\n\n"
            f"✔️ Signal triggered by confirmed Chandelier Exit direction flip."
        )
        return self.send(message)

    def send_trade_opened(self, signal, lot_size, trade_id):
        emoji = "🟢" if signal["type"] == "BUY" else "🔴"
        return self.send(
            f"{emoji} <b>TRADE OPENED #{trade_id}</b>\n\n"
            f"Type: {signal['type']}\nEntry: {signal['entry']}\n"
            f"SL: {signal['stop_loss']}\nTP: {signal['take_profit']}\nLots: {lot_size}"
        )

    def send_trade_closed(self, trade_id, profit, reason=""):
        emoji = "✅" if profit > 0 else "❌"
        return self.send(
            f"{emoji} <b>TRADE CLOSED #{trade_id}</b>\n\n"
            f"Profit: {profit:+.2f} (account currency)\nReason: {reason}\n"
            f"Time: {datetime.now(timezone.utc):%H:%M:%S UTC}"
        )

    def send_daily_report(self, stats, account):
        currency = account.get("currency", "account currency")
        return self.send(
            f"📊 <b>DAILY REPORT - {datetime.now(timezone.utc):%Y-%m-%d}</b>\n\n"
            f"💼 <b>Account</b>\nBalance: {account['balance']:,.2f} {currency}\n"
            f"Equity: {account['equity']:,.2f} {currency}\n"
            f"Unrealized P&L: {account['profit']:+,.2f} {currency}\n\n"
            f"📈 <b>Today's Performance</b>\nTrades: {stats['trades']}\n"
            f"Wins: {stats['wins']} | Losses: {stats['losses']}\n"
            f"Win rate: {stats['win_rate']:.1f}%\n"
            f"Daily P&L: {stats['profit']:+.2f} {currency}"
        )
