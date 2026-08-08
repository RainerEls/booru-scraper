import os

from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", 'changeme')
DB_PASS = os.getenv("DB_PASS", 'changeme')
DB_HOST = os.getenv("DB_HOST", '127.0.0.1')
DB_PORT = os.getenv("DB_PORT", '5432')
DB_NAME = os.getenv("DB_NAME", 'booru-db')

GELBOORU_API_KEY = os.getenv("GELBOORU_API_KEY", None)
GELBOORU_USER_ID = os.getenv("GELBOORU_USER_ID", None)
GELBOORU_RATE_LIMIT = os.getenv("GELBOORU_RATE_LIMIT", '1')
