import base64
import hashlib
import json
import os
import time
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .Minifier import Minifier
from .ModuleResolver import SourceModule

RUNTIME_TEMPLATE = Path(__file__).with_name("RuntimeTemplate.py")

UNPACK_PRELUDE = (
    "import base64 as _pypack_b,zlib as _pypack_z\n"
    "def _pypack_unpack(b):\n"
    " import json;return json.loads(_pypack_z.decompress(_pypack_b.b85decode(b)))"
)


class ChunkBuilder:
    """Writes module tables, the bundle runtime and the manifest to disk.

    A chunk is a plain Python file holding the source of every module assigned
    to it, keyed by dotted name. The entry chunk additionally carries the
    runtime that installs an import hook for those modules and starts the
    entry point.
    """

    KIND_MODULE = 0
    KIND_PACKAGE = 1
    KIND_NAMESPACE = 2

    def __init__(
        self,
        output_dir: str,
        minifier: Optional[Minifier] = None,
        compress: bool = True,
        quiet: bool = False,
    ):
        """
        Args:
            output_dir: Directory where the chunks and manifest are written.
            minifier: Minifier used on each module source. Defaults to the
                conservative configuration.
            compress: Store module tables deflated and base85-encoded. Set to
                False to keep the chunks readable.
            quiet: Suppress progress output.
        """
        self.output_dir = Path(output_dir)
        self.minifier = minifier or Minifier()
        self.compress = compress
        self.quiet = quiet
        self.chunk_hashes: Dict[str, str] = {}
        self.chunk_files: Dict[str, str] = {}
        self.source_bytes = 0
        self.runtime_bytes = 0

    def generate_chunk_hash(self, content: str) -> str:
        """Generate a short content hash used for cache busting."""
        return hashlib.md5(content.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _pack_blob(data: bytes) -> str:
        """Deflate and base85-encode. The alphabet has no quote or backslash,
        so the result drops straight into a string literal."""
        return base64.b85encode(zlib.compress(data, 9)).decode("ascii")

    def _load_runtime(self) -> str:
        """Build the runtime bootstrap that goes into the entry chunk.

        Its docstrings and comments explain the bundle format to whoever
        maintains pypack, not to whoever runs the bundle, so they are stripped
        on the way in. Compressed bundles then carry it as a blob that the
        prelude expands into the chunk's own namespace.
        """
        source = self.minifier.minify_runtime(
            RUNTIME_TEMPLATE.read_text(encoding="utf-8")
        )
        if not self.compress:
            return source

        blob = self._pack_blob(source.encode("utf-8"))
        return (
            "exec(compile(_pypack_z.decompress(_pypack_b.b85decode("
            f"'{blob}')),'<pypack-runtime>','exec'))"
        )

    def _table_entries(self, modules: List[SourceModule]) -> Dict[str, list]:
        """Build a chunk's ``name -> (source, kind)`` table.

        Sources are stored as-is and executed later by the runtime, so nothing
        in them is rewritten and their semantics are preserved exactly. Where
        a module came from is deliberately not recorded: the runtime places it
        under the bundle's own directory, so the output carries no trace of
        the machine that built it.
        """
        table = {}

        for module in sorted(modules, key=lambda item: item.name):
            source = self.minifier.minify(module.source, filename=module.name)
            self.source_bytes += len(source.encode("utf-8"))

            if module.is_package:
                kind = (
                    self.KIND_PACKAGE if module.path is not None else self.KIND_NAMESPACE
                )
            else:
                kind = self.KIND_MODULE

            table[module.name] = [source, kind]

        return table

    def _render_module_table(self, modules: List[SourceModule]) -> str:
        """Render a chunk's module table, compressed unless asked otherwise."""
        table = self._table_entries(modules)

        if not self.compress:
            lines = ["__PYPACK_MODULES__ = {"]
            for name, (source, kind) in table.items():
                lines.append("    %r: (%r, %r)," % (name, source, kind))
            lines.append("}")
            return "\n".join(lines)

        payload = json.dumps(table, ensure_ascii=False, separators=(",", ":"))
        blob = self._pack_blob(payload.encode("utf-8"))
        return "\n".join(
            [UNPACK_PRELUDE, f"__PYPACK_MODULES__=_pypack_unpack('{blob}')"]
        )

    def build_chunk(
        self,
        chunk_name: str,
        modules: List[SourceModule],
        is_entry_chunk: bool = False,
        entry_module: Optional[str] = None,
        module_chunks: Optional[Dict[str, str]] = None,
    ) -> Tuple[Optional[Path], Optional[str]]:
        """Write one chunk to the output directory.

        Args:
            chunk_name: Name of the chunk.
            modules: Modules assigned to this chunk.
            is_entry_chunk: Whether to embed the runtime and start the entry.
            entry_module: Dotted name of the module to run as ``__main__``.
            module_chunks: Map of module name to chunk for modules that live
                in other chunks, so the runtime can load them lazily.

        Returns:
            A (path, filename) pair, or (None, None) when the chunk is empty.
        """
        if not modules:
            if not self.quiet:
                print(f"  ! skipping empty chunk: {chunk_name}")
            return None, None

        parts = [f"__PYPACK_CHUNK__={chunk_name!r}"]
        parts.append(self._render_module_table(modules))

        if is_entry_chunk:
            compact = {"separators": (",", ":"), "sort_keys": True}
            parts.append(f"__PYPACK_ENTRY__={entry_module!r}")
            parts.append(
                "__PYPACK_MODULE_CHUNKS__=" + json.dumps(module_chunks or {}, **compact)
            )
            parts.append(
                "__PYPACK_CHUNK_FILES__=" + json.dumps(self.chunk_files, **compact)
            )
            runtime = self._load_runtime()
            self.runtime_bytes = len(runtime.encode("utf-8"))
            parts.append(runtime)

        content = "\n".join(parts) + "\n"
        chunk_hash = self.generate_chunk_hash(content)
        self.chunk_hashes[chunk_name] = chunk_hash

        filename = f"{chunk_name}.{chunk_hash}.py"
        self.chunk_files[chunk_name] = filename
        output_path = self.output_dir / filename

        os.makedirs(self.output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)

        return output_path, filename

    def generate_chunk_manifest(
        self,
        chunks: Dict[str, List[SourceModule]],
        module_chunks: Dict[str, str],
        chunk_dependencies: Dict[str, set],
        entry_module: Optional[str] = None,
    ) -> Path:
        """Write ``manifest.json`` describing the emitted chunks."""
        manifest = {
            "version": int(time.time()),
            "entry": entry_module,
            "chunks": {
                chunk_name: {
                    "file": self.chunk_files.get(chunk_name),
                    "modules": sorted(module.name for module in modules),
                    "files": sorted(
                        str(module.path) for module in modules if module.path
                    ),
                    "imports": sorted(chunk_dependencies.get(chunk_name, set())),
                }
                for chunk_name, modules in chunks.items()
                if chunk_name in self.chunk_files
            },
            "moduleToChunk": dict(sorted(module_chunks.items())),
            "fileMap": {
                f"{chunk_name}.py": filename
                for chunk_name, filename in sorted(self.chunk_files.items())
            },
        }

        os.makedirs(self.output_dir, exist_ok=True)
        manifest_path = self.output_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")

        return manifest_path
