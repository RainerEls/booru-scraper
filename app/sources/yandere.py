import asyncio
import html
import itertools
import logging
from functools import partial

from app import config, http
from app.http import api_get
from app.sources.base import PostTaggedSource, notes_to_markdown, pages_by_number

logger = logging.getLogger(__name__)

API_URL = "https://yande.re"
RATE_LIMIT = config.rate_limit("YANDERE")
PAGE_SIZE = 100

session = http.client(
    params={"login": config.YANDERE_USER_ID, "password_hash": config.YANDERE_PASSWORD_HASH}
    if config.YANDERE_USER_ID
    else None
)


class YandereSource(PostTaggedSource):
    ratings = {"s": "safe", "q": "sketchy", "e": "unsafe"}
    rating_terms = {"safe": "rating:s", "sketchy": "rating:q", "unsafe": "rating:e"}
    tag_categories = {
        "general": "general",
        "artist": "artist",
        "copyright": "copyright",
        "character": "character",
        "circle": "copyright",
        "faults": "metadata",
    }
    search_hint = "Sort with order:score."
    post_page = "https://yande.re/post/show/{id}"

    async def fetch_page(self, terms, page):
        params = {"tags": " ".join(terms), "limit": PAGE_SIZE, "page": page, "api_version": 2, "include_tags": 1}
        r = await api_get(session, f"{API_URL}/post.json", params)
        logger.debug(f"Search Post Raw: {r.text}")
        body = r.json()
        self.seen_categories.update(
            (tag, self.tag_categories.get(tag_type, "general")) for tag, tag_type in body["tags"].items()
        )
        return [{**post, "tags": html.unescape(post["tags"])} for post in body["posts"]]

    def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = self.search_terms(tags, blacklist_tags, rating)
        logger.debug(terms)
        return pages_by_number(partial(self.fetch_page, terms), PAGE_SIZE, RATE_LIMIT, itertools.count(1))

    async def grab_notes(self, post):
        if not post.get("last_noted_at"):
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(session, f"{API_URL}/note.json", {"post_id": post["id"]})
        return notes_to_markdown(r.json())
