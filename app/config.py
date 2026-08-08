import os

from dotenv import load_dotenv

load_dotenv()

GELBOORU_API_KEY = os.getenv("GELBOORU_API_KEY", None)
GELBOORU_USER_ID = os.getenv("GELBOORU_USER_ID", None)
GELBOORU_RATE_LIMIT = os.getenv("GELBOORU_RATE_LIMIT", 1)
