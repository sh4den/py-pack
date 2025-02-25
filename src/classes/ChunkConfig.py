from typing import List, Optional
from pathlib import Path


class ChunkConfig:
    def __init__(
        self, name: str, entry_points: List[str], includes: Optional[List[str]] = None
    ):
        self.name = name
        self.entry_points = [Path(ep) for ep in entry_points]
        self.includes = includes or []
