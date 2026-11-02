import asyncio
import logging
import xml.etree.ElementTree as ET
from contextvars import ContextVar

import httpx

logger = logging.getLogger(__name__)

RETRY_DELAYS = (5, 10, 20, 40)
REJECTED_STATUSES = (401, 403)
run_log = ContextVar("run_log", default=lambda level, message: getattr(logger, level)(message))

_clients = []


class ApiError(httpx.HTTPError):
    def __init__(self, url, status):
        super().__init__(f"{url} returned HTTP {status}")
        self.host = httpx.URL(url).host
        self.status = status


class SetupProblem(Exception):
    pass


class CredentialsRejected(SetupProblem):
    def __init__(self, url, status=None):
        detail = f" (HTTP {status})" if status else ""
        super().__init__(f"{httpx.URL(url).host} refused the request{detail}. Check the credentials for it in .env.")


def describe(e):
    if isinstance(e, ApiError):
        host, status = e.host, e.status
    elif isinstance(e, httpx.HTTPStatusError):
        host, status = e.request.url.host, e.response.status_code
    elif isinstance(e, httpx.TimeoutException):
        return "The request timed out"
    elif isinstance(e, httpx.TransportError):
        return "The connection failed"
    elif isinstance(e, (ValueError, LookupError, ET.ParseError)):
        return "The response wasn't in the expected format"
    else:
        return str(e) or repr(e)
    if status == 429:
        return f"{host} is rate limiting requests"
    if status >= 500:
        return f"{host} is having problems (HTTP {status})"
    return f"{host} returned HTTP {status}"


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

    if r.status_code in REJECTED_STATUSES:
        raise CredentialsRejected(url, r.status_code)
    # raise_for_status() would put the query string, and so any credentials, in its message.
    if r.is_error:
        raise ApiError(url, r.status_code)
    return r
