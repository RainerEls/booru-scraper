import requests

from .. import config

API_KEY = config.GELBOORU_API_KEY
USER_ID = config.GELBOORU_USER_ID

s = requests.session()
s.params = {'api_key': API_KEY, 'user_id': USER_ID, 'json': 1}



# payloadTags = {'limit': 1, 'order': 'DESC', 'orderBy': 'count'}

def search_posts(limit, tags):
    page = 1
    response_number = 100
    search_results = []
    payloadPost = {'tags': tags, 'limit': min(limit, 100)}
    while response_number >= 100 and limit >= len(search_results):
        r = s.get('https://gelbooru.com/index.php?page=dapi&s=post&q=index&pid=' + str(page), params=payloadPost)
        page_results = r.json()['post']
        response_number = len(page_results)
        page += 1
        search_results.extend(page_results)
    return search_results



def grab_tags(tags):
    total_tags=len(tags)
    tags_list = list(tags)
    tags_range = range(0, total_tags, 100)
    tag_data = []
    for tag_range in tags_range:
        tag_set = " ".join(tags_list[tag_range:tag_range+100])
        #print(tag_set)
        payloadTags = {'names': tag_set}
        r = s.get('https://gelbooru.com/index.php?page=dapi&s=tag&q=index', params=payloadTags)
        tag_results = r.json()['tag']
        tag_data.extend(tag_results)
    return tag_data





results = search_posts(2, 'cat')
all_tags = set()
for result in results:
    separate_tags = result['tags'].split(" ")
    all_tags.update(separate_tags)


final_tags = grab_tags(all_tags)
print(final_tags)