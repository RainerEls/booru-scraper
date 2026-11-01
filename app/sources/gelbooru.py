import asyncio
import html
import logging
import xml.etree.ElementTree as ET

from app import config, constants, http
from app.sources.base import BooruSource, api_get, html_note_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://gelbooru.com/index.php"
RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)
PAGE_SIZE = 100
# Gelbooru refuses offsets past 20,000, so only sort: searches, which can't
# continue from an id, are limited by this.
MAX_SORTED_PAGES = 201

class GelbooruSource(BooruSource):
    ratings = constants.GELBOORU_RATINGS_CONVERSIONS
    tag_types = constants.GELBOORU_TAG_TYPES

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.append(f"rating:{constants.FRONTEND_TO_GELBOORU_RATINGS.get(rating)}")
        custom_order = any(term.startswith("sort:") for term in terms)
        logger.debug(terms)

        page = 0
        cursor = []
        while page < MAX_SORTED_PAGES:
            payloadPost = {
                "tags": " ".join(terms + cursor),
                "limit": PAGE_SIZE,
                "page": "dapi",
                "s": "post",
                "q": "index",
                "pid": page,
            }
            r = await api_get(http.gelbooru_session, API_URL, payloadPost)
            logger.debug(f"Search Post Status Code: {r.status_code}")
            logger.debug(f"Search Post Raw: {r.text}")
            posts = r.json().get("post", [])

            for post in posts:
                yield {
                    **post,
                    "tags": " ".join(html.unescape(t) for t in post["tags"].split(" ")),
                }

            if len(posts) < PAGE_SIZE:
                return

            if custom_order:
                page += 1
            else:
                cursor = [f"id:<{posts[-1]['id']}"]

            await asyncio.sleep(RATE_LIMIT)

    async def grab_tags(self, tags):

        total_tags = len(tags)
        tags_list = list(tags)
        tags_range = range(0, total_tags, 100)
        tag_data = []

        for tag_range in tags_range:
            tag_set = " ".join(tags_list[tag_range : tag_range + 100])
            payloadTags = {
                "names": tag_set,
                "page": "dapi",
                "s": "tag",
                "q": "index",
            }
            r = await api_get(http.gelbooru_session, API_URL, payloadTags)
            tag_results = r.json().get("tag", [])
            tag_data.extend(tag_results)
            await asyncio.sleep(RATE_LIMIT)

        unescaped_tags = [
            {**tag, "name": html.unescape(tag["name"])} for tag in tag_data
        ]
        return unescaped_tags

    async def grab_notes(self, post):
        if post.get("has_notes") != "true":
            return None
        await asyncio.sleep(RATE_LIMIT)
        payloadNote = {
            "page": "dapi",
            "s": "note",
            "q": "index",
            "post_id": post["id"],
        }
        r = await api_get(http.gelbooru_session, API_URL, payloadNote)
        root = ET.fromstring(r.text)
        return [
            {
                "x": int(note.get("x")),
                "y": int(note.get("y")),
                "width": int(note.get("width")),
                "height": int(note.get("height")),
                "body": html_note_to_markdown(note.get("body")),
            }
            for note in root.findall("note")
            if int(note.get("width")) and int(note.get("height"))
        ]
