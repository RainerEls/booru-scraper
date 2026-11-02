import asyncio
import logging
from collections import Counter

import httpx

from app import db, notify, szurubooru

logger = logging.getLogger(__name__)

CONCURRENT_FILE_CHECKS = 5
REPULLABLE = ("missing", "damaged")
REPULL_DELAY = 1.1


async def removal_records(operation):
    snapshots = await szurubooru.all_results("snapshots", query=f"operation:{operation} type:post")
    return {
        int(snapshot["id"]): f"by {(snapshot['user'] or {}).get('name', 'unknown')}, {snapshot['time']}"
        for snapshot in snapshots
    }


async def file_problem(booru_post, semaphore):
    async with semaphore:
        try:
            r = await szurubooru.head_file(booru_post["contentUrl"])
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
        "post_id": post.id,
        "source": post.source,
        "source_post_id": post.source_post_id,
        "booru_post_id": post.szurubooru_post_id,
        "image_url": post.image_url,
    }


async def build_report(posts):
    booru_posts = {p["id"]: p for p in await szurubooru.all_results("posts/", fields="id,contentUrl,fileSize")}
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
        await db.finish_check(check_id, "error", error=f"Nothing was reported, as the check couldn't finish: {e!r}")
        await notify.send("integrity_problems", "The check couldn't finish. See Activity for the error.")
    else:
        await db.finish_check(check_id, "done", report=report)
        counts = report["counts"]
        if counts["missing"] or counts["damaged"]:
            await notify.send("integrity_problems", f"{counts['missing']} missing, {counts['damaged']} damaged")


async def repull_post(issue, kind, source):
    post = await source.find_post(issue["source_post_id"])
    if post is None:
        return "The source no longer has this post"
    if kind == "damaged":
        await szurubooru.replace_content(issue["booru_post_id"], post["file_url"])
        return "File replaced"
    post["notes"] = await source.grab_notes(post)
    r = await szurubooru.upload_post(post, source.safety(post), source.source_links(post))
    r.raise_for_status()
    await db.finish_post(issue["post_id"], "processed", r.json()["id"])
    return "Uploaded again"


async def run_repull(check_id, report, sources):
    issues = [issue for issue in report["issues"] if issue["kind"] in REPULLABLE]
    posts = await db.get_posts([issue["post_id"] for issue in issues])
    try:
        current = {i["post_id"]: i["kind"] for i in (await build_report(posts))["issues"]}
    except Exception as e:
        logger.exception("Re-pull failed")
        await db.finish_check(check_id, "done", report=report, error=f"Nothing was re-pulled: {e!r}")
        return
    for issue in issues:
        kind = current.get(issue["post_id"], "intact")
        if kind not in REPULLABLE:
            issue["repull"] = f"Left alone, as it's now {kind}"
            continue
        try:
            issue["repull"] = await repull_post(issue, kind, sources[issue["source"]])
        except Exception as e:
            logger.exception(f"Re-pull failed for post {issue['post_id']}")
            issue["repull"] = f"Failed: {e!r}"
        await asyncio.sleep(REPULL_DELAY)
    await db.finish_check(check_id, "done", report=report)
