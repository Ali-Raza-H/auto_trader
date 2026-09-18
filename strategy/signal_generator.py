from datetime import datetime, timedelta, timezone

from config import Config
from indicators.technical import TechnicalIndicators
from logs.logger import logger


class SignalGenerator:
    def __init__(self):
        self.last_signal = None
        self.last_signal_time = None

    def generate(self, df):
        enriched = TechnicalIndicators.calculate_all(df)
        if len(enriched) < 50:
            logger.warning("Not enough candle data for signal generation")
            return None

        current = enriched.iloc[-1]
        previous = enriched.iloc[-2]
        session = self.get_session()
        if not session:
            return None

        buy_conditions = self.check_buy_conditions(current, previous)
        sell_conditions = self.check_sell_conditions(current, previous)
        buy_score = sum(buy_conditions.values())
        sell_score = sum(sell_conditions.values())

        signal = None
        if buy_score >= Config.MIN_CONDITIONS:
            signal = self.build_signal("BUY", current, buy_conditions, buy_score, session, enriched)
        elif sell_score >= Config.MIN_CONDITIONS:
            signal = self.build_signal("SELL", current, sell_conditions, sell_score, session, enriched)

        if signal and self.is_duplicate(signal):
            return None
        if signal:
            self.last_signal = signal
            self.last_signal_time = datetime.now(timezone.utc)
            logger.signal(
                f"Signal generated: {signal['type']} | "
                f"Score: {signal['score']}/7 | RR: 1:{signal['rr']}"
            )
        return signal

    @staticmethod
    def check_buy_conditions(curr, prev):
        return {
            "EMA9 > EMA21": curr["ema9"] > curr["ema21"],
            "EMA21 > EMA50": curr["ema21"] > curr["ema50"],
            "RSI Bullish Zone": 45 < curr["rsi"] < 68,
            "MACD Above Signal": curr["macd"] > curr["macd_signal"],
            "MACD Hist Increasing": curr["macd_hist"] > prev["macd_hist"],
            "Price Above EMA21": curr["close"] > curr["ema21"],
            "Bullish Momentum": curr["momentum"] > 0,
        }

    @staticmethod
    def check_sell_conditions(curr, prev):
        return {
            "EMA9 < EMA21": curr["ema9"] < curr["ema21"],
            "EMA21 < EMA50": curr["ema21"] < curr["ema50"],
            "RSI Bearish Zone": 32 < curr["rsi"] < 55,
            "MACD Below Signal": curr["macd"] < curr["macd_signal"],
            "MACD Hist Decreasing": curr["macd_hist"] < prev["macd_hist"],
            "Price Below EMA21": curr["close"] < curr["ema21"],
            "Bearish Momentum": curr["momentum"] < 0,
        }

    def build_signal(self, signal_type, current, conditions, score, session, df):
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
            "score": score,
            "conditions": dict(conditions),
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

    def is_duplicate(self, signal):
        if not self.last_signal or not self.last_signal_time:
            return False
        elapsed = datetime.now(timezone.utc) - self.last_signal_time
        return self.last_signal["type"] == signal["type"] and elapsed < timedelta(minutes=15)
