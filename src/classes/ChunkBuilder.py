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

    def __init__(self, output_dir: str):
        """
        Initialize a new ChunkBuilder instance.

        Args:
            output_dir (str): Directory where bundled chunks will be output
        """
        self.output_dir = Path(output_dir)
        self.minifier = Minifier()
        self.chunk_hashes = {}

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
        self, chunk_name: str, modules: Set[Path], sorted_modules: List[Path]
    ) -> Tuple[Path, str]:
        """
        Build a chunk file containing multiple module contents.

        Args:
            chunk_name (str): Name of the chunk
            modules (Set[Path]): Set of module paths to include in the chunk
            sorted_modules (List[Path]): Topologically sorted modules

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
            if module in sorted_modules:
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
        self.chunk_hashes[chunk_name] = chunk_hash
        hashed_filename = f"{chunk_name}.{chunk_hash}.py"
        output_path = self.output_dir / hashed_filename

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
