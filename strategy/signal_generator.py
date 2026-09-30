from datetime import datetime, timezone

import pandas as pd

from config import Config
from indicators.technical import TechnicalIndicators
from logs.logger import logger


class SignalGenerator:
    def __init__(self):
        self.last_signal = None

    def generate(self, df):
        minimum_bars = Config.CHANDELIER_ATR_PERIOD + 1
        if len(df) < minimum_bars:
            logger.warning(
                f"Not enough candle data for Chandelier Exit: need {minimum_bars}, got {len(df)}"
            )
            return None

        enriched = TechnicalIndicators.calculate_all(df)
        if len(enriched) < minimum_bars:
            logger.warning("Not enough valid candles for Chandelier Exit signal generation")
            return None

        session = self.get_session()
        if not session:
            return None

        state = self.chandelier_state(df)
        if state is None:
            return None

        direction = state["direction"]
        signal_type = None
        if direction.iloc[-1] == 1 and direction.iloc[-2] == -1:
            signal_type = "BUY"
        elif direction.iloc[-1] == -1 and direction.iloc[-2] == 1:
            signal_type = "SELL"
        if not signal_type:
            return None

        candle_time = enriched.index[-1]
        if (
            self.last_signal
            and self.last_signal["type"] == signal_type
            and self.last_signal.get("candle_time") == candle_time
        ):
            return None

        current = enriched.iloc[-1]
        signal = self.build_signal(signal_type, current, session, enriched)
        signal["signal_method"] = "Chandelier Exit"
        signal["candle_time"] = candle_time
        signal["chandelier_stop"] = round(
            float(state["long_stop"].iloc[-1] if signal_type == "BUY" else state["short_stop"].iloc[-1]),
            Config.PRICE_DECIMALS,
        )
        signal["chandelier_atr"] = round(float(state["atr"].iloc[-1]), Config.PRICE_DECIMALS)

        self.last_signal = signal
        logger.signal(
            f"Chandelier Exit flip: {signal['type']} | "
            f"ATR distance {signal['chandelier_atr']} | Stop {signal['chandelier_stop']} | "
            f"RR: 1:{signal['rr']}"
        )
        return signal

    @staticmethod
    def chandelier_state(df):
        """Match TradingView Chandelier Exit direction using completed candles."""
        period = Config.CHANDELIER_ATR_PERIOD
        if len(df) < period + 1:
            return None

        true_range = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"] - df["close"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = pd.Series(float("nan"), index=df.index)
        if len(df) >= period:
            atr.iloc[period - 1] = true_range.iloc[:period].mean()
            for index in range(period, len(df)):
                atr.iloc[index] = (
                    atr.iloc[index - 1] * (period - 1) + true_range.iloc[index]
                ) / period
        atr *= Config.CHANDELIER_ATR_MULTIPLIER

        long_extreme = df["close"].rolling(period, min_periods=period).max()
        short_extreme = df["close"].rolling(period, min_periods=period).min()
        long_stop = (long_extreme - atr).copy()
        short_stop = (short_extreme + atr).copy()
        direction = [1] * len(df)

        for index in range(1, len(df)):
            previous_long_stop = long_stop.iloc[index - 1]
            previous_short_stop = short_stop.iloc[index - 1]
            if pd.isna(previous_long_stop):
                previous_long_stop = long_stop.iloc[index]
            if pd.isna(previous_short_stop):
                previous_short_stop = short_stop.iloc[index]
            if pd.isna(previous_long_stop) or pd.isna(previous_short_stop):
                direction[index] = direction[index - 1]
                continue

            if df["close"].iloc[index - 1] > previous_long_stop:
                long_stop.iloc[index] = max(long_stop.iloc[index], previous_long_stop)
            if df["close"].iloc[index - 1] < previous_short_stop:
                short_stop.iloc[index] = min(short_stop.iloc[index], previous_short_stop)

            if df["close"].iloc[index] > previous_short_stop:
                direction[index] = 1
            elif df["close"].iloc[index] < previous_long_stop:
                direction[index] = -1
            else:
                direction[index] = direction[index - 1]

        return pd.DataFrame(
            {
                "direction": direction,
                "long_stop": long_stop,
                "short_stop": short_stop,
                "atr": atr,
            },
            index=df.index,
        )

    def build_signal(self, signal_type, current, session, df):
        atr = float(current["atr"])
        entry = float(current["close"])
        if signal_type == "BUY":
            stop_loss = entry - atr * Config.ATR_SL_MULTI
            take_profit = entry + atr * Config.ATR_TP_MULTI
            breakeven = entry + atr * Config.ATR_BE_MULTI
        else:
            stop_loss = entry + atr * Config.ATR_SL_MULTI
            take_profit = entry - atr * Config.ATR_TP_MULTI
            breakeven = entry - atr * Config.ATR_BE_MULTI

        sl_distance = abs(entry - stop_loss)
        tp_distance = abs(entry - take_profit)
        return {
            "type": signal_type,
            "entry": round(entry, Config.PRICE_DECIMALS),
            "stop_loss": round(stop_loss, Config.PRICE_DECIMALS),
            "take_profit": round(take_profit, Config.PRICE_DECIMALS),
            "breakeven": round(breakeven, Config.PRICE_DECIMALS),
            "sl_pips": round(sl_distance, Config.PRICE_DECIMALS),
            "tp_pips": round(tp_distance, Config.PRICE_DECIMALS),
            "rr": round(tp_distance / sl_distance, 2) if sl_distance else 0,
            "atr": round(atr, Config.PRICE_DECIMALS),
            "rsi": round(float(current["rsi"]), 2),
            "macd": round(float(current["macd"]), 4),
            "ema9": round(float(current["ema9"]), Config.PRICE_DECIMALS),
            "ema21": round(float(current["ema21"]), Config.PRICE_DECIMALS),
            "ema50": round(float(current["ema50"]), Config.PRICE_DECIMALS),
            "trend": current["trend"],
            "session": session,
            "timestamp": datetime.now(timezone.utc),
            "levels": TechnicalIndicators.detect_support_resistance(df),
        }

    def check_trend_end(self, df, position_type):
        enriched = TechnicalIndicators.calculate_all(df)
        if len(enriched) < 2:
            return False, []
        current = enriched.iloc[-1]
        previous = enriched.iloc[-2]
        warnings = []
        exit_now = False

        if position_type == "BUY":
            if current["ema9"] < current["ema21"]:
                warnings.append("🚨 EXIT NOW: EMA9 crossed below EMA21")
                exit_now = True
            if current["close"] < current["ema50"]:
                warnings.append("🚨 EXIT NOW: Price broke below EMA50")
                exit_now = True
            if current["rsi"] > 75:
                warnings.append("⚠️ WARNING: RSI extremely overbought")
            if current["macd_hist"] < previous["macd_hist"] and current["macd_hist"] < 0:
                warnings.append("⚠️ WARNING: MACD momentum fading")
            if current["close"] < current["ema9"]:
                warnings.append("⚠️ WARNING: Price below EMA9")
        else:
            if current["ema9"] > current["ema21"]:
                warnings.append("🚨 EXIT NOW: EMA9 crossed above EMA21")
                exit_now = True
            if current["close"] > current["ema50"]:
                warnings.append("🚨 EXIT NOW: Price broke above EMA50")
                exit_now = True
            if current["rsi"] < 25:
                warnings.append("⚠️ WARNING: RSI extremely oversold")
            if current["macd_hist"] > previous["macd_hist"] and current["macd_hist"] > 0:
                warnings.append("⚠️ WARNING: MACD momentum fading")
            if current["close"] > current["ema9"]:
                warnings.append("⚠️ WARNING: Price above EMA9")
        return exit_now, warnings

    @staticmethod
    def get_session():
        hour = datetime.now(timezone.utc).hour
        in_london = Config.LONDON_OPEN <= hour < Config.LONDON_CLOSE
        in_new_york = Config.NY_OPEN <= hour < Config.NY_CLOSE
        if in_london and in_new_york:
            return "LONDON + NEW YORK (Best)"
        if in_london:
            return "LONDON"
        if in_new_york:
            return "NEW YORK"
        return None

