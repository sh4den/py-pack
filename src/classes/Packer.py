import os
import ast
from pathlib import Path
import re
from typing import Dict, Set, List
import time
from collections import defaultdict
from termcolor import colored

from .ChunkConfig import ChunkConfig
from .ChunkBuilder import ChunkBuilder


class Packer:
    """
    A module bundler that packs Python files into chunks based on their dependencies.

    The Packer analyzes import dependencies, splits modules into configurable chunks,
    and uses ChunkBuilder to generate the actual chunks and manifest file.
    """

    def __init__(self, entry_point: str, output_dir: str):
        """
        Initialize a new Packer instance.

        Args:
            entry_point (str): Path to the main entry point Python file
            output_dir (str): Directory where bundled chunks will be output
        """
        self.entry_point = Path(entry_point)
        self.output_dir = Path(output_dir)
        self.processed_files = set()
        self.dependencies = {}
        self.chunks: Dict[str, Set[Path]] = {}
        self.module_to_chunk: Dict[Path, str] = {}
        self.sorted_modules = []
        self.chunk_builder = ChunkBuilder(output_dir)

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
            project_root = Path.cwd()

            # Construct paths properly for multi-part module names
            if len(parts) == 1:
                # Single module name like 'os' or 'utils'
                possible_paths = [
                    # Try relative to current file first
                    file_dir / parts[0] / "__init__.py",
                    file_dir / f"{parts[0]}.py",
                    # Try from parent directory (for sibling packages)
                    file_dir.parent / parts[0] / "__init__.py",
                    file_dir.parent / f"{parts[0]}.py",
                    # Try from project root
                    project_root / parts[0] / "__init__.py",
                    project_root / f"{parts[0]}.py",
                ]
            else:
                # Multi-part like 'utils.math_helpers' or 'models.user'
                # Try relative to current file first
                relative_module = file_dir / Path(*parts[:-1]) / f"{parts[-1]}.py"
                relative_package = file_dir / Path(*parts) / "__init__.py"
                # Try from parent directory (for sibling packages like utils.math_helpers when in services/)
                parent_module = file_dir.parent / Path(*parts[:-1]) / f"{parts[-1]}.py"
                parent_package = file_dir.parent / Path(*parts) / "__init__.py"
                # Try from project root
                module_file = project_root / Path(*parts[:-1]) / f"{parts[-1]}.py"
                package_init = project_root / Path(*parts) / "__init__.py"

                possible_paths = [
                    relative_module,
                    relative_package,
                    parent_module,  # This will find example/utils/math_helpers.py from example/services/
                    parent_package,
                    module_file,
                    package_init,
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

    def pack(self):
        """
        Execute the complete packing process with auto-generated chunks if none configured.
        """
        start_time = time.time()

        print(colored("\n🚀 Building chunks...", "cyan"))

        # Don't auto-generate chunks - we'll handle chunking based on size
        # if not hasattr(self, "chunk_configs") or not self.chunk_configs:
        #     self.process_file(self.entry_point)
        #     print(colored("  ➜  Auto-generating chunk configuration", "blue"))
        #     self.auto_generate_chunks()

        self.process_file(self.entry_point)
        self.topological_sort()

        # Put all modules in main chunk by default
        self.chunks["main"] = set(self.processed_files)
        for module in self.processed_files:
            self.module_to_chunk[module] = "main"

        self.chunk_builder.processed_files = self.processed_files

        chunk_info = []
        total_size = 0
        chunk_dependencies = {}
        valid_chunks = {}
        processed_modules = set()

        chunk_processing_order = [name for name in self.chunks.keys() if name != "main"]

        if "main" in self.chunks:
            chunk_processing_order.append("main")

        for chunk_name in chunk_processing_order:
            if chunk_name not in self.chunks:
                continue

            modules = self.chunks[chunk_name]
            chunk_dependencies[chunk_name] = self.get_chunk_imports(chunk_name)
            chunk_path, hashed_filename = self.chunk_builder.build_chunk(
                chunk_name, modules, self.sorted_modules, self.module_to_chunk
            )

            if chunk_path is None:
                continue

            valid_chunks[chunk_name] = modules
            processed_modules.update(modules)

            size = os.path.getsize(chunk_path) / 1024
            total_size += size
            chunk_info.append((chunk_name, hashed_filename, size))

        valid_chunk_dependencies = {}
        for chunk_name in valid_chunks:
            valid_chunk_dependencies[chunk_name] = {
                dep
                for dep in chunk_dependencies.get(chunk_name, set())
                if dep in valid_chunks
            }

        self.chunk_builder.generate_chunk_manifest(
            valid_chunks, self.module_to_chunk, valid_chunk_dependencies
        )

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
