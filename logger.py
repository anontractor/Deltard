# logger.py
import logging
from pathlib import Path

LOG_FILE = Path(__file__).parent / "automation.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()  # optional: also log to console
    ]
)

logger = logging.getLogger("timesheet_automation")
