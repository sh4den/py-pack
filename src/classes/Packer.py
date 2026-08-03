import ast
import fnmatch
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from termcolor import colored

from .ChunkBuilder import ChunkBuilder
from .ChunkConfig import ChunkConfig
from .Minifier import Minifier
from .ModuleResolver import (
    ImportScanner,
    ModuleResolver,
    SourceModule,
    ancestors_of,
    package_root_for,
    read_source,
)


class Packer:
    """Bundles a Python project into one or more self-contained chunks.

    The packer walks the import graph from an entry point, embeds every
    first-party module it reaches, and emits chunks that a small runtime
    imports through ``sys.meta_path``. Module sources are never rewritten, so
    the bundle behaves like the original project: relative imports, circular
    imports, star imports, ``__all__``, ``global`` and modules sharing
    top-level names all keep working.

    Third-party and standard library imports are left alone and resolved
    normally at runtime.
    """

    def __init__(
        self,
        entry_point: str,
        output_dir: str,
        roots: Optional[Iterable[str]] = None,
        include: Optional[Iterable[str]] = None,
        minify: bool = True,
        aggressive_minify: bool = False,
        compress: bool = True,
        quiet: bool = False,
    ):
        """
        Args:
            entry_point: Path to the entry module, or to a package directory
                containing a ``__main__.py``.
            output_dir: Directory the chunks are written to.
            roots: Extra source roots to resolve absolute imports against.
                The entry point's own root is always included.
            include: Dotted names or fnmatch patterns of modules to bundle
                even though nothing imports them by a literal name. This is
                the escape hatch for plugin registries and any other module
                loaded under a name built at runtime.
            minify: Whether to minify each bundled module.
            aggressive_minify: Enable minifier transforms that are observable
                at runtime (dropping annotations and docstrings).
            compress: Store module sources deflated and base85-encoded. Set to
                False to keep the chunks readable.
            quiet: Suppress progress output.
        """
        self.entry_point = self._resolve_entry_point(entry_point)
        self.output_dir = Path(output_dir).resolve()
        self.include_patterns = list(include or ())
        self.quiet = quiet

        source_root, entry_name = package_root_for(self.entry_point)
        self.project_root = source_root
        self.entry_module = entry_name

        self.resolver = ModuleResolver([source_root])
        for root in roots or ():
            self.resolver.add_root(root)

        self.scanner = ImportScanner()
        self.minifier = Minifier(
            enabled=minify, aggressive=aggressive_minify, quiet=quiet
        )
        self.chunk_builder = ChunkBuilder(
            str(self.output_dir), self.minifier, compress=compress, quiet=quiet
        )

        self.chunk_configs: List[ChunkConfig] = []

        self.modules: Dict[str, SourceModule] = {}
        self.module_dependencies: Dict[str, Set[str]] = {}
        self.external_modules: Set[str] = set()
        self.missing_modules: Set[str] = set()
        self.computed_imports: List[Tuple[str, int, str]] = []

        self.chunks: Dict[str, List[SourceModule]] = {}
        self.module_to_chunk: Dict[str, str] = {}
        self.sorted_modules: List[str] = []


    @staticmethod
    def _resolve_entry_point(entry_point: str) -> Path:
        """Resolve the entry point, accepting a package directory."""
        path = Path(entry_point).resolve()

        if path.is_dir():
            main = path / "__main__.py"
            if main.is_file():
                return main
            raise FileNotFoundError(
                f"{path} is a directory but has no __main__.py to use as an entry point"
            )

        if not path.is_file():
            raise FileNotFoundError(f"entry point not found: {path}")

        return path

    def configure_chunks(self, chunks: List[ChunkConfig]) -> None:
        """Configure how modules are split across chunks."""
        self.chunk_configs = list(chunks)

    def _log(self, message: str) -> None:
        if not self.quiet:
            print(message)


    def _add_module(
        self, name: str, path: Optional[Path], is_package: bool, source: str
    ) -> SourceModule:
        """Record a module in the graph."""
        search_path = None
        if is_package:
            search_path = path.parent if path is not None else None

        module = SourceModule(name, path, is_package, source, search_path)
        self.modules[name] = module
        return module

    def _require(self, name: str, queue: deque, required: bool) -> None:
        """Pull a dotted name into the bundle if it is a first-party module.

        Args:
            name: Absolute dotted module name.
            queue: Work queue of modules still to be scanned.
            required: False for names that may well be attributes rather than
                submodules, which must not be reported as missing.
        """
        if not name or name in self.modules or name in self.external_modules:
            return

        resolved = self.resolver.resolve(name)

        if resolved is None:
            namespace = self.resolver.namespace_dir(name)
            if namespace is not None:
                module = self._add_module(name, None, True, "")
                module.search_path = namespace
                for parent in ancestors_of(name):
                    self._require(parent, queue, required=False)
                return

            self.external_modules.add(name)
            if required and self.resolver.has_native_extension(name):
                self.missing_modules.add(name)
            return

        path, is_package = resolved
        try:
            source = read_source(path)
        except OSError as error:
            self._log(colored(f"  ! cannot read {path}: {error}", "yellow"))
            self.external_modules.add(name)
            return

        module = self._add_module(name, path, is_package, source)
        queue.append(module)

        for parent in ancestors_of(name):
            self._require(parent, queue, required=False)

    def build_graph(self) -> None:
        """Walk the import graph starting from the entry point."""
        self.modules.clear()
        self.module_dependencies.clear()
        self.external_modules.clear()
        self.missing_modules.clear()
        self.computed_imports.clear()

        source = read_source(self.entry_point)
        is_package = self.entry_point.name == "__init__.py"
        entry = self._add_module(
            self.entry_module, self.entry_point, is_package, source
        )

        queue: deque = deque([entry])
        for parent in ancestors_of(self.entry_module):
            self._require(parent, queue, required=False)
        self._apply_includes(queue)

        while queue:
            module = queue.popleft()
            self.module_dependencies[module.name] = self._scan_module(module, queue)

    def _apply_includes(self, queue: deque) -> None:
        """Pull in the modules named by the explicit include patterns."""
        if not self.include_patterns:
            return

        available = self.resolver.iter_module_names()
        for pattern in self.include_patterns:
            matched = [
                name
                for name in available
                if name == pattern
                or name.startswith(f"{pattern}.")
                or fnmatch.fnmatchcase(name, pattern)
            ]
            if not matched:
                self._log(
                    colored(f"  ! nothing matches --include {pattern}", "yellow")
                )
            for name in matched:
                self._require(name, queue, required=True)

    def _scan_module(self, module: SourceModule, queue: deque) -> Set[str]:
        """Parse a module and pull in everything it imports."""
        try:
            tree = ast.parse(module.source, filename=str(module.path or module.name))
        except SyntaxError as error:
            raise SyntaxError(
                f"cannot parse {module.path or module.name}: {error}"
            ) from error

        required, speculative, computed = self.scanner.scan(tree, module.package)

        for line, call in computed:
            self.computed_imports.append((module.name, line, call))

        for name in sorted(required):
            for ancestor in ancestors_of(name):
                self._require(ancestor, queue, required=False)
            self._require(name, queue, required=True)

        for name in sorted(speculative):
            self._require(name, queue, required=False)

        dependencies: Set[str] = set()
        for name in required | speculative:
            if name not in self.modules:
                continue
            dependencies.add(name)
            dependencies.update(
                parent for parent in ancestors_of(name) if parent in self.modules
            )
        return dependencies

    def process_file(self, file_path: Path) -> None:
        """Add one more file, and its dependencies, to an existing graph.

        Call it after :meth:`build_graph` to reach a module that no import
        statement names, such as a plugin loaded from a computed string. The
        ``include`` constructor argument does the same thing by pattern and is
        usually easier.
        """
        path = Path(file_path).resolve()
        name = self.resolver.module_name_for(path)
        if name is None:
            root, name = package_root_for(path)
            self.resolver.add_root(root)

        queue: deque = deque()
        self._require(name, queue, required=True)
        while queue:
            module = queue.popleft()
            self.module_dependencies[module.name] = self._scan_module(module, queue)

    def topological_sort(self) -> None:
        """Order modules so dependencies come first, tolerating cycles.

        The order only makes the output deterministic and readable. Execution
        order at runtime is decided by Python's import machinery, which is why
        a dependency cycle is a warning here rather than an error.
        """
        visited: Set[str] = set()
        on_stack: Set[str] = set()
        order: List[str] = []
        cycles: Set[Tuple[str, str]] = set()

        def visit(name: str) -> None:
            stack = [(name, iter(sorted(self.module_dependencies.get(name, ()))))]
            on_stack.add(name)
            visited.add(name)

            while stack:
                current, children = stack[-1]
                for child in children:
                    if child in on_stack:
                        cycles.add((current, child))
                        continue
                    if child in visited:
                        continue
                    visited.add(child)
                    on_stack.add(child)
                    stack.append(
                        (child, iter(sorted(self.module_dependencies.get(child, ()))))
                    )
                    break
                else:
                    stack.pop()
                    on_stack.discard(current)
                    order.append(current)

        for name in sorted(self.modules):
            if name not in visited:
                visit(name)

        for source, target in sorted(cycles):
            self._log(
                colored(
                    f"  ➜  circular import: {source} ↔ {target} (handled at runtime)",
                    "blue",
                )
            )

        self.sorted_modules = order


    def _matches_include(self, module: SourceModule, pattern: str) -> bool:
        """Match an include pattern against a module's path."""
        if module.path is None:
            return False

        candidates = [str(module.path), module.path.as_posix()]
        try:
            candidates.append(module.path.relative_to(self.project_root).as_posix())
        except ValueError:
            pass

        return any(re.search(pattern, candidate) for candidate in candidates)

    def assign_modules_to_chunks(self) -> None:
        """Split the graph into chunks according to the configuration.

        The entry module always stays in ``main``; anything not claimed by a
        chunk config joins it.
        """
        self.chunks = {"main": []}
        self.module_to_chunk = {self.entry_module: "main"}

        for config in self.chunk_configs:
            if config.name == "main":
                continue
            self.chunks.setdefault(config.name, [])

            wanted: Set[str] = set()
            for entry in config.entry_points:
                path = entry if entry.is_absolute() else self.project_root / entry
                name = self.resolver.module_name_for(path.resolve())
                if name is None:
                    self._log(
                        colored(f"  ! chunk {config.name}: unknown file {entry}", "yellow")
                    )
                elif name not in self.modules:
                    self._log(
                        colored(
                            f"  ! chunk {config.name}: {name} is not reachable from the entry point",
                            "yellow",
                        )
                    )
                else:
                    wanted.add(name)

            wanted.update(name for name in config.modules if name in self.modules)

            for name, module in self.modules.items():
                if any(self._matches_include(module, p) for p in config.includes):
                    wanted.add(name)

            for name in wanted:
                if name not in self.module_to_chunk:
                    self.module_to_chunk[name] = config.name

        for name in self.modules:
            self.module_to_chunk.setdefault(name, "main")

        ordering = {name: index for index, name in enumerate(self.sorted_modules)}
        for name, chunk_name in self.module_to_chunk.items():
            self.chunks.setdefault(chunk_name, []).append(self.modules[name])
        for modules in self.chunks.values():
            modules.sort(key=lambda module: ordering.get(module.name, 0))

    def get_chunk_imports(self, chunk_name: str) -> Set[str]:
        """Return the chunks a chunk depends on."""
        imports = set()
        for module in self.chunks.get(chunk_name, []):
            for dependency in self.module_dependencies.get(module.name, ()):
                other = self.module_to_chunk.get(dependency)
                if other and other != chunk_name:
                    imports.add(other)
        return imports

    def auto_generate_chunks(self, min_chunk_size: int = 2) -> None:
        """Derive one chunk per top-level package of the import graph.

        Must be called after :meth:`build_graph`.
        """
        groups: Dict[str, List[str]] = defaultdict(list)
        for name in self.modules:
            top_level = name.split(".")[0]
            if top_level != self.entry_module.split(".")[0]:
                groups[top_level].append(name)

        configs = [
            ChunkConfig(name=top_level, modules=sorted(names))
            for top_level, names in sorted(groups.items())
            if len(names) >= min_chunk_size
        ]
        self.configure_chunks(configs)


    def pack(self) -> Dict[str, object]:
        """Run the full build and write the chunks and manifest to disk."""
        start_time = time.time()
        self._log(colored("\n🚀 Building chunks...", "cyan"))

        if self.entry_module not in self.modules:
            self.build_graph()
        self.topological_sort()
        self.assign_modules_to_chunks()

        self.chunk_builder.chunk_files.clear()
        self.chunk_builder.chunk_hashes.clear()
        self.chunk_builder.source_bytes = 0
        self.chunk_builder.runtime_bytes = 0

        original_size = sum(
            len(module.source.encode("utf-8")) for module in self.modules.values()
        )

        order = [name for name in sorted(self.chunks) if name != "main"] + ["main"]

        chunk_info: List[Tuple[str, str, float]] = []
        total_size = 0
        emitted: Dict[str, List[SourceModule]] = {}

        for chunk_name in order:
            modules = self.chunks.get(chunk_name, [])
            is_entry_chunk = chunk_name == "main"

            module_chunks = None
            if is_entry_chunk:
                module_chunks = {
                    name: chunk
                    for name, chunk in self.module_to_chunk.items()
                    if chunk != "main"
                }

            chunk_path, filename = self.chunk_builder.build_chunk(
                chunk_name,
                modules,
                is_entry_chunk=is_entry_chunk,
                entry_module=self.entry_module if is_entry_chunk else None,
                module_chunks=module_chunks,
            )
            if chunk_path is None:
                continue

            emitted[chunk_name] = modules
            size = chunk_path.stat().st_size
            total_size += size
            chunk_info.append((chunk_name, filename, size / 1024))

        chunk_dependencies = {
            chunk_name: {
                dependency
                for dependency in self.get_chunk_imports(chunk_name)
                if dependency in emitted
            }
            for chunk_name in emitted
        }

        self.chunk_builder.generate_chunk_manifest(
            emitted, self.module_to_chunk, chunk_dependencies, self.entry_module
        )

        build_time = time.time() - start_time
        self._report(chunk_info, original_size, total_size, build_time)

        return {
            "entry": self.entry_module,
            "chunks": {name: filename for name, filename, _ in chunk_info},
            "modules": len(self.modules),
            "output_dir": self.output_dir,
        }

    def _report(
        self,
        chunk_info: List[Tuple[str, str, float]],
        original_size: int,
        total_size: int,
        build_time: float,
    ) -> None:
        """Print the build summary."""
        if self.quiet:
            return

        for name in sorted(self.missing_modules):
            print(
                colored(
                    f"  ! {name} is a compiled extension and cannot be bundled; "
                    "it must be installed where the bundle runs",
                    "yellow",
                )
            )

        if self.computed_imports:
            print(
                colored(
                    "\n⚠️   Imports built at runtime, which cannot be traced:",
                    "yellow",
                    attrs=["bold"],
                )
            )
            for module_name, line, call in self.computed_imports:
                print(colored(f"  ! {module_name}:{line}  {call}", "yellow"))
            print(
                colored(
                    "    Bundle the targets explicitly with include=[...] "
                    "(--include on the command line).",
                    "yellow",
                )
            )

        print(colored("\n✨ Build completed successfully!", "green"))
        print(colored("\n📄  Output files:", "white", attrs=["bold"]))

        for _, filename, size in chunk_info:
            size_text = f"{size:.2f} KB"
            print(
                f"  {colored('➜', 'green')} {filename.ljust(40)} {colored(size_text, 'yellow')}"
            )

        source_size = self.chunk_builder.source_bytes
        runtime_size = self.chunk_builder.runtime_bytes
        ratio = (
            ((original_size - source_size) / original_size * 100)
            if original_size
            else 0.0
        )

        print(colored("\n📊  Statistics:", "white", attrs=["bold"]))

        column_width = 40
        stats = [
            ("Modules bundled:", str(len(self.modules)), "cyan"),
            ("Original source:", f"{original_size / 1024:.2f} KB", "cyan"),
            (
                "Bundled source:",
                f"{source_size / 1024:.2f} KB ({ratio:+.1f}%)",
                "green",
            ),
            (
                "On disk:",
                f"{total_size / 1024:.2f} KB "
                f"(incl. {runtime_size / 1024:.2f} KB runtime)",
                "yellow",
            ),
            ("Build time:", f"{build_time:.2f}s", "yellow"),
        ]

        for label, value, color_name in stats:
            attrs = ["bold"] if label == "Bundled source:" else []
            print(
                f"  {colored('➜', 'green')} {label.ljust(column_width)} "
                f"{colored(value, color_name, attrs=attrs)}"
            )

        external = sorted(self._external_roots())
        if external:
            print(colored("\n🔗  External imports (resolved at runtime):", "white", attrs=["bold"]))
            print("  " + colored(", ".join(external), "blue"))

        print(
            colored("\n📁  Output directory:", "white", attrs=["bold"])
            + " "
            + colored(str(self.output_dir) + "/", "blue")
        )

    def _external_roots(self) -> Set[str]:
        """Top-level names of imports left for the runtime to resolve."""
        return {
            name.split(".")[0]
            for name in self.external_modules
            if name.split(".")[0] not in self.modules
        }
