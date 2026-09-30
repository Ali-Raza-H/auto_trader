import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    # OANDA SETTINGS
    OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
    OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
    OANDA_PRACTICE = os.getenv("OANDA_PRACTICE", "true").lower() == "true"
    OANDA_URL = (
        "https://api-fxpractice.oanda.com"
        if OANDA_PRACTICE
        else "https://api-fxtrade.oanda.com"
    )

    # TRADING SETTINGS
    SYMBOL = os.getenv("OANDA_SYMBOL", "XAU_USD")
    TIMEFRAME = os.getenv("TIMEFRAME", "M15")
    CANDLES_COUNT = int(os.getenv("CANDLES_COUNT", "500"))
    UNITS_PER_LOT = int(os.getenv("UNITS_PER_LOT", "100"))
    PRICE_DECIMALS = int(os.getenv("PRICE_DECIMALS", "3"))
    POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
    MAGIC_COMMENT = os.getenv("MAGIC_COMMENT", "XAUUSD M15 Bot")

    # INDICATORS
    EMA_FAST = 9
    EMA_MEDIUM = 21
    EMA_SLOW = 50
    RSI_PERIOD = 14
    ATR_PERIOD = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    CHANDELIER_ATR_PERIOD = 22
    CHANDELIER_ATR_MULTIPLIER = 3.0

    # ATR-BASED TRADE MANAGEMENT
    ATR_SL_MULTI = 1.5
    ATR_TP_MULTI = 2.5
    ATR_BE_MULTI = 0.8
    ATR_TRAIL_MULTI = 1.0
    BREAKEVEN_OFFSET = 0.02

    # RISK MANAGEMENT
    RISK_PERCENT = float(os.getenv("RISK_PERCENT", "0.25"))
    MAX_DAILY_TRADES = 15
    MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "1.0"))
    DRAWDOWN_BASELINE_BALANCE = float(os.getenv("DRAWDOWN_BASELINE_BALANCE", "100000"))
    MAX_ACCOUNT_DRAWDOWN = float(os.getenv("MAX_ACCOUNT_DRAWDOWN", "5.0"))
    MAX_LOT_SIZE = 5.0
    MIN_LOT_SIZE = 0.01
    MAX_SPREAD = 0.5  # XAU_USD price units

    # SESSION TIMES (UTC)
    LONDON_OPEN = 8
    LONDON_CLOSE = 17
    NY_OPEN = 13
    NY_CLOSE = 21

    # TELEGRAM
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    TIMEFRAME_MAP = {
        "M1": "M1",
        "M5": "M5",
        "M15": "M15",
        "M30": "M30",
        "H1": "H1",
        "H4": "H4",
        "D1": "D",
    }
