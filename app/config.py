import logging
import os
import sys
import time

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
time.tzset()


def _setting(name):
    value = os.getenv(name, "")
    return "" if value == name else value


LOGLEVEL = os.getenv("LOGLEVEL", "INFO")

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

SZURUBOORU_BASE_URL = _setting("SZURUBOORU_BASE_URL")
SZURUBOORU_USER_ID = _setting("SZURUBOORU_USER_ID")
SZURUBOORU_API_TOKEN = _setting("SZURUBOORU_API_TOKEN")
CONCURRENT_UPLOADS = int(os.getenv("CONCURRENT_UPLOADS", "5"))
BLACKLIST_TAGS = os.getenv("BLACKLIST_TAGS", "")

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

APPRISE_URLS = os.getenv("APPRISE_URLS", "")
NOTIFY_ON = os.getenv("NOTIFY_ON", "")

GELBOORU_API_KEY = _setting("GELBOORU_API_KEY")
GELBOORU_USER_ID = _setting("GELBOORU_USER_ID")

DANBOORU_USER_ID = _setting("DANBOORU_USER_ID")
DANBOORU_API_KEY = _setting("DANBOORU_API_KEY")

E621_USER_ID = _setting("E621_USER_ID")
E621_API_KEY = _setting("E621_API_KEY")

RULE34_USER_ID = _setting("RULE34_USER_ID")
RULE34_API_KEY = _setting("RULE34_API_KEY")

YANDERE_USER_ID = _setting("YANDERE_USER_ID")
YANDERE_PASSWORD_HASH = _setting("YANDERE_PASSWORD_HASH")

_SZURUBOORU_SETTINGS = {
    "SZURUBOORU_BASE_URL": SZURUBOORU_BASE_URL,
    "SZURUBOORU_USER_ID": SZURUBOORU_USER_ID,
    "SZURUBOORU_API_TOKEN": SZURUBOORU_API_TOKEN,
}
_REQUIRED = {
    "DB_USER": DB_USER,
    "DB_PASS": DB_PASS,
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
}


def missing_szurubooru():
    return [name for name, value in _SZURUBOORU_SETTINGS.items() if not value]


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
