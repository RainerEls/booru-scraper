from abc import ABC, abstractmethod


class BooruSource(ABC):
    @abstractmethod
    def search_posts(self, tags="", blacklist_tags="", rating=None):
        """Yield matching posts, newest first, fetching pages as they are consumed."""

    @abstractmethod
    async def grab_tags(self, tags):
        pass

    async def grab_notes(self, post):
        return None
