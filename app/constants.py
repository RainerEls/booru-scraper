from enum import StrEnum

GELBOORU_TAG_TYPES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "metadata",
    6: "general",
}

YANDERE_TAG_TYPES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "copyright",
    6: "metadata",
}

GELBOORU_RATINGS_CONVERSIONS = {
    "general": "safe",
    "safe": "safe",
    "sensitive": "sketchy",
    "questionable": "sketchy",
    "explicit": "unsafe",
}

YANDERE_RATINGS_CONVERSIONS = {"s": "safe", "q": "sketchy", "e": "unsafe"}

FRONTEND_TO_GELBOORU_RATINGS = {
    "safe": "general",
    "sketchy": "questionable",
    "unsafe": "explicit",
}


class Source(StrEnum):
    GELBOORU = "gelbooru"
    DANBOORU = "danbooru"
    YANDERE = "yandere"
