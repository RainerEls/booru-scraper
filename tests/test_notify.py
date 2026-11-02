import asyncio
from types import SimpleNamespace

import apprise
import pytest

from app import config, notify, scrape


class FakeServices:
    def __init__(self, delivered=True):
        self.sent = []
        self.delivered = delivered
        self.closed = False

    def __len__(self):
        return 1

    async def async_notify(self, **message):
        self.sent.append(message)
        return self

    def __bool__(self):
        return self.delivered

    results = (SimpleNamespace(name="JSON", status=apprise.AppriseResultStatus.FAILURE),)

    def close(self):
        self.closed = True


@pytest.fixture
def services(monkeypatch):
    fake = FakeServices()
    monkeypatch.setattr(notify, "services", fake)
    monkeypatch.setattr(notify, "chosen", set(notify.EVENTS))
    return fake


def test_notify_on_limits_what_is_sent(monkeypatch, services):
    monkeypatch.setattr(config, "NOTIFY_ON", "scrape_failed, not_an_event")
    notify.setup()

    asyncio.run(notify.send("scrape_finished", "ignored"))
    asyncio.run(notify.send("scrape_failed", "sent"))

    assert [message["body"] for message in services.sent] == ["sent"]
    assert services.sent[0]["notify_type"] == apprise.NotifyType.FAILURE


def test_nothing_is_sent_without_a_service(monkeypatch, caplog):
    monkeypatch.setattr(notify, "services", apprise.Apprise())
    monkeypatch.setattr(notify.apprise.Apprise, "async_notify", None)

    asyncio.run(notify.send("scrape_failed", "body"))

    assert not caplog.text


def test_a_failed_delivery_is_logged_by_service_name_only(services, caplog):
    services.delivered = False

    asyncio.run(notify.send("scrape_failed", "body"))

    assert "didn't reach: JSON" in caplog.text
    assert services.closed


def test_an_apprise_error_does_not_reach_the_caller(monkeypatch, services, caplog):
    async def explode(**message):
        raise ValueError("json://secret")

    monkeypatch.setattr(services, "async_notify", explode)

    asyncio.run(notify.send("scrape_failed", "body"))

    assert "ValueError" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "status, failures, processed, schedule_id, event",
    [
        ("done", [], 2, None, "scrape_finished"),
        ("done", [], 0, None, "scrape_finished"),
        ("done", [], 2, 1, "schedule_finished"),
        ("done", [], 0, 1, None),
        ("done", [{"id": 1}], 2, 1, "scrape_failed"),
        ("error", [], 0, None, "scrape_failed"),
        ("interrupted", [{"id": 1}], 0, 1, "scrape_interrupted"),
        ("stopped", [{"id": 1}], 0, None, None),
    ],
)
def test_run_outcome_picks_the_event(status, failures, processed, schedule_id, event):
    job = scrape.Job()
    job.status = status
    job.failures = failures
    job.counters["processed"] = processed

    assert scrape.run_event(job, scrape.ScrapeSpec("fake", 1, schedule_id=schedule_id)) == event
