# booru-scraper

A small web app for pulling posts from booru-style image boards and uploading them into a self-hosted [szurubooru](https://github.com/rr-/szurubooru) instance. Kick off a scrape from a browser, watch progress live, and let it handle tag syncing and dedup against what's already in your booru.

## Features

- Web UI for starting scrape jobs (source, rating, tag/blacklist filters, post limit)
- Live progress over server-sent events, with a running log and pass/fail counts
- Dedup against your szurubooru instance by MD5 and by source post ID, so re-running a scrape won't create duplicates
- Automatically pulls in and syncs any tags it doesn't already know about
- Run history stored in Postgres
- Pluggable source architecture, though Gelbooru is the only one wired up right now — it's the best of the boorus to scrape from an API standpoint. A Yandere source exists in `app/sources/` but isn't registered yet

## Stack

FastAPI + SQLAlchemy (async) on the backend, Postgres for storage, server-rendered Jinja templates with SSE for the frontend. Runs as two containers via Docker Compose.

## Setup

1. Copy the example env file and fill in your details:

   ```bash
   cp .env.example .env
   ```

   You'll need a `SZURUBOORU_BASE_URL`, a `SZURUBOORU_USER_ID`, and a `SZURUBOORU_API_TOKEN` for an account with permission to create posts. You'll also need a `GELBOORU_API_KEY` and `GELBOORU_USER_ID` — Gelbooru's API rejects unauthenticated requests outright, so these aren't optional.

2. Bring it up:

   ```bash
   docker compose up -d --build
   ```

   The app will be available at `http://localhost:8192`, and Postgres data persists to a Docker volume.

## Running locally without Docker

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

You'll still need a Postgres instance reachable with the credentials in `.env`.

## Adding a source

Sources implement `search_posts()` and `grab_tags()` from `app/sources/base.py`. Register a new one in the `SOURCES` dict in `app/main.py` and it'll show up in the UI's source dropdown automatically. You'll also want to add a tag-type map and a rating-conversion map for it in `app/constants.py` — see `GELBOORU_TAG_TYPES`/`GELBOORU_RATINGS_CONVERSIONS` for the pattern.

PRs for other sources (Danbooru, Rule34, etc.) are welcome — `Source` in `constants.py` already has a `DANBOORU` entry stubbed in, so that one's a good starting point. Keep rate limiting in mind (see `YANDERE_RATE_LIMIT`/`GELBOORU_RATE_LIMIT` in `app/config.py` for how existing sources configure theirs) and make sure ratings/tag types get mapped onto the `safe`/`sketchy`/`unsafe` and general/artist/copyright/character/metadata conventions the rest of the app expects.

## License

AGPL-3.0 — see [LICENSE](LICENSE).
