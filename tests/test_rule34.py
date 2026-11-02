import asyncio

import httpx
import pytest
from helpers import collect, mock_client, tags_of

from app.sources import gelbooru, rule34
from app.sources.rule34 import PAGE_SIZE, Rule34Source


def post(id, rating="questionable", **fields):
    return {
        "id": id,
        "hash": f"hash-{id}",
        "file_url": "http://cdn.test/a.png",
        "rating": rating,
        "tags": "cat entro&#039;pi",
        "has_notes": False,
        **fields,
    }


def use_rule34(monkeypatch, handler):
    monkeypatch.setattr(rule34, "session", mock_client(handler))


def test_search_pages_by_id_and_normalises_posts(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        count = PAGE_SIZE if len(requests) == 1 else 1
        return httpx.Response(200, json=[post(5000 - i) for i in range(count)])

    use_rule34(monkeypatch, handler)
    posts = collect(Rule34Source(), tags="cat", blacklist_tags="dog", rating="sketchy")

    assert requests[0]["tags"] == "-dog cat rating:questionable"
    assert requests[1]["tags"] == f"-dog cat rating:questionable id:<{5001 - PAGE_SIZE}"
    assert posts[0]["md5"] == "hash-5000"
    assert posts[0]["tags"] == "cat entro'pi"


def test_sorted_searches_page_by_offset(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        count = PAGE_SIZE if len(requests) == 1 else 1
        return httpx.Response(200, json=[post(i) for i in range(count)])

    use_rule34(monkeypatch, handler)
    collect(Rule34Source(), tags="cat sort:score")

    assert [r["pid"] for r in requests] == ["0", "1"]
    assert {r["tags"] for r in requests} == {"cat sort:score"}


def test_no_matches_is_an_empty_body(monkeypatch):
    use_rule34(monkeypatch, lambda request: httpx.Response(200, text=""))

    assert collect(Rule34Source(), tags="nothing") == []


def test_a_refused_request_explains_itself(monkeypatch):
    use_rule34(monkeypatch, lambda request: httpx.Response(200, json="Missing authentication."))

    with pytest.raises(RuntimeError, match="Missing authentication"):
        collect(Rule34Source(), tags="cat")


def test_ratings_are_normalised_and_mismatches_dropped(monkeypatch):
    posts = [post(1), post(2, rating="Questionable"), post(3, rating="Explicit")]
    use_rule34(monkeypatch, lambda request: httpx.Response(200, json=posts))

    results = collect(Rule34Source(), rating="sketchy")

    assert [p["id"] for p in results] == [1, 2]
    assert results[1]["rating"] == "questionable"


def gelbooru_knows(monkeypatch, tags):
    def handler(request):
        return httpx.Response(200, json={"tag": tags})

    monkeypatch.setattr(gelbooru, "session", mock_client(handler))


def test_tags_gelbooru_knows_are_not_looked_up_on_rule34(monkeypatch):
    gelbooru_knows(monkeypatch, [{"name": "known", "type": 4}])
    names = []

    def handler(request):
        names.append(request.url.params["name"])
        return httpx.Response(200, text='<tag type="array"/>')

    use_rule34(monkeypatch, handler)
    tags = tags_of(Rule34Source(), ["known", "unknown"])

    assert names == ["unknown"]
    assert tags == [{"name": "known", "category": "character"}, {"name": "unknown", "category": "general"}]


def test_a_gelbooru_failure_falls_back_to_rule34(monkeypatch):
    monkeypatch.setattr(
        gelbooru, "session", mock_client(lambda request: httpx.Response(401))
    )
    use_rule34(monkeypatch, lambda request: httpx.Response(200, text='<tags><tag type="1"/></tags>'))

    assert tags_of(Rule34Source(), ["a"]) == [{"name": "a", "category": "artist"}]


def test_tag_lookup_escapes_names(monkeypatch):
    gelbooru_knows(monkeypatch, [])
    names = []

    def handler(request):
        names.append(request.url.params["name"])
        return httpx.Response(200, text='<tags><tag type="1"/></tags>')

    use_rule34(monkeypatch, handler)
    tags_of(Rule34Source(), ["entro'pi"])

    assert names == ["entro&#039;pi"]


def test_a_failed_lookup_keeps_the_tags_already_found(monkeypatch):
    gelbooru_knows(monkeypatch, [])
    responses = [httpx.Response(200, text='<tags><tag type="4"/></tags>')] + [httpx.Response(403)]
    use_rule34(monkeypatch, lambda request: responses.pop(0))

    assert tags_of(Rule34Source(), ["first", "second"]) == [{"name": "first", "category": "character"}]


def test_notes_parse_and_skip_inactive_ones(monkeypatch):
    xml = (
        '<notes><note x="1" y="2" width="3" height="4" body="&lt;b&gt;hi&lt;/b&gt;" is_active="true"/>'
        '<note x="1" y="2" width="3" height="4" body="old" is_active="false"/></notes>'
    )
    use_rule34(monkeypatch, lambda request: httpx.Response(200, text=xml))
    source = Rule34Source()

    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": False})) is None
    assert asyncio.run(source.grab_notes({"id": 1, "has_notes": True})) == [
        {"x": 1, "y": 2, "width": 3, "height": 4, "body": "**hi**"}
    ]
