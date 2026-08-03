"""Resolution of Python import statements to source files on disk.

This module contains the pieces that turn a file path into a dotted module
name, a dotted module name back into a file path, and a parsed module into the
set of module names it depends on. Everything the bundler knows about Python's
import system lives here.
"""

import ast
import io
import keyword
import os
import re
import tokenize
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

EXTENSION_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")

SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "site-packages",
        "build",
        "dist",
    }
)

_CODING_RE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-_.a-zA-Z0-9]+")


def _strip_coding_cookie(text: str) -> str:
    """Neutralise a PEP 263 encoding declaration.

    Bundled sources are stored as `str` and handed to `compile()`, which
    rejects a unicode string that still carries an encoding declaration.
    The line is blanked rather than dropped so line numbers stay accurate.
    """
    lines = text.split("\n")
    for index in range(min(2, len(lines))):
        if _CODING_RE.match(lines[index]):
            lines[index] = ""
            break
    return "\n".join(lines)


def read_source(path: Path) -> str:
    """Read a Python source file, honouring its declared encoding."""
    with open(path, "rb") as handle:
        raw = handle.read()

    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except SyntaxError:
        encoding = "utf-8"

    text = raw.decode(encoding, errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("﻿"):
        text = text[1:]
    return _strip_coding_cookie(text)


def is_valid_module_name(dotted: str) -> bool:
    """Check that a dotted name could name a real Python module."""
    if not dotted:
        return False
    return all(
        part.isidentifier() and not keyword.iskeyword(part)
        for part in dotted.split(".")
    )


def ancestors_of(dotted: str) -> List[str]:
    """Return the parent package names of a dotted name, outermost first."""
    parts = dotted.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def absolute_import_name(
    package: Optional[str], level: int, module: Optional[str]
) -> Optional[str]:
    """Turn a possibly relative import into an absolute dotted name.

    Args:
        package: The importing module's ``__package__``.
        level: Number of leading dots (0 for an absolute import).
        module: The dotted name written after the dots, if any.

    Returns:
        The absolute dotted name, or None if the import reaches above the
        top-level package (which Python itself rejects at runtime).
    """
    if level == 0:
        return module

    parts = package.split(".") if package else []
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    if not base:
        return None

    name = ".".join(base)
    return f"{name}.{module}" if module else name


def package_root_for(path: Path) -> Tuple[Path, str]:
    """Split a source file into (source root, dotted module name).

    The root is found by walking up for as long as the parent directory is a
    regular package, mirroring how ``python -m`` locates a module.
    """
    path = Path(path).resolve()

    if path.name == "__init__.py":
        parts = [path.parent.name]
        directory = path.parent.parent
    else:
        parts = [path.stem]
        directory = path.parent

    while (directory / "__init__.py").is_file() and directory != directory.parent:
        parts.append(directory.name)
        directory = directory.parent

    dotted = ".".join(reversed(parts))
    if not is_valid_module_name(dotted):
        dotted = "__pypack_entry__"
    return directory, dotted


def _describe_call(node: ast.Call) -> str:
    """Render a call site for a diagnostic message."""
    try:
        return ast.unparse(node)
    except Exception:
        return "<dynamic import>"


class SourceModule:
    """A single Python module that will be embedded in the bundle."""

    __slots__ = ("name", "path", "is_package", "source", "search_path")

    def __init__(
        self,
        name: str,
        path: Optional[Path],
        is_package: bool,
        source: str,
        search_path: Optional[Path] = None,
    ):
        self.name = name
        self.path = path
        self.is_package = is_package
        self.source = source
        self.search_path = search_path

    @property
    def package(self) -> str:
        """The value Python would give this module's ``__package__``."""
        if self.is_package:
            return self.name
        return self.name.rpartition(".")[0]

    def __repr__(self) -> str:
        return f"SourceModule({self.name!r})"


class ModuleResolver:
    """Maps dotted module names onto files beneath a set of source roots."""

    def __init__(self, roots: Iterable[Path] = ()):
        self.roots: List[Path] = []
        for root in roots:
            self.add_root(root)

    def add_root(self, root) -> None:
        """Append a source root, keeping the list ordered and unique."""
        resolved = Path(root).resolve()
        if resolved not in self.roots:
            self.roots.append(resolved)

    def resolve(self, dotted: str) -> Optional[Tuple[Path, bool]]:
        """Locate a dotted name, returning (path, is_package) or None."""
        if not is_valid_module_name(dotted):
            return None

        parts = dotted.split(".")
        for root in self.roots:
            base = root.joinpath(*parts)

            init = base / "__init__.py"
            if init.is_file():
                return init.resolve(), True

            module = base.parent / f"{parts[-1]}.py"
            if module.is_file():
                return module.resolve(), False

        return None

    def namespace_dir(self, dotted: str) -> Optional[Path]:
        """Return the directory backing an implicit namespace package."""
        if not is_valid_module_name(dotted):
            return None

        parts = dotted.split(".")
        for root in self.roots:
            base = root.joinpath(*parts)
            if base.is_dir() and not (base / "__init__.py").is_file():
                return base.resolve()
        return None

    def has_native_extension(self, dotted: str) -> bool:
        """Check whether a name resolves to a compiled extension module."""
        if not is_valid_module_name(dotted):
            return False

        parts = dotted.split(".")
        for root in self.roots:
            base = root.joinpath(*parts)
            for suffix in EXTENSION_SUFFIXES:
                if list(base.parent.glob(f"{parts[-1]}*{suffix}")):
                    return True
        return False

    def iter_module_names(self) -> List[str]:
        """List every importable module beneath the source roots.

        Used to expand explicit include patterns, which is how a project tells
        the bundler about modules only reachable through a computed name.
        """
        names: List[str] = []
        seen: Set[str] = set()

        for root in self.roots:
            for directory, subdirectories, filenames in os.walk(root):
                subdirectories[:] = [
                    name
                    for name in sorted(subdirectories)
                    if name not in SKIPPED_DIRECTORIES and not name.endswith(".egg-info")
                ]
                for filename in sorted(filenames):
                    if not filename.endswith(".py"):
                        continue
                    name = self.module_name_for(Path(directory) / filename)
                    if name is not None and name not in seen:
                        seen.add(name)
                        names.append(name)

        return names

    def module_name_for(self, path: Path) -> Optional[str]:
        """Return the dotted name a file would be imported under."""
        path = Path(path).resolve()
        for root in self.roots:
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue

            parts = list(relative.parts)
            if parts[-1] == "__init__.py":
                parts = parts[:-1]
            else:
                parts[-1] = parts[-1][: -len(".py")]

            dotted = ".".join(parts)
            if is_valid_module_name(dotted):
                return dotted
        return None


class ImportScanner:
    """Collects the module names an AST depends on."""

    DYNAMIC_IMPORT_FUNCTIONS = ("import_module", "__import__", "find_spec")

    def scan(
        self, tree: ast.AST, package: str
    ) -> Tuple[Set[str], Set[str], List[Tuple[int, str]]]:
        """Walk a module AST and collect the names it imports.

        Returns:
            A (required, speculative, computed) triple. Required names must
            exist for the module to run; speculative ones may well be
            attributes rather than submodules, so failing to resolve them is
            not an error. Computed entries are ``(line, source)`` pairs for
            dynamic imports whose target cannot be known before running.
        """
        required: Set[str] = set()
        speculative: Set[str] = set()
        computed: List[Tuple[int, str]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    required.add(alias.name)

            elif isinstance(node, ast.ImportFrom):
                base = absolute_import_name(package, node.level, node.module)
                if base is None:
                    continue

                required.add(base)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    speculative.add(f"{base}.{alias.name}")

            elif isinstance(node, ast.Call):
                if not self._is_dynamic_import(node):
                    continue
                targets = self._dynamic_targets(node, package)
                if targets:
                    speculative.update(targets)
                elif node.args:
                    computed.append((node.lineno, _describe_call(node)))

        return required, speculative, computed

    def _is_dynamic_import(self, node: ast.Call) -> bool:
        """Check whether a call is one of the dynamic import helpers."""
        func = node.func
        if isinstance(func, ast.Attribute):
            called = func.attr
        elif isinstance(func, ast.Name):
            called = func.id
        else:
            return False
        return called in self.DYNAMIC_IMPORT_FUNCTIONS

    def _dynamic_targets(self, node: ast.Call, package: str) -> Set[str]:
        """Extract module names from ``import_module``/``__import__`` calls."""
        if not node.args:
            return set()

        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            return set()

        target = first.value
        if not target.startswith("."):
            return {target} if is_valid_module_name(target) else set()

        anchor = self._relative_anchor(node, package)
        if anchor is None:
            return set()

        level = len(target) - len(target.lstrip("."))
        remainder = target[level:] or None
        absolute = absolute_import_name(anchor, level, remainder)
        return {absolute} if absolute else set()

    @staticmethod
    def _relative_anchor(node: ast.Call, package: str) -> Optional[str]:
        """Find the ``package=`` anchor of a relative ``import_module`` call."""
        candidate = node.args[1] if len(node.args) > 1 else None
        for keyword_node in node.keywords:
            if keyword_node.arg == "package":
                candidate = keyword_node.value

        if candidate is None:
            return None
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
        if isinstance(candidate, ast.Name) and candidate.id in ("__name__", "__package__"):
            return package
        return None
