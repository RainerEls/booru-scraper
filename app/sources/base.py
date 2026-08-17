from abc import ABC, abstractmethod


class BooruSource (ABC):
    @abstractmethod
    async def search_posts(self, limit=5, tags="", blacklist_tags="", rating=None):
        pass

    @abstractmethod
    async def grab_tags(self, tags):
        pass