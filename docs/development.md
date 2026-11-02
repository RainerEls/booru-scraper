# Development

The backend is FastAPI and async SQLAlchemy, with Postgres for storage. The frontend is server-rendered Jinja templates, with server-sent events for live progress.

## Running without Docker

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

Each source is one module in `app/sources/`.

1. Implement `search_posts()` and `grab_tags()` from `base.py`. `grab_notes()` is optional. `base.py` has paging helpers for the common API styles.
2. Build the module's `session` with `http.client()` inside a `configure()` function, called on import and from `apply_settings()` in `app/scrape.py`, so saved settings apply without a restart.
3. Set `post_page`, the URL of a post, which is saved as its source.
4. Set three maps:
   - `ratings`: its ratings to `safe`, `sketchy` or `unsafe`
   - `rating_terms`: the reverse, as search terms
   - `tag_categories`: its tag types to Szurubooru categories, which are created on the booru if missing
5. Optionally set `credentials`, required values that grey the source out in the UI if missing, and `search_hint`, shown under the Tags field.
6. Register it in `SOURCES` in `app/scrape.py`, which adds it to the UI's source dropdown.
7. Add its credentials and `<NAME>_RATE_LIMIT` to `SETTINGS` in `app/config.py`, which puts them on the Settings page.
