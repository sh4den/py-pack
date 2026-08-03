# Python Package Chunker (PyPack)

[![tests](https://github.com/sh4den/py-pack/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/sh4den/py-pack/actions/workflows/tests.yml)

A webpack-like bundler for Python that packs a project into self-contained chunks.

![image](https://github.com/user-attachments/assets/0598d4c8-88a6-4c68-8b59-a8e0c4f48454)

## Features

-   📦 Whole-project bundling into one file, or into several chunks
-   🔍 Static import-graph analysis, including relative and dynamic imports
-   🧬 Exact Python semantics: each module keeps its own namespace
-   🗺️ Chunk manifest generation
-   🔄 Lazy chunk loading at runtime
-   ⚡ Webpack-like configuration
-   📜 Regex-based file matching

## Installation

```bash
git clone https://github.com/sh4den/py-pack.git
cd py-pack
pip install -e .
```

## Usage

```bash
python src/main.py ./example/app.py -o ./dist
python dist/main.*.py
```

Or from Python:

```python
from classes.ChunkConfig import ChunkConfig
from classes.Packer import Packer

packer = Packer("./example/app.py", "./dist")
packer.configure_chunks([
    ChunkConfig(name="vendor", includes=[r"^utils/"]),
])
packer.pack()
```

The entry point may be a `.py` file or a package directory containing a
`__main__.py`. A module inside a package is bundled the way `python -m` would
run it, so its relative imports keep working.

### Command line

| Flag | Meaning |
| --- | --- |
| `-o, --output DIR` | Output directory (default `./dist`) |
| `-r, --root DIR` | Extra source root for resolving absolute imports |
| `-i, --include PATTERN` | Bundle modules nothing imports by a literal name |
| `--chunk NAME=REGEX` | Put modules whose path matches `REGEX` into `NAME` |
| `--auto-chunks` | Derive one chunk per top-level package |
| `--no-minify` | Emit readable, uncompressed chunks |
| `--aggressive-minify` | Also drop annotations and docstrings |
| `-q, --quiet` | Suppress build output |

## How it works

Each module's source is stored in the chunk as a string, keyed by its dotted
name. The entry chunk carries a small runtime that registers a
`sys.meta_path` finder for those names and then executes the entry module as
`__main__`.

The bundle is self-contained. It records nothing about the machine that built
it: a module's `__file__` and a package's `__path__` are anchored to the
directory the chunk lives in, so the same bundle behaves identically wherever
it runs. In particular a bundled package can never quietly load an unbundled
submodule from the original source tree — the paths name no real file.

Output is built for size. Sources are minified, the module table is deflated
and base85-encoded, and the runtime is stripped and compressed alongside it,
so a chunk contains no comments and the runtime costs a flat 2.3 KB however
big the project is. Where a module came from is not stored at all, since its
name already says where it goes. `--no-minify` turns all of that off and emits
a readable chunk instead.

| Project | Source | Bundle |
| --- | --- | --- |
| `example/app.py`, 10 modules | 4.5 KB | 4.1 KB |
| synthetic, 723 small modules | 11.1 KB | 6.7 KB |

Small projects stay close to their source size because the runtime is a fixed
cost; it stops mattering as soon as there is real code to bundle.

Nothing in your source is rewritten, and every module is executed in its own
namespace, so the bundle behaves like the project it came from:

-   relative imports (`from ..core import thing`) at any depth
-   circular imports, including the half-initialised-module case
-   two modules that define the same top-level names
-   `__all__`, star imports, `global`, module-level `__getattr__`
-   `__name__`, `__package__` and `__spec__`
-   implicit namespace packages
-   a local module that shadows a standard-library one
-   docstrings, annotations and anything reading them at runtime
-   source files with a non-UTF-8 encoding declaration

Standard-library and third-party imports are left alone and resolved normally
when the bundle runs, so a bundled project still needs its dependencies
installed.

## Configuration

### ChunkConfig

-   `name`: Name of the chunk. `main` is reserved for the entry chunk.
-   `entry_points`: Files that must land in this chunk. Relative paths are
    resolved against the source root.
-   `includes`: Regular expressions matched (with `re.search`) against each
    module's absolute path and its path relative to the source root, so
    `r"^services/"` works on every platform.
-   `modules`: Dotted module names to place in this chunk.

The entry module always stays in `main`; anything no config claims joins it.
Other chunks are read from disk only when one of their modules is first
imported.

## Output

-   `main.<hash>.py` — the entry chunk: run this
-   `<name>.<hash>.py` — one file per additional chunk
-   `manifest.json` — entry point, chunk contents, module-to-chunk map and
    inter-chunk dependencies

## Limitations

-   **Names built at runtime.** `import_module(f"plugins.{name}")` cannot be
    traced. The build warns and points at the call site; list the targets with
    `--include 'plugins.*'` to bundle them.
-   **Compiled extensions** (`.so`, `.pyd`) cannot be inlined and must be
    installed where the bundle runs.
-   **Data files** are not collected, and `__file__` points inside the bundle
    rather than at the original source, so code that opens a file next to its
    module needs that file copied alongside the chunk. Tracebacks are
    unaffected: they read the bundled source through the loader.
-   **`--aggressive-minify`** drops annotations and docstrings. That breaks
    `typing.get_type_hints`, pydantic, and `argparse(description=__doc__)`, so
    it is off by default.

## Tests

```bash
python tests/test_internals.py   # import resolution, chunking, manifest
python tests/test_bundles.py     # every example, bundled vs. unbundled
```

`tests/test_bundles.py` runs each project directly and then as a bundle, and
requires stdout, stderr and the exit status to match.

The projects it exercises live in `tests/projects.py`, written out to a
temporary directory as each run starts. They are defined as data rather than
checked in as files so the suite works from a bare checkout, and they cover
the awkward corners listed above: relative imports at depth, namespace
packages, colliding module names, circular imports, computed imports, a
latin-1 source, and a packaged CLI entered through `python -m`.

### Continuous integration

`.github/workflows/tests.yml` runs on every push to `main` and on pull
requests targeting it:

-   **tests** — both suites, on Python 3.13 and 3.14.
-   **bundle-compat** — builds a bundle, then runs it on 3.9 through 3.14 and
    diffs the output against the unbundled project. The bundler needs 3.13+
    but what it emits has to keep working on older interpreters, and only this
    job would notice if it stopped.

## Requirements

-   The bundler: Python 3.13+ (see `pyproject.toml`), `python-minifier`,
    `termcolor`
-   The bundles it produces: Python 3.9+ (checked on 3.9 through 3.14 in CI)

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
