import os
import ast
from pathlib import Path
import json
import hashlib
import time
from typing import Dict, Set, Tuple, List

from .Minifier import Minifier


class ChunkBuilder:
    """
    Handles the building of chunks from module files, including minification
    and manifest generation.
    """

    def __init__(self, output_dir: str, project_root: str = None):
        """
        Initialize a new ChunkBuilder instance.

        Args:
            output_dir (str): Directory where bundled chunks will be output
            project_root (str, optional): Root directory of the project for resolving internal imports
        """
        self.output_dir = Path(output_dir)
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.minifier = Minifier()
        self.chunk_hashes = {}
        self.processed_files = set()

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

    def _is_internal_module(self, module_name: str, current_module: Path) -> bool:
        """
        Check if a module is internal to the project.

        Args:
            module_name (str): The module name to check
            current_module (Path): Path of the current module being processed

        Returns:
            bool: True if the module is internal to the project
        """
        parts = module_name.split(".")
        module_dir = current_module.parent

        # Check relative to current file first (most common for project imports)
        if len(parts) == 1:
            # Single module name like 'utils'
            if (module_dir / parts[0] / "__init__.py").exists():
                return True
            if (module_dir / f"{parts[0]}.py").exists():
                return True
            # Check parent directory for sibling packages
            if (module_dir.parent / parts[0] / "__init__.py").exists():
                return True
            if (module_dir.parent / f"{parts[0]}.py").exists():
                return True
        else:
            # Multi-part like 'utils.math_helpers'
            relative_module = module_dir / Path(*parts[:-1]) / f"{parts[-1]}.py"
            if relative_module.exists():
                return True
            relative_package = module_dir / Path(*parts) / "__init__.py"
            if relative_package.exists():
                return True
            # Check parent directory for sibling packages
            parent_module = module_dir.parent / Path(*parts[:-1]) / f"{parts[-1]}.py"
            if parent_module.exists():
                return True
            parent_package = module_dir.parent / Path(*parts) / "__init__.py"
            if parent_package.exists():
                return True

        # Check from project root
        module_path = self.project_root / Path(module_name.replace(".", "/") + ".py")
        if module_path.exists():
            return True

        package_init = (
            self.project_root / Path(module_name.replace(".", "/")) / "__init__.py"
        )
        if package_init.exists():
            return True

        return False

    def _resolve_internal_module_path(self, module_name: str, current_module: Path) -> Path:
        """
        Resolve an internal module name to its file path.

        Args:
            module_name (str): The module name to resolve
            current_module (Path): Path of the current module being processed

        Returns:
            Path: Resolved path to the module file, or None if not found
        """
        module_path = self.project_root / Path(module_name.replace(".", "/") + ".py")
        if module_path.exists():
            return module_path

        package_init = (
            self.project_root / Path(module_name.replace(".", "/")) / "__init__.py"
        )
        if package_init.exists():
            return package_init

        module_dir = current_module.parent
        relative_module_path = module_dir / Path(module_name.replace(".", "/") + ".py")
        if relative_module_path.exists():
            return relative_module_path

        relative_package_init = (
            module_dir / Path(module_name.replace(".", "/")) / "__init__.py"
        )
        if relative_package_init.exists():
            return relative_package_init

        return None

    def _resolve_relative_import(
        self, current_module: Path, import_node: ast.ImportFrom
    ) -> Path:
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
    chunk_path = os.path.join(os.path.dirname(__file__), f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, chunk_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
    """

    def build_chunk(
        self,
        chunk_name: str,
        modules: Set[Path],
        sorted_modules: List[Path],
        module_to_chunk: Dict[Path, str],
    ) -> Tuple[Path, str]:
        """
        Build a chunk file containing multiple module contents.

        Args:
            chunk_name (str): Name of the chunk
            modules (Set[Path]): Set of module paths to include in the chunk
            sorted_modules (List[Path]): Topologically sorted modules
            module_to_chunk (Dict[Path, str]): Mapping of modules to their chunks

        Returns:
            Tuple[Path, str]: Tuple of (chunk output path, hashed filename)
            Returns (None, None) if the chunk would be empty or contains no meaningful content
        """
        modules_in_sorted = [m for m in modules if m in sorted_modules]
        if not modules_in_sorted:
            print(f"Skipping empty chunk: {chunk_name}")
            return None, None

        if chunk_name != "main":
            modules_in_other_chunks = []
            for module in modules_in_sorted:
                assigned_chunk = module_to_chunk.get(module)
                if (
                    assigned_chunk
                    and assigned_chunk != chunk_name
                    and assigned_chunk == "main"
                ):
                    modules_in_other_chunks.append(module)

            for module in modules_in_other_chunks:
                if module in modules:
                    modules.remove(module)

            modules_in_sorted = [m for m in modules if m in sorted_modules]
            if not modules_in_sorted:
                print(
                    f"Skipping redundant chunk: {chunk_name} (all modules already in other chunks)"
                )
                return None, None

        chunk_code = []
        import_tracker = {
            "standard": set(),
            "relative": set(),
            "third_party": set(),
            "chunk_imports": set(),
        }

        chunk_code.append(f"# Chunk: {chunk_name}")

        if chunk_name == "main":
            chunk_code.append(self._get_loader_code())

            for mod in self.processed_files:
                if mod in module_to_chunk and module_to_chunk[mod] != "main":
                    chunk_module = module_to_chunk[mod]
                    print("test", module_to_chunk)
                    import_tracker["chunk_imports"].add(f"{chunk_module}")

        for module in modules:
            if module in sorted_modules:
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
                                elif self._is_internal_module(name.name, module):
                                    # Skip internal imports - they will be bundled in this chunk
                                    continue
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
                                if self._is_stdlib_module(node.module):
                                    import_line = f"from {node.module} import {', '.join(n.name for n in node.names)}"
                                    import_tracker["standard"].add(import_line)
                                elif self._is_internal_module(node.module, module):
                                    # Skip internal imports - they will be bundled in this chunk
                                    continue
                                else:
                                    import_line = f"from {node.module} import {', '.join(n.name for n in node.names)}"
                                    import_tracker["third_party"].add(import_line)

        chunk_code.extend(sorted(import_tracker["standard"]))
        chunk_code.extend(sorted(import_tracker["third_party"]))
        chunk_code.extend(sorted(import_tracker["relative"]))

        if chunk_name == "main" and import_tracker["chunk_imports"]:
            for chunk_import in sorted(import_tracker["chunk_imports"]):
                chunk_hash = self.chunk_hashes.get(chunk_import)
                if chunk_hash:
                    chunk_code.append(f"__load_chunk__('{chunk_import}.{chunk_hash}')")
                else:
                    chunk_code.append(f"__load_chunk__('{chunk_import}')")

        has_meaningful_content = False
        for module in sorted_modules:  # Use sorted order instead of iterating through modules set
            if module in modules:  # Only process if it's in this chunk
                with open(module, "r") as f:
                    content = f.read()
                    lines = content.split("\n")
                    filtered_lines = []
                    in_main_block = False

                    for line in lines:
                        stripped = line.strip()

                        if stripped and not stripped.startswith(("import ", "from ")):
                            if stripped == "if __name__ == '__main__':":
                                in_main_block = True
                                if chunk_name == "main":
                                    filtered_lines.append(line)
                            elif in_main_block:
                                if chunk_name == "main":
                                    filtered_lines.append(line)
                            else:
                                filtered_lines.append(line)

                        if in_main_block and not stripped:
                            if not any(
                                next_line.startswith(" ")
                                for next_line in lines[lines.index(line) + 1 :]
                                if next_line.strip()
                            ):
                                in_main_block = False

                    module_content = "\n".join(filtered_lines)

                    if module_content.strip():
                        has_meaningful_content = True

                    chunk_code.append(f"\n# Module: {module.name}")

                    try:
                        minified_content = self.minify_code(module_content)
                        chunk_code.append(minified_content)
                    except Exception as e:
                        print(f"Error processing module {module}: {e}")
                        chunk_code.append(module_content)

        if not has_meaningful_content:
            print(f"Skipping chunk {chunk_name} with no meaningful content")
            return None, None

        chunk_content = "\n\n".join(chunk_code)
        chunk_hash = self.generate_chunk_hash(chunk_content)
        self.chunk_hashes[chunk_name] = chunk_hash
        hashed_filename = f"{chunk_name}.{chunk_hash}.py"
        output_path = self.output_dir / hashed_filename

        if chunk_name != "main":
            for module in modules:
                if module in self.processed_files and module in module_to_chunk:
                    self.processed_files.add((module, chunk_hash))

        os.makedirs(self.output_dir, exist_ok=True)

        with open(output_path, "w", newline="\n") as f:
            f.write(self.minifier.minify(chunk_content))

        return output_path, hashed_filename

    def generate_chunk_manifest(
        self,
        chunks: Dict[str, Set[Path]],
        module_to_chunk: Dict[Path, str],
        chunk_dependencies: Dict[str, Set[str]],
    ):
        """
        Generate a manifest.json file containing chunk metadata.

        Args:
            chunks (Dict[str, Set[Path]]): Dictionary mapping chunk names to their modules
            module_to_chunk (Dict[Path, str]): Dictionary mapping modules to their chunk name
            chunk_dependencies (Dict[str, Set[str]]): Dictionary mapping chunk names to their dependent chunks
        """
        manifest = {
            "version": int(time.time()),
            "chunks": {
                chunk_name: {
                    "modules": [str(m) for m in modules],
                    "file": f"{chunk_name}.{self.chunk_hashes[chunk_name]}.py",
                    "imports": list(chunk_dependencies.get(chunk_name, [])),
                }
                for chunk_name, modules in chunks.items()
            },
            "moduleToChunk": {
                str(module): chunk_name
                for module, chunk_name in module_to_chunk.items()
            },
            "fileMap": {
                f"{chunk_name}.py": f"{chunk_name}.{self.chunk_hashes[chunk_name]}.py"
                for chunk_name in chunks.keys()
            },
        }

        with open(self.output_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)
