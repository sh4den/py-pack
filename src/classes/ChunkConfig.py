from pathlib import Path
from typing import List, Optional, Union


class ChunkConfig:
    """Describes one output chunk and which modules belong to it."""

    def __init__(
        self,
        name: str,
        entry_points: Optional[List[Union[str, Path]]] = None,
        includes: Optional[List[str]] = None,
        modules: Optional[List[str]] = None,
    ):
        """
        Args:
            name: Name of the chunk. ``main`` is reserved for the entry chunk.
            entry_points: Files that must land in this chunk. Relative paths
                are resolved against the project root.
            includes: Regular expressions matched against each module's path.
                Both the absolute path and the path relative to the source
                root (with forward slashes) are tried, so a pattern such as
                ``r"^services/"`` works on every platform.
            modules: Dotted module names to place in this chunk.
        """
        self.name = name
        self.entry_points = [Path(entry) for entry in entry_points or []]
        self.includes = list(includes or [])
        self.modules = list(modules or [])
