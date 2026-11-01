import asyncio
import html
import itertools
import logging
import math
import xml.etree.ElementTree as ET

import httpx

from app import config, constants, http
from app.sources.base import BooruSource, api_get, notes_to_markdown, run_log
from app.sources.gelbooru import GelbooruSource

logger = logging.getLogger(__name__)

API_URL = "https://api.rule34.xxx/index.php"
RATE_LIMIT = float(config.RULE34_RATE_LIMIT)
PAGE_SIZE = 1000

RATING_TERMS = {"safe": "rating:safe", "sketchy": "rating:questionable", "unsafe": "rating:explicit"}


async def fetch_page(terms, page=0):
    payloadPost = {
        "tags": " ".join(terms),
        "limit": PAGE_SIZE,
        "page": "dapi",
        "s": "post",
        "q": "index",
        "pid": page,
    }
    r = await api_get(http.rule34_session, API_URL, payloadPost)
    logger.debug(f"Search Post Raw: {r.text}")
    # No matches is an empty body, and a refused request is a bare JSON string.
    body = r.json() if r.text.strip() else []
    if isinstance(body, str):
        raise RuntimeError(f"Rule34 refused the search: {body}")
    return body


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
    for page in itertools.count():
        posts = await fetch_page(terms, page)
        for post in posts:
            yield post
        if len(posts) < PAGE_SIZE:
            return
        await asyncio.sleep(RATE_LIMIT)


class Rule34Source(BooruSource):
    ratings = constants.RULE34_RATINGS_CONVERSIONS
    tag_types = constants.RULE34_TAG_TYPES
    slow_tag_lookup = True

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.append(RATING_TERMS[rating])
        logger.debug(terms)

        sorted_search = any(term.startswith("sort:") for term in terms)
        async for post in by_offset(terms) if sorted_search else by_id(terms):
            post_rating = post["rating"].lower()
            # Search filters miss ratings stored with different capitalisation.
            if rating and self.ratings.get(post_rating) != rating:
                continue
            yield {
                **post,
                "rating": post_rating,
                "md5": post["hash"],
                "tags": " ".join(html.unescape(t) for t in post["tags"].split()),
            }

    async def grab_tags(self, tags):
        found = await self.tags_known_to_gelbooru(tags)
        known = {t["name"] for t in found}
        remaining = [tag for tag in tags if tag not in known]
        minutes = math.ceil(len(remaining) * RATE_LIMIT / 60)
        run_log.get()(
            "info",
            f"Gelbooru knew {len(found)}/{len(tags)} tag(s), looking up the rest one at a time, about {minutes} min",
        )
        looked_up = 0
        try:
            for looked_up, tag in enumerate(remaining):
                if looked_up and looked_up % 100 == 0:
                    run_log.get()("info", f"Looked up {looked_up}/{len(remaining)} tags")
                found.append({"name": tag, "type": await self.tag_type(tag)})
                await asyncio.sleep(RATE_LIMIT)
        except (RuntimeError, httpx.TransportError) as e:
            run_log.get()(
                "warning",
                f"Tag lookup stopped at {looked_up}/{len(remaining)}, a later run will finish it: {e!r}",
            )
        return found

    async def tags_known_to_gelbooru(self, tags):
        try:
            known = await GelbooruSource().grab_tags(list(tags))
        except (RuntimeError, httpx.TransportError, ValueError) as e:
            run_log.get()("warning", f"Could not ask Gelbooru about tags: {e!r}")
            return []
        return [{"name": t["name"], "type": t["type"]} for t in known if t["name"] in tags]

    async def tag_type(self, tag):
        # Tags are stored escaped, with apostrophes in their numeric form.
        name = html.escape(tag).replace("&#x27;", "&#039;")
        r = await api_get(http.rule34_session, API_URL, {"page": "dapi", "s": "tag", "q": "index", "name": name})
        match = ET.fromstring(r.text).find("tag")
        return int(match.get("type")) if match is not None else 0

    async def grab_notes(self, post):
        if not post.get("has_notes"):
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(http.rule34_session, API_URL, {"page": "dapi", "s": "note", "q": "index", "post_id": post["id"]})
        return notes_to_markdown(
            {
                "x": int(note.get("x")),
                "y": int(note.get("y")),
                "width": int(note.get("width")),
                "height": int(note.get("height")),
                "body": note.get("body"),
                "is_active": note.get("is_active") == "true",
            }
            for note in ET.fromstring(r.text).findall("note")
        )
