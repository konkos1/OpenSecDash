from abc import ABC


class Plugin(ABC):
    id: str
    name: str
    version: str
