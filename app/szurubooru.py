import asyncio

from app import config, http

RATE_LIMIT = float(config.SZURUBOORU_RATE_LIMIT)

async def get_tag(tag_name):
    r = await http.szurubooru_session.get('https://booru.example.com/api/tag/' + str(tag_name))
    return r

async def create_tag(tag_name, tag_category):
    payload = {'names': tag_name, 'category': tag_category}
    r = await http.szurubooru_session.post('https://booru.example.com/api/tags', json=payload)
    return r

async def update_tag(tag_name, tag_category, version):
    payload = {'names': tag_name, 'category': tag_category, 'version': version}
    r = await http.szurubooru_session.put('https://booru.example.com/api/tag/' + str(tag_name), json=payload)
    return r

async def sync_tag(tag_name, tag_category):
    await asyncio.sleep(RATE_LIMIT)
    tag_exists = await get_tag(tag_name=tag_name)
    if tag_exists.status_code == 200:
        await update_tag(tag_name=tag_name, tag_category=tag_category, version=tag_exists.json()['version'])
    else:
        await create_tag(tag_name=tag_name, tag_category=tag_category)

async def upload_post(contentUrl, tags, safety, source=None): #TODO: add support for pulling notes from other boorus
    await asyncio.sleep(RATE_LIMIT)
    tags_list = tags.split(" ")
    payload = {'contentUrl': contentUrl, 'tags': tags_list, 'safety': safety}
    if source is not None:
        payload['source'] = source
    r = await http.szurubooru_session.post('https://booru.example.com/api/posts/', json=payload)
    return r