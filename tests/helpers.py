import asyncio

import httpx


def mock_client(handler, **options):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)


def tags_of(source, names):
    async def run():
        return [tag async for tag in source.grab_tags(names)]

    return asyncio.run(run())


def collect(source, limit=None, **search):
    async def run():
        results = []
        async for post in source.search_posts(**search):
            results.append(post)
            if len(results) == limit:
                break
        return results

    return asyncio.run(run())
