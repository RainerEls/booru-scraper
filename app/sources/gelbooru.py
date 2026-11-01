import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET

from app import config, constants, http
from app.sources.base import BooruSource, api_get, html_note_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://gelbooru.com/index.php"
RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)
PAGE_SIZE = 100
# Gelbooru refuses offsets past 20,000.
MAX_OFFSET_PAGES = 201
SCORE_SORT = re.compile(r"sort:score(:desc|:asc)?")


async def fetch_page(terms, page=0):
    payloadPost = {
        "tags": " ".join(terms),
        "limit": PAGE_SIZE,
        "page": "dapi",
        "s": "post",
        "q": "index",
        "pid": page,
    }
    r = await api_get(http.gelbooru_session, API_URL, payloadPost)
    logger.debug(f"Search Post Status Code: {r.status_code}")
    logger.debug(f"Search Post Raw: {r.text}")
    return [
        {**post, "tags": " ".join(html.unescape(t) for t in post["tags"].split(" "))}
        for post in r.json().get("post", [])
    ]


async def by_id(terms):
    cursor = []
    while True:
        posts = await fetch_page(terms + cursor)
        for post in posts:
            yield post
        if len(posts) < PAGE_SIZE:
            return
        cursor = [f"id:<{posts[-1]['id']}"]
        await asyncio.sleep(RATE_LIMIT)


async def by_offset(terms):
    for page in range(MAX_OFFSET_PAGES):
        posts = await fetch_page(terms, page)
        for post in posts:
            yield post
        if len(posts) < PAGE_SIZE:
            return
        await asyncio.sleep(RATE_LIMIT)


async def by_score(terms, sort_term):
    """Offset page to the cap, finish the score it stopped on by id, then restart beyond it.

    Tied posts have no stable order, and a single score can outnumber the cap.
    """
    beyond = ">" if sort_term.endswith(":asc") else "<"
    unsorted_terms = [term for term in terms if term != sort_term]
    bound = []
    while True:
        seen = set()
        last = None
        async for post in by_offset(terms + bound):
            seen.add(post["id"])
            last = post
            yield post
        if len(seen) < MAX_OFFSET_PAGES * PAGE_SIZE:
            return
        async for post in by_id(unsorted_terms + [f"score:{last['score']}"]):
            if post["id"] not in seen:
                yield post
        bound = [f"score:{beyond}{last['score']}"]


class GelbooruSource(BooruSource):
    ratings = constants.GELBOORU_RATINGS_CONVERSIONS
    tag_types = constants.GELBOORU_TAG_TYPES

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.extend(constants.FRONTEND_TO_GELBOORU_RATING_TERMS[rating])
        logger.debug(terms)

        sort_term = next((term for term in terms if term.startswith("sort:")), None)
        if sort_term is None:
            posts = by_id(terms)
        elif SCORE_SORT.fullmatch(sort_term):
            posts = by_score(terms, sort_term)
        else:
            posts = by_offset(terms)

        async for post in posts:
            yield post

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
