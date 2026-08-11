import asyncio
import html
import logging

from .. import config, constants, http

logger = logging.getLogger(__name__)

RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)


async def search_posts(limit=5, tags="", blacklist_tags="", rating=None):

    page = 1
    response_number = 100
    search_results = []
    bad_tags = ""
    rated_search = ""

    if rating:
        rated_search = f"rating:{constants.FRONTEND_TO_GELBOORU_RATINGS.get(rating)}"
    if blacklist_tags:
        bad_tags = ["-" + tag for tag in blacklist_tags.split(" ")]
    all_tags = " ".join(bad_tags) + " " + tags + " " + rated_search
    logger.debug(str(all_tags))

    while response_number >= 100 and limit >= len(search_results):
        payloadPost = {
            "tags": all_tags,
            "limit": 100,
            "page": "dapi",
            "s": "post",
            "q": "index",
            "pid": page,
        }
        r = await http.gelbooru_session.get(
            "https://gelbooru.com/index.php", params=payloadPost
        )
        logger.debug(f"Search Post Status Code: {r.status_code}")
        logger.debug(f"Search Post Raw: {r.text}")
        page_results = r.json()["post"]
        response_number = len(page_results)
        page += 1
        search_results.extend(page_results)
        await asyncio.sleep(RATE_LIMIT)
    
    # Will *hopefully* fix the escaped tags in szurubooru posts
    for post in search_results:
        post["tags"] = " ".join(html.unescape(t) for t in post["tags"].split(" "))
    return search_results[:limit]


async def grab_tags(tags):

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
        r = await http.gelbooru_session.get(
            "https://gelbooru.com/index.php", params=payloadTags
        )
        tag_results = r.json()["tag"]
        tag_data.extend(tag_results)
        await asyncio.sleep(RATE_LIMIT)

    unescaped_tags = [
        {**tag, "name": html.unescape(tag["name"])} for tag in tag_data
    ]
    return unescaped_tags
