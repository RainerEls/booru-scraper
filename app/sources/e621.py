import asyncio
import logging

from app import config, http
from app.http import api_get
from app.sources.base import PostTaggedSource, danbooru_style_pages, notes_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://e621.net"
PAGE_SIZE = 320
MAX_NUMBERED_PAGES = 750


def configure():
    global RATE_LIMIT, session
    RATE_LIMIT = config.rate_limit("E621")
    session = http.client(
        headers={"User-Agent": f"booru-scraper (by {config.E621_USER_ID or 'anonymous'} on e621)"},
        auth=(config.E621_USER_ID, config.E621_API_KEY) if config.E621_USER_ID else None,
    )


configure()


async def fetch_page(terms, page):
    r = await api_get(session, f"{API_URL}/posts.json", {"tags": " ".join(terms), "limit": PAGE_SIZE, "page": page})
    logger.debug(f"Search Post Raw: {r.text}")
    return r.json()["posts"]


class E621Source(PostTaggedSource):
    ratings = {"s": "safe", "q": "sketchy", "e": "unsafe"}
    rating_terms = {"safe": "rating:s", "sketchy": "rating:q", "unsafe": "rating:e"}
    tag_categories = {
        "general": "general",
        "artist": "artist",
        "contributor": "artist",
        "copyright": "copyright",
        "character": "character",
        "species": "species",
        "invalid": "general",
        "meta": "metadata",
        "lore": "general",
    }
    search_hint = "Sort with order:score."
    post_page = "https://e621.net/posts/{id}"

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = self.search_terms(tags, blacklist_tags, rating)
        logger.debug(terms)

        async for post in danbooru_style_pages(fetch_page, terms, PAGE_SIZE, MAX_NUMBERED_PAGES, RATE_LIMIT):
            # Posts hidden from the current account come back without a file.
            if not post["file"]["url"]:
                continue
            for group, category in self.tag_categories.items():
                self.seen_categories.update(dict.fromkeys(post["tags"][group], category))
            yield {
                **post,
                "md5": post["file"]["md5"],
                "parent_id": post["relationships"]["parent_id"],
                "file_url": post["file"]["url"],
                "width": post["file"]["width"],
                "height": post["file"]["height"],
                "tags": " ".join(tag for group in post["tags"].values() for tag in group),
                "source": "\n".join(post["sources"]),
            }

    async def grab_notes(self, post):
        if not post["has_notes"]:
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(session, f"{API_URL}/notes.json", {"search[post_id]": post["id"]})
        return notes_to_markdown(r.json())
