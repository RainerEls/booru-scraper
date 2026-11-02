import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app import config, db

logger = logging.getLogger(__name__)

URL_NAME = "SZURUBOORU_BASE_URL"
TOKEN_NAME = "SZURUBOORU_API_TOKEN"

unreadable = set()


class Invalid(Exception):
    pass


def cipher():
    if not config.SETTINGS_KEY:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(config.SETTINGS_KEY.encode()).digest()))


def decrypted(name, value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except (AttributeError, InvalidToken):
        unreadable.add(name)
        return ""


async def load():
    unreadable.clear()
    saved = await db.get_settings()
    values = {}
    for name, value in saved.items():
        setting = config.SETTINGS_BY_NAME.get(name)
        if setting:
            values[name] = decrypted(name, value) if setting.secret else value
    config.apply(values)
    for name in sorted(unreadable):
        logger.warning(f"The saved value of {name} can't be decrypted with SETTINGS_KEY, so enter it again")


def check(setting, value):
    if not value:
        return
    try:
        number = setting.parse(value)
    except ValueError:
        raise Invalid(f"{setting.label} ({setting.name}) has to be a number.") from None
    if setting.parse is int and number < 1 or setting.parse is float and not 0 <= number < float("inf"):
        raise Invalid(f"{setting.label} ({setting.name}) is out of range.")
    if setting.name == URL_NAME and not value.startswith(("http://", "https://")):
        raise Invalid(f"{setting.label} has to start with http:// or https://.")


def changes_from(values, cleared):
    changes = {}
    for name in [*values, *cleared]:
        setting = config.SETTINGS_BY_NAME.get(name)
        if setting is None:
            raise Invalid(f"{name} isn't a setting.")
        if config.set_by_env(name):
            raise Invalid(f"{name} is set in .env, so it has to be changed there.")
    for name in cleared:
        changes[name] = None
    for name, value in values.items():
        setting = config.SETTINGS_BY_NAME[name]
        value = value.strip()
        if value and setting.secret and not config.SETTINGS_KEY:
            raise Invalid("Set SETTINGS_KEY in .env and restart to save keys and tokens here.")
        check(setting, value)
        changes[name] = (cipher().encrypt(value.encode()).decode() if setting.secret else value) if value else None
    return changes


def require_token_with_new_url(values, cleared):
    url = values.get(URL_NAME, config.SZURUBOORU_BASE_URL).strip()
    if url == config.SZURUBOORU_BASE_URL or not config.SZURUBOORU_API_TOKEN:
        return
    if not values.get(TOKEN_NAME) and TOKEN_NAME not in cleared:
        raise Invalid(
            "Enter the API token again when changing the Szurubooru URL, so it can't be sent somewhere else. "
            "If the token is in .env, change the URL there too."
        )


async def save(values, cleared):
    require_token_with_new_url(values, cleared)
    await db.save_settings(changes_from(values, cleared))
    await load()


def view():
    groups = {}
    for setting in config.SETTINGS:
        value = getattr(config, setting.name)
        groups.setdefault(setting.group, []).append(
            {
                "setting": setting,
                "value": "" if setting.secret else str(value),
                "is_set": bool(value),
                "from_env": config.set_by_env(setting.name),
                "unreadable": setting.name in unreadable,
            }
        )
    return groups
