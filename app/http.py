import asyncio
import logging
from contextvars import ContextVar

import httpx

logger = logging.getLogger(__name__)

RETRY_DELAYS = (5, 10, 20, 40)
run_log = ContextVar("run_log", default=lambda level, message: getattr(logger, level)(message))

_clients = []


class ApiError(httpx.HTTPError):
    def __init__(self, url, status):
        super().__init__(f"{url} returned HTTP {status}")
        self.status = status


def client(**options):
    new_client = httpx.AsyncClient(**options)
    _clients.append(new_client)
    return new_client


async def close_clients():
    for open_client in _clients:
        await open_client.aclose()


async def api_get(session, url, params=None):
    for delay in RETRY_DELAYS:
        try:
            r = await session.get(url, params=params)
        except httpx.TransportError as e:
            problem = repr(e)
        else:
            if r.status_code != 429 and r.status_code < 500:
                break
            problem = f"HTTP {r.status_code}"
            retry_after = r.headers.get("Retry-After", "")
            delay = int(retry_after) if retry_after.isdigit() else delay
        run_log.get()("warning", f"{url} failed with {problem}, retrying in {delay} s")
        await asyncio.sleep(delay)
    else:
        r = await session.get(url, params=params)

    # raise_for_status() would put the query string, and so any credentials, in its message.
    if r.is_error:
        raise ApiError(url, r.status_code)
    return r
