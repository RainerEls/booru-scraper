import asyncio
import logging
import math
import re
import time
from itertools import combinations

from app import config, http
from app.http import ApiError, api_get, run_log
from app.sources.base import PostTaggedSource, danbooru_style_pages, notes_to_markdown

logger = logging.getLogger(__name__)

API_URL = "https://danbooru.donmai.us"
PAGE_SIZE = 200
MAX_NUMBERED_PAGES = 1000
POST_FIELDS = (
    "id,md5,parent_id,rating,source,last_noted_at,file_ext,file_url,large_file_url,image_width,image_height,"
    "tag_string,tag_string_general,tag_string_artist,tag_string_copyright,tag_string_character,tag_string_meta"
)
FREE_ACCOUNT_TAGS = 2
UNCOUNTED_TERMS = ("rating:", "id:")
METATAG = re.compile(r"-?[a-z_]+:.", re.IGNORECASE)
PAIR_CANDIDATES = 4
SCAN_LOG_INTERVAL = 5000


def configure():
    global RATE_LIMIT, session
    RATE_LIMIT = config.rate_limit("DANBOORU")
    session = http.client(
        headers={"User-Agent": "booru-scraper"},
        auth=(config.DANBOORU_USER_ID, config.DANBOORU_API_KEY) if config.DANBOORU_USER_ID else None,
    )


configure()


next_request = 0


async def paced_get(path, params):
    global next_request
    now = time.monotonic()
    wait = max(0, next_request - now)
    next_request = now + wait + RATE_LIMIT
    await asyncio.sleep(wait)
    return await api_get(session, f"{API_URL}/{path}", params)


async def fetch_page(terms, page):
    try:
        r = await paced_get("posts.json", {"tags": " ".join(terms), "limit": PAGE_SIZE, "page": page, "only": POST_FIELDS})
    except ApiError as e:
        if e.status == 422:
            raise RuntimeError(
                "Danbooru refused the search (HTTP 422). Plain tags past the account's limit are checked locally, "
                "but order: and other metatags have to fit within it (2 without a paid account)."
            ) from e
        if e.status == 500 and any(term.startswith("order:") for term in terms):
            raise RuntimeError(
                "Danbooru failed on a sorted search (HTTP 500), which usually means it has too many posts to sort. "
                "Try a rarer tag or remove order:."
            ) from e
        raise
    logger.debug(f"Search Post Raw: {r.text}")
    return r.json()


async def tag_limit():
    r = await paced_get("profile.json", {"only": "tag_query_limit"})
    return r.json()["tag_query_limit"]


async def canonical_names(names):
    r = await paced_get(
        "tag_aliases.json",
        {
            "search[antecedent_name_comma]": ",".join(names),
            "search[status]": "active",
            "limit": len(names),
            "only": "antecedent_name,consequent_name",
        },
    )
    aliases = {alias["antecedent_name"]: alias["consequent_name"] for alias in r.json()}
    return [aliases.get(name, name) for name in names]


async def pair_size(pair):
    r = await paced_get("counts/posts.json", {"tags": " ".join(pair)})
    count = r.json()["counts"]["posts"]
    # Danbooru answers null when a search is too big to count.
    return math.inf if count is None else count


async def narrowest_tags(tags, slots):
    """Pick the tags to send, and how many posts they match, or None if that isn't known."""
    if not tags or not slots:
        return [], None
    r = await paced_get("tags.json", {"search[name_comma]": ",".join(tags), "limit": len(tags), "only": "name,post_count"})
    counts = {tag["name"]: tag["post_count"] for tag in r.json()}
    rarest = sorted(tags, key=lambda tag: counts.get(tag, 0))
    if slots != 2 or len(tags) < 2:
        return rarest[:slots], counts.get(rarest[0], 0)
    sizes = {pair: await pair_size(pair) for pair in combinations(rarest[:PAIR_CANDIDATES], 2)}
    best = min(sizes, key=sizes.get)
    return list(best), None if sizes[best] == math.inf else sizes[best]


async def plan_search(terms):
    """Split a search into the terms sent to Danbooru and the tags to check on each post."""
    counted = [term for term in terms if not term.startswith(UNCOUNTED_TERMS)]
    grouped = any(term.lower() in ("or", "and") or term.startswith("(") for term in terms)
    if grouped or len(counted) <= FREE_ACCOUNT_TAGS:
        return terms, set(), set()
    limit = await tag_limit()
    server_side = [term for term in counted if METATAG.match(term) or "*" in term or term.startswith("~")]
    if len(counted) <= limit or len(server_side) > limit:
        return terms, set(), set()

    local = [term for term in counted if term not in server_side]
    names = dict(zip(local, await canonical_names([term.removeprefix("-").lower() for term in local]), strict=True))
    required = list(dict.fromkeys(names[term] for term in local if not term.startswith("-")))
    excluded = {names[term] for term in local if term.startswith("-")}
    sent, matches = await narrowest_tags(required, limit - len(server_side))
    search = server_side + sent + [term for term in terms if term.startswith(UNCOUNTED_TERMS)]

    scan = f"up to {matches} posts" if matches is not None else "an unknown number of posts"
    run_log.get()("info", f"Searching Danbooru for: {' '.join(search)}. Checking the other tags locally on {scan}.")
    cap = MAX_NUMBERED_PAGES * PAGE_SIZE
    if any(term.startswith("order:") for term in server_side) and (matches is None or matches > cap):
        run_log.get()("warning", f"Sorted Danbooru searches stop after {cap} posts, so later matches will be missed.")
    return search, set(required) - set(sent), excluded


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
    search_hint = (
        "Sort with order:score. Without a paid account, Danbooru takes 2 tags including the sort, plus a rating. "
        "Any more are checked locally, which makes the search slower."
    )
    post_page = "https://danbooru.donmai.us/posts/{id}"

    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms, required, excluded = await plan_search(self.search_terms(tags, "", rating))
        blacklist = excluded.union(blacklist_tags.split())
        logger.debug(terms)
        scanned = matched = 0

        async for post in danbooru_style_pages(fetch_page, terms, PAGE_SIZE, MAX_NUMBERED_PAGES, 0):
            scanned += 1
            if required and scanned % SCAN_LOG_INTERVAL == 0:
                run_log.get()("info", f"Scanned {scanned} Danbooru posts, {matched} matched")
            post_tags = post["tag_string"].split()
            # Account-restricted posts come back without a file.
            if not post.get("file_url") or blacklist.intersection(post_tags) or not required.issubset(post_tags):
                continue
            matched += 1
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
        r = await paced_get("notes.json", {"search[post_id]": post["id"]})
        return notes_to_markdown(r.json())
