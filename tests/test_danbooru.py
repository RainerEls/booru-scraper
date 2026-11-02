import asyncio

import httpx
import pytest
from helpers import collect, mock_client, tags_of

from app import http
from app.sources import danbooru
from app.sources.danbooru import PAGE_SIZE, DanbooruSource


def post(id, **fields):
    return {
        "id": id,
        "file_url": f"http://cdn.test/{id}.png",
        "large_file_url": f"http://cdn.test/{id}.webm",
        "file_ext": "png",
        "tag_string": "cat an_artist",
        "tag_string_general": "cat",
        "tag_string_artist": "an_artist",
        "tag_string_copyright": "",
        "tag_string_character": "",
        "tag_string_meta": "",
        "image_width": 10,
        "image_height": 20,
        **fields,
    }


def use_danbooru(monkeypatch, handler):
    monkeypatch.setattr(danbooru, "session", mock_client(handler))


def test_search_pages_by_id_and_normalises_posts(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        if len(requests) == 1:
            return httpx.Response(200, json=[post(id) for id in range(1000, 1000 - PAGE_SIZE, -1)])
        return httpx.Response(200, json=[post(5)])

    use_danbooru(monkeypatch, handler)
    posts = collect(DanbooruSource(), tags="cat", rating="sketchy")

    assert requests[0]["tags"] == "cat rating:s,q"
    assert requests[1]["page"] == f"b{1001 - PAGE_SIZE}"
    assert len(posts) == PAGE_SIZE + 1
    assert posts[0]["tags"] == "cat an_artist"
    assert (posts[0]["width"], posts[0]["height"]) == (10, 20)


def test_order_searches_use_numbered_pages(monkeypatch):
    pages = []

    def handler(request):
        pages.append(request.url.params["page"])
        return httpx.Response(200, json=[post(id) for id in range(PAGE_SIZE if len(pages) == 1 else 1)])

    use_danbooru(monkeypatch, handler)
    collect(DanbooruSource(), tags="cat order:score")

    assert pages == ["1", "2"]


def test_blacklist_is_applied_locally_and_restricted_posts_are_dropped(monkeypatch):
    posts = [post(1), post(2, tag_string="cat dog"), post(3, file_url=None)]
    del posts[2]["file_url"]
    tags_sent = []

    def handler(request):
        tags_sent.append(request.url.params["tags"])
        return httpx.Response(200, json=posts)

    use_danbooru(monkeypatch, handler)
    results = collect(DanbooruSource(), tags="cat", blacklist_tags="dog")

    assert [p["id"] for p in results] == [1]
    assert tags_sent == ["cat"]


def test_ugoira_uses_the_video_file(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(200, json=[post(1, file_ext="zip")]))

    assert collect(DanbooruSource())[0]["file_url"] == "http://cdn.test/1.webm"


def test_tag_types_come_from_the_post(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(200, json=[post(1)]))
    source = DanbooruSource()
    collect(source)

    tags = tags_of(source, ["cat", "an_artist", "unseen"])

    assert {t["name"]: t["category"] for t in tags} == {
        "cat": "general",
        "an_artist": "artist",
        "unseen": "general",
    }


def test_tag_limit_errors_explain_themselves(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(422))

    with pytest.raises(RuntimeError, match="have to fit"):
        collect(DanbooruSource(), tags="order:score score:>5")


def test_sorted_searches_too_big_for_danbooru_explain_themselves(monkeypatch):
    use_danbooru(monkeypatch, lambda request: httpx.Response(500))

    with pytest.raises(RuntimeError, match="too many posts to sort"):
        collect(DanbooruSource(), tags="cat order:score")


def over_limit_handler(sent, posts=(), limit=2, aliases=None, counts=None, pairs=None):
    def handler(request):
        path, params = request.url.path, request.url.params
        if path == "/profile.json":
            return httpx.Response(200, json={"tag_query_limit": limit})
        if path == "/tag_aliases.json":
            return httpx.Response(
                200,
                json=[
                    {"antecedent_name": name, "consequent_name": target}
                    for name, target in (aliases or {}).items()
                    if name in params["search[antecedent_name_comma]"].split(",")
                ],
            )
        if path == "/tags.json":
            return httpx.Response(
                200,
                json=[
                    {"name": name, "post_count": count}
                    for name, count in (counts or {}).items()
                    if name in params["search[name_comma]"].split(",")
                ],
            )
        if path == "/counts/posts.json":
            return httpx.Response(200, json={"counts": {"posts": (pairs or {}).get(params["tags"])}})
        sent.append(params["tags"])
        return httpx.Response(200, json=list(posts))

    return handler


def tagged(id, tags):
    return post(id, tag_string=tags)


def test_searches_within_the_limit_make_no_extra_requests(monkeypatch):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=[post(1)])

    use_danbooru(monkeypatch, handler)
    collect(DanbooruSource(), tags="cat dog", rating="safe")

    assert paths == ["/posts.json"]


def test_tags_past_the_limit_are_checked_on_each_post(monkeypatch):
    sent = []
    posts = [tagged(1, "a b c d"), tagged(2, "a b c"), tagged(3, "a b d")]
    counts = {"a": 10, "b": 20, "c": 30, "d": 40}
    use_danbooru(monkeypatch, over_limit_handler(sent, posts, counts=counts, pairs={"a b": 5}))

    results = collect(DanbooruSource(), tags="a b c d", rating="safe")

    assert sent == ["a b rating:g"]
    assert [p["id"] for p in results] == [1]


def test_the_smallest_pair_is_sent_rather_than_the_two_rarest_tags(monkeypatch):
    sent = []
    counts = {"a": 10, "b": 20, "c": 30}
    pairs = {"a b": 9, "a c": 2, "b c": 15}
    use_danbooru(monkeypatch, over_limit_handler(sent, counts=counts, pairs=pairs))

    collect(DanbooruSource(), tags="a b c")

    assert sent == ["a c"]


def test_pairs_too_big_to_count_are_avoided(monkeypatch):
    sent = []
    counts = {"a": 10, "b": 20, "c": 30}
    use_danbooru(monkeypatch, over_limit_handler(sent, counts=counts, pairs={"b c": 15}))

    collect(DanbooruSource(), tags="a b c")

    assert sent == ["b c"]


def test_only_the_rarest_tags_are_compared(monkeypatch):
    compared = []
    counts = {name: index for index, name in enumerate("abcdef", 1)}
    handler = over_limit_handler([], counts=counts)

    def recording(request):
        if request.url.path == "/counts/posts.json":
            compared.append(request.url.params["tags"])
        return handler(request)

    use_danbooru(monkeypatch, recording)
    collect(DanbooruSource(), tags="a b c d e f")

    assert compared == ["a b", "a c", "a d", "b c", "b d", "c d"]


def test_aliases_are_resolved_before_checking_posts(monkeypatch):
    sent = []
    posts = [tagged(1, "a b real_name"), tagged(2, "a b alias")]
    handler = over_limit_handler(sent, posts, aliases={"alias": "real_name"}, counts={"a": 1, "b": 2, "real_name": 3})
    use_danbooru(monkeypatch, handler)

    results = collect(DanbooruSource(), tags="a b ALIAS")

    assert sent == ["a b"]
    assert [p["id"] for p in results] == [1]


def test_negated_tags_are_checked_locally_once_over_the_limit(monkeypatch):
    sent = []
    posts = [tagged(1, "a b"), tagged(2, "a b real_name")]
    use_danbooru(monkeypatch, over_limit_handler(sent, posts, aliases={"alias": "real_name"}))

    results = collect(DanbooruSource(), tags="a b -alias")

    assert sent == ["a b"]
    assert [p["id"] for p in results] == [1]


def test_order_uses_a_slot_and_warns_when_matches_pass_the_page_cap(monkeypatch):
    sent = []
    messages = []
    token = http.run_log.set(lambda level, message: messages.append((level, message)))
    counts = {"a": 300_000, "b": 500_000}
    posts = [tagged(1, "a b")]
    use_danbooru(monkeypatch, over_limit_handler(sent, posts, counts=counts))
    try:
        results = collect(DanbooruSource(), tags="a b order:score")
    finally:
        http.run_log.reset(token)

    assert sent == ["order:score a"]
    assert [p["id"] for p in results] == [1]
    assert any(level == "warning" and "200000" in message for level, message in messages)


def test_paid_accounts_send_more_tags_and_detect_their_limit(monkeypatch):
    sent = []
    counts = {"a": 10, "b": 20, "c": 30, "d": 40}
    use_danbooru(monkeypatch, over_limit_handler(sent, limit=3, counts=counts))

    collect(DanbooruSource(), tags="a b c d")
    collect(DanbooruSource(), tags="a b c")

    assert sent == ["a b c", "a b c"]


def test_metatags_and_wildcards_stay_on_the_server(monkeypatch):
    sent = []
    posts = [tagged(1, "b c"), tagged(2, "b")]
    use_danbooru(monkeypatch, over_limit_handler(sent, posts, counts={"b": 1, "c": 2}))

    results = collect(DanbooruSource(), tags="a* score:>5 b c")

    assert sent == ["a* score:>5"]
    assert [p["id"] for p in results] == [1]


def test_tags_that_only_look_like_metatags_are_filtered(monkeypatch):
    sent = []
    posts = [tagged(1, "a b :d"), tagged(2, "a b")]
    use_danbooru(monkeypatch, over_limit_handler(sent, posts, counts={"a": 1, "b": 2, ":d": 3}, pairs={"a b": 1}))

    results = collect(DanbooruSource(), tags="a b :d")

    assert sent == ["a b"]
    assert [p["id"] for p in results] == [1]


def test_too_many_metatags_are_left_for_danbooru_to_refuse(monkeypatch):
    sent = []
    use_danbooru(monkeypatch, over_limit_handler(sent))

    collect(DanbooruSource(), tags="a* score:>5 order:score")

    assert sent == ["a* score:>5 order:score"]


def test_grouped_searches_are_sent_unchanged(monkeypatch):
    sent = []
    use_danbooru(monkeypatch, over_limit_handler(sent))

    collect(DanbooruSource(), tags="( a or b ) c")

    assert sent == ["( a or b ) c"]


def test_scanning_reports_progress(monkeypatch):
    messages = []
    token = http.run_log.set(lambda level, message: messages.append(message))
    monkeypatch.setattr(danbooru, "SCAN_LOG_INTERVAL", 2)
    posts = [tagged(1, "a b c"), tagged(2, "a b"), tagged(3, "a b")]
    use_danbooru(monkeypatch, over_limit_handler([], posts, counts={"a": 1, "b": 2, "c": 3}, pairs={"a b": 1}))
    try:
        collect(DanbooruSource(), tags="a b c")
    finally:
        http.run_log.reset(token)

    assert "Scanned 2 Danbooru posts, 1 matched" in messages


def test_searches_ask_only_for_the_fields_they_use(monkeypatch):
    fields = []

    def handler(request):
        fields.append(request.url.params["only"])
        return httpx.Response(200, json=[post(1, md5="abc", rating="g", source="http://source.test")])

    use_danbooru(monkeypatch, handler)
    result = collect(DanbooruSource(), tags="cat")[0]

    assert set(fields[0].split(",")) >= {"id", "md5", "rating", "source", "file_url", "tag_string", "last_noted_at"}
    assert (result["md5"], result["rating"]) == ("abc", "g")


def test_notes_come_through_as_markdown(monkeypatch):
    note = {"x": 1, "y": 2, "width": 3, "height": 4, "body": "<b>hi</b>", "is_active": True}
    use_danbooru(monkeypatch, lambda request: httpx.Response(200, json=[note, {**note, "is_active": False}]))
    source = DanbooruSource()

    assert asyncio.run(source.grab_notes({"id": 1})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "last_noted_at": "2026-01-01"})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "**hi**"}
    ]
