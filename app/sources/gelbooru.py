import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET

from app import config, constants, http
from app.sources.base import BooruSource

logger = logging.getLogger(__name__)

RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)

_TAG_PAIRS = [
    (r"<b>|<strong>", r"</b>|</strong>", "**"),
    (r"<i>|<em>", r"</i>|</em>", "*"),
    (r"<s>|<del>", r"</s>|</del>", "~~"),
]

_ESCAPE_CHARS = "\\*_`[]"
_MD_ESCAPE = re.compile("([" + re.escape(_ESCAPE_CHARS) + "])")


def html_note_to_markdown(text):
    text = _MD_ESCAPE.sub(r"\\\1", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</?span[^>]*>", "", text)

    closers = ""
    for open_pat, close_pat, marker in _TAG_PAIRS:
        opens = len(re.findall(open_pat, text, re.IGNORECASE))
        closes = len(re.findall(close_pat, text, re.IGNORECASE))
        text = re.sub(open_pat, marker, text, flags=re.IGNORECASE)
        text = re.sub(close_pat, marker, text, flags=re.IGNORECASE)
        if opens > closes:
            closers += marker * (opens - closes)

    text = re.sub(r"<[^>]+>", "", text)
    return text + closers


class GelbooruSource(BooruSource):
    async def search_posts(self, limit=5, tags="", blacklist_tags="", rating=None):

        page = 0
        search_results = []
        bad_tags = ""
        rated_search = ""

        if rating:
            rated_search = (
                f"rating:{constants.FRONTEND_TO_GELBOORU_RATINGS.get(rating)}"
            )
        if blacklist_tags:
            bad_tags = ["-" + tag for tag in blacklist_tags.split(" ")]
        all_tags = " ".join(bad_tags) + " " + tags + " " + rated_search
        logger.debug(str(all_tags))

        while limit > len(search_results):
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
            r_json = r.json()
            logger.debug(f"Search Post Status Code: {r.status_code}")
            logger.debug(f"Search Post Raw: {r.text}")
            page_results = r_json.get("post", [])
            total_count = r_json["@attributes"]["count"]
            page += 1
            search_results.extend(page_results)

            if not page_results or len(search_results) >= total_count:
                break

            await asyncio.sleep(RATE_LIMIT)

        search_results = search_results[:limit]
        final_results = []
        for post in search_results:
            post = {
                **post,
                "tags": " ".join(html.unescape(t) for t in post["tags"].split(" ")),
            }
            if post.get("has_notes") == "true":
                post["notes"] = await self.grab_notes(post["id"])
                logger.debug(f"Notes: {post['notes']}")
                await asyncio.sleep(RATE_LIMIT)
            final_results.append(post)
        return final_results

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

    async def grab_notes(self, id):
        payloadNote = {
            "page": "dapi",
            "s": "note",
            "q": "index",
            "post_id": id,
        }
        r = await http.gelbooru_session.get(
            "https://gelbooru.com/index.php", params=payloadNote
        )
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
