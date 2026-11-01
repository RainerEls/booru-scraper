import asyncio
import html
import logging

from app import config, constants, http
from app.sources.base import BooruSource, api_get, html_note_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://yande.re"
RATE_LIMIT = float(config.YANDERE_RATE_LIMIT)
PAGE_SIZE = 100

FRONTEND_TO_YANDERE_RATINGS = {
    "safe": "s",
    "sketchy": "q",
    "unsafe": "e",
}

TAG_TYPE_IDS = {
    "general": 0,
    "artist": 1,
    "copyright": 3,
    "character": 4,
    "circle": 5,
    "faults": 6,
}


class YandereSource(BooruSource):
    ratings = constants.YANDERE_RATINGS_CONVERSIONS
    tag_types = constants.YANDERE_TAG_TYPES

    def __init__(self):
        self.seen_tag_types = {}

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.append(f"rating:{FRONTEND_TO_YANDERE_RATINGS.get(rating)}")
        all_tags = " ".join(terms)
        logger.debug(all_tags)

        page = 1
        while True:
            payloadPost = {
                "tags": all_tags,
                "limit": PAGE_SIZE,
                "page": page,
                "api_version": 2,
                "include_tags": 1,
            }
            r = await api_get(http.yandere_session, f"{API_URL}/post.json", payloadPost)
            logger.debug(f"Search Post Raw: {r.text}")
            body = r.json()
            self.seen_tag_types.update(body["tags"])

            for post in body["posts"]:
                yield {
                    **post,
                    "tags": " ".join(html.unescape(t) for t in post["tags"].split()),
                }

            if len(body["posts"]) < PAGE_SIZE:
                return

            page += 1
            await asyncio.sleep(RATE_LIMIT)

    async def grab_tags(self, tags):
        return [
            {"name": tag, "type": TAG_TYPE_IDS.get(self.seen_tag_types.get(tag), 0)}
            for tag in tags
        ]

    async def grab_notes(self, post):
        if not post.get("last_noted_at"):
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(http.yandere_session, f"{API_URL}/note.json", {"post_id": post["id"]})
        return [
            {
                "x": note["x"],
                "y": note["y"],
                "width": note["width"],
                "height": note["height"],
                "body": html_note_to_markdown(note["body"]),
            }
            for note in r.json()
            if note["is_active"] and note["width"] and note["height"]
        ]
