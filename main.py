import time
from datetime import datetime, timezone

import schedule

from broker.oanda_connector import OandaConnector
from config import Config
from indicators.technical import TechnicalIndicators
from logs.logger import logger
from notifications.telegram_bot import TelegramNotifier
from risk.risk_manager import RiskManager
from strategy.signal_generator import SignalGenerator


class XAUUSDBot:
    def __init__(self):
        logger.info(f"🚀 Initializing {Config.SYMBOL} {Config.TIMEFRAME} Bot (Linux/OANDA)")
        self.broker = OandaConnector()
        self.signal = SignalGenerator()
        self.telegram = TelegramNotifier()
        self.risk = None
        self.active_signal = None
        self.running = True
        self.iteration = 0
        self.last_managed_trade = None

    def start(self):
        if not self.broker.connect():
            logger.error("Cannot connect to OANDA. Check API credentials and practice/live mode.")
            return
        self.risk = RiskManager(self.broker)
        self.telegram.send_bot_status(
            f"✅ Bot Started | {Config.SYMBOL} {Config.TIMEFRAME} | "
            f"{'DEMO' if Config.OANDA_PRACTICE else 'LIVE'}"
        )
        schedule.every().day.at("21:00").do(self.send_daily_report)
        logger.success("Bot started successfully")
        self.print_settings()
        self.run_loop()

    def run_loop(self):
        logger.info(f"Starting main loop; polling every {Config.POLL_SECONDS}s")
        while self.running:
            try:
                self.iteration += 1
                schedule.run_pending()
                self.tick()
                time.sleep(Config.POLL_SECONDS)
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                self.stop()
            except Exception as exc:
                logger.error(f"Loop error: {exc}")
                self.telegram.send_warning(f"Bot error: {exc}")
                time.sleep(5)
                self.broker.reconnect()

    def tick(self):
        candles = self.broker.get_candles()
        price = self.broker.get_live_price()
        if candles is None or candles.empty or not price:
            logger.warning("Market data unavailable")
            return

        positions = self.broker.get_open_positions()
        if positions is None:
            logger.warning("Open positions unavailable; skipping trading and management this tick")
            return
        if positions:
            self.manage_position(positions[0], candles)
        else:
            self.active_signal = None
            self.look_for_signal(candles)

        if self.iteration % 20 == 0:
            self.log_status(price)

    def look_for_signal(self, candles):
        if not self.risk or not self.risk.can_trade():
            return
        signal = self.signal.generate(candles)
        if not signal:
            return

        lot_size = self.risk.calculate_lot_size(signal["entry"], signal["stop_loss"])
        if lot_size <= 0:
            logger.warning("Signal skipped: calculated position size is below the minimum lot")
            return
        logger.signal(
            f"{signal['type']} | {signal['signal_method']} | Entry {signal['entry']} | "
            f"SL {signal['stop_loss']} | TP {signal['take_profit']} | Lots {lot_size}"
        )
        self.telegram.send_signal(signal, lot_size)

        result = self.broker.place_order(
            signal["type"],
            lot_size,
            signal["stop_loss"],
            signal["take_profit"],
            f"{Config.MAGIC_COMMENT} | {signal['signal_method']}",
        )
        if result:
            self.active_signal = signal
            trade_id = self.broker.get_trade_id(result)
            self.last_managed_trade = trade_id
            self.telegram.send_trade_opened(signal, lot_size, trade_id)

    def manage_position(self, position, candles):
        if not self.risk:
            return
        position_type = "BUY" if float(position.get("currentUnits", 0)) > 0 else "SELL"
        trade_id = position.get("id", "N/A")
        profit = float(position.get("unrealizedPL", 0))

        exit_now, warnings = self.signal.check_trend_end(candles, position_type)
        for warning in warnings:
            logger.warning(warning)
            self.telegram.send_warning(f"Trade #{trade_id}\n{warning}")

        if not self.active_signal and trade_id != "N/A":
            self.active_signal = self._signal_from_position(position, candles, position_type)

        if exit_now:
            if self.broker.close_position(trade_id):
                self.telegram.send_trade_closed(trade_id, profit, "Trend End Signal")
                self.active_signal = None
            return

        if self.active_signal:
            self.risk.manage_breakeven(position, self.active_signal)
            enriched = TechnicalIndicators.calculate_all(candles)
            self.risk.apply_trailing_stop(position, float(enriched.iloc[-1]["atr"]))

    @staticmethod
    def _signal_from_position(position, candles, position_type):
        entry = float(position.get("price", 0))
        stop = float(position.get("stopLossOrder", {}).get("price", entry) or entry)
        take_profit = float(position.get("takeProfitOrder", {}).get("price", entry) or entry)
        atr = float(TechnicalIndicators.calculate_all(candles).iloc[-1]["atr"])
        return {
            "type": position_type,
            "entry": entry,
            "stop_loss": stop,
            "take_profit": take_profit,
            "breakeven": entry + atr * Config.ATR_BE_MULTI if position_type == "BUY" else entry - atr * Config.ATR_BE_MULTI,
        }

    def log_status(self, price):
        account = self.broker.get_account_info()
        stats = self.broker.get_daily_stats()
        if account:
            currency = account.get("currency", "")
            daily_summary = (
                f"Daily P&L {stats['profit']:+.2f} {currency} | Trades {stats['trades']} | "
                f"Win rate {stats['win_rate']:.1f}%"
                if stats
                else "Daily stats unavailable"
            )
            logger.info(
                f"STATUS {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC | "
                f"Price {price['bid']:.3f}/{price['ask']:.3f} {Config.SYMBOL.split('_')[-1]} | "
                f"Spread {price['spread']:.4f} | Balance {account['balance']:,.2f} {currency} | "
                f"Equity {account['equity']:,.2f} {currency} | {daily_summary}"
            )

    def send_daily_report(self):
        account = self.broker.get_account_info()
        stats = self.broker.get_daily_stats()
        if account and stats:
            self.telegram.send_daily_report(stats, account)

    def print_settings(self):
        logger.info(
            f"Settings | Symbol {Config.SYMBOL} | Timeframe {Config.TIMEFRAME} | "
            f"Risk {Config.RISK_PERCENT}% | Max trades {Config.MAX_DAILY_TRADES} | "
            f"Max daily loss {Config.MAX_DAILY_LOSS}% | "
            f"Max account drawdown {Config.MAX_ACCOUNT_DRAWDOWN}% | "
            f"Baseline {Config.DRAWDOWN_BASELINE_BALANCE:,.2f} | SL {Config.ATR_SL_MULTI}x ATR | "
            f"TP {Config.ATR_TP_MULTI}x ATR | Chandelier Entry "
            f"{Config.CHANDELIER_ATR_PERIOD}/{Config.CHANDELIER_ATR_MULTIPLIER}x ATR | "
            "Sessions London + New York only"
        )

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.broker.disconnect()
        self.telegram.send_bot_status("🔴 Bot Stopped")
        logger.info("Bot stopped successfully")


if __name__ == "__main__":
    XAUUSDBot().start()
