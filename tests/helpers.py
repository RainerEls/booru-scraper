import asyncio

import httpx


def mock_client(handler, **options):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)


def collect(source, limit=None, **search):
    async def run():
        results = []
        async for post in source.search_posts(**search):
            results.append(post)
            if len(results) == limit:
                break
        return results

    return asyncio.run(run())
