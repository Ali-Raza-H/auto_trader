import numpy as np
import pandas as pd

from config import Config


class TechnicalIndicators:
    @staticmethod
    def calculate_all(df):
        """Return a copy of the candle data enriched with all strategy indicators."""
        result = df.copy()
        close = result["close"]

        result["ema9"] = TechnicalIndicators.ema(result, Config.EMA_FAST)
        result["ema21"] = TechnicalIndicators.ema(result, Config.EMA_MEDIUM)
        result["ema50"] = TechnicalIndicators.ema(result, Config.EMA_SLOW)
        result["rsi"] = TechnicalIndicators.rsi(result, Config.RSI_PERIOD)
        result["atr"] = TechnicalIndicators.atr(result, Config.ATR_PERIOD)

        macd, signal, histogram = TechnicalIndicators.macd(result)
        result["macd"] = macd
        result["macd_signal"] = signal
        result["macd_hist"] = histogram

        result["volume_ma"] = result["volume"].rolling(20).mean()
        result["high_20"] = result["high"].rolling(20).max()
        result["low_20"] = result["low"].rolling(20).min()
        result["trend"] = np.where(
            result["ema9"] > result["ema21"], "BULLISH", "BEARISH"
        )
        result["momentum"] = close.diff(5)

        result["body_size"] = (result["close"] - result["open"]).abs()
        result["upper_wick"] = result["high"] - result[["open", "close"]].max(axis=1)
        result["lower_wick"] = result[["open", "close"]].min(axis=1) - result["low"]
        result["is_bullish_candle"] = result["close"] > result["open"]

        return result.replace([np.inf, -np.inf], np.nan).dropna()

    @staticmethod
    def ema(df, period):
        return df["close"].ewm(span=period, adjust=False).mean()

    @staticmethod
    def rsi(df, period=14):
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
        avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def atr(df, period=14):
        previous_close = df["close"].shift()
        true_range = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - previous_close).abs(),
                (df["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return true_range.ewm(com=period - 1, adjust=False).mean()

    @staticmethod
    def macd(df, fast=None, slow=None, signal=None):
        fast = fast or Config.MACD_FAST
        slow = slow or Config.MACD_SLOW
        signal = signal or Config.MACD_SIGNAL
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        return macd_line, signal_line, macd_line - signal_line

    @staticmethod
    def bollinger_bands(df, period=20, std=2):
        average = df["close"].rolling(period).mean()
        deviation = df["close"].rolling(period).std()
        return average + deviation * std, average, average - deviation * std

    @staticmethod
    def detect_support_resistance(df, lookback=50):
        recent = df.tail(lookback)
        levels = []
        for index in range(2, len(recent) - 2):
            high = recent["high"].iloc[index]
            low = recent["low"].iloc[index]
            if high > recent["high"].iloc[index - 1:index + 3].drop(recent.index[index]).max():
                levels.append(("resistance", float(high)))
            if low < recent["low"].iloc[index - 1:index + 3].drop(recent.index[index]).min():
                levels.append(("support", float(low)))
        return levels
