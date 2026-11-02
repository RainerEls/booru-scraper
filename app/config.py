import logging
import os
import sys
import time

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
time.tzset()

LOGLEVEL = os.getenv("LOGLEVEL", "INFO")

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

SZURUBOORU_BASE_URL = os.getenv("SZURUBOORU_BASE_URL")
SZURUBOORU_USER_ID = os.getenv("SZURUBOORU_USER_ID")
SZURUBOORU_API_TOKEN = os.getenv("SZURUBOORU_API_TOKEN")
CONCURRENT_UPLOADS = int(os.getenv("CONCURRENT_UPLOADS", "5"))
BLACKLIST_TAGS = os.getenv("BLACKLIST_TAGS", "")

GELBOORU_API_KEY = os.getenv("GELBOORU_API_KEY")
GELBOORU_USER_ID = os.getenv("GELBOORU_USER_ID")

DANBOORU_USER_ID = os.getenv("DANBOORU_USER_ID")
DANBOORU_API_KEY = os.getenv("DANBOORU_API_KEY")

E621_USER_ID = os.getenv("E621_USER_ID")
E621_API_KEY = os.getenv("E621_API_KEY")

RULE34_USER_ID = os.getenv("RULE34_USER_ID")
RULE34_API_KEY = os.getenv("RULE34_API_KEY")

YANDERE_USER_ID = os.getenv("YANDERE_USER_ID")
YANDERE_PASSWORD_HASH = os.getenv("YANDERE_PASSWORD_HASH")

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


def rate_limit(service):
    return float(os.getenv(f"{service}_RATE_LIMIT", "1"))


def validate():
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        logger.critical(
            "Missing required environment variable(s): %s - "
            "copy .env.example to .env and fill in the missing values.",
            ", ".join(missing),
        )
        sys.exit(1)
