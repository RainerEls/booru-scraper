import asyncio
import html
import logging

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
                "limit": 100,
                "page": page,
            }
            r = await http.yandere_session.get(
                "https://yande.re/post.json", params=payloadPost
            )
            logger.debug(f"Search Post Status Code: {r.status_code}")
            logger.debug(f"Search Post Raw: {r.text}")
            posts = r.json()
            if not posts:
                return

            for post in posts:
                yield {
                    **post,
                    "tags": " ".join(html.unescape(t) for t in post["tags"].split(" ")),
                }

            page += 1
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
