import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Annotated, Literal

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import auth, config, db, http, integrity, notify, szurubooru
from app.db import utcnow
from app.http import run_log
from app.sources.danbooru import DanbooruSource
from app.sources.e621 import E621Source
from app.sources.gelbooru import GelbooruSource
from app.sources.rule34 import Rule34Source
from app.sources.yandere import YandereSource

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.getLevelName(config.LOGLEVEL))
logging.getLogger("httpx").setLevel(logging.WARNING)


class HideHealthChecks(logging.Filter):
    def filter(self, record):
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(HideHealthChecks())

SOURCES = {
    "gelbooru": GelbooruSource(),
    "danbooru": DanbooruSource(),
    "yandere": YandereSource(),
    "e621": E621Source(),
    "rule34": Rule34Source(),
}
FEW_KNOWN_TAGS = 3000
SCHEDULE_CHECK_SECONDS = 60
SCHEDULED_STOP_AFTER_KNOWN = 100
PERMANENT_UPLOAD_ERRORS = ("DownloadTooLargeError", "InvalidPostContentError")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    notify.setup()
    await db.migrate()
    await db.release_stale_claims()
    await db.mark_interrupted_work()
    scheduler = asyncio.create_task(run_schedules())

    yield

    scheduler.cancel()
    for job in jobs.values():
        job.stop("interrupted")
    await asyncio.gather(*[job.task for job in jobs.values() if job.task], return_exceptions=True)
    await http.close_clients()
    await db.close_db()


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


class Scrape(BaseModel):
    source: Literal[*SOURCES]
    limit: int = Field(ge=1, le=10000)
    tags: str | None = None
    blacklist_tags: str | None = None
    rating: Literal["safe", "sketchy", "unsafe", ""] | None = None


class NewSchedule(Scrape):
    interval_hours: int = Field(ge=1, le=720)


class ScheduleState(BaseModel):
    enabled: bool


app = FastAPI(lifespan=lifespan)
app.add_middleware(auth.RequireLogin)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def duration(run):
    if not run.finished_at:
        return "—"
    minutes, seconds = divmod(int((run.finished_at - run.created_at).total_seconds()), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"


def next_run(schedule):
    if schedule.last_run_at is None:
        return utcnow()
    return schedule.last_run_at + timedelta(hours=schedule.interval_hours)


def local_time(moment):
    return moment.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")


def repeatable(run):
    return bool(run.post_limit) and run.retry_of is None


templates.env.globals["logged_in"] = auth.logged_in
templates.env.tests["repeatable"] = repeatable
templates.env.filters["duration"] = duration
templates.env.filters["local_time"] = local_time
templates.env.filters["next_run"] = next_run


def response_json(r):
    try:
        return r.json()
    except ValueError:
        return {}


async def process_post(source, db_entry, post, semaphore, job):
    async with semaphore:
        try:
            r = await szurubooru.upload_post(post, source.safety(post), source.source_links(post))
            body = response_json(r)

            if r.status_code == 200:
                await db.finish_post(db_entry, "processed", body["id"])
                job.count("processed", f"Post {post['id']} uploaded")
            elif r.status_code == 400 and body.get("name") == "PostAlreadyUploadedError":
                other_post = body["otherPostId"]
                await szurubooru.merge_into_post(other_post, post["tags"].split(), source.source_links(post))
                await db.finish_post(db_entry, "merged")
                job.count("skipped", f"Post {post['id']} already uploaded, merged into booru post {other_post}")
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
        except Exception as e:
            logger.exception(f"Unhandled exception processing post {post['id']}")
            job.failures.append({"id": post["id"], "error": repr(e), "url": post["file_url"], "tags": post["tags"]})
            job.count("failed", f"Unhandled error for post {post['id']}: {e!r}", "error")
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
    except Exception as e:
        logger.exception("Search failed")
        job.error(f"Search failed, continuing with the {len(new_posts)} new post(s) found: {e!r}")


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
            job.log("warning", f"Tag {tag['name']} failed to sync: {e!r}")
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
    except httpx.HTTPError as e:
        job.log("warning", f"Post {post['id']} could not be merged into booru post {booru_post}, retried next run: {e!r}")
    else:
        await db.finish_post(db_entry, "merged")
        job.log("info", f"Post {post['id']} merged into booru post {booru_post}")


async def merge_duplicates(job, source, duplicates):
    if not duplicates:
        return
    job.start_phase("Merging duplicates", len(duplicates))
    for db_entry, post in duplicates:
        await merge_duplicate(job, source, db_entry, post)
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
        if source.slow_tag_lookup and await db.count_tags() < FEW_KNOWN_TAGS:
            job.log(
                "warning",
                f"{spec.source} is rate limited and looks up many tags one at a time, "
                "so runs on a fresh instance can be slow until more tags are known.",
            )
        try:
            await szurubooru.ensure_tag_categories(source.tag_categories.values())
        except httpx.HTTPError as e:
            job.log("warning", f"Could not set up tag categories: {e!r}")

        job.log("info", f"Searching {spec.source}")
        await find_new_posts(job, spec, new_posts, duplicates)
        await sync_unknown_tags(job, source, new_posts + duplicates)

        job.start_phase("Uploading", len(new_posts))
        semaphore = asyncio.Semaphore(config.CONCURRENT_UPLOADS)
        await asyncio.gather(*[process_post(source, entry, post, semaphore, job) for entry, post in new_posts])
        await merge_duplicates(job, source, duplicates)
        job.log("info", " - ".join(f"{field.title()}: {count}" for field, count in job.counters.items()))
    except asyncio.CancelledError:
        job.status = job.stop_status or "stopped"
        job.log("warning", job.status.capitalize())
    except Exception as e:
        logger.exception("Scrape failed")
        job.error(f"Scrape failed: {e!r}")
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


def check_busy(checks):
    return any(check.status in db.CHECK_BUSY for check in checks)


@app.get("/health")
async def health():
    try:
        await db.ping()
    except Exception:
        logger.exception("Health check could not reach the database")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok"}


def login_page(request, error=None, status_code=200):
    return templates.TemplateResponse(
        request=request, name="login.html", context={"error": error}, status_code=status_code
    )


@app.get("/login", response_class=HTMLResponse)
async def show_login(request: Request):
    if not auth.enabled() or auth.logged_in(request):
        return RedirectResponse("/", status_code=303)
    return login_page(request)


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request):
    if not auth.enabled():
        return RedirectResponse("/", status_code=303)
    client = request.client.host if request.client else ""
    if auth.locked_out(client):
        return login_page(request, "Too many failed attempts. Try again in a few minutes.", 429)
    if not auth.password_matches(await auth.submitted_password(request)):
        auth.record_failure(client)
        return login_page(request, "Wrong password.", 401)
    auth.clear_failures(client)
    response = RedirectResponse("/", status_code=303)
    auth.start_session(response)
    return response


@app.post("/logout")
async def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    auth.end_session(request, response)
    return response


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "always_blacklisted": config.BLACKLIST_TAGS,
            "schedules": await db.get_schedules(),
            "stop_after_known": SCHEDULED_STOP_AFTER_KNOWN,
        },
    )


@app.get("/api/sources")
async def get_sources():
    return [
        {"name": name, "configured": source.configured, "hint": source.search_hint}
        for name, source in SOURCES.items()
    ]


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
            "check_running": check_busy(checks),
            "repullable": integrity.REPULLABLE,
        },
    )


@app.post("/api/integrity-checks", status_code=202)
async def start_integrity_check(background_tasks: BackgroundTasks):
    if check_busy(await db.get_checks()):
        raise HTTPException(status_code=409, detail="A check is already running")
    background_tasks.add_task(integrity.run_check, await db.create_check())
    return {}


@app.post("/api/integrity-checks/{check_id}/repull", status_code=202)
async def start_repull(check_id: int, background_tasks: BackgroundTasks):
    checks = await db.get_checks()
    if check_busy(checks):
        raise HTTPException(status_code=409, detail="A check is already running")
    if not checks or checks[0].id != check_id or not checks[0].report:
        raise HTTPException(status_code=409, detail="Only the latest finished check can be re-pulled")
    await db.mark_check_repulling(check_id)
    background_tasks.add_task(integrity.run_repull, check_id, checks[0].report, SOURCES)
    return {}


@app.get("/api/scrapes/active")
async def active_scrapes():
    return active_jobs()


def active_jobs():
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


def start_job(spec):
    for job_id in [job_id for job_id, job in jobs.items() if job.finished]:
        del jobs[job_id]
    job_id = str(uuid.uuid4())
    job = Job()
    jobs[job_id] = job
    job.task = asyncio.create_task(run_scrape(job_id, job, spec))
    return job_id


def require_configured(source):
    if source not in SOURCES or not SOURCES[source].configured:
        raise HTTPException(status_code=422, detail=f"{source} needs its credentials in .env")


@app.post("/scrape")
async def scrape(scrape_request: Scrape):
    require_configured(scrape_request.source)
    spec = ScrapeSpec(
        source=scrape_request.source,
        limit=scrape_request.limit,
        tags=" ".join((scrape_request.tags or "").split()),
        blacklist_tags=scrape_request.blacklist_tags or "",
        rating=scrape_request.rating or None,
    )
    return {"job_id": start_job(spec)}


@app.post("/api/scrapes/{job_id}/stop", status_code=202)
async def stop_scrape(job: Annotated[Job, Depends(get_job)]):
    job.stop()
    return {}


@app.post("/api/runs/{run_id}/retry")
async def retry_run(run_id: int):
    run = await db.get_run(run_id)
    if run is None or not run.failed_posts:
        raise HTTPException(status_code=404, detail="That run has no failed posts")
    require_configured(run.source)
    await db.release_rejected(run.source, run.failed_posts)
    spec = ScrapeSpec(
        source=run.source,
        limit=len(run.failed_posts),
        tags=f"Retry of run {run_id}",
        post_ids=run.failed_posts,
        retry_of=run_id,
    )
    return {"job_id": start_job(spec)}


@app.post("/api/runs/{run_id}/again")
async def run_again(run_id: int):
    run = await db.get_run(run_id)
    if run is None or not repeatable(run):
        raise HTTPException(status_code=404, detail="That run can't be repeated")
    require_configured(run.source)
    spec = ScrapeSpec(
        source=run.source,
        limit=run.post_limit,
        tags=run.tags or "",
        blacklist_tags=run.blacklist_tags or "",
        rating=run.rating,
    )
    return {"job_id": start_job(spec)}


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
        source = SOURCES.get(schedule.source)
        if schedule.enabled and source and source.configured and next_run(schedule) <= utcnow():
            await start_schedule(schedule)
            return


async def run_schedules():
    while True:
        await asyncio.sleep(SCHEDULE_CHECK_SECONDS)
        try:
            await start_due_schedule()
        except Exception:
            logger.exception("Could not start a scheduled scrape")


@app.post("/api/schedules", status_code=201)
async def create_schedule(new_schedule: NewSchedule):
    require_configured(new_schedule.source)
    schedule_id = await db.create_schedule(
        source=new_schedule.source,
        tags=" ".join((new_schedule.tags or "").split()),
        blacklist_tags=new_schedule.blacklist_tags or "",
        rating=new_schedule.rating or None,
        post_limit=new_schedule.limit,
        interval_hours=new_schedule.interval_hours,
    )
    return {"id": schedule_id}


@app.patch("/api/schedules/{schedule_id}", status_code=204)
async def update_schedule(schedule_id: int, state: ScheduleState):
    await db.set_schedule_enabled(schedule_id, state.enabled)


@app.delete("/api/schedules/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: int):
    await db.delete_schedule(schedule_id)


@app.post("/api/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: int):
    schedule = await db.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Unknown schedule")
    require_configured(schedule.source)
    if active_jobs():
        raise HTTPException(status_code=409, detail="A scrape is already running")
    return {"job_id": await start_schedule(schedule)}
