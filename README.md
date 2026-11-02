# booru-scraper

<!-- BADGES:START -->
[![tests](https://gitea.rkahome.work/SleepyShoggoth/booru-scraper/actions/workflows/tests.yml/badge.svg)](https://gitea.rkahome.work/SleepyShoggoth/booru-scraper/actions/workflows/tests.yml) [Open tracker issues](https://gitea.rkahome.work/SleepyShoggoth/homelab-tracker/issues?q=booru-scraper&type=all&state=open) [Pending Renovate PRs](https://gitea.rkahome.work/SleepyShoggoth/booru-scraper/pulls?type=all&state=open&q=renovate)
<!-- BADGES:END -->

A small web app for pulling posts from booru-style image boards and uploading them into a self-hosted [szurubooru](https://github.com/rr-/szurubooru) instance. Kick off a scrape from a browser, watch progress live, and let it handle tag syncing and dedup against what's already in your booru.

## Features

- Web UI for starting scrape jobs (source, rating, tag/blacklist filters, how many new posts to fetch)
- Live progress over server-sent events, with a running log and pass/fail counts
- Dedup against your szurubooru instance by MD5 and by source post ID, so re-running a scrape won't create duplicates. When another source has the same file, its tags and link are added to the existing post
- Automatically pulls in and syncs any tags it doesn't already know about
- Run history stored in Postgres, with each run's log and buttons to retry the posts that failed or run the same search again
- Saved searches that run on a schedule, one at a time. Each picks up what's new and stops after 100 known posts in a row, so it doesn't crawl a whole tag every run
- A stop button for a running scrape, which keeps the tags synced so far
- Light and dark themes that follow your system, with a switch in the nav
- Optional password login
- Optional notifications through Apprise, with a choice of which events to send
- A restart saves the log of any running scrape and marks it interrupted
- `BLACKLIST_TAGS` in `.env` applies a blacklist to every scrape, on top of the one in the form
- Integrity checks that compare uploaded posts with what's on szurubooru, telling deliberately deleted or merged posts apart from ones that went missing. They only report, with a separate re-pull button for posts that are missing or damaged, which never touches deleted or merged ones
- Pluggable source architecture, with Gelbooru, Danbooru, Yandere, e621 and Rule34 wired up

## Stack

FastAPI + SQLAlchemy (async) on the backend, Postgres for storage, server-rendered Jinja templates with SSE for the frontend. Runs as two containers via Docker Compose.

## Setup

1. Copy the example env file and fill in your details:

   ```bash
   cp .env.example .env
   ```

   You'll need a `SZURUBOORU_BASE_URL`, a `SZURUBOORU_USER_ID`, and a `SZURUBOORU_API_TOKEN` for an account with permission to create posts. You'll also need a `GELBOORU_API_KEY` and `GELBOORU_USER_ID` — Gelbooru's API rejects unauthenticated requests outright, so these aren't optional. `YANDERE_USER_ID` and `YANDERE_PASSWORD_HASH` are optional, as are `DANBOORU_USER_ID`/`DANBOORU_API_KEY` and `E621_USER_ID`/`E621_API_KEY`. Without a paid account, Danbooru allows 2 search tags plus a rating; the blacklist is applied locally and doesn't count. `RULE34_USER_ID` (the number from the account page, not the username) and `RULE34_API_KEY` are required for Rule34, which allows 60 requests a minute. Its tag lookups go to Gelbooru first, in batches, and anything left is looked up on Rule34 one at a time, so its first runs are slow.

   Set `TZ` to your timezone, such as `America/Chicago`, to show times in it. Times are stored in UTC either way.

2. Bring it up:

   ```bash
   docker compose up -d --build
   ```

   The app will be available at `http://localhost:8192`, and Postgres data persists to a Docker volume.

## Login

Login is optional and off by default, so anyone who can reach the port can use the app until you turn it on. Set `APP_PASSWORD` in `.env` and restart to require a password. If the password contains a `$`, put it in single quotes.

This is meant for a trusted network. Over plain HTTP, someone able to watch the traffic can read the password, and there's no MFA, so anything reachable from the internet should sit behind HTTPS and a proper auth proxy. If you already have an auth proxy, leave `APP_PASSWORD` empty and don't publish the app's port.

- Once the app is served over HTTPS, set `COOKIE_SECURE=true`. Leave it off for plain HTTP, or browsers will refuse the login cookie.
- Sessions last a week and live in memory, so a restart logs everyone out. Changing the password needs a restart, so it does too.
- Five wrong passwords lock that address out of new logins for five minutes. Behind a proxy, every client can look like the same address.
- If a proxy sits in front of the app, it has to pass the original `Host` header, or requests that change anything are refused as cross-site.
- `/health` stays open so monitoring keeps working. It only reports whether the app can reach its database.

## Notifications

Notifications are optional and off until you add a service. Set `APPRISE_URLS` to one or more [Apprise URLs](https://appriseit.com/services/), separated by commas, such as `ntfys://my-topic,discord://webhook_id/webhook_token`. These URLs often hold tokens, so treat them like any other secret. An existing [Apprise API](https://github.com/caronc/apprise-api) server works too, through `apprise://host/key`.

To send only some notifications, list them in `NOTIFY_ON`, separated by commas. Leave it empty for all of them.

| Event | Sent when |
| --- | --- |
| `scrape_finished` | A scrape you started finishes without problems |
| `schedule_finished` | A scheduled scrape uploads new posts. Runs that find nothing new stay silent |
| `scrape_failed` | Any scrape hits an error or fails to upload a post |
| `scrape_interrupted` | A restart cuts a scrape short |
| `integrity_problems` | An integrity check finds missing or damaged posts, or can't finish |

Stopping a scrape yourself sends nothing. A failed notification is logged and never affects the scrape.

## Health check

`GET /health` answers 200 while the app can reach its database and 503 when it can't. The compose file polls it, so `docker ps` shows the app's health, and monitoring tools can poll it too.

## Upgrading

Pull the new version and restart it. The app brings the database up to date when it starts, including databases from versions older than the migrations, and refuses to start if a migration fails.

To go back to an older version, undo its schema changes first, while the newer version is still installed:

```bash
docker compose exec booru-scraper alembic history
docker compose exec booru-scraper alembic downgrade <revision the older version shipped>
```

Then switch versions. Downgrades keep your data, except for what a column being dropped held.

## Running locally without Docker

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

You'll still need a Postgres instance reachable with the credentials in `.env`.

## Tests

```bash
pip install -r requirements-dev.txt
ruff check app tests
pytest
```

## Changing the schema

Change the models in `app/db.py`, then generate a migration against a database that's up to date and read what it wrote:

```bash
alembic revision --autogenerate -m "describe the change"
```

Autogenerate misses some changes, such as renames, so check it. Write a `downgrade()` too. CI runs every migration up and back down, and fails if the migrations don't match the models.

## Adding a source

Each source is one module in `app/sources/`. It implements `search_posts()` and `grab_tags()` from `base.py` (`grab_notes()` is optional), creates its `session` with `http.client()`, sets `post_page` (the URL of a post, saved as its source), and optionally `credentials` (required values, which grey it out in the UI if missing) and `search_hint` (shown under the Tags field). It also sets three maps: `ratings` (its ratings to `safe`/`sketchy`/`unsafe`), `rating_terms` (the reverse, as search terms) and `tag_categories` (its tag types to szurubooru categories, which are created on the booru if missing). `base.py` has paging helpers for the common API styles.

Register it in `SOURCES` in `app/scrape.py` and it shows up in the UI's source dropdown. Add its credentials to `app/config.py` and `.env.example`; its rate limit is read from `<NAME>_RATE_LIMIT` through `config.rate_limit()`.

## Recent changes

<!-- CHANGES:START -->
_No releases yet._ [Releases](https://gitea.rkahome.work/SleepyShoggoth/booru-scraper/releases)
<!-- CHANGES:END -->

## Contributing

<!-- CONTRIBUTING:START -->
Feature branch → pull request → conventional commits → CI releases and deploys. See [CONTRIBUTING.md](https://gitea.rkahome.work/SleepyShoggoth/actions/src/branch/main/CONTRIBUTING.md) in `actions`.
<!-- CONTRIBUTING:END -->

## License

AGPL-3.0 — see [LICENSE](LICENSE).

The fonts in `app/static/fonts` are under the SIL Open Font License, with each font's license file next to it. The icons in `app/static/icons` are from [Lucide](https://lucide.dev), under its ISC license.
