from datetime import datetime, timezone

import pandas as pd
import requests

from config import Config
from logs.logger import logger


class OandaConnector:
    def __init__(self):
        self.api_key = Config.OANDA_API_KEY
        self.account_id = Config.OANDA_ACCOUNT_ID
        self.base_url = Config.OANDA_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.connected = False

    def _request(self, method, path, **kwargs):
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self.headers,
            timeout=15,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def connect(self):
        if not self.api_key or not self.account_id:
            logger.error("OANDA_API_KEY and OANDA_ACCOUNT_ID must be set")
            return False
        try:
            account = self.get_account_info()
            self.connected = account is not None
            if account:
                logger.success(
                    f"OANDA connected ({'DEMO' if Config.OANDA_PRACTICE else 'LIVE'}) | "
                    f"Account: {self.account_id} | Balance: ${account['balance']:,.2f} "
                    f"| Equity: ${account['equity']:,.2f} {account['currency']}"
                )
            return self.connected
        except requests.RequestException as exc:
            logger.error(f"OANDA connection failed: {exc}")
            return False

    def disconnect(self):
        self.connected = False
        logger.info("OANDA disconnected")

    def reconnect(self):
        self.disconnect()
        return self.connect()

    def get_account_info(self):
        try:
            account = self._request("GET", f"/v3/accounts/{self.account_id}")["account"]
            return {
                "balance": float(account["balance"]),
                "equity": float(account["NAV"]),
                "profit": float(account["unrealizedPL"]),
                "margin_used": float(account["marginUsed"]),
                "margin_free": float(account["marginAvailable"]),
                "currency": account["currency"],
                "open_trades": int(account["openTradeCount"]),
            }
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.error(f"Account info error: {exc}")
            return None

    def get_candles(self, symbol=None, timeframe=None, count=None):
        symbol = symbol or Config.SYMBOL
        timeframe = timeframe or Config.TIMEFRAME
        count = count or Config.CANDLES_COUNT
        granularity = Config.TIMEFRAME_MAP.get(timeframe)
        if not granularity:
            logger.error(f"Unsupported OANDA timeframe: {timeframe}")
            return None
        try:
            payload = self._request(
                "GET",
                f"/v3/instruments/{symbol}/candles",
                params={"count": count, "granularity": granularity, "price": "M"},
            )
            rows = [
                {
                    "time": pd.to_datetime(candle["time"], utc=True),
                    "open": float(candle["mid"]["o"]),
                    "high": float(candle["mid"]["h"]),
                    "low": float(candle["mid"]["l"]),
                    "close": float(candle["mid"]["c"]),
                    "volume": int(candle["volume"]),
                }
                for candle in payload.get("candles", [])
                if candle.get("complete")
            ]
            if not rows:
                logger.warning(f"No complete candles received for {symbol} {timeframe}")
                return None
            result = pd.DataFrame(rows).set_index("time").sort_index()
            logger.info(f"Fetched {len(result)} candles | {symbol} {timeframe}")
            return result
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.error(f"Candle fetch error: {exc}")
            return None

    def get_live_price(self, symbol=None):
        symbol = symbol or Config.SYMBOL
        try:
            prices = self._request(
                "GET",
                f"/v3/accounts/{self.account_id}/pricing",
                params={"instruments": symbol},
            ).get("prices", [])
            if not prices:
                return None
            price = prices[0]
            bid = float(price["bids"][0]["price"])
            ask = float(price["asks"][0]["price"])
            return {
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "spread": ask - bid,
                "time": price.get("time"),
            }
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.error(f"Price fetch error: {exc}")
            return None

    def get_spread(self):
        price = self.get_live_price()
        return price["spread"] if price else float("inf")

    def place_order(self, order_type, lot_size, stop_loss, take_profit, comment=""):
        spread = self.get_spread()
        if spread > Config.MAX_SPREAD:
            logger.warning(f"Spread too high: {spread:.4f} > {Config.MAX_SPREAD:.4f}")
            return None

        units = max(1, int(round(lot_size * Config.UNITS_PER_LOT)))
        if order_type == "SELL":
            units = -units
        decimals = Config.PRICE_DECIMALS
        payload = {
            "order": {
                "type": "MARKET",
                "instrument": Config.SYMBOL,
                "units": str(units),
                "timeInForce": "FOK",
                "stopLossOnFill": {
                    "price": f"{stop_loss:.{decimals}f}",
                    "timeInForce": "GTC",
                },
                "takeProfitOnFill": {
                    "price": f"{take_profit:.{decimals}f}",
                    "timeInForce": "GTC",
                },
                "clientExtensions": {"comment": comment or Config.MAGIC_COMMENT},
            }
        }
        try:
            result = self._request(
                "POST",
                f"/v3/accounts/{self.account_id}/orders",
                json=payload,
            )
            trade_id = self.get_trade_id(result)
            logger.trade(
                f"Order placed | {order_type} | Trade: {trade_id} | Units: {units} "
                f"| SL: {stop_loss:.{decimals}f} | TP: {take_profit:.{decimals}f}"
            )
            return result
        except requests.RequestException as exc:
            logger.error(f"Order error: {exc}")
            return None

    @staticmethod
    def get_trade_id(result):
        fill = result.get("orderFillTransaction", {})
        opened = fill.get("tradeOpened", {})
        return opened.get("tradeID") or fill.get("tradeID") or "N/A"

    def close_position(self, trade_id):
        try:
            self._request("PUT", f"/v3/accounts/{self.account_id}/trades/{trade_id}/close")
            logger.trade(f"Position {trade_id} closed")
            return True
        except requests.RequestException as exc:
            logger.error(f"Close error for {trade_id}: {exc}")
            return False

    def modify_sl(self, trade_id, new_sl):
        try:
            self._request(
                "PUT",
                f"/v3/accounts/{self.account_id}/trades/{trade_id}/orders",
                json={
                    "stopLoss": {
                        "price": f"{new_sl:.{Config.PRICE_DECIMALS}f}",
                        "timeInForce": "GTC",
                    }
                },
            )
            return True
        except requests.RequestException as exc:
            logger.error(f"Modify SL error for {trade_id}: {exc}")
            return False

    def get_open_positions(self):
        try:
            return self._request(
                "GET",
                f"/v3/accounts/{self.account_id}/trades",
                params={"instrument": Config.SYMBOL, "state": "OPEN"},
            ).get("trades", [])
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.error(f"Get positions error: {exc}")
            return []

    def get_daily_stats(self):
        try:
            trades = self._request(
                "GET",
                f"/v3/accounts/{self.account_id}/trades",
                params={"instrument": Config.SYMBOL, "state": "CLOSED", "count": 500},
            ).get("trades", [])
            today = datetime.now(timezone.utc).date()
            today_trades = []
            for trade in trades:
                closed_at = trade.get("closeTime") or trade.get("openTime")
                if closed_at and pd.to_datetime(closed_at, utc=True).date() == today:
                    today_trades.append(trade)
            profits = [float(trade.get("realizedPL", 0)) for trade in today_trades]
            wins = sum(profit > 0 for profit in profits)
            losses = sum(profit < 0 for profit in profits)
            return {
                "trades": len(today_trades),
                "profit": sum(profits),
                "wins": wins,
                "losses": losses,
                "win_rate": wins / len(today_trades) * 100 if today_trades else 0,
            }
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.error(f"Daily stats error: {exc}")
            return {"trades": 0, "profit": 0, "wins": 0, "losses": 0, "win_rate": 0}
