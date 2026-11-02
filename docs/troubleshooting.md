# Troubleshooting

## Runs that stop straight away

A run stops, with the reason in its log and a `scrape_failed` notification, when:

- Szurubooru's URL, user or token isn't set, it can't be reached, or it refuses them
- The source needs a key that isn't set
- A source or Szurubooru refuses the request with HTTP 401 or 403 at any point. Posts not yet uploaded are picked up by a later run

## Errors that don't stop a run

If a search fails partway, the posts already found are still uploaded. A post or tag that fails is reported and tried again by a later run. Rate limits and server errors are retried first.

## Known quirks

- Yandere ignores bad credentials and answers as if you weren't logged in, so a wrong key there can't be detected.
- A Szurubooru account that can't edit tags or posts gets one warning per run rather than a stopped run, because its uploads can still work. On Szurubooru's default settings, a `regular` account can create tags but can't change an existing tag's category.

## Health check

`GET /health` answers 200 while the app can reach its database and 503 when it can't. The compose file polls it, so `docker ps` shows the app's health, and monitoring tools can poll it too.
