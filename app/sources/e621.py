import asyncio
import logging

from app import config, constants, http
from app.sources.base import (
    PostTaggedSource,
    api_get,
    notes_to_markdown,
    pages_by_id,
    pages_by_number,
)

logger = logging.getLogger(__name__)

API_URL = "https://e621.net"
RATE_LIMIT = float(config.E621_RATE_LIMIT)
PAGE_SIZE = 320
MAX_NUMBERED_PAGES = 750

RATING_TERMS = {"safe": "rating:s", "sketchy": "rating:q", "unsafe": "rating:e"}

TAG_GROUPS = {
    "general": 0,
    "artist": 1,
    "contributor": 2,
    "copyright": 3,
    "character": 4,
    "species": 5,
    "invalid": 6,
    "meta": 7,
    "lore": 8,
}


class E621Source(PostTaggedSource):
    ratings = constants.E621_RATINGS_CONVERSIONS
    tag_types = constants.E621_TAG_TYPES

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.append(RATING_TERMS[rating])
        logger.debug(terms)

        async def fetch(page):
            r = await api_get(
                http.e621_session,
                f"{API_URL}/posts.json",
                {"tags": " ".join(terms), "limit": PAGE_SIZE, "page": page},
            )
            logger.debug(f"Search Post Raw: {r.text}")
            return r.json()["posts"]

        if any(term.startswith("order:") for term in terms):
            posts = pages_by_number(fetch, PAGE_SIZE, MAX_NUMBERED_PAGES, RATE_LIMIT)
        else:
            posts = pages_by_id(fetch, PAGE_SIZE, RATE_LIMIT)

        async for post in posts:
            # Posts hidden from the current account come back without a file.
            if not post["file"]["url"]:
                continue
            for group, tag_type in TAG_GROUPS.items():
                self.seen_tag_types.update(dict.fromkeys(post["tags"][group], tag_type))
            yield {
                **post,
                "md5": post["file"]["md5"],
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
        r = await api_get(http.e621_session, f"{API_URL}/notes.json", {"search[post_id]": post["id"]})
        return notes_to_markdown(r.json())
