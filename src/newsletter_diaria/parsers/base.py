from __future__ import annotations

from abc import ABC, abstractmethod

from newsletter_diaria.models import Item, Source


class SourceParser(ABC):
    key = ""

    @abstractmethod
    def parse(self, source: Source) -> list[Item]:
        raise NotImplementedError
