from .sources import gelbooru

results = gelbooru.search_posts(1, '', 'cat dog')
all_tags = set()
for result in results:
    separate_tags = result['tags'].split(" ")
    all_tags.update(separate_tags)