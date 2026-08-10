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

from .sources import gelbooru

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.getLevelName(config.LOGLEVEL))

job_queues = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await http.create_clients()
    await db.init_db()

    yield

    await http.close_clients()
    await db.close_db()


class Scrape(BaseModel):
    source: str
    limit: int
    tags: str | None = None
    blacklist_tags: str | None = None
    rating: Literal["safe", "sketchy", "unsafe", ""] | None = None


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


async def process_post(post, semaphore, lock, queue, counters):
    async with semaphore:
        contentUrl = post["file_url"]
        tags = post["tags"]
        safety = constants.GELBOORU_RATINGS_CONVERSIONS.get(
            post["rating"], "sketchy"
        )
        booru_source = "gelbooru"
        source = post["source"]
        source_id = post["id"]
        md5 = post["md5"]

        # Store in db first
        db_entry = await db.store_post(
            booru_source, source_id, None, md5, contentUrl, "queued"
        )
        logger.debug(f"{db_entry} entry added")
        # Attempt to upload to szurubooru
        r = await szurubooru.upload_post(contentUrl, tags, safety, source)
        logger.debug("Attempting to upload post to szurubooru")

        # Succeed: add the szurubooru post id to the db entry and update the entry status
        if r.status_code == 200:
            async with lock:
                counters['processed'] += 1
                queue.put_nowait(
                    {
                        "processed": counters['processed'],
                        "skipped": counters['skipped'],
                        "failed": counters['failed'],
                        "total": counters['total'],
                        "log": "post processed",
                        "level": "debug",
                    }
                )
                logger.info("Szurubooru post creation succeeded.")
            await db.add_szurubooru_post_id(db_entry, r.json()["id"])
            await db.update_post_status(booru_source, source_id, "processed")
        # Fail: set status to failure
        elif (
            r.status_code == 400 and r.json()["name"] == "PostAlreadyUploadedError"
        ):
            async with lock:
                counters['skipped'] += 1
                queue.put_nowait(
                    {
                        "processed": counters['processed'],
                        "skipped": counters['skipped'],
                        "failed": counters['failed'],
                        "total": counters['total'],
                        "log": "Skipped",
                        "level": "debug",
                    }
                )
            await db.update_post_status(booru_source, source_id, "skipped")
        else:
            async with lock:
                counters['failed'] += 1
                queue.put_nowait(
                    {
                        "processed": counters['processed'],
                        "skipped": counters['skipped'],
                        "failed": counters['failed'],
                        "total": counters['total'],
                        "log": "post failed to process",
                        "level": "debug",
                    }
                )
            logger.debug(
                f"Szurubooru creation failed with status code: {r.status_code}"
            )
            await db.update_post_status(booru_source, source_id, "failed")

async def run_scrape(
    run_id, queue, limit, tags, blacklist_tags, rating
):  # TODO: add rate_limit, source, etc.

    total = 0
    processed = 0
    skipped = 0
    failed = 0
    run_status = "done"
    if tags:
        tags = " ".join(tags.split())
    if blacklist_tags:
        blacklist_tags = " ".join(blacklist_tags.split())

    logger.info("Scrape started")
    run = await db.create_run(run_id, "gelbooru", tags, blacklist_tags, rating, "running")

    try:
        # Send search with user parameters
        scrape_results = await gelbooru.search_posts(
            limit=limit, tags=tags, blacklist_tags=blacklist_tags, rating=rating
        )
        scrape_tags = set()
        unknown_tags = set()
        new_posts = []

        # Check if results exist in db by source->source_id: True = pass
        for result in scrape_results:
            total += 1
            queue.put_nowait(
                {
                    "processed": processed,
                    "skipped": skipped,
                    "failed": failed,
                    "total": total,
                    "log": f"Total {total}",
                    "level": "debug",
                }
            )
            if await db.post_exists(source="gelbooru", source_post_id=result["id"]):
                skipped += 1
                queue.put_nowait(
                    {
                        "processed": processed,
                        "skipped": skipped,
                        "failed": failed,
                        "total": total,
                        "log": "post already exists in db. Skipping",
                        "level": "debug",
                    }
                )
                logger.debug(
                    "Gelbooru: " + str(result["id"]) + " already exists in db. Skipping"
                )
            # MD5 check: True = store_post() skipped
            elif await db.md5_exists(md5=result["md5"]):
                skipped += 1
                queue.put_nowait(
                    {
                        "processed": processed,
                        "skipped": skipped,
                        "failed": failed,
                        "total": total,
                        "log": "md5 already exists in db. Skipping",
                        "level": "debug",
                    }
                )
                logger.debug(
                    "MD5: "
                    + str(result["md5"])
                    + " already exists in db. Adding source entry and skipping."
                )
                await db.store_post(
                    source="gelbooru",
                    source_post_id=result["id"],
                    szurubooru_post_id=None,
                    md5=result["md5"],
                    image_url=result["file_url"],
                    status="skipped",
                )
            # Otherwise: Begin processing
            else:
                # Pool tags into a set
                scrape_tags.update(result["tags"].split(" "))
                logger.debug(result["tags"].split(" "))
                # Pool new posts into a list
                new_posts.append(result)
                logger.debug(f"Appended: {result}")

        # Check each tag against db
        for tag in scrape_tags:
            if not await db.tag_exists(tag):
                # Unknown tags -> new set
                unknown_tags.add(tag)

        # Run grab_tags with new set
        if unknown_tags:
            logger.info("Grabbing unknown tags")
            new_tags = await gelbooru.grab_tags(unknown_tags)
            # For loop takes data from grab_tags and saves to db
            for tag in new_tags:
                logger.debug(f"Tag data: {tag}")
                await db.store_tag(tag_name=tag["name"], tag_type=tag["type"])
                await szurubooru.sync_tag(
                    tag_name=tag["name"],
                    tag_category=constants.GELBOORU_TAG_TYPES.get(
                        tag["type"], "general"
                    ),
                )

        # Process all new posts
        semaphore = asyncio.Semaphore(int(config.CONCURRENT_UPLOADS))
        lock = asyncio.Lock()
        counters = {'processed': processed, 'skipped': skipped, 'failed': failed, 'total': total}
        await asyncio.gather(*[process_post(post, semaphore, lock, queue, counters) for post in new_posts])

        processed += counters['processed']
        skipped += counters['skipped']
        failed += counters['failed']
            
    except Exception as e:
        run_status = "error"
        logger.exception(f"Scrape failed: {e}")  # noqa: TRY401
    finally:
        await db.update_run(run, total, processed, skipped, failed, run_status)
        queue.put_nowait({"status": run_status})


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/history", response_class=HTMLResponse)
async def history(request: Request):
    runs = db.get_runs()
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
async def scrape(background_tasks: BackgroundTasks, scrape: Scrape):
    job_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    job_queues[job_id] = queue
    scrape.rating = scrape.rating or None
    background_tasks.add_task(
        run_scrape,
        job_id,
        queue,
        scrape.limit,
        scrape.tags,
        scrape.blacklist_tags,
        scrape.rating,
    )
    return {"job_id": job_id}
