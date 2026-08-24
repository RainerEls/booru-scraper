import asyncio
import html
import logging

import requests

from app import config, constants, http
from app.sources.base import BooruSource

logger = logging.getLogger(__name__)

RATE_LIMIT = float(config.YANDERE_RATE_LIMIT)
YANDERE_RATINGS_CONVERSIONS = constants.YANDERE_RATINGS_CONVERSIONS

FRONTEND_TO_YANDERE_RATINGS = {
    'safe': 's',
    'sketchy': 'q',
    'unsafe': 'e',
}

class YandereSource(BooruSource):
    async def search_posts(self, limit=5, tags="", blacklist_tags="", rating=None):

        page = 0
        search_results = []
        bad_tags = ""
        rated_search = ""

        if rating:
            rated_search = f"rating:{FRONTEND_TO_YANDERE_RATINGS.get(rating)}"
        if blacklist_tags:
            bad_tags = ["-" + tag for tag in blacklist_tags.split(" ")]
        all_tags = " ".join(bad_tags) + " " + tags + " " + rated_search
        logger.debug(str(all_tags))

        while limit > len(search_results):
            payloadPost = {
                "tags": all_tags,
                "limit": 100,
                "page": page,
            }
            r = await http.yandere_session.get(
                "https://yande.re/post.json", params=payloadPost
            )
            logger.debug(f"Search Post Status Code: {r.status_code}")
            logger.debug(f"Search Post Raw: {r.text}")
            page_results = r.json()
            page += 1
            search_results.extend(page_results)

            if not page_results:
                break

            await asyncio.sleep(RATE_LIMIT)
        
        search_results = [
            {**post,
            "tags": " ".join(html.unescape(t) for t in post["tags"].split(" ")),
            "rating": YANDERE_RATINGS_CONVERSIONS.get}
            for post in search_results
        ]
        return search_results[:limit]


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
            }
            r = await http.yandere_session.get(
                "https://yande.re/tag.json", params=payloadTags
            )
            tag_results = r.json()["tag"]
            tag_data.extend(tag_results)
            await asyncio.sleep(RATE_LIMIT)

        unescaped_tags = [
            {**tag, "name": html.unescape(tag["name"])} for tag in tag_data
        ]
        return unescaped_tags


# session = requests.session()
# session.params = {'limit': 1, 'page': 0, 'tags': 'cat'}

# def search_posts():
#     r = session.get("https://yande.re/post.json")
#     print(r.text)
#     print(r.status_code)
#     # return r.text

# search_posts()