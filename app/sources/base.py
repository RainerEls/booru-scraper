import asyncio
import logging
import re
from abc import ABC, abstractmethod
from contextvars import ContextVar

import httpx

logger = logging.getLogger(__name__)

RETRY_DELAYS = (5, 10, 20, 40)
retry_notice = ContextVar("retry_notice", default=logger.warning)

_TAG_PAIRS = [
    (r"<b>|<strong>", r"</b>|</strong>", "**"),
    (r"<i>|<em>", r"</i>|</em>", "*"),
    (r"<s>|<del>", r"</s>|</del>", "~~"),
]

_ESCAPE_CHARS = "\\*_`[]"
_MD_ESCAPE = re.compile("([" + re.escape(_ESCAPE_CHARS) + "])")


def html_note_to_markdown(text):
    text = _MD_ESCAPE.sub(r"\\\1", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</?span[^>]*>", "", text)

    closers = ""
    for open_pat, close_pat, marker in _TAG_PAIRS:
        opens = len(re.findall(open_pat, text, re.IGNORECASE))
        closes = len(re.findall(close_pat, text, re.IGNORECASE))
        text = re.sub(open_pat, marker, text, flags=re.IGNORECASE)
        text = re.sub(close_pat, marker, text, flags=re.IGNORECASE)
        if opens > closes:
            closers += marker * (opens - closes)

    text = re.sub(r"<[^>]+>", "", text)
    return text + closers


async def api_get(session, url, params):
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
        retry_notice.get()(f"{url} failed with {problem}, retrying in {delay} s")
        await asyncio.sleep(delay)
    else:
        r = await session.get(url, params=params)

    # Not raise_for_status(): its message includes the query string, and so any credentials.
    if r.is_error:
        raise RuntimeError(f"{url} returned HTTP {r.status_code}")
    return r


def notes_to_markdown(notes):
    return [
        {
            "x": note["x"],
            "y": note["y"],
            "width": note["width"],
            "height": note["height"],
            "body": html_note_to_markdown(note["body"]),
        }
        for note in notes
        if note["is_active"] and note["width"] and note["height"]
    ]


class BooruSource(ABC):
    ratings = {}
    tag_types = {}

    @abstractmethod
    def search_posts(self, tags="", blacklist_tags="", rating=None):
        """Yield matching posts, newest first, fetching pages as they are consumed."""

    @abstractmethod
    async def grab_tags(self, tags):
        pass

    async def grab_notes(self, post):
        return None
