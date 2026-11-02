import logging

import apprise

from app import config

logger = logging.getLogger(__name__)
logging.getLogger("apprise").setLevel(logging.WARNING)

EVENTS = {
    "scrape_finished": (apprise.NotifyType.SUCCESS, "Scrape finished"),
    "schedule_finished": (apprise.NotifyType.SUCCESS, "Scheduled scrape uploaded new posts"),
    "scrape_failed": (apprise.NotifyType.FAILURE, "Scrape had problems"),
    "scrape_interrupted": (apprise.NotifyType.WARNING, "Scrape interrupted by a restart"),
    "integrity_problems": (apprise.NotifyType.WARNING, "Integrity check found problems"),
}

services = apprise.Apprise(asset=apprise.AppriseAsset(app_id="Booru Scraper", app_desc="Booru Scraper"))
chosen = set()


def setup():
    chosen.clear()
    services.clear()
    names = set(config.NOTIFY_ON.replace(",", " ").split()) or set(EVENTS)
    chosen.update(names & set(EVENTS))
    if names - set(EVENTS):
        logger.warning(f"Ignoring unknown NOTIFY_ON event(s): {', '.join(sorted(names - set(EVENTS)))}")
    if not config.APPRISE_URLS:
        return
    services.add(config.APPRISE_URLS)
    if len(services):
        logger.info(f"Notifications go to {len(services)} service(s) for: {', '.join(sorted(chosen))}")
    else:
        logger.warning("APPRISE_URLS holds no URL that Apprise understands")


async def send(event, body):
    if event not in chosen or not len(services):
        return
    notify_type, title = EVENTS[event]
    try:
        result = await services.async_notify(title=title, body=body, notify_type=notify_type)
    except Exception as e:
        logger.warning(f"Could not send the {event} notification: {type(e).__name__}")
        return
    if not result:
        failed = [service.name for service in result.results if service.status != apprise.AppriseResultStatus.SUCCESS]
        logger.warning(f"The {event} notification didn't reach: {', '.join(failed)}")
    result.close()
