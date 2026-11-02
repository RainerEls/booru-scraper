# Upgrading

Update your checkout and restart. The app brings the database up to date when it starts, including databases from versions older than the migrations, and refuses to start if a migration fails.

```bash
git pull
docker compose up -d
```

Add `--build` if you build the image from source.

## Going back to an older version

Undo its schema changes first, while the newer version is still installed:

```bash
docker compose exec booru-scraper alembic history
docker compose exec booru-scraper alembic downgrade <revision the older version shipped>
```

Then switch versions. Downgrades keep your data, except for what a column being dropped held. Going back past the settings page drops saved settings, so copy them to `.env` first.
