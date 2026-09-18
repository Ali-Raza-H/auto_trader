import logging
import os
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

class Logger:
    def __init__(self):
        # Create logs directory
        os.makedirs("logs", exist_ok=True)
        
        # Log filename with date
        log_file = f"logs/xauusd_{datetime.now().strftime('%Y%m%d')}.log"
        
        # Configure logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(message)s",
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger("XAUUSD_BOT")
    
    def info(self, msg):
        print(f"{Fore.CYAN}[INFO] {msg}{Style.RESET_ALL}")
        self.logger.info(msg)
    
    def success(self, msg):
        print(f"{Fore.GREEN}[SUCCESS] {msg}{Style.RESET_ALL}")
        self.logger.info(f"SUCCESS: {msg}")
    
    def warning(self, msg):
        print(f"{Fore.YELLOW}[WARNING] {msg}{Style.RESET_ALL}")
        self.logger.warning(msg)
    
    def error(self, msg):
        print(f"{Fore.RED}[ERROR] {msg}{Style.RESET_ALL}")
        self.logger.error(msg)
    
    def trade(self, msg):
        print(f"{Fore.MAGENTA}[TRADE] {msg}{Style.RESET_ALL}")
        self.logger.info(f"TRADE: {msg}")
    
    def signal(self, msg):
        print(f"{Fore.YELLOW}[SIGNAL] {msg}{Style.RESET_ALL}")
        self.logger.info(f"SIGNAL: {msg}")

logger = Logger()
