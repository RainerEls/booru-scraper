import asyncio
import base64
from urllib.parse import quote, urljoin

from app import config, http
from app.http import api_get

RATE_LIMIT = config.rate_limit("SZURUBOORU")
BASE_URL = config.SZURUBOORU_BASE_URL
PAGE_SIZE = 100
UPLOAD_TIMEOUT = 300.0

_token = base64.b64encode(f"{config.SZURUBOORU_USER_ID}:{config.SZURUBOORU_API_TOKEN}".encode()).decode()
session = http.client(
    headers={"Authorization": f"Token {_token}", "Content-Type": "application/json", "Accept": "application/json"},
    timeout=30.0,
)


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


async def replace_content(post_id, contentUrl):
    current = await api_get(session, f"{BASE_URL}/post/{post_id}", {"fields": "version"})
    payload = {"version": current.json()["version"], "contentUrl": contentUrl}
    r = await session.put(f"{BASE_URL}/post/{post_id}", json=payload, timeout=UPLOAD_TIMEOUT)
    r.raise_for_status()


async def upload_post(
    contentUrl, tags, safety, image_width, image_height, source=None, notes=None
):
    await asyncio.sleep(RATE_LIMIT)
    tags_list = tags.split()
    payload = {"contentUrl": contentUrl, "tags": tags_list, "safety": safety}
    if source is not None:
        payload["source"] = source
    if notes:
        payload["notes"] = []
        for note in notes:
            x, y, w, h = note["x"], note["y"], note["width"], note["height"]
            payload["notes"].append(
                {
                    "polygon": [
                        [x / image_width, y / image_height],
                        [x / image_width, (y + h) / image_height],
                        [(x + w) / image_width, (y + h) / image_height],
                        [(x + w) / image_width, y / image_height],
                    ],
                    "text": note["body"],
                }
            )
    return await session.post(f"{BASE_URL}/posts/", json=payload, timeout=UPLOAD_TIMEOUT)
