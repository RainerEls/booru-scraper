import re
from abc import ABC, abstractmethod

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
    r = await session.get(url, params=params)
    # Not raise_for_status(): its message includes the query string, and so any credentials.
    if r.is_error:
        raise RuntimeError(f"{url} returned HTTP {r.status_code}")
    return r


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
