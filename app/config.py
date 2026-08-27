import os
import sys
import logging

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

LOGLEVEL = os.getenv("LOGLEVEL", "INFO")

DB_USER = os.getenv("DB_USER", None)
DB_PASS = os.getenv("DB_PASS", None)
DB_HOST = os.getenv("DB_HOST", None)
DB_PORT = os.getenv("DB_PORT", None)
DB_NAME = os.getenv("DB_NAME", None)

SZURUBOORU_BASE_URL = os.getenv("SZURUBOORU_BASE_URL", None)
SZURUBOORU_USER_ID = os.getenv("SZURUBOORU_USER_ID", None)
SZURUBOORU_API_TOKEN = os.getenv("SZURUBOORU_API_TOKEN", None)
SZURUBOORU_RATE_LIMIT = os.getenv("SZURUBOORU_RATE_LIMIT", "1")
CONCURRENT_UPLOADS = os.getenv("CONCURRENT_UPLOADS", "5")

GELBOORU_API_KEY = os.getenv("GELBOORU_API_KEY", None)
GELBOORU_USER_ID = os.getenv("GELBOORU_USER_ID", None)
GELBOORU_RATE_LIMIT = os.getenv("GELBOORU_RATE_LIMIT", "1")

YANDERE_RATE_LIMIT = os.getenv("YANDERE_RATE_LIMIT", "1")

_REQUIRED = {
    "SZURUBOORU_BASE_URL": SZURUBOORU_BASE_URL,
    "SZURUBOORU_USER_ID": SZURUBOORU_USER_ID,
    "SZURUBOORU_API_TOKEN": SZURUBOORU_API_TOKEN,
    "DB_USER": DB_USER,
    "DB_PASS": DB_PASS,
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
}

def validate():
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        logger.critical(
            f"Missing required environment variable(s): %s - "
            "copy .env.example to .env and fill in the missing values.",
            ", ".join(missing)
        )
        sys.exit(1)
        