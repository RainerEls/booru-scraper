import base64

import httpx

from app import config

GELBOORU_API_KEY = config.GELBOORU_API_KEY
GELBOORU_USER_ID = config.GELBOORU_USER_ID
GELBOORU_RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)

SZURUBOORU_USER_ID = config.SZURUBOORU_USER_ID
SZURUBOORU_API_TOKEN = config.SZURUBOORU_API_TOKEN
SZURUBOORU_RATE_LIMIT = float(config.SZURUBOORU_RATE_LIMIT)

szurubooru_session = None
gelbooru_session = None

szurubooru_token_auth_str = f"{SZURUBOORU_USER_ID}:{SZURUBOORU_API_TOKEN}".encode()
szurubooru_token_auth = base64.b64encode(szurubooru_token_auth_str)


async def create_clients():
    global szurubooru_session, gelbooru_session, yandere_session

    szurubooru_session = httpx.AsyncClient(
        headers={
            "Authorization": "Token " + szurubooru_token_auth.decode(encoding="utf-8"),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30.0
    )

    gelbooru_session = httpx.AsyncClient(
        params={"api_key": GELBOORU_API_KEY, "user_id": GELBOORU_USER_ID, "json": 1}
    )

    yandere_session = httpx.AsyncClient()

    


async def close_clients():
    await szurubooru_session.aclose()
    await gelbooru_session.aclose()
    await yandere_session.aclose()
