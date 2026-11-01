import asyncio
import html
from urllib.parse import quote

from app import config, http

RATE_LIMIT = float(config.SZURUBOORU_RATE_LIMIT)
BASE_URL = config.SZURUBOORU_BASE_URL
UPLOAD_TIMEOUT = 300.0


async def ensure_tag_categories(names):
    r = await http.szurubooru_session.get(f"{BASE_URL}/tag-categories")
    r.raise_for_status()
    existing = r.json()["results"]
    missing = set(names) - {category["name"] for category in existing}
    for order, name in enumerate(sorted(missing), start=len(existing) + 1):
        payload = {"name": name, "color": "default", "order": order}
        r = await http.szurubooru_session.post(f"{BASE_URL}/tag-categories", json=payload)
        r.raise_for_status()


async def get_tag(tag_name):
    r = await http.szurubooru_session.get(f"{BASE_URL}/tag/{quote(tag_name, safe='')}")
    return r


async def create_tag(tag_name, tag_category):
    payload = {"names": [tag_name], "category": tag_category}
    r = await http.szurubooru_session.post(f"{BASE_URL}/tags", json=payload)
    return r


async def update_tag(tag_name, tag_category, version):
    payload = {"names": [tag_name], "category": tag_category, "version": version}
    r = await http.szurubooru_session.put(f"{BASE_URL}/tag/{quote(tag_name, safe='')}", json=payload)
    return r


async def merge_tag(remove_name, remove_version, merge_to_name, merge_to_version):
    payload = {
        "remove": remove_name,
        "removeVersion": remove_version,
        "mergeTo": merge_to_name,
        "mergeToVersion": merge_to_version,
    }
    r = await http.szurubooru_session.post(f"{BASE_URL}/tag-merge/", json=payload)
    return r


async def sync_tag(tag_name, tag_category):
    await asyncio.sleep(RATE_LIMIT)

    clean_name = html.unescape(tag_name)

    clean_lookup = await get_tag(tag_name=clean_name)
    if clean_lookup.status_code == 200:
        clean_tag = clean_lookup.json()
        r = await update_tag(
            tag_name=clean_name, tag_category=tag_category, version=clean_tag["version"]
        )
        r.raise_for_status()

        if clean_name != tag_name:
            await asyncio.sleep(RATE_LIMIT)
            escaped_lookup = await get_tag(tag_name=tag_name)
            if escaped_lookup.status_code == 200:
                escaped_tag = escaped_lookup.json()
                await asyncio.sleep(RATE_LIMIT)
                await merge_tag(
                    remove_name=tag_name,
                    remove_version=escaped_tag["version"],
                    merge_to_name=clean_name,
                    merge_to_version=clean_tag["version"],
                )
    else:
        r = await create_tag(tag_name=clean_name, tag_category=tag_category)
        r.raise_for_status()


async def replace_content(post_id, contentUrl):
    current = await http.szurubooru_session.get(f"{BASE_URL}/post/{post_id}", params={"fields": "version"})
    current.raise_for_status()
    payload = {"version": current.json()["version"], "contentUrl": contentUrl}
    r = await http.szurubooru_session.put(f"{BASE_URL}/post/{post_id}", json=payload, timeout=UPLOAD_TIMEOUT)
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
    r = await http.szurubooru_session.post(f"{BASE_URL}/posts/", json=payload, timeout=UPLOAD_TIMEOUT)
    return r
