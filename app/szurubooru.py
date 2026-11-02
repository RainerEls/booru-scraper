import asyncio
import base64
from urllib.parse import quote, urljoin

import httpx

from app import config, http
from app.http import ApiError, SetupProblem, api_get
from app.sources.base import MAX_SOURCE_LENGTH

RATE_LIMIT = config.rate_limit("SZURUBOORU")
BASE_URL = config.SZURUBOORU_BASE_URL
PAGE_SIZE = 100
UPLOAD_TIMEOUT = 300.0

_token = base64.b64encode(f"{config.SZURUBOORU_USER_ID}:{config.SZURUBOORU_API_TOKEN}".encode()).decode()
session = http.client(
    headers={"Authorization": f"Token {_token}", "Content-Type": "application/json", "Accept": "application/json"},
    timeout=30.0,
)


async def check_connection():
    if missing := config.missing_szurubooru():
        raise SetupProblem(f"Szurubooru isn't set up. Set {', '.join(missing)} in .env.")
    try:
        r = await api_get(session, f"{BASE_URL}/tag-categories")
        r.json()["results"]
    except ApiError as e:
        raise SetupProblem(
            f"Szurubooru answered HTTP {e.status}. Check SZURUBOORU_BASE_URL and SZURUBOORU_USER_ID in .env."
        ) from e
    except httpx.TransportError as e:
        raise SetupProblem(f"Could not reach szurubooru at {BASE_URL}. Check SZURUBOORU_BASE_URL in .env.") from e
    except (ValueError, LookupError, TypeError) as e:
        raise SetupProblem(
            f"{BASE_URL} doesn't look like the szurubooru API. SZURUBOORU_BASE_URL should end in /api."
        ) from e


async def all_results(path, **params):
    results = []
    while True:
        r = await api_get(session, f"{BASE_URL}/{path}", {**params, "limit": PAGE_SIZE, "offset": len(results)})
        body = r.json()
        results.extend(body["results"])
        if not body["results"] or len(results) >= body["total"]:
            return results


async def head_file(content_url):
    return await session.head(urljoin(f"{BASE_URL}/", f"../{content_url}"))


async def ensure_tag_categories(names):
    r = await api_get(session, f"{BASE_URL}/tag-categories")
    existing = r.json()["results"]
    missing = set(names) - {category["name"] for category in existing}
    for order, name in enumerate(sorted(missing), start=len(existing) + 1):
        payload = {"name": name, "color": "default", "order": order}
        r = await session.post(f"{BASE_URL}/tag-categories", json=payload)
        r.raise_for_status()


async def get_tag(tag_name):
    return await session.get(f"{BASE_URL}/tag/{quote(tag_name, safe='')}")


async def create_tag(tag_name, tag_category):
    payload = {"names": [tag_name], "category": tag_category}
    return await session.post(f"{BASE_URL}/tags", json=payload)


async def update_tag(tag_name, tag_category, version):
    payload = {"category": tag_category, "version": version}
    return await session.put(f"{BASE_URL}/tag/{quote(tag_name, safe='')}", json=payload)


async def sync_tag(tag_name, tag_category):
    await asyncio.sleep(RATE_LIMIT)
    existing = await get_tag(tag_name)
    if existing.status_code == 200:
        r = await update_tag(tag_name, tag_category, existing.json()["version"])
    else:
        r = await create_tag(tag_name, tag_category)
    r.raise_for_status()


async def replace_content(post_id, content_url):
    current = await api_get(session, f"{BASE_URL}/post/{post_id}", {"fields": "version"})
    payload = {"version": current.json()["version"], "contentUrl": content_url}
    r = await session.put(f"{BASE_URL}/post/{post_id}", json=payload, timeout=UPLOAD_TIMEOUT)
    r.raise_for_status()


async def merge_into_post(post_id, tags, source):
    await asyncio.sleep(RATE_LIMIT)
    current = (await api_get(session, f"{BASE_URL}/post/{post_id}", {"fields": "version,tags,source"})).json()
    known = {name for tag in current["tags"] for name in tag["names"]}
    new_tags = [tag for tag in tags if tag not in known]
    links = (current["source"] or "").splitlines()
    new_links = [link for link in source.splitlines() if link not in links]
    if not new_tags and not new_links:
        return
    payload = {
        "version": current["version"],
        "tags": [tag["names"][0] for tag in current["tags"]] + new_tags,
        "source": "\n".join(links + new_links)[:MAX_SOURCE_LENGTH],
    }
    r = await session.put(f"{BASE_URL}/post/{post_id}", json=payload)
    r.raise_for_status()


def note_payload(note, image_width, image_height):
    x, y, w, h = note["x"], note["y"], note["width"], note["height"]
    return {
        "polygon": [
            [x / image_width, y / image_height],
            [x / image_width, (y + h) / image_height],
            [(x + w) / image_width, (y + h) / image_height],
            [(x + w) / image_width, y / image_height],
        ],
        "text": note["body"],
    }


async def upload_post(post, safety, source=None):
    await asyncio.sleep(RATE_LIMIT)
    payload = {"contentUrl": post["file_url"], "tags": post["tags"].split(), "safety": safety}
    if source:
        payload["source"] = source
    if post.get("notes"):
        payload["notes"] = [note_payload(note, post["width"], post["height"]) for note in post["notes"]]
    return await session.post(f"{BASE_URL}/posts/", json=payload, timeout=UPLOAD_TIMEOUT)
