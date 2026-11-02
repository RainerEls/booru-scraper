import logging
import os
import sys
import time
from dataclasses import dataclass

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()
time.tzset()


def _setting(name):
    value = os.getenv(name, "")
    return "" if value == name else value


@dataclass(frozen=True)
class Setting:
    name: str
    group: str
    label: str
    secret: bool = False
    default: str = ""
    parse: type = str
    hint: str = ""
    choices: tuple = ()


NO_CHOICES = "none"
NOTIFY_EVENTS = (
    ("scrape_finished", "A scrape you started finishes"),
    ("schedule_finished", "A scheduled scrape uploads new posts"),
    ("scrape_failed", "A scrape has problems"),
    ("scrape_interrupted", "A restart interrupts a scrape"),
    ("integrity_problems", "An integrity check finds problems"),
)


def chosen_names(value):
    return value.replace(",", " ").split()


def _rate_limit(group, service):
    return Setting(f"{service}_RATE_LIMIT", group, "Seconds between requests", default="1", parse=float)


SETTINGS = (
    Setting("SZURUBOORU_BASE_URL", "Szurubooru", "API URL", hint="Ends in /api, such as https://booru.example.com/api."),
    Setting("SZURUBOORU_USER_ID", "Szurubooru", "User"),
    Setting("SZURUBOORU_API_TOKEN", "Szurubooru", "API token", secret=True),
    _rate_limit("Szurubooru", "SZURUBOORU"),
    Setting("CONCURRENT_UPLOADS", "Szurubooru", "Uploads at once", default="5", parse=int),
    Setting("BLACKLIST_TAGS", "Searching", "Always blacklisted tags", hint="Applies to every scrape."),
    Setting("GELBOORU_USER_ID", "Gelbooru", "User ID"),
    Setting("GELBOORU_API_KEY", "Gelbooru", "API key", secret=True, hint="Required to use Gelbooru."),
    _rate_limit("Gelbooru", "GELBOORU"),
    Setting("DANBOORU_USER_ID", "Danbooru", "Username"),
    Setting("DANBOORU_API_KEY", "Danbooru", "API key", secret=True, hint="Optional."),
    _rate_limit("Danbooru", "DANBOORU"),
    Setting("E621_USER_ID", "e621", "Username"),
    Setting("E621_API_KEY", "e621", "API key", secret=True, hint="Optional."),
    _rate_limit("e621", "E621"),
    Setting("RULE34_USER_ID", "Rule34", "User ID", hint="The number from the account page, not the username."),
    Setting("RULE34_API_KEY", "Rule34", "API key", secret=True, hint="Required to use Rule34."),
    _rate_limit("Rule34", "RULE34"),
    Setting("YANDERE_USER_ID", "Yandere", "Username"),
    Setting("YANDERE_PASSWORD_HASH", "Yandere", "Password hash", secret=True, hint="Optional."),
    _rate_limit("Yandere", "YANDERE"),
    Setting(
        "APPRISE_URLS",
        "Notifications",
        "Apprise URLs",
        secret=True,
        hint="One or more, separated by commas. Leave empty for no notifications.",
    ),
    Setting("NOTIFY_ON", "Notifications", "Events to send", choices=NOTIFY_EVENTS),
)
SETTINGS_BY_NAME = {setting.name: setting for setting in SETTINGS}
_SZURUBOORU_REQUIRED = ("SZURUBOORU_BASE_URL", "SZURUBOORU_USER_ID", "SZURUBOORU_API_TOKEN")


def apply(saved):
    for setting in SETTINGS:
        value = _setting(setting.name) or saved.get(setting.name) or setting.default
        globals()[setting.name] = setting.parse(value)


apply({})

LOGLEVEL = os.getenv("LOGLEVEL", "INFO")

DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
ALLOW_SETTINGS_WITHOUT_LOGIN = os.getenv("ALLOW_SETTINGS_WITHOUT_LOGIN", "false").lower() == "true"
SETTINGS_KEY = os.getenv("SETTINGS_KEY", "")

_REQUIRED = {
    "DB_USER": DB_USER,
    "DB_PASS": DB_PASS,
    "DB_HOST": DB_HOST,
    "DB_PORT": DB_PORT,
    "DB_NAME": DB_NAME,
}


def set_by_env(name):
    return bool(_setting(name))


def missing_szurubooru():
    return [name for name in _SZURUBOORU_REQUIRED if not globals()[name]]


def rate_limit(service):
    return globals()[f"{service}_RATE_LIMIT"]


def validate():
    missing = [name for name, value in _REQUIRED.items() if not value]
    if missing:
        logger.critical(
            "Missing required environment variable(s): %s - "
            "copy .env.example to .env and fill in the missing values.",
            ", ".join(missing),
        )
        sys.exit(1)
