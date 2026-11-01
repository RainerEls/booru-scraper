import asyncio
import logging

from app import config, constants, http
from app.sources.base import BooruSource, api_get, notes_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://danbooru.donmai.us"
RATE_LIMIT = float(config.DANBOORU_RATE_LIMIT)
PAGE_SIZE = 200
MAX_NUMBERED_PAGES = 1000

RATING_TERMS = {"safe": "rating:g", "sketchy": "rating:s,q", "unsafe": "rating:e"}

TAG_FIELDS = {
    "tag_string_general": 0,
    "tag_string_artist": 1,
    "tag_string_copyright": 3,
    "tag_string_character": 4,
    "tag_string_meta": 5,
}


async def fetch_page(terms, page):
    try:
        r = await api_get(
            http.danbooru_session,
            f"{API_URL}/posts.json",
            {"tags": " ".join(terms), "limit": PAGE_SIZE, "page": page},
        )
    except RuntimeError as e:
        if "HTTP 422" in str(e):
            raise RuntimeError(
                "Danbooru refused the search (HTTP 422). Without a paid account it allows 2 tags, plus a rating."
            ) from e
        raise
    logger.debug(f"Search Post Raw: {r.text}")
    return r.json()


async def by_id(terms):
    page = 1
    while True:
        posts = await fetch_page(terms, page)
        for post in posts:
            yield post
        if len(posts) < PAGE_SIZE:
            return
        page = f"b{posts[-1]['id']}"
        await asyncio.sleep(RATE_LIMIT)


async def by_number(terms):
    for page in range(1, MAX_NUMBERED_PAGES + 1):
        posts = await fetch_page(terms, page)
        for post in posts:
            yield post
        if len(posts) < PAGE_SIZE:
            return
        await asyncio.sleep(RATE_LIMIT)


class DanbooruSource(BooruSource):
    ratings = constants.DANBOORU_RATINGS_CONVERSIONS
    tag_types = constants.DANBOORU_TAG_TYPES

    def __init__(self):
        self.seen_tag_types = {}

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = tags.split()
        if rating:
            terms.append(RATING_TERMS[rating])
        blacklist = set(blacklist_tags.split())
        logger.debug(terms)

        ordered = any(term.startswith("order:") for term in terms)
        async for post in by_number(terms) if ordered else by_id(terms):
            post_tags = post["tag_string"].split()
            # Account-restricted posts come back without a file.
            if not post.get("file_url") or blacklist.intersection(post_tags):
                continue
            for field, tag_type in TAG_FIELDS.items():
                self.seen_tag_types.update(dict.fromkeys(post[field].split(), tag_type))
            yield {
                **post,
                # Ugoira files are zips, which szurubooru can't take; large_file_url is a video.
                "file_url": post["large_file_url"] if post["file_ext"] == "zip" else post["file_url"],
                "tags": post["tag_string"],
                "width": post["image_width"],
                "height": post["image_height"],
            }

    async def grab_tags(self, tags):
        return [{"name": tag, "type": self.seen_tag_types.get(tag, 0)} for tag in tags]

    async def grab_notes(self, post):
        if not post.get("last_noted_at"):
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(
            http.danbooru_session, f"{API_URL}/notes.json", {"search[post_id]": post["id"]}
        )
        return notes_to_markdown(r.json())
