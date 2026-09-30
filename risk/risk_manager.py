import json
import math
import os
from pathlib import Path

from config import Config
from logs.logger import logger


RISK_STATE_PATH = Path(__file__).resolve().parents[1] / ".risk_state.json"


class RiskManager:
    def __init__(self, broker):
        self.broker = broker
        self._breakeven_tickets = set()
        self._last_trailing_sl = {}
        self._drawdown_locked = False
        self._load_risk_state()

    def _load_risk_state(self):
        try:
            state = json.loads(RISK_STATE_PATH.read_text())
            baseline = float(state["baseline_balance"])
            if baseline <= 0:
                raise ValueError("baseline_balance must be positive")
            if not math.isclose(baseline, Config.DRAWDOWN_BASELINE_BALANCE):
                logger.warning(
                    "Risk state baseline differs from configuration; preserving the saved baseline"
                )
            self._baseline_balance = baseline
            self._drawdown_locked = bool(state.get("drawdown_locked", False))
        except FileNotFoundError:
            self._baseline_balance = Config.DRAWDOWN_BASELINE_BALANCE
            self._save_risk_state()
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._baseline_balance = Config.DRAWDOWN_BASELINE_BALANCE
            self._drawdown_locked = True
            logger.error(f"Risk state unavailable or invalid; trading locked: {exc}")

    def _save_risk_state(self):
        temporary_path = RISK_STATE_PATH.with_suffix(".tmp")
        try:
            temporary_path.write_text(
                json.dumps(
                    {
                        "baseline_balance": self._baseline_balance,
                        "drawdown_locked": self._drawdown_locked,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary_path, RISK_STATE_PATH)
            return True
        except OSError as exc:
            logger.error(f"Could not persist risk state; trading locked: {exc}")
            self._drawdown_locked = True
            return False

    def calculate_lot_size(self, entry, stop_loss, balance=None):
        account = self.broker.get_account_info() if balance is None else None
        if balance is None and account is None:
            logger.warning("Position sizing failed: account balance unavailable")
            return 0.0
        if account is not None:
            quote_currency = Config.SYMBOL.rsplit("_", 1)[-1]
            if account.get("currency") != quote_currency:
                logger.error(
                    "Position sizing blocked: account currency "
                    f"{account.get('currency')} differs from {Config.SYMBOL} quote currency "
                    f"{quote_currency}; a quote-to-account conversion factor is required"
                )
                return 0.0
        balance = balance if balance is not None else account["balance"]
        price_risk = abs(entry - stop_loss)
        risk_amount = balance * Config.RISK_PERCENT / 100
        if (
            balance <= 0
            or price_risk <= 0
            or Config.UNITS_PER_LOT <= 0
            or Config.MIN_LOT_SIZE <= 0
            or Config.MAX_LOT_SIZE < Config.MIN_LOT_SIZE
        ):
            return 0.0

        # Never round position size up beyond the configured risk budget.
        units = risk_amount / price_risk
        lots = min(units / Config.UNITS_PER_LOT, Config.MAX_LOT_SIZE)
        lot_size = math.floor(lots * 100) / 100
        if lot_size < Config.MIN_LOT_SIZE:
            logger.warning(
                f"Signal skipped: risk budget supports {lots:.4f} lots, below minimum "
                f"{Config.MIN_LOT_SIZE:.2f}"
            )
            return 0.0
        logger.info(
            f"Position size | Balance ${balance:,.2f} | Risk ${risk_amount:,.2f} "
            f"| Price risk {price_risk:.3f} | Lots {lot_size}"
        )
        return lot_size

    def can_trade(self):
        if self._drawdown_locked:
            logger.error("Risk check failed: cumulative drawdown lock is active")
            return False

        account = self.broker.get_account_info()
        stats = self.broker.get_daily_stats()
        positions = self.broker.get_open_positions()
        if account is None or stats is None or positions is None:
            logger.warning("Risk check failed: account, trade stats, or positions unavailable")
            return False

        equity = float(account.get("equity", 0))
        drawdown_percent = (
            (self._baseline_balance - equity) / self._baseline_balance * 100
            if self._baseline_balance > 0
            else float("inf")
        )
        if drawdown_percent >= Config.MAX_ACCOUNT_DRAWDOWN:
            self._drawdown_locked = True
            logger.error(
                f"Cumulative drawdown lock triggered: {drawdown_percent:.2f}% "
                f"(limit {Config.MAX_ACCOUNT_DRAWDOWN:.2f}%)"
            )
            self._save_risk_state()
            return False

        try:
            spread_ok = self.broker.get_spread() <= Config.MAX_SPREAD
        except Exception as exc:
            logger.error(f"Spread check unavailable; trading blocked: {exc}")
            return False

        checks = {
            "max_trades": stats["trades"] < Config.MAX_DAILY_TRADES,
            "max_loss": self._loss_percent(stats, account) < Config.MAX_DAILY_LOSS,
            "open_position": not positions,
            "spread": spread_ok,
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
