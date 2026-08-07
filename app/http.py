import config
import requests

API_KEY = config.API_KEY
USER_ID = config.USER_ID

s = requests.session()
s.params = {'api_key': API_KEY, 'user_id': USER_ID, 'json': 1}

payloadPost = {'limit': 1}
payloadTags = {'limit': 1, 'order': 'DESC', 'orderBy': 'count'}
r = s.get('https://gelbooru.com/index.php?page=dapi&s=tag&q=index', params=payloadTags)
print(r.json())