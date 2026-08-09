import time

import requests

from .. import config

API_KEY = config.GELBOORU_API_KEY
USER_ID = config.GELBOORU_USER_ID
RATE_LIMIT = float(config.GELBOORU_RATE_LIMIT)

session = requests.session()
session.params = {'api_key': API_KEY, 'user_id': USER_ID, 'json': 1}

def search_posts(limit=5, tags='', blacklist_tags=''):

    page = 1
    response_number = 100
    search_results = []

    bad_tags = ["-" + tag for tag in blacklist_tags.split(" ")]
    all_tags = " ".join(bad_tags) + " " + tags
    payloadPost = {'tags': all_tags, 'limit': min(limit, 100)}

    while response_number >= 100 and limit >= len(search_results):
        r = session.get('https://gelbooru.com/index.php?page=dapi&s=post&q=index&pid=' + str(page), params=payloadPost)
        page_results = r.json()['post']
        response_number = len(page_results)
        page += 1
        search_results.extend(page_results)
        time.sleep(RATE_LIMIT)
    return search_results

def grab_tags(tags):

    total_tags=len(tags)
    tags_list = list(tags)
    tags_range = range(0, total_tags, 100)
    tag_data = []

    for tag_range in tags_range:
        tag_set = " ".join(tags_list[tag_range:tag_range+100])
        payloadTags = {'names': tag_set}
        r = session.get('https://gelbooru.com/index.php?page=dapi&s=tag&q=index', params=payloadTags)
        tag_results = r.json()['tag']
        tag_data.extend(tag_results)
    return tag_data