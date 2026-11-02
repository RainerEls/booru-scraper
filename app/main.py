import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app import auth, config, db, http, integrity, scrape, settings
from app.scrape import SOURCES, Job, ScrapeSpec, active_jobs, jobs, next_run, start_job, start_schedule

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.getLevelName(config.LOGLEVEL))
logging.getLogger("httpx2").setLevel(logging.WARNING)


class HideHealthChecks(logging.Filter):
    def filter(self, record):
        return "/health" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(HideHealthChecks())


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    await db.migrate()
    await settings.load()
    await scrape.apply_settings()
    await db.release_stale_claims()
    await db.mark_interrupted_work()
    scheduler = asyncio.create_task(scrape.run_schedules())

    yield

    scheduler.cancel()
    await scrape.interrupt_jobs()
    await http.close_clients()
    await db.close_db()


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


class SettingsChange(BaseModel):
    values: dict[str, str] = {}
    cleared: list[str] = []


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


def local_time(moment):
    return moment.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M")


def repeatable(run):
    return bool(run.post_limit) and run.retry_of is None


templates.env.globals["logged_in"] = auth.logged_in
templates.env.globals["missing_szurubooru"] = config.missing_szurubooru
templates.env.tests["repeatable"] = repeatable
templates.env.filters["duration"] = duration
templates.env.filters["local_time"] = local_time
templates.env.filters["next_run"] = next_run


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
            "stop_after_known": scrape.SCHEDULED_STOP_AFTER_KNOWN,
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
    require_szurubooru()
    if check_busy(await db.get_checks()):
        raise HTTPException(status_code=409, detail="A check is already running")
    background_tasks.add_task(integrity.run_check, await db.create_check())
    return {}


@app.post("/api/integrity-checks/{check_id}/repull", status_code=202)
async def start_repull(check_id: int, background_tasks: BackgroundTasks):
    require_szurubooru()
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


def settings_editable():
    return auth.enabled() or config.ALLOW_SETTINGS_WITHOUT_LOGIN


@app.get("/settings", response_class=HTMLResponse)
async def show_settings(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"groups": settings.view(), "editable": settings_editable(), "has_key": bool(config.SETTINGS_KEY)},
    )


@app.post("/api/settings", status_code=204)
async def save_settings(request: Request, change: SettingsChange):
    if not auth.same_origin(request):
        raise HTTPException(status_code=403, detail="Cross-site request refused")
    if not settings_editable():
        raise HTTPException(status_code=403, detail="Settings are read-only without a login")
    if active_jobs() or check_busy(await db.get_checks()):
        raise HTTPException(status_code=409, detail="Wait for the running scrape or check to finish")
    try:
        await settings.save(change.values, change.cleared)
    except settings.Invalid as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    await scrape.apply_settings()


def require_szurubooru():
    if missing := config.missing_szurubooru():
        raise HTTPException(status_code=422, detail=f"Set {', '.join(missing)} in Settings first")


def require_configured(source):
    if source not in SOURCES or not SOURCES[source].configured:
        raise HTTPException(status_code=422, detail=f"{source} needs its credentials in Settings")


@app.post("/scrape")
async def begin_scrape(scrape_request: Scrape):
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
