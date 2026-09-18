from config import Config
from logs.logger import logger


class RiskManager:
    def __init__(self, broker):
        self.broker = broker
        self._breakeven_tickets = set()
        self._last_trailing_sl = {}

    def calculate_lot_size(self, entry, stop_loss, balance=None):
        account = self.broker.get_account_info()
        balance = balance if balance is not None else (account["balance"] if account else 0)
        price_risk = abs(entry - stop_loss)
        risk_amount = balance * Config.RISK_PERCENT / 100
        if balance <= 0 or price_risk <= 0:
            return Config.MIN_LOT_SIZE

        # OANDA units are converted using the configured XAU contract size.
        units = risk_amount / price_risk
        lots = units / Config.UNITS_PER_LOT
        lot_size = round(max(Config.MIN_LOT_SIZE, min(lots, Config.MAX_LOT_SIZE)), 2)
        logger.info(
            f"Position size | Balance ${balance:,.2f} | Risk ${risk_amount:,.2f} "
            f"| Price risk {price_risk:.3f} | Lots {lot_size}"
        )
        return lot_size

    def can_trade(self):
        stats = self.broker.get_daily_stats()
        account = self.broker.get_account_info()
        checks = {
            "account": account is not None,
            "max_trades": stats["trades"] < Config.MAX_DAILY_TRADES,
            "max_loss": bool(account) and self._loss_percent(stats, account) < Config.MAX_DAILY_LOSS,
            "open_position": not self.broker.get_open_positions(),
            "spread": self.broker.get_spread() <= Config.MAX_SPREAD,
        }
        for name, passed in checks.items():
            if not passed:
                logger.warning(f"Risk check failed: {name}")
                return False
        return True

    @staticmethod
    def _loss_percent(stats, account):
        balance = float(account.get("balance", 0))
        if balance <= 0:
            return float("inf")
        return abs(min(float(stats.get("profit", 0)), 0)) / balance * 100

    def manage_breakeven(self, position, signal):
        ticket = str(position.get("id", ""))
        if not ticket or ticket in self._breakeven_tickets:
            return
        price = self.broker.get_live_price()
        if not price:
            return

        is_buy = float(position.get("currentUnits", 0)) > 0
        entry = float(position.get("price", 0))
        current_sl = self._current_stop(position)
        reached = price["bid"] >= signal["breakeven"] if is_buy else price["ask"] <= signal["breakeven"]
        better_than_entry = (not current_sl or current_sl < entry) if is_buy else (not current_sl or current_sl > entry)
        if reached and better_than_entry:
            new_sl = entry + Config.BREAKEVEN_OFFSET if is_buy else entry - Config.BREAKEVEN_OFFSET
            if self.broker.modify_sl(ticket, new_sl):
                self._breakeven_tickets.add(ticket)
                logger.trade(f"Breakeven set for ticket {ticket}: {new_sl:.3f}")

    def apply_trailing_stop(self, position, atr):
        ticket = str(position.get("id", ""))
        price = self.broker.get_live_price()
        if not ticket or not price or not atr or atr <= 0:
            return

        is_buy = float(position.get("currentUnits", 0)) > 0
        current_sl = self._current_stop(position)
        new_sl = price["bid"] - atr * Config.ATR_TRAIL_MULTI if is_buy else price["ask"] + atr * Config.ATR_TRAIL_MULTI
        previous_sl = self._last_trailing_sl.get(ticket, current_sl)
        improves = new_sl > max(current_sl, previous_sl) if is_buy else (not current_sl and not previous_sl) or new_sl < min(
            value for value in (current_sl, previous_sl) if value
        )
        if improves and self.broker.modify_sl(ticket, new_sl):
            self._last_trailing_sl[ticket] = new_sl
            logger.trade(f"Trailing stop updated for {ticket}: {new_sl:.3f}")

    @staticmethod
    def _current_stop(position):
        stop = position.get("stopLossOrder", {})
        if isinstance(stop, dict):
            try:
                return float(stop.get("price", 0) or 0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0
