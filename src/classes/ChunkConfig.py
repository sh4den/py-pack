from typing import List, Optional, Union
from pathlib import Path


class ChunkConfig:
    def __init__(
        self, name: str, entry_points: List[Union[str, Path]], includes: Optional[List[str]] = None
    ):
        self.name = name
        self.entry_points = [Path(ep) if isinstance(ep, str) else ep for ep in entry_points]
        self.includes = includes or []
