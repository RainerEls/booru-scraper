import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import config, constants, db, http, szurubooru
from app.sources.gelbooru import GelbooruSource

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.getLevelName(config.LOGLEVEL))

SOURCES = {"gelbooru": GelbooruSource()}

job_queues = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    await http.create_clients()
    await db.init_db()
    await db.release_stale_claims()

    yield

    await http.close_clients()
    await db.close_db()


class Scrape(BaseModel):
    source: constants.Source | None = "Unknown"
    limit: int
    tags: str | None = None
    blacklist_tags: str | None = None
    rating: Literal["safe", "sketchy", "unsafe", ""] | None = None


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def emit(queue, counters, level, message):
    queue.put_nowait({**counters, "log": message, "level": level})


def bump(queue, counters, field, level, message):
    counters[field] += 1
    emit(queue, counters, level, message)


def log_and_queue(queue, level, message, counters):
    getattr(logger, level)(message)
    emit(queue, counters, level, message)


def response_json(r):
    try:
        return r.json()
    except ValueError:
        return {}


async def process_post(
    booru_source, db_entry, post, semaphore, queue, counters, failures
):
    async with semaphore:
        source_id = post["id"]
        try:
            contentUrl = post["file_url"]
            tags = post["tags"]
            safety = constants.GELBOORU_RATINGS_CONVERSIONS.get(
                post["rating"], "sketchy"
            )
            source = post["source"]
            image_width = post["width"]
            image_height = post["height"]
            notes = post.get(
                "notes"
            )  # Uses .get() because most posts won't have notes added and will raise a KeyError

            logger.debug("Attempting to upload post to szurubooru")
            r = await szurubooru.upload_post(
                contentUrl, tags, safety, image_width, image_height, source, notes
            )
            body = response_json(r)

            if r.status_code == 200:
                bump(queue, counters, "processed", "debug", "post processed")
                logger.info("Szurubooru post creation succeeded.")
                await db.add_szurubooru_post_id(db_entry, body["id"])
                await db.update_post_status(booru_source, source_id, "processed")
            elif (
                r.status_code == 400 and body.get("name") == "PostAlreadyUploadedError"
            ):
                bump(
                    queue,
                    counters,
                    "skipped",
                    "debug",
                    "Post already uploaded - Skipping",
                )
                await db.update_post_status(booru_source, source_id, "skipped")
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
                    queue,
                    counters,
                    "failed",
                    "warning",
                    f"Post {source_id} failed with status code: {r.status_code} {error_code} - {contentUrl}",
                )
                logger.debug(
                    f"Szurubooru creation failed with status code: {r.status_code}"
                )
                await db.update_post_status(booru_source, source_id, "failed")
        except Exception as e:
            bump(
                queue,
                counters,
                "failed",
                "error",
                f"Unhandled error for post {source_id}: {e}",
            )
            logger.exception(f"Unhandled exception processing post {source_id}")


async def run_scrape(run_id, queue, limit, booru_source, tags, blacklist_tags, rating):

    source = SOURCES[booru_source]
    counters = {"processed": 0, "skipped": 0, "failed": 0, "total": 0}
    run_status = "done"
    new_posts = []
    failures = []
    if tags:
        tags = " ".join(tags.split())
    if blacklist_tags:
        blacklist_tags = " ".join(blacklist_tags.split())

    logger.info(
        f"Scrape started - Limit: {limit}, Rating: {rating}, Tags: [{tags}], Blacklist Tags: [{blacklist_tags}]"
    )
    run = await db.create_run(
        run_id, booru_source, tags, blacklist_tags, rating, limit, "running"
    )

    try:
        scrape_results = await source.search_posts(
            limit=limit, tags=tags, blacklist_tags=blacklist_tags, rating=rating
        )
        scrape_tags = set()
        unknown_tags = set()

        for result in scrape_results:
            counters["total"] += 1
            log_and_queue(queue, "debug", "Total increased", counters)
            if await db.post_exists(source=booru_source, source_post_id=result["id"]):
                counters["skipped"] += 1
                log_and_queue(
                    queue,
                    "debug",
                    f"Gelbooru: {result['id']} already exists in DB. Skipping",
                    counters,
                )
            elif await db.md5_exists(md5=result["md5"]):
                counters["skipped"] += 1
                log_and_queue(
                    queue,
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
                    log_and_queue(
                        queue,
                        "debug",
                        f"Gelbooru: {result['id']} is already claimed by another run. Skipping",
                        counters,
                    )
                else:
                    new_posts.append((db_entry, result))
                    scrape_tags.update(result["tags"].split(" "))
                    logger.debug(result["tags"].split(" "))
                    logger.debug(f"Appended: {result}")

        for tag in scrape_tags:
            if not await db.tag_exists(tag):
                unknown_tags.add(tag)
        log_and_queue(queue, "info", f"Total unknown tags: {unknown_tags}", counters)

        if unknown_tags:
            logger.info("Grabbing unknown tags")
            new_tags = await source.grab_tags(unknown_tags)
            for tag in new_tags:
                logger.debug(f"Tag data: {tag}")
                await db.store_tag(tag_name=tag["name"], tag_type=tag["type"])
                await szurubooru.sync_tag(
                    tag_name=tag["name"],
                    tag_category=constants.GELBOORU_TAG_TYPES.get(
                        tag["type"], "general"
                    ),
                )

        semaphore = asyncio.Semaphore(int(config.CONCURRENT_UPLOADS))
        await asyncio.gather(
            *[
                process_post(
                    booru_source, db_entry, post, semaphore, queue, counters, failures
                )
                for db_entry, post in new_posts
            ],
            return_exceptions=True,
        )
        log_and_queue(
            queue,
            "info",
            f"Total: {counters['total']} - Processed: {counters['processed']} - Skipped: {counters['skipped']} - Failed: {counters['failed']}",
            counters,
        )
    except Exception as e:
        run_status = "error"
        logger.exception(f"Scrape failed: {e}")  # noqa: TRY401
    finally:
        await db.release_claims([db_entry for db_entry, _ in new_posts])
        if failures:
            log_and_queue(
                queue,
                "warning",
                f"{len(failures)} post(s) could not be uploaded and need handling by hand:",
                counters,
            )
            for failure in failures:
                log_and_queue(
                    queue,
                    "warning",
                    f"  {failure['id']} - {failure['error']}",
                    counters,
                )
                log_and_queue(queue, "warning", f"    url:  {failure['url']}", counters)
                log_and_queue(
                    queue, "warning", f"    tags: {failure['tags']}", counters
                )
        await db.update_run(
            run,
            counters["total"],
            counters["processed"],
            counters["skipped"],
            counters["failed"],
            run_status,
        )
        queue.put_nowait({"status": run_status})
        del job_queues[run_id]


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/sources")
async def get_sources():
    return list(SOURCES.keys())


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    runs = await db.get_runs()
    return templates.TemplateResponse(
        request=request, name="history.html", context={"runs": runs}
    )


@app.get("/scrape/progress/{job_id}", response_class=EventSourceResponse)
async def scrape_progress(job_id):
    queue = job_queues[job_id]
    while True:
        msg = await queue.get()
        yield msg
        if msg.get("status") in ("done", "error"):
            break


@app.post("/scrape")
async def scrape(background_tasks: BackgroundTasks, scrape_request: Scrape):
    job_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    job_queues[job_id] = queue
    scrape_request.rating = scrape_request.rating or None
    background_tasks.add_task(
        run_scrape,
        job_id,
        queue,
        scrape_request.limit,
        scrape_request.source,
        scrape_request.tags,
        scrape_request.blacklist_tags,
        scrape_request.rating,
    )
    return {"job_id": job_id}
