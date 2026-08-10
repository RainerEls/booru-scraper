import base64
import time

import requests

from app import config

USER_ID = config.SZURUBOORU_USER_ID
API_TOKEN = config.SZURUBOORU_API_TOKEN
RATE_LIMIT = float(config.SZURUBOORU_RATE_LIMIT)

token_auth_str = f"{USER_ID}:{API_TOKEN}".encode()
token_auth = base64.b64encode(token_auth_str)

session = requests.session()
session.headers = {'Authorization': 'Token ' + token_auth.decode(encoding='utf-8'),
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'}


def get_tag(tag_name):
    r = session.get('https://booru.example.com/api/tag/' + str(tag_name))
    return r

def create_tag(tag_name, tag_category):
    payload = {'names': tag_name, 'category': tag_category}
    r = session.post('https://booru.example.com/api/tags', json=payload)
    return r

def update_tag(tag_name, tag_category, version):
    payload = {'names': tag_name, 'category': tag_category, 'version': version}
    r = session.put('https://booru.example.com/api/tag/' + str(tag_name), json=payload)
    return r

def sync_tag(tag_name, tag_category):
    time.sleep(RATE_LIMIT)
    tag_exists = get_tag(tag_name=tag_name)
    if tag_exists.status_code == 200:
        update_tag(tag_name=tag_name, tag_category=tag_category, version=tag_exists.json()['version'])
    else:
        create_tag(tag_name=tag_name, tag_category=tag_category)

def upload_post(contentUrl, tags, safety, source=None): #TODO: add support for pulling notes from other boorus
    time.sleep(RATE_LIMIT)
    tags_list = tags.split(" ")
    payload = {'contentUrl': contentUrl, 'tags': tags_list, 'safety': safety}
    if source is not None:
        payload['source'] = source
    r = session.post('https://booru.example.com/api/posts/', json=payload)
    return r