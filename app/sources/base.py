import asyncio
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from functools import partial

MAX_SOURCE_LENGTH = 2048
ID_LOOKUP_DELAY = 1.1

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


async def pages_by_id(fetch, page_size, delay, first, after):
    page = first
    while True:
        posts = await fetch(page)
        for post in posts:
            yield post
        if len(posts) < page_size:
            return
        page = after(posts[-1]["id"])
        await asyncio.sleep(delay)


async def pages_by_number(fetch, page_size, delay, numbers):
    for page in numbers:
        posts = await fetch(page)
        for post in posts:
            yield post
        if len(posts) < page_size:
            return
        await asyncio.sleep(delay)


def dapi_pages_by_id(fetch_page, terms, page_size, delay):
    return pages_by_id(lambda cursor: fetch_page(terms + cursor), page_size, delay, [], lambda id: [f"id:<{id}"])


def danbooru_style_pages(fetch_page, terms, page_size, max_pages, delay):
    fetch = partial(fetch_page, terms)
    if any(term.startswith("order:") for term in terms):
        return pages_by_number(fetch, page_size, delay, range(1, max_pages + 1))
    return pages_by_id(fetch, page_size, delay, 1, lambda id: f"b{id}")


def dapi_params(kind, **params):
    return {"page": "dapi", "s": kind, "q": "index", **params}


def notes_to_markdown(notes):
    return [
        {
            "x": note["x"],
            "y": note["y"],
            "width": note["width"],
            "height": note["height"],
            "body": html_note_to_markdown(note["body"]),
        }
        for note in notes
        if note["is_active"] and note["width"] and note["height"]
    ]


def xml_notes(text):
    return notes_to_markdown(
        {
            "x": int(note.get("x")),
            "y": int(note.get("y")),
            "width": int(note.get("width")),
            "height": int(note.get("height")),
            "body": note.get("body"),
            "is_active": note.get("is_active") != "false",
        }
        for note in ET.fromstring(text).findall("note")
    )


class BooruSource(ABC):
    ratings = {}
    rating_terms = {}
    tag_categories = {}
    slow_tag_lookup = False
    credentials = ()
    search_hint = ""
    post_page = ""

    @property
    def configured(self):
        return all(self.credentials)

    def source_links(self, post):
        links = [self.post_page.format(id=post["id"]), post.get("source")]
        return "\n".join(filter(None, links))[:MAX_SOURCE_LENGTH]

    def search_terms(self, tags, blacklist_tags, rating):
        terms = ["-" + tag for tag in blacklist_tags.split()] + tags.split()
        if rating:
            terms.extend(self.rating_terms[rating].split())
        return terms

    def safety(self, post):
        return self.ratings.get(post["rating"], "sketchy")

    @abstractmethod
    def search_posts(self, tags="", blacklist_tags="", rating=None):
        """Yield matching posts, newest first, fetching pages as they are consumed."""

    @abstractmethod
    def grab_tags(self, tags):
        """Yield the name and szurubooru category of each tag, as they are found."""

    def parent_of(self, post):
        # Sources without a parent say 0, null or nothing, depending on the source.
        return int(post.get("parent_id") or 0) or None

    async def find_post(self, post_id):
        found = [post async for post in self.search_posts(tags=f"id:{post_id}")]
        return found[0] if found else None

    async def posts_by_id(self, post_ids):
        for post_id in post_ids:
            post = await self.find_post(post_id)
            if post:
                yield post
            await asyncio.sleep(ID_LOOKUP_DELAY)

    async def md5_of(self, post_id):
        post = await self.find_post(post_id)
        await asyncio.sleep(ID_LOOKUP_DELAY)
        return post["md5"] if post else None

    async def grab_notes(self, post):
        return None


class PostTaggedSource(BooruSource):
    """A source whose search results already say what category each tag is."""

    def __init__(self):
        self.seen_categories = {}

    async def grab_tags(self, tags):
        for tag in tags:
            yield {"name": tag, "category": self.seen_categories.get(tag, "general")}
