import os
import ast
from pathlib import Path
import re
from typing import Dict, Optional, Set, List
import json
import hashlib
import time
from collections import defaultdict
from termcolor import colored

from .Minifier import Minifier
from .ChunkConfig import ChunkConfig


class Packer:
    """
    A module bundler that packs Python files into chunks based on their dependencies.

    The Packer analyzes import dependencies, splits modules into configurable chunks,
    and generates a manifest file for runtime chunk loading.
    """

    def __init__(self, entry_point: str, output_dir: str):
        """
        Initialize a new Packer instance.

        Args:
            entry_point (str): Path to the main entry point Python file
            output_dir (str): Directory where bundled chunks will be output
        """
        self.minifier = Minifier()

        self.entry_point = Path(entry_point)
        self.output_dir = Path(output_dir)
        self.processed_files = set()
        self.dependencies = {}
        self.chunks: Dict[str, Set[Path]] = {}
        self.module_to_chunk: Dict[Path, str] = {}
        self.sorted_modules = []

    def configure_chunks(self, chunks: List[ChunkConfig]):
        """
        Configure how modules should be split into chunks.

        Args:
            chunks (List[ChunkConfig]): List of chunk configurations defining chunk names,
                                      entry points and module include patterns
        """
        self.chunk_configs = chunks

    def analyze_dynamic_imports(self, file_path: Path) -> List[str]:
        """
        Analyze a Python file for dynamic imports using __import__ or import_module.

        Args:
            file_path (Path): Path to the Python file to analyze

        Returns:
            List[str]: List of dynamically imported module names
        """
        with open(file_path, "r") as f:
            tree = ast.parse(f.read())

        dynamic_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Name) and node.func.id == "__import__"
                ) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                ):
                    if node.args:
                        if isinstance(node.args[0], ast.Str):
                            dynamic_imports.append(node.args[0].s)
        return dynamic_imports

    def assign_modules_to_chunks(self):
        """
        Assign all processed modules to their respective chunks based on chunk configs.

        Modules are assigned to chunks based on:
        1. Entry points defined in chunk configs
        2. Include patterns matching module paths
        3. Dependencies on other modules in chunks
        4. Fallback to main chunk if no other assignment rules match
        """
        self.chunks["main"] = {self.entry_point}
        self.module_to_chunk[self.entry_point] = "main"

        for chunk_config in self.chunk_configs:
            self.chunks[chunk_config.name] = set()

            for entry in chunk_config.entry_points:
                self.chunks[chunk_config.name].add(entry)
                self.module_to_chunk[entry] = chunk_config.name

            for include_pattern in chunk_config.includes:
                for file in self.processed_files:
                    if re.match(include_pattern, str(file)):
                        self.chunks[chunk_config.name].add(file)
                        self.module_to_chunk[file] = chunk_config.name

        for module in self.processed_files:
            if module not in self.module_to_chunk:
                chunk_counts = {}
                for dep in self.dependencies.get(module, []):
                    if dep in self.module_to_chunk:
                        chunk = self.module_to_chunk[dep]
                        chunk_counts[chunk] = chunk_counts.get(chunk, 0) + 1

                if chunk_counts:
                    best_chunk = max(chunk_counts.items(), key=lambda x: x[1])[0]
                    self.chunks[best_chunk].add(module)
                    self.module_to_chunk[module] = best_chunk
                else:
                    self.chunks["main"].add(module)
                    self.module_to_chunk[module] = "main"

    def generate_chunk_hash(self, content: str) -> str:
        """
        Generate a short hash for chunk content for cache busting.

        Args:
            content (str): The chunk's source code content

        Returns:
            str: 8-character MD5 hash of the content
        """
        return hashlib.md5(content.encode()).hexdigest()[:8]

    def minify_code(self, content: str) -> str:
        """
        Minify Python code to reduce chunk size.

        Args:
            content (str): Python source code to minify

        Returns:
            str: Minified Python code, or original if minification fails
        """
        try:
            return self.minifier.minify(content)
        except Exception as e:
            print(f"Minification error: {e}")
            return content

    def build_chunk(self, chunk_name: str, modules: Set[Path]):
        """
        Build a chunk file containing multiple module contents.

        Args:
            chunk_name (str): Name of the chunk
            modules (Set[Path]): Set of module paths to include in the chunk

        Returns:
            Tuple[Path, str]: Tuple of (chunk output path, hashed filename)
        """
        chunk_code = []
        import_tracker = {
            "standard": set(),
            "relative": set(),
            "third_party": set(),
        }

        chunk_code.append(f"# Chunk: {chunk_name}")
        chunk_code.append(self._get_loader_code())

        for module in modules:
            if module in self.sorted_modules:
                with open(module, "r") as f:
                    content = f.read()
                    tree = ast.parse(content)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for name in node.names:
                                import_line = f"import {name.name}"
                                if name.asname:
                                    import_line += f" as {name.asname}"
                                if self._is_stdlib_module(name.name):
                                    import_tracker["standard"].add(import_line)
                                else:
                                    import_tracker["third_party"].add(import_line)

                        elif isinstance(node, ast.ImportFrom):
                            if node.level > 0:
                                module_path = self._resolve_relative_import(
                                    module, node
                                )
                                if module_path and module_path in modules:
                                    continue
                                import_line = self._format_relative_import(node)
                                import_tracker["relative"].add(import_line)
                            else:
                                import_line = f"from {node.module} import {', '.join(n.name for n in node.names)}"
                                if self._is_stdlib_module(node.module):
                                    import_tracker["standard"].add(import_line)
                                else:
                                    import_tracker["third_party"].add(import_line)

        chunk_code.extend(sorted(import_tracker["standard"]))
        chunk_code.extend(sorted(import_tracker["third_party"]))
        chunk_code.extend(sorted(import_tracker["relative"]))

        for module in modules:
            if module in self.sorted_modules:
                with open(module, "r") as f:
                    content = f.read()
                    lines = content.split("\n")
                    filtered_lines = []

                    for line in lines:
                        if not line.strip() or not line.strip().startswith(
                            ("import ", "from ")
                        ):
                            filtered_lines.append(line)

                    module_content = "\n".join(filtered_lines)
                    chunk_code.append(f"\n# Module: {module.name}")

                    try:
                        minified_content = self.minify_code(module_content)
                        chunk_code.append(minified_content)
                    except Exception as e:
                        print(f"Error processing module {module}: {e}")
                        chunk_code.append(module_content)

        chunk_content = "\n\n".join(chunk_code)
        chunk_hash = self.generate_chunk_hash(chunk_content)
        hashed_filename = f"{chunk_name}.{chunk_hash}.py"
        output_path = self.output_dir / hashed_filename

        os.makedirs(self.output_dir, exist_ok=True)

        with open(output_path, "w", newline="\n") as f:
            f.write(self.minifier.minify(chunk_content))

        return output_path, hashed_filename

    def _is_stdlib_module(self, module_name: str) -> bool:
        """Check if a module is from the Python standard library."""
        import sys
        import importlib.util

        base_module = module_name.split(".")[0]

        if base_module in sys.builtin_module_names:
            return True

        spec = importlib.util.find_spec(base_module)
        if spec is None:
            return False

        return "site-packages" not in str(spec.origin or "")

    def _resolve_relative_import(
        self, current_module: Path, import_node: ast.ImportFrom
    ) -> Optional[Path]:
        """Resolve a relative import to its absolute path."""
        current_path = current_module.parent
        for _ in range(import_node.level - 1):
            current_path = current_path.parent

        if import_node.module:
            return current_path / f"{import_node.module.replace('.', '/')}.py"
        return current_path / "__init__.py"

    def _format_relative_import(self, node: ast.ImportFrom) -> str:
        """Format a relative import statement."""
        dots = "." * node.level
        module = f"{dots}{node.module if node.module else ''}"
        names = ", ".join(
            n.name + (f" as {n.asname}" if n.asname else "") for n in node.names
        )
        return f"from {module} import {names}"

    def generate_chunk_manifest(self):
        """
        Generate a manifest.json file containing chunk metadata.

        The manifest includes:
        - Version timestamp
        - Chunk definitions and their modules
        - Module to chunk mappings
        - File mappings for chunk loading
        """
        manifest = {
            "version": int(time.time()),
            "chunks": {
                chunk_name: {
                    "modules": [str(m) for m in modules],
                    "file": f"{chunk_name}.{self.chunk_hashes[chunk_name]}.py",
                    "imports": list(self.get_chunk_imports(chunk_name)),
                }
                for chunk_name, modules in self.chunks.items()
            },
            "moduleToChunk": {
                str(module): chunk_name
                for module, chunk_name in self.module_to_chunk.items()
            },
            "fileMap": {
                f"{chunk_name}.py": f"{chunk_name}.{self.chunk_hashes[chunk_name]}.py"
                for chunk_name in self.chunks.keys()
            },
        }

        with open(self.output_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

    def get_chunk_imports(self, chunk_name: str) -> Set[str]:
        """
        Get the set of other chunks that a chunk depends on.

        Args:
            chunk_name (str): Name of the chunk to analyze

        Returns:
            Set[str]: Set of chunk names that this chunk imports from
        """
        imports = set()
        for module in self.chunks[chunk_name]:
            for dep in self.dependencies.get(module, []):
                if dep in self.module_to_chunk:
                    dep_chunk = self.module_to_chunk[dep]
                    if dep_chunk != chunk_name:
                        imports.add(dep_chunk)
        return imports

    def process_file(self, file_path: Path) -> None:
        """
        Process a Python file and recursively analyze its dependencies.

        Args:
            file_path (Path): Path to the Python file to process
        """
        if file_path in self.processed_files:
            return

        self.processed_files.add(file_path)

        try:
            with open(file_path, "r") as f:
                tree = ast.parse(f.read())
        except FileNotFoundError:
            print(f"Warning: File not found: {file_path}")
            return

        dependencies = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for name in node.names:
                    dependencies.add(name.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    dependencies.add(node.module)

        file_dir = file_path.parent
        module_paths = set()

        for dep in dependencies:
            parts = dep.split(".")
            possible_paths = [
                file_dir / "/".join(parts) / "__init__.py",
                file_dir / f"{'/'.join(parts)}.py",
            ]

            for path in possible_paths:
                if path.exists():
                    module_paths.add(path)
                    break

        self.dependencies[file_path] = module_paths
        for dep_path in module_paths:
            self.process_file(dep_path)

    def topological_sort(self) -> None:
        """
        Sort modules based on their dependencies using depth-first search.

        Detects circular dependencies and produces an ordering where dependencies
        come before dependent modules.

        Raises:
            Exception: If a circular dependency is detected
        """
        visited = set()
        temp_mark = set()
        order = []

        def visit(node: Path):
            if node in temp_mark:
                raise Exception(f"Circular dependency detected involving {node}")
            if node not in visited:
                temp_mark.add(node)
                for dep in self.dependencies.get(node, []):
                    visit(dep)
                temp_mark.remove(node)
                visited.add(node)
                order.append(node)

        for module in self.processed_files:
            if module not in visited:
                visit(module)

        self.sorted_modules = order

    def auto_generate_chunks(
        self, min_chunk_size: int = 2, similarity_threshold: float = 0.5
    ):
        """
        Automatically generate chunk configurations based on module dependencies and patterns.

        Args:
            min_chunk_size (int): Minimum number of modules to form a chunk
            similarity_threshold (float): Threshold for grouping modules (0.0 to 1.0)
        """
        dir_groups = defaultdict(set)
        for module in self.processed_files:
            dir_name = module.parent.name
            dir_groups[dir_name].add(module)

        auto_chunks = []
        for dir_name, modules in dir_groups.items():
            if len(modules) >= min_chunk_size:
                chunk_config = ChunkConfig(
                    name="chunk",
                    entry_points=[next(iter(modules))],
                    includes=[rf".*/{dir_name}/.*\.py"],
                )
                auto_chunks.append(chunk_config)

        dependency_groups = self._group_by_dependencies(similarity_threshold)
        for group_idx, modules in enumerate(dependency_groups):
            if len(modules) >= min_chunk_size:
                chunk_config = ChunkConfig(
                    name=f"chunk_deps_{group_idx}",
                    entry_points=[next(iter(modules))],
                    includes=[rf".*/{m.parent.name}/.*\.py" for m in modules],
                )
                auto_chunks.append(chunk_config)

        self.configure_chunks(auto_chunks)

    def _group_by_dependencies(self, similarity_threshold: float) -> List[Set[Path]]:
        """
        Group modules based on their dependency relationships.

        Args:
            similarity_threshold (float): Threshold for considering modules similar

        Returns:
            List[Set[Path]]: List of module groups
        """
        groups = []
        unassigned = set(self.processed_files)

        while unassigned:
            current = unassigned.pop()
            current_group = {current}

            deps = self.dependencies.get(current, set())
            dependents = {
                m
                for m in self.processed_files
                if current in self.dependencies.get(m, set())
            }

            for module in list(unassigned):
                module_deps = self.dependencies.get(module, set())
                module_dependents = {
                    m
                    for m in self.processed_files
                    if module in self.dependencies.get(m, set())
                }

                deps_similarity = (
                    len(deps & module_deps) / len(deps | module_deps)
                    if deps or module_deps
                    else 0
                )

                deps_flow_similarity = (
                    len(dependents & module_dependents)
                    / len(dependents | module_dependents)
                    if dependents or module_dependents
                    else 0
                )

                if (
                    deps_similarity > similarity_threshold
                    or deps_flow_similarity > similarity_threshold
                ):
                    current_group.add(module)
                    unassigned.remove(module)

            groups.append(current_group)

        return groups

    def _get_loader_code(self) -> str:
        """
        Return the code for the chunk loader.
        """
        return """
def __load_chunk__(name):
    import importlib.util
    import sys
    import os
    if name in sys.modules:
        return sys.modules[name]
    chunk_path = os.path.join(os.path.dirname(__file__), f"{{name}}.py")
    spec = importlib.util.spec_from_file_location(name, chunk_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
    """

    def pack(self):
        """
        Execute the complete packing process with auto-generated chunks if none configured.
        """
        start_time = time.time()

        print(colored("\n🚀 Building chunks...", "cyan"))

        if not hasattr(self, "chunk_configs") or not self.chunk_configs:
            self.process_file(self.entry_point)
            print(colored("  ➜  Auto-generating chunk configuration", "blue"))
            self.auto_generate_chunks()

        self.process_file(self.entry_point)
        self.topological_sort()
        self.assign_modules_to_chunks()

        self.chunk_hashes = {}
        total_size = 0
        chunk_info = []

        for chunk_name, modules in self.chunks.items():
            chunk_path, hashed_filename = self.build_chunk(chunk_name, modules)
            self.chunk_hashes[chunk_name] = hashed_filename.split(".")[1]
            size = os.path.getsize(chunk_path) / 1024
            total_size += size
            chunk_info.append((chunk_name, hashed_filename, size))

        self.generate_chunk_manifest()

        build_time = time.time() - start_time
        print(colored("\n✨ Build completed successfully!", "green"))
        print(colored("\nOutput files:", "white", attrs=["bold"]))

        for name, filename, size in chunk_info:
            size_text = f"{size:.2f} KB"
            print(
                f"  {colored('➜ ', 'green')} {filename.ljust(40)} {colored(size_text, 'yellow')}"
            )

        print(
            colored("\nTotal size: ", "white", attrs=["bold"])
            + colored(f"{total_size:.2f} KB", "yellow")
        )
        print(
            colored("Build time: ", "white", attrs=["bold"])
            + colored(f"{build_time:.2f}s", "yellow")
        )
        print(
            colored("\nOutput directory: ", "white", attrs=["bold"])
            + colored(str(self.output_dir), "blue")
        )
