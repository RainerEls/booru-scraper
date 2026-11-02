import asyncio
import logging

from app import config, http
from app.http import ApiError, api_get
from app.sources.base import PostTaggedSource, danbooru_style_pages, notes_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://danbooru.donmai.us"
RATE_LIMIT = config.rate_limit("DANBOORU")
PAGE_SIZE = 200
MAX_NUMBERED_PAGES = 1000

session = http.client(
    headers={"User-Agent": "booru-scraper"},
    auth=(config.DANBOORU_USER_ID, config.DANBOORU_API_KEY) if config.DANBOORU_USER_ID else None,
)


async def fetch_page(terms, page):
    try:
        r = await api_get(session, f"{API_URL}/posts.json", {"tags": " ".join(terms), "limit": PAGE_SIZE, "page": page})
    except ApiError as e:
        if e.status == 422:
            raise RuntimeError(
                "Danbooru refused the search (HTTP 422). Without a paid account it allows 2 tags, plus a rating."
            ) from e
        raise
    logger.debug(f"Search Post Raw: {r.text}")
    return r.json()


class DanbooruSource(PostTaggedSource):
    ratings = {"g": "safe", "s": "sketchy", "q": "sketchy", "e": "unsafe"}
    rating_terms = {"safe": "rating:g", "sketchy": "rating:s,q", "unsafe": "rating:e"}
    tag_categories = {
        "tag_string_general": "general",
        "tag_string_artist": "artist",
        "tag_string_copyright": "copyright",
        "tag_string_character": "character",
        "tag_string_meta": "metadata",
    }
    search_hint = "Sort with order:score. Without a paid account: 2 tags, plus a rating."
    post_page = "https://danbooru.donmai.us/posts/{id}"

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = self.search_terms(tags, "", rating)
        blacklist = set(blacklist_tags.split())
        logger.debug(terms)

        async for post in danbooru_style_pages(fetch_page, terms, PAGE_SIZE, MAX_NUMBERED_PAGES, RATE_LIMIT):
            post_tags = post["tag_string"].split()
            # Account-restricted posts come back without a file.
            if not post.get("file_url") or blacklist.intersection(post_tags):
                continue
            for field, category in self.tag_categories.items():
                self.seen_categories.update(dict.fromkeys(post[field].split(), category))
            yield {
                **post,
                # Ugoira files are zips, which szurubooru can't take; large_file_url is a video.
                "file_url": post["large_file_url"] if post["file_ext"] == "zip" else post["file_url"],
                "tags": post["tag_string"],
                "width": post["image_width"],
                "height": post["image_height"],
            }

    async def grab_notes(self, post):
        if not post.get("last_noted_at"):
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(session, f"{API_URL}/notes.json", {"search[post_id]": post["id"]})
        return notes_to_markdown(r.json())
