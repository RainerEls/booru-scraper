# Sources

| Source | Credentials | Limits to know about |
| --- | --- | --- |
| Gelbooru | Required: user ID and API key | It rejects unauthenticated requests |
| Danbooru | Optional: username and API key | 2 search tags without a paid account. See below |
| e621 | Optional: username and API key | |
| Rule34 | Required: user ID and API key | 60 requests a minute. Slow first runs. See below |
| Yandere | Optional: username and password hash | Bad credentials can't be detected |

Each source has a **Seconds between requests** setting, 1 by default.

## Danbooru

Without a paid account, Danbooru allows 2 search tags plus a rating, and `order:` counts as one of the 2. The app reads your account's limit from Danbooru, so paid accounts need no setting.

You can still search for more tags. Danbooru lists every tag on each post it returns, so the app sends the 2 tags that match the fewest posts and checks the rest itself. For example, `hatsune_miku cat_ears long_hair blue_eyes` sends `hatsune_miku cat_ears` and filters about 2,500 posts, which takes under a minute.

- More tags make a search slower rather than more precise on Danbooru's side, because the app has to read every post the 2 sent tags match. Runs log how far they've got.
- This works for plain tags and `-tag`. Wildcards, `~tag` and metatags like `score:>10` have to fit within the limit. Searches using `or` or brackets are sent as they are.
- Sorted searches can only reach the first 200,000 posts, so later matches are missed. The run warns you when this applies.
- Danbooru times out sorting tags with millions of posts, such as `1girl order:score`. Add a rarer tag or remove `order:`.

The blacklist is always applied by the app, so it doesn't count towards the limit.

Danbooru asks for around 1 read request a second in longer sessions, which is the default.

## Rule34

The user ID is the number from the account page, not the username. Tag lookups go to Gelbooru first, in batches, and anything left is looked up on Rule34 one at a time, so first runs are slow until the app knows more tags.

## Related posts

Posts that a source marks as parent and child are linked as relations on Szurubooru after each run, along with any siblings that share a parent. Posts are matched by MD5, so a relation still works if the image came from another source or was uploaded by hand.

- Parents aren't downloaded just to complete a family. If the parent isn't on the booru, its children are still linked to each other, and to the parent if it arrives later.
- Links are only ever added, so relations you've made yourself stay.
- A run that gets interrupted or can't reach Szurubooru finishes the linking on the next run from that source.
- Posts scraped before this feature don't have a parent on record. A new child still links to its older parent, but a new parent can't find its older children.
- Danbooru's animated ugoira posts aren't linked, because the file on the booru is a converted video with a different MD5.
- Linking relies on the `md5:` search, so it needs a Szurubooru version that has it. 2.5 does.
