import asyncio
import html
import itertools
import logging
import math
import xml.etree.ElementTree as ET
from functools import partial

import httpx

from app import config, http
from app.http import api_get, run_log
from app.sources.base import BooruSource, dapi_pages_by_id, dapi_params, pages_by_number, xml_notes
from app.sources.gelbooru import GelbooruSource

logger = logging.getLogger(__name__)

API_URL = "https://api.rule34.xxx/index.php"
RATE_LIMIT = config.rate_limit("RULE34")
PAGE_SIZE = 1000

session = http.client(params={"api_key": config.RULE34_API_KEY, "user_id": config.RULE34_USER_ID, "json": 1})


async def fetch_page(terms, page=0):
    params = dapi_params("post", tags=" ".join(terms), limit=PAGE_SIZE, pid=page)
    r = await api_get(session, API_URL, params)
    logger.debug(f"Search Post Raw: {r.text}")
    # No matches is an empty body, and a refused request is a bare JSON string.
    body = r.json() if r.text.strip() else []
    if isinstance(body, str):
        raise RuntimeError(f"Rule34 refused the search: {body}")
    return body


class Rule34Source(BooruSource):
    ratings = {"safe": "safe", "questionable": "sketchy", "explicit": "unsafe"}
    rating_terms = {"safe": "rating:safe", "sketchy": "rating:questionable", "unsafe": "rating:explicit"}
    tag_categories = GelbooruSource.tag_categories
    slow_tag_lookup = True
    credentials = (config.RULE34_API_KEY, config.RULE34_USER_ID)
    search_hint = "Sort with sort:score."
    post_page = "https://rule34.xxx/index.php?page=post&s=view&id={id}"

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = self.search_terms(tags, blacklist_tags, rating)
        logger.debug(terms)

        if any(term.startswith("sort:") for term in terms):
            posts = pages_by_number(partial(fetch_page, terms), PAGE_SIZE, RATE_LIMIT, itertools.count())
        else:
            posts = dapi_pages_by_id(fetch_page, terms, PAGE_SIZE, RATE_LIMIT)

        async for post in posts:
            post_rating = post["rating"].lower()
            # Search filters miss ratings stored with different capitalisation.
            if rating and self.ratings.get(post_rating) != rating:
                continue
            yield {**post, "rating": post_rating, "md5": post["hash"], "tags": html.unescape(post["tags"])}

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
                found.append({"name": tag, "category": await self.category(tag)})
                await asyncio.sleep(RATE_LIMIT)
        except httpx.HTTPError as e:
            run_log.get()(
                "warning",
                f"Tag lookup stopped at {looked_up}/{len(remaining)}, a later run will finish it: {e!r}",
            )
        return found

    async def tags_known_to_gelbooru(self, tags):
        try:
            known = await GelbooruSource().grab_tags(tags)
        except (httpx.HTTPError, ValueError) as e:
            run_log.get()("warning", f"Could not ask Gelbooru about tags: {e!r}")
            return []
        return [tag for tag in known if tag["name"] in tags]

    async def category(self, tag):
        # Tags are stored escaped, with apostrophes in their numeric form.
        name = html.escape(tag).replace("&#x27;", "&#039;")
        r = await api_get(session, API_URL, dapi_params("tag", name=name))
        match = ET.fromstring(r.text).find("tag")
        return self.tag_categories.get(int(match.get("type")), "general") if match is not None else "general"

    async def grab_notes(self, post):
        if not post.get("has_notes"):
            return None
        await asyncio.sleep(RATE_LIMIT)
        r = await api_get(session, API_URL, dapi_params("note", post_id=post["id"]))
        return xml_notes(r.text)
