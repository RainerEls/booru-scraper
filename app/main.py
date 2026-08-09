import logging

from fastapi import FastAPI

from app import config, constants, db, szurubooru

from .sources import gelbooru

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.getLevelName(config.LOGLEVEL))

app = FastAPI()

def run_scrape(limit, tags, blacklist_tags, rate_limit):
    logger.info('Scrape started')
    # Send search with user parameters
    scrape_results = gelbooru.search_posts(limit=limit, tags=tags, blacklist_tags=blacklist_tags)
    scrape_tags = set()
    unknown_tags = set()
    new_posts = []

    # Check if results exist in db by source->source_id: True = pass
    for result in scrape_results:
        if db.post_exists(source='gelbooru', source_post_id=result['id']):
            logger.debug('Gelbooru: ' + str(result['id']) + ' already exists in db. Skipping')
        # MD5 check: True = store_post() skipped
        elif db.md5_exists(md5=result['md5']):
            logger.debug('MD5: ' + str(result['md5']) + ' already exists in db. Adding source entry and skipping.')
            db.store_post(source='gelbooru', source_post_id=result['id'], szurubooru_post_id=None, md5=result['md5'], image_url=result['file_url'], status='skipped')
        # Otherwise: Begin processing
        else:
            # Pool tags into a set
            scrape_tags.update(result['tags'].split(" "))
            logger.debug(result['tags'].split(" "))
            # Pool new posts into a list
            new_posts.append(result)
            logger.debug(f'Appended: {result}')

    # Check each tag against db
    for tag in scrape_tags:
        if not db.tag_exists(tag):
            # Unknown tags -> new set
            unknown_tags.add(tag)

    # Run grab_tags with new set        
    if unknown_tags:
        logger.info('Grabbing unknown tags')
        new_tags = gelbooru.grab_tags(unknown_tags)
        # For loop takes data from grab_tags and saves to db
        for tag in new_tags:
            logger.debug(f'Tag data: {tag}')
            db.store_tag(tag_name=tag['name'], tag_type=tag['type'])
            szurubooru.sync_tag(tag_name=tag['name'], tag_category=constants.GELBOORU_TAG_TYPES[tag['type']])
    
    # Process all new posts
    for post in new_posts:
        contentUrl = post['file_url']
        tags = post['tags']
        safety = constants.GELBOORU_RATINGS_CONVERSIONS[post['rating']]
        booru_source = 'gelbooru'
        source = post['source']
        source_id = post['id']
        md5 = post['md5']

        # Store in db first
        db_entry = db.store_post(booru_source, source_id, None, md5, contentUrl, 'queued')
        logger.debug(f'{db_entry} entry added')
        # Attempt to upload to szurubooru
        r =szurubooru.upload_post(contentUrl, tags, safety, source)
        logger.debug('Attempting to upload post to szurubooru')

        # Succeed: add the szurubooru post id to the db entry and update the entry status
        if r.status_code == 201:
            logger.info('Szurubooru post creation succeeded.')
            db.add_szurubooru_post_id(db_entry, r.json()['id'])
            db.update_post_status(booru_source, source_id, 'processed')
        # Fail: set status to failure
        else:
            logger.debug(f'Szurubooru creation failed with status code: {r.status_code}')
            db.update_post_status(booru_source, source_id, 'failed')

@app.get("/")
async def root():
    return {"message": "Hello World"}