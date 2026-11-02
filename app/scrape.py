import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx

from app import config, db, notify, szurubooru
from app.db import utcnow
from app.http import REJECTED_STATUSES, ApiError, CredentialsRejected, SetupProblem, describe, forbidden, run_log
from app.sources.danbooru import DanbooruSource
from app.sources.e621 import E621Source
from app.sources.gelbooru import GelbooruSource
from app.sources.rule34 import Rule34Source
from app.sources.yandere import YandereSource

logger = logging.getLogger(__name__)

FEW_KNOWN_TAGS = 3000
SCHEDULE_CHECK_SECONDS = 60
SCHEDULED_STOP_AFTER_KNOWN = 100
NO_POST_EDITS = "The szurubooru account can't edit posts, so duplicates won't be merged."
PERMANENT_UPLOAD_ERRORS = ("DownloadTooLargeError", "InvalidPostContentError")

SOURCES = {
    "gelbooru": GelbooruSource(),
    "danbooru": DanbooruSource(),
    "yandere": YandereSource(),
    "e621": E621Source(),
    "rule34": Rule34Source(),
}

jobs = {}


class Job:
    def __init__(self):
        self.events = []
        self.updated = asyncio.Event()
        self.finished = False
        self.status = "done"
        self.counters = {"processed": 0, "skipped": 0, "failed": 0, "total": 0}
        self.progress = {"phase": "Starting", "done": 0, "goal": 0}
        self.failures = []
        self.lines = []
        self.warned = set()
        self.task = None
        self.stop_status = None

    def publish(self, event):
        self.events.append(event)
        self.updated.set()
        self.updated = asyncio.Event()

    def state(self):
        return {**self.counters, **self.progress}

    def start_phase(self, phase, goal):
        self.progress = {"phase": phase, "done": 0, "goal": goal}
        self.publish(self.state())

    def advance(self):
        self.progress["done"] += 1
        self.publish(self.state())

    def log(self, level, message):
        getattr(logger, level)(message)
        if level == "debug":
            self.publish(self.state())
        else:
            self.lines.append({"level": level, "message": message})
            self.publish({**self.state(), "log": message, "level": level})

    def count(self, field, message, level="debug"):
        self.counters[field] += 1
        self.log(level, message)

    def warn_once(self, message):
        if message not in self.warned:
            self.warned.add(message)
            self.log("warning", message)

    def error(self, message):
        self.status = "error"
        self.log("error", message)

    def stop(self, status="stopped"):
        if self.task and self.stop_status is None:
            self.stop_status = status
            self.task.cancel()

    def finish(self):
        self.finished = True
        self.publish({"status": self.status})


@dataclass
class ScrapeSpec:
    source: str
    limit: int
    tags: str = ""
    blacklist_tags: str = ""
    rating: str | None = None
    post_ids: list | None = None
    schedule_id: int | None = None
    stop_after_known: int | None = None
    retry_of: int | None = None


def next_run(schedule):
    if schedule.last_run_at is None:
        return utcnow()
    return schedule.last_run_at + timedelta(hours=schedule.interval_hours)


def response_json(r):
    try:
        return r.json()
    except ValueError:
        return {}


async def merge_uploaded_post(job, source, db_entry, post, other_post):
    try:
        await szurubooru.merge_into_post(other_post, post["tags"].split(), source.source_links(post))
    except (httpx.HTTPError, CredentialsRejected) as e:
        if not forbidden(e):
            raise
        job.warn_once(NO_POST_EDITS)
        await db.finish_post(db_entry, "skipped")
        job.count("skipped", f"Post {post['id']} already uploaded as booru post {other_post}")
    else:
        await db.finish_post(db_entry, "merged")
        job.count("skipped", f"Post {post['id']} already uploaded, merged into booru post {other_post}")


async def process_post(source, db_entry, post, semaphore, job):
    async with semaphore:
        try:
            r = await szurubooru.upload_post(post, source.safety(post), source.source_links(post))
            if r.status_code in REJECTED_STATUSES:
                raise CredentialsRejected(szurubooru.BASE_URL, r.status_code)
            body = response_json(r)

            if r.status_code == 200:
                await db.finish_post(db_entry, "processed", body["id"])
                job.count("processed", f"Post {post['id']} uploaded")
            elif r.status_code == 400 and body.get("name") == "PostAlreadyUploadedError":
                await merge_uploaded_post(job, source, db_entry, post, body["otherPostId"])
            else:
                error_code = body.get("name", "Unknown")
                if error_code in PERMANENT_UPLOAD_ERRORS:
                    await db.finish_post(db_entry, "rejected")
                job.failures.append(
                    {"id": post["id"], "error": error_code, "url": post["file_url"], "tags": post["tags"]}
                )
                job.count(
                    "failed",
                    f"Post {post['id']} failed with status code: {r.status_code} {error_code} - {post['file_url']}",
                    "warning",
                )
        except SetupProblem:
            raise
        except Exception as e:
            logger.exception(f"Unhandled exception processing post {post['id']}")
            job.failures.append({"id": post["id"], "error": describe(e), "url": post["file_url"], "tags": post["tags"]})
            job.count("failed", f"Post {post['id']} failed: {describe(e)}", "error")
        finally:
            job.advance()


async def claim_if_new(job, source_name, post, duplicates):
    claim = {"source": source_name, "source_post_id": post["id"], "md5": post["md5"], "image_url": post["file_url"]}
    if await db.post_exists(source_name, post["id"]):
        job.count("skipped", f"{source_name}: {post['id']} already exists in DB, skipping")
        return None
    is_duplicate = await db.md5_exists(post["md5"])
    db_entry = await db.claim_post(**claim, status="queued")
    if db_entry is None:
        job.count("skipped", f"{source_name}: {post['id']} is already claimed by another run, skipping")
    elif is_duplicate:
        duplicates.append((db_entry, post))
        job.count("skipped", f"MD5 {post['md5']} already exists in DB, will merge post {post['id']} into it")
        return None
    return db_entry


async def find_new_posts(job, spec, new_posts, duplicates):
    source = SOURCES[spec.source]
    if spec.post_ids:
        posts = source.posts_by_id(spec.post_ids)
    else:
        posts = source.search_posts(tags=spec.tags, blacklist_tags=spec.blacklist_tags, rating=spec.rating)
    job.start_phase("Searching", spec.limit)
    known_in_a_row = 0
    try:
        async for post in posts:
            job.count("total", f"Found post {post['id']}")
            db_entry = await claim_if_new(job, spec.source, post, duplicates)
            if db_entry is None:
                known_in_a_row += 1
                if known_in_a_row == spec.stop_after_known:
                    job.log("info", f"Caught up after {known_in_a_row} known posts in a row")
                    return
                continue
            known_in_a_row = 0
            post["notes"] = await source.grab_notes(post)
            new_posts.append((db_entry, post))
            job.advance()
            if len(new_posts) >= spec.limit:
                return
    except SetupProblem:
        raise
    except Exception as e:
        logger.exception("Search failed")
        job.error(f"Search failed, continuing with the {len(new_posts)} new post(s) found: {describe(e)}")


async def sync_unknown_tags(job, source, posts):
    post_tags = {tag for _, post in posts for tag in post["tags"].split()}
    unknown_tags = {tag for tag in post_tags if not await db.tag_exists(tag)}
    job.log("info", f"Found {len(posts)} post(s) to upload or merge with {len(unknown_tags)} unknown tag(s)")
    if not unknown_tags:
        return

    job.start_phase("Syncing tags", len(unknown_tags))
    async for tag in source.grab_tags(unknown_tags):
        try:
            await szurubooru.sync_tag(tag["name"], tag["category"])
        except httpx.HTTPError as e:
            if not forbidden(e):
                job.log("warning", f"Tag {tag['name']} failed to sync: {describe(e)}")
            elif e.request.method == "PUT":
                job.warn_once("The szurubooru account can't edit tags, so existing tags keep their category.")
                await db.store_tag(tag["name"])
            else:
                job.log("warning", "The szurubooru account can't read or create tags, so no tags will be written.")
                break
        else:
            await db.store_tag(tag["name"])
        job.advance()


async def merge_duplicate(job, source, db_entry, post):
    booru_post = await db.booru_post_for_md5(post["md5"])
    if booru_post is None:
        job.log("info", f"Post {post['id']} has no uploaded original to merge into yet")
        return
    try:
        await szurubooru.merge_into_post(booru_post, post["tags"].split(), source.source_links(post))
    except ApiError as e:
        if e.status != 404:
            raise
        await db.finish_post(db_entry, "skipped")
        job.log("info", f"Post {post['id']} wasn't merged, as booru post {booru_post} no longer exists")
    else:
        await db.finish_post(db_entry, "merged")
        job.log("info", f"Post {post['id']} merged into booru post {booru_post}")


async def merge_duplicates(job, source, duplicates):
    if not duplicates:
        return
    job.start_phase("Merging duplicates", len(duplicates))
    for db_entry, post in duplicates:
        try:
            await merge_duplicate(job, source, db_entry, post)
        except (httpx.HTTPError, CredentialsRejected) as e:
            if forbidden(e):
                job.warn_once(NO_POST_EDITS)
                return
            job.log("warning", f"Post {post['id']} could not be merged, retried next run: {describe(e)}")
        job.advance()


def report_failures(job):
    if not job.failures:
        return
    job.log("warning", f"{len(job.failures)} post(s) could not be uploaded:")
    for failure in job.failures:
        outcome = "won't be tried again" if failure["error"] in PERMANENT_UPLOAD_ERRORS else "retried next run"
        job.log("warning", f"  {failure['id']} - {failure['error']}, {outcome}")
        job.log("warning", f"    url:  {failure['url']}")
        job.log("warning", f"    tags: {failure['tags']}")


def run_event(job, spec):
    if job.status == "stopped":
        return None
    if job.status == "interrupted":
        return "scrape_interrupted"
    if job.status == "error" or job.failures:
        return "scrape_failed"
    if spec.schedule_id is None:
        return "scrape_finished"
    return "schedule_finished" if job.counters["processed"] else None


async def notify_run(job, spec):
    event = run_event(job, spec)
    if event is None:
        return
    counters = job.counters
    lines = [
        f"{spec.source}: {spec.tags or 'all posts'}",
        f"Uploaded {counters['processed']}, skipped {counters['skipped']}, failed {counters['failed']}",
    ]
    lines += [line["message"] for line in job.lines if line["level"] == "error"][:1]
    await notify.send(event, "\n".join(lines))


async def check_setup(name, source):
    if not source.configured:
        raise SetupProblem(f"{name} needs its API key and user id set in .env.")
    await szurubooru.check_connection()


async def upload_posts(job, source, new_posts):
    job.start_phase("Uploading", len(new_posts))
    semaphore = asyncio.Semaphore(config.CONCURRENT_UPLOADS)
    try:
        async with asyncio.TaskGroup() as group:
            for entry, post in new_posts:
                group.create_task(process_post(source, entry, post, semaphore, job))
    except* SetupProblem as problems:
        raise problems.exceptions[0] from None


async def run_scrape(run_id, job, spec):
    source = SOURCES[spec.source]
    new_posts = []
    duplicates = []
    spec.blacklist_tags = " ".join(dict.fromkeys(f"{config.BLACKLIST_TAGS} {spec.blacklist_tags}".split()))
    run = None
    logger.info(f"Scrape started: {spec}")

    try:
        run = await db.create_run(
            uuid=run_id,
            source=spec.source,
            tags=spec.tags,
            blacklist_tags=spec.blacklist_tags,
            rating=spec.rating,
            post_limit=spec.limit,
            schedule_id=spec.schedule_id,
            retry_of=spec.retry_of,
        )
        run_log.set(job.log)
        await check_setup(spec.source, source)
        if source.slow_tag_lookup and await db.count_tags() < FEW_KNOWN_TAGS:
            job.log(
                "warning",
                f"{spec.source} is rate limited and looks up many tags one at a time, "
                "so runs on a fresh instance can be slow until more tags are known.",
            )
        try:
            await szurubooru.ensure_tag_categories(source.tag_categories.values())
        except (httpx.HTTPError, CredentialsRejected) as e:
            if forbidden(e):
                job.log("warning", "The szurubooru account can't manage tag categories, so missing ones won't be created.")
            else:
                job.log("warning", f"Could not set up tag categories: {describe(e)}")

        job.log("info", f"Searching {spec.source}")
        await find_new_posts(job, spec, new_posts, duplicates)
        await sync_unknown_tags(job, source, new_posts + duplicates)

        await upload_posts(job, source, new_posts)
        await merge_duplicates(job, source, duplicates)
        counters = job.counters
        job.log(
            "info",
            f"Uploaded: {counters['processed']} - Skipped: {counters['skipped']} - "
            f"Failed: {counters['failed']} - Total: {counters['total']}",
        )
    except SetupProblem as e:
        job.error(f"Stopped: {e}")
    except asyncio.CancelledError:
        job.status = job.stop_status or "stopped"
        job.log("warning", job.status.capitalize())
    except Exception as e:
        logger.exception("Scrape failed")
        job.error(f"Scrape failed: {describe(e)}")
    finally:
        await db.release_claims([entry for entry, _ in new_posts + duplicates])
        report_failures(job)
        if run is not None:
            await db.update_run(
                run,
                **job.counters,
                status=job.status,
                log=job.lines,
                failed_posts=[failure["id"] for failure in job.failures],
            )
        job.finish()
        await notify_run(job, spec)


def active_jobs():
    return [job_id for job_id, job in jobs.items() if not job.finished]


def start_job(spec):
    for job_id in [job_id for job_id, job in jobs.items() if job.finished]:
        del jobs[job_id]
    job_id = str(uuid.uuid4())
    job = Job()
    jobs[job_id] = job
    job.task = asyncio.create_task(run_scrape(job_id, job, spec))
    return job_id


async def start_schedule(schedule):
    await db.mark_schedule_started(schedule.id)
    spec = ScrapeSpec(
        source=schedule.source,
        limit=schedule.post_limit,
        tags=schedule.tags,
        blacklist_tags=schedule.blacklist_tags,
        rating=schedule.rating,
        schedule_id=schedule.id,
        stop_after_known=SCHEDULED_STOP_AFTER_KNOWN,
    )
    return start_job(spec)


async def start_due_schedule():
    if active_jobs():
        return
    for schedule in await db.get_schedules():
        if schedule.enabled and schedule.source in SOURCES and next_run(schedule) <= utcnow():
            await start_schedule(schedule)
            return


async def run_schedules():
    while True:
        await asyncio.sleep(SCHEDULE_CHECK_SECONDS)
        try:
            await start_due_schedule()
        except Exception:
            logger.exception("Could not start a scheduled scrape")



async def interrupt_jobs():
    for job in jobs.values():
        job.stop("interrupted")
    await asyncio.gather(*[job.task for job in jobs.values() if job.task], return_exceptions=True)
