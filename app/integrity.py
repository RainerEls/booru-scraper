import asyncio
import logging
from collections import Counter
from urllib.parse import urljoin

import httpx

from app import config, db, http
from app.sources.base import api_get

logger = logging.getLogger(__name__)

BASE_URL = config.SZURUBOORU_BASE_URL
FILE_ROOT = BASE_URL.removesuffix("/api") + "/"
PAGE_SIZE = 100
CONCURRENT_FILE_CHECKS = 5


async def all_results(path, **params):
    results = []
    while True:
        r = await api_get(
            http.szurubooru_session, f"{BASE_URL}/{path}", {**params, "limit": PAGE_SIZE, "offset": len(results)}
        )
        body = r.json()
        results.extend(body["results"])
        if not body["results"] or len(results) >= body["total"]:
            return results


async def removal_records(operation):
    snapshots = await all_results("snapshots", query=f"operation:{operation} type:post")
    return {
        int(snapshot["id"]): f"by {(snapshot['user'] or {}).get('name', 'unknown')}, {snapshot['time']}"
        for snapshot in snapshots
    }


async def file_problem(booru_post, semaphore):
    async with semaphore:
        try:
            r = await http.szurubooru_session.head(urljoin(FILE_ROOT, booru_post["contentUrl"]))
        except httpx.TransportError:
            return "unverified", "No response from the file server"
    if r.status_code == 404:
        return "damaged", "File is gone"
    if r.status_code != 200:
        return "unverified", f"File server answered HTTP {r.status_code}"
    size = int(r.headers.get("content-length", -1))
    if size != booru_post["fileSize"]:
        return "damaged", f"File is {size} bytes, expected {booru_post['fileSize']}"
    return "intact", None


async def check_post(post, booru_posts, deleted, merged, semaphore):
    booru_post = booru_posts.get(post.szurubooru_post_id)
    if booru_post:
        kind, detail = await file_problem(booru_post, semaphore)
    elif post.szurubooru_post_id in deleted:
        kind, detail = "deleted", deleted[post.szurubooru_post_id]
    elif post.szurubooru_post_id in merged:
        kind, detail = "merged", merged[post.szurubooru_post_id]
    else:
        kind, detail = "missing", "Gone, with no record of it being deleted or merged"
    return {
        "kind": kind,
        "detail": detail,
        "source": post.source,
        "source_post_id": post.source_post_id,
        "booru_post_id": post.szurubooru_post_id,
        "image_url": post.image_url,
    }


async def build_report(posts):
    booru_posts = {p["id"]: p for p in await all_results("posts/", fields="id,contentUrl,fileSize")}
    deleted = await removal_records("deleted")
    merged = await removal_records("merged")
    semaphore = asyncio.Semaphore(CONCURRENT_FILE_CHECKS)
    results = await asyncio.gather(
        *[check_post(post, booru_posts, deleted, merged, semaphore) for post in posts]
    )
    return {
        "counts": Counter(result["kind"] for result in results),
        "issues": [result for result in results if result["kind"] != "intact"],
    }


async def run_check(check_id):
    try:
        report = await build_report(await db.get_uploaded_posts())
    except Exception as e:
        logger.exception("Integrity check failed")
        await db.finish_check(check_id, "error", error=repr(e))
    else:
        await db.finish_check(check_id, "done", report=report)
