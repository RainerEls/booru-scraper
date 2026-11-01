import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET

from app import config, constants, http
from app.sources.base import BooruSource

logger = logging.getLogger(__name__)

API_URL = "https://gelbooru.com/index.php"
RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)
PAGE_SIZE = 100
# Gelbooru refuses offsets past 20,000.
MAX_PAGES = 201

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


async def api_get(params):
    r = await http.gelbooru_session.get(API_URL, params=params)
    # Not raise_for_status(): its message includes the URL, and so the API key.
    if r.is_error:
        raise RuntimeError(f"Gelbooru returned HTTP {r.status_code}")
    return r


class GelbooruSource(BooruSource):
    async def search_posts(self, tags="", blacklist_tags="", rating=None):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.append(f"rating:{constants.FRONTEND_TO_GELBOORU_RATINGS.get(rating)}")
        all_tags = " ".join(terms)
        logger.debug(all_tags)

        for page in range(MAX_PAGES):
            payloadPost = {
                "tags": all_tags,
                "limit": PAGE_SIZE,
                "page": "dapi",
                "s": "post",
                "q": "index",
                "pid": page,
            }
            r = await api_get(payloadPost)
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
            r = await api_get(payloadTags)
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
        r = await api_get(payloadNote)
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
