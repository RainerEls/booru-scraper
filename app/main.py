import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import config, constants, db, http, integrity, szurubooru
from app.sources.base import run_log
from app.sources.danbooru import DanbooruSource
from app.sources.e621 import E621Source
from app.sources.gelbooru import GelbooruSource
from app.sources.rule34 import Rule34Source
from app.sources.yandere import YandereSource

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.getLevelName(config.LOGLEVEL))
logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCES = {
    "gelbooru": GelbooruSource(),
    "danbooru": DanbooruSource(),
    "yandere": YandereSource(),
    "e621": E621Source(),
    "rule34": Rule34Source(),
}
FEW_KNOWN_TAGS = 3000
CHECK_BUSY = ("running", "repulling")

jobs = {}


class Job:
    def __init__(self):
        self.events = []
        self.updated = asyncio.Event()
        self.finished = False

    def publish(self, event):
        self.events.append(event)
        self.updated.set()
        self.updated = asyncio.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    await http.create_clients()
    await db.init_db()
    await db.release_stale_claims()
    await db.fail_interrupted_runs()

    yield

    await http.close_clients()
    await db.close_db()


class Scrape(BaseModel):
    source: constants.Source
    limit: int = Field(ge=1, le=10000)
    tags: str | None = None
    blacklist_tags: str | None = None
    rating: Literal["safe", "sketchy", "unsafe", ""] | None = None


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def emit(job, counters, level, message):
    if level == "debug":
        job.publish({**counters})
    else:
        job.publish({**counters, "log": message, "level": level})


def bump(job, counters, field, level, message):
    counters[field] += 1
    emit(job, counters, level, message)


def log_and_emit(job, level, message, counters):
    getattr(logger, level)(message)
    emit(job, counters, level, message)


def response_json(r):
    try:
        return r.json()
    except ValueError:
        return {}


async def process_post(
    booru_source, db_entry, post, semaphore, job, counters, failures
):
    async with semaphore:
        source_id = post["id"]
        try:
            contentUrl = post["file_url"]
            tags = post["tags"]
            safety = SOURCES[booru_source].ratings.get(post["rating"], "sketchy")
            source = post["source"]
            image_width = post["width"]
            image_height = post["height"]
            notes = post["notes"]

            logger.debug("Attempting to upload post to szurubooru")
            r = await szurubooru.upload_post(
                contentUrl, tags, safety, image_width, image_height, source, notes
            )
            body = response_json(r)

            if r.status_code == 200:
                logger.info("Szurubooru post creation succeeded.")
                await db.add_szurubooru_post_id(db_entry, body["id"])
                await db.update_post_status(booru_source, source_id, "processed")
                bump(job, counters, "processed", "debug", "post processed")
            elif (
                r.status_code == 400 and body.get("name") == "PostAlreadyUploadedError"
            ):
                await db.update_post_status(booru_source, source_id, "skipped")
                bump(
                    job,
                    counters,
                    "skipped",
                    "debug",
                    "Post already uploaded - Skipping",
                )
            else:
                error_code = body.get("name", "Unknown")
                failures.append(
                    {
                        "id": source_id,
                        "error": error_code,
                        "url": contentUrl,
                        "tags": tags,
                    }
                )
                bump(
                    job,
                    counters,
                    "failed",
                    "warning",
                    f"Post {source_id} failed with status code: {r.status_code} {error_code} - {contentUrl}",
                )
                logger.debug(
                    f"Szurubooru creation failed with status code: {r.status_code}"
                )
        except Exception as e:
            bump(
                job,
                counters,
                "failed",
                "error",
                f"Unhandled error for post {source_id}: {e!r}",
            )
            logger.exception(f"Unhandled exception processing post {source_id}")


async def run_scrape(run_id, job, limit, booru_source, tags, blacklist_tags, rating):

    source = SOURCES[booru_source]
    counters = {"processed": 0, "skipped": 0, "failed": 0, "total": 0}
    run_status = "done"
    new_posts = []
    failures = []
    tags = " ".join((tags or "").split())
    blacklist_tags = " ".join((blacklist_tags or "").split())

    logger.info(
        f"Scrape started - Limit: {limit}, Rating: {rating}, Tags: [{tags}], Blacklist Tags: [{blacklist_tags}]"
    )
    run = None

    try:
        run = await db.create_run(
            run_id, booru_source, tags, blacklist_tags, rating, limit, "running"
        )
        run_log.set(lambda level, message: log_and_emit(job, level, message, counters))
        if source.slow_tag_lookup and await db.count_tags() < FEW_KNOWN_TAGS:
            log_and_emit(
                job,
                "warning",
                f"{booru_source} is rate limited and looks up many tags one at a time, "
                "so runs on a fresh instance can be slow until more tags are known.",
                counters,
            )
        try:
            await szurubooru.ensure_tag_categories(source.tag_types.values())
        except httpx.HTTPError as e:
            log_and_emit(job, "warning", f"Could not set up tag categories: {e!r}", counters)
        log_and_emit(job, "info", f"Searching {booru_source}", counters)
        scrape_tags = set()
        unknown_tags = set()

        try:
            async for result in source.search_posts(
                tags=tags, blacklist_tags=blacklist_tags, rating=rating
            ):
                counters["total"] += 1
                log_and_emit(job, "debug", "Total increased", counters)
                if await db.post_exists(source=booru_source, source_post_id=result["id"]):
                    counters["skipped"] += 1
                    log_and_emit(
                        job,
                        "debug",
                        f"{booru_source}: {result['id']} already exists in DB. Skipping",
                        counters,
                    )
                elif await db.md5_exists(md5=result["md5"]):
                    counters["skipped"] += 1
                    log_and_emit(
                        job,
                        "debug",
                        f"MD5: {result['md5']} already exists in db. Adding source entry and skipping.",
                        counters,
                    )
                    await db.claim_post(
                        source=booru_source,
                        source_post_id=result["id"],
                        md5=result["md5"],
                        image_url=result["file_url"],
                        status="skipped",
                    )
                else:
                    db_entry = await db.claim_post(
                        source=booru_source,
                        source_post_id=result["id"],
                        md5=result["md5"],
                        image_url=result["file_url"],
                        status="queued",
                    )
                    if db_entry is None:
                        counters["skipped"] += 1
                        log_and_emit(
                            job,
                            "debug",
                            f"{booru_source}: {result['id']} is already claimed by another run. Skipping",
                            counters,
                        )
                    else:
                        result["notes"] = await source.grab_notes(result)
                        new_posts.append((db_entry, result))
                        scrape_tags.update(result["tags"].split())
                        logger.debug(f"Appended: {result}")
                        if len(new_posts) >= limit:
                            break
        except Exception as e:
            run_status = "error"
            logger.exception("Search failed")
            emit(
                job,
                counters,
                "error",
                f"Search failed, continuing with the {len(new_posts)} new post(s) found: {e!r}",
            )

        for tag in scrape_tags:
            if not await db.tag_exists(tag):
                unknown_tags.add(tag)
        log_and_emit(
            job,
            "info",
            f"Found {len(new_posts)} new post(s) with {len(unknown_tags)} unknown tag(s)",
            counters,
        )

        if unknown_tags:
            logger.info("Grabbing unknown tags")
            new_tags = await source.grab_tags(unknown_tags)
            for synced, tag in enumerate(new_tags):
                logger.debug(f"Tag data: {tag}")
                if synced % 100 == 0:
                    log_and_emit(
                        job, "info", f"Syncing tags: {synced}/{len(new_tags)}", counters
                    )
                try:
                    await szurubooru.sync_tag(
                        tag_name=tag["name"],
                        tag_category=source.tag_types.get(tag["type"], "general"),
                    )
                except httpx.HTTPError as e:
                    log_and_emit(
                        job, "warning", f"Tag {tag['name']} failed to sync: {e!r}", counters
                    )
                else:
                    await db.store_tag(tag_name=tag["name"], tag_type=tag["type"])

        log_and_emit(job, "info", f"Uploading {len(new_posts)} post(s)", counters)
        semaphore = asyncio.Semaphore(int(config.CONCURRENT_UPLOADS))
        await asyncio.gather(
            *[
                process_post(
                    booru_source, db_entry, post, semaphore, job, counters, failures
                )
                for db_entry, post in new_posts
            ],
            return_exceptions=True,
        )
        log_and_emit(
            job,
            "info",
            f"Total: {counters['total']} - Processed: {counters['processed']} - Skipped: {counters['skipped']} - Failed: {counters['failed']}",
            counters,
        )
    except Exception as e:
        run_status = "error"
        logger.exception("Scrape failed")
        emit(job, counters, "error", f"Scrape failed: {e!r}")
    finally:
        await db.release_claims([db_entry for db_entry, _ in new_posts])
        if failures:
            log_and_emit(
                job,
                "warning",
                f"{len(failures)} post(s) could not be uploaded and will be retried next run:",
                counters,
            )
            for failure in failures:
                log_and_emit(
                    job,
                    "warning",
                    f"  {failure['id']} - {failure['error']}",
                    counters,
                )
                log_and_emit(job, "warning", f"    url:  {failure['url']}", counters)
                log_and_emit(
                    job, "warning", f"    tags: {failure['tags']}", counters
                )
        if run is not None:
            await db.update_run(
                run,
                counters["total"],
                counters["processed"],
                counters["skipped"],
                counters["failed"],
                run_status,
            )
        job.finished = True
        job.publish({"status": run_status})


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/sources")
async def get_sources():
    return list(SOURCES.keys())


@app.get("/history")
async def history():
    return RedirectResponse("/activity", status_code=308)


@app.get("/activity", response_class=HTMLResponse)
async def activity(request: Request):
    runs = await db.get_runs()
    checks = await db.get_checks()
    return templates.TemplateResponse(
        request=request,
        name="activity.html",
        context={
            "runs": runs,
            "checks": checks,
            "check_running": any(check.status in CHECK_BUSY for check in checks),
            "repullable": integrity.REPULLABLE,
        },
    )


@app.post("/api/integrity-checks", status_code=202)
async def start_integrity_check(background_tasks: BackgroundTasks):
    if any(check.status in CHECK_BUSY for check in await db.get_checks()):
        raise HTTPException(status_code=409, detail="A check is already running")
    background_tasks.add_task(integrity.run_check, await db.create_check())
    return {}


@app.post("/api/integrity-checks/{check_id}/repull", status_code=202)
async def start_repull(check_id: int, background_tasks: BackgroundTasks):
    checks = await db.get_checks()
    if any(check.status in CHECK_BUSY for check in checks):
        raise HTTPException(status_code=409, detail="A check is already running")
    if not checks or checks[0].id != check_id or not checks[0].report:
        raise HTTPException(status_code=409, detail="Only the latest finished check can be re-pulled")
    await db.mark_check_repulling(check_id)
    background_tasks.add_task(integrity.run_repull, check_id, checks[0].report, SOURCES)
    return {}


@app.get("/api/scrapes/active")
async def active_scrapes():
    return [job_id for job_id, job in jobs.items() if not job.finished]


def get_job(job_id):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Unknown job")
    return jobs[job_id]


@app.get("/scrape/progress/{job_id}", response_class=EventSourceResponse)
async def scrape_progress(job: Annotated[Job, Depends(get_job)]):
    sent = 0
    while True:
        while sent < len(job.events):
            event = job.events[sent]
            sent += 1
            yield event
            if "status" in event:
                return
        await job.updated.wait()


@app.post("/scrape")
async def scrape(background_tasks: BackgroundTasks, scrape_request: Scrape):
    for job_id in [job_id for job_id, job in jobs.items() if job.finished]:
        del jobs[job_id]
    job_id = str(uuid.uuid4())
    job = Job()
    jobs[job_id] = job
    scrape_request.rating = scrape_request.rating or None
    background_tasks.add_task(
        run_scrape,
        job_id,
        job,
        scrape_request.limit,
        scrape_request.source,
        scrape_request.tags,
        scrape_request.blacklist_tags,
        scrape_request.rating,
    )
    return {"job_id": job_id}
