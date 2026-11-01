from enum import StrEnum

GELBOORU_TAG_TYPES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "metadata",
    6: "general",
}

DANBOORU_TAG_TYPES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "metadata",
}

E621_TAG_TYPES = {
    0: "general",
    1: "artist",
    2: "artist",
    3: "copyright",
    4: "character",
    5: "species",
    6: "general",
    7: "metadata",
    8: "general",
}

RULE34_TAG_TYPES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "metadata",
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

DANBOORU_RATINGS_CONVERSIONS = {"g": "safe", "s": "sketchy", "q": "sketchy", "e": "unsafe"}

E621_RATINGS_CONVERSIONS = YANDERE_RATINGS_CONVERSIONS

RULE34_RATINGS_CONVERSIONS = {"safe": "safe", "questionable": "sketchy", "explicit": "unsafe"}

FRONTEND_TO_GELBOORU_RATING_TERMS = {
    "safe": ["rating:general"],
    "sketchy": ["-rating:general", "-rating:explicit"],
    "unsafe": ["rating:explicit"],
}


class Source(StrEnum):
    GELBOORU = "gelbooru"
    DANBOORU = "danbooru"
    YANDERE = "yandere"
    E621 = "e621"
    RULE34 = "rule34"
