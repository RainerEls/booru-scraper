import asyncio
import html
import itertools
import logging
import re
from functools import partial

from app import config, http
from app.http import api_get
from app.sources.base import BooruSource, dapi_pages_by_id, dapi_params, pages_by_number, xml_notes

logger = logging.getLogger(__name__)

API_URL = "https://gelbooru.com/index.php"
RATE_LIMIT = config.rate_limit("GELBOORU")
PAGE_SIZE = 100
TAG_BATCH_SIZE = 100
# Gelbooru refuses offsets past 20,000.
MAX_OFFSET_PAGES = 201
SCORE_SORT = re.compile(r"sort:score(:desc|:asc)?")

session = http.client(params={"api_key": config.GELBOORU_API_KEY, "user_id": config.GELBOORU_USER_ID, "json": 1})


async def fetch_page(terms, page=0):
    params = dapi_params("post", tags=" ".join(terms), limit=PAGE_SIZE, pid=page)
    r = await api_get(session, API_URL, params)
    logger.debug(f"Search Post Raw: {r.text}")
    return [{**post, "tags": html.unescape(post["tags"])} for post in r.json().get("post", [])]


def by_id(terms):
    return dapi_pages_by_id(fetch_page, terms, PAGE_SIZE, RATE_LIMIT)


def by_offset(terms):
    return pages_by_number(partial(fetch_page, terms), PAGE_SIZE, RATE_LIMIT, range(MAX_OFFSET_PAGES))


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
    ratings = {
        "general": "safe",
        "safe": "safe",
        "sensitive": "sketchy",
        "questionable": "sketchy",
        "explicit": "unsafe",
    }
    rating_terms = {
        "safe": "rating:general",
        "sketchy": "-rating:general -rating:explicit",
        "unsafe": "rating:explicit",
    }
    tag_categories = {0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "metadata", 6: "general"}
    credentials = (config.GELBOORU_API_KEY, config.GELBOORU_USER_ID)
    search_hint = "Sort with sort:score or sort:updated."
    post_page = "https://gelbooru.com/index.php?page=post&s=view&id={id}"

    def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = self.search_terms(tags, blacklist_tags, rating)
        logger.debug(terms)

        sort_term = next((term for term in terms if term.startswith("sort:")), None)
        if sort_term is None:
            return by_id(terms)
        if SCORE_SORT.fullmatch(sort_term):
            return by_score(terms, sort_term)
        return by_offset(terms)

    async def grab_tags(self, tags):
        for batch in itertools.batched(tags, TAG_BATCH_SIZE):
            r = await api_get(session, API_URL, dapi_params("tag", names=" ".join(batch)))
            for tag in r.json().get("tag", []):
                yield {"name": html.unescape(tag["name"]), "category": self.tag_categories.get(tag["type"], "general")}
            await asyncio.sleep(RATE_LIMIT)

    async def grab_notes(self, post):
        if post.get("has_notes") != "true":
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(session, API_URL, dapi_params("note", post_id=post["id"]))
        return xml_notes(r.text)
