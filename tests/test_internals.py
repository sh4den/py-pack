"""Unit tests for import resolution, chunking and the emitted bundle."""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
EXAMPLES = REPO_ROOT / "example"

sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import projects  # noqa: E402
from classes.ChunkConfig import ChunkConfig  # noqa: E402
from classes.ModuleResolver import (  # noqa: E402
    ImportScanner,
    ModuleResolver,
    absolute_import_name,
    ancestors_of,
    package_root_for,
    read_source,
)
from classes.Packer import Packer  # noqa: E402

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))


def equal(name, actual, expected):
    check(name, actual == expected, f"expected {expected!r}, got {actual!r}")


def pack(entry, **kwargs):
    """Pack a project into a throwaway directory and return (packer, dir)."""
    output_dir = Path(tempfile.mkdtemp(prefix="pypack-unit-"))
    packer = Packer(str(entry), str(output_dir), quiet=True, **kwargs)
    return packer, output_dir


# ---------------------------------------------------------------------------
# Naming and relative imports
# ---------------------------------------------------------------------------


def test_package_root_for():
    root, name = package_root_for(EXAMPLES / "app.py")
    equal("root of a plain script", root, EXAMPLES)
    equal("name of a plain script", name, "app")

    root, name = package_root_for(projects.path("packaged") / "toolkit" / "cli.py")
    equal("root of a module in a package", root, projects.path("packaged"))
    equal("name of a module in a package", name, "toolkit.cli")

    root, name = package_root_for(projects.path("relatives") / "app" / "core" / "util" / "helpers.py")
    equal("root of a deeply nested module", root, projects.path("relatives"))
    equal("name of a deeply nested module", name, "app.core.util.helpers")

    root, name = package_root_for(projects.path("relatives") / "app" / "__init__.py")
    equal("name of a package __init__", name, "app")


def test_absolute_import_name():
    equal("absolute import", absolute_import_name("pkg.sub", 0, "os.path"), "os.path")
    equal("one dot", absolute_import_name("pkg.sub", 1, None), "pkg.sub")
    equal("one dot with module", absolute_import_name("pkg.sub", 1, "x"), "pkg.sub.x")
    equal("two dots", absolute_import_name("pkg.sub", 2, "other"), "pkg.other")
    equal("three dots from two levels", absolute_import_name("pkg.sub", 3, "x"), None)
    equal("relative from a top-level module", absolute_import_name("", 1, "x"), None)
    equal("ancestors", ancestors_of("a.b.c"), ["a", "a.b"])


def test_resolver():
    resolver = ModuleResolver([projects.path("namespaces")])

    equal(
        "resolves a package",
        resolver.resolve("ns.alpha"),
        ((projects.path("namespaces") / "ns" / "alpha" / "__init__.py").resolve(), True),
    )
    equal(
        "resolves a module",
        resolver.resolve("ns.alpha.a"),
        ((projects.path("namespaces") / "ns" / "alpha" / "a.py").resolve(), False),
    )
    equal("namespace package is not a file", resolver.resolve("ns"), None)
    equal(
        "namespace package has a directory",
        resolver.namespace_dir("ns"),
        (projects.path("namespaces") / "ns").resolve(),
    )
    equal("rejects keywords", resolver.resolve("ns.class"), None)
    equal("rejects empty names", resolver.resolve(""), None)
    equal("unknown name", resolver.resolve("nope.nope"), None)


def test_import_scanner():
    scanner = ImportScanner()
    tree = ast.parse(
        "import a.b.c\n"
        "from .sib import thing\n"
        "from ..up import other\n"
        "import importlib\n"
        "importlib.import_module('dyn.mod')\n"
        "importlib.import_module('.rel', package=__name__)\n"
        "importlib.import_module('plug.' + name)\n"
    )
    required, speculative, computed = scanner.scan(tree, "pkg.sub")

    check("plain import recorded", "a.b.c" in required)
    check("relative import resolved", "pkg.sub.sib" in required)
    check("parent-relative import resolved", "pkg.up" in required)
    check("from-import name is speculative", "pkg.sub.sib.thing" in speculative)
    check("absolute dynamic import found", "dyn.mod" in speculative)
    check("relative dynamic import found", "pkg.sub.rel" in speculative)
    equal("computed import reported once", len(computed), 1)
    check("computed import keeps its line", computed and computed[0][0] == 7)


def test_encoding_cookie_is_neutralised():
    source = read_source(projects.path("tricky") / "accents.py")
    check("cookie removed", "coding: latin-1" not in source)
    check("non-ascii decoded", "café crème" in source)
    try:
        compile(source, "<test>", "exec")
        check("source compiles as a unicode string", True)
    except SyntaxError as error:
        check("source compiles as a unicode string", False, str(error))


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def test_graph_contents():
    packer, output_dir = pack(EXAMPLES / "app.py")
    try:
        packer.build_graph()
        equal(
            "first-party modules bundled",
            sorted(packer.modules),
            [
                "app",
                "models",
                "models.product",
                "models.user",
                "services",
                "services.calculator",
                "services.store",
                "utils",
                "utils.math_helpers",
                "utils.string_helpers",
            ],
        )
        check("no stdlib bundled", not any(n.startswith("os") for n in packer.modules))
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_third_party_stays_external():
    packer, output_dir = pack(projects.path("packaged") / "toolkit" / "cli.py")
    try:
        packer.build_graph()
        equal(
            "only first-party modules bundled",
            sorted(packer.modules),
            ["toolkit", "toolkit.cli", "toolkit.client", "toolkit.core"],
        )
        check("requests left external", "requests" in packer.external_modules)
        check("argparse left external", "argparse" in packer.external_modules)
        check("dataclasses left external", "dataclasses" in packer.external_modules)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_cycles_do_not_raise():
    packer, output_dir = pack(projects.path("tricky") / "main.py")
    try:
        packer.build_graph()
        packer.topological_sort()
        check("cycle members present", {"cyc_a", "cyc_b"} <= set(packer.modules))
        equal(
            "every module is ordered",
            sorted(packer.sorted_modules),
            sorted(packer.modules),
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_package_directory_entry_point():
    packer, output_dir = pack(projects.path("relatives") / "app")
    try:
        equal("directory entry resolves to __main__", packer.entry_module, "app.__main__")
        packer.build_graph()
        check("package __init__ bundled", "app" in packer.modules)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_chunk_assignment_and_lazy_loading():
    packer, output_dir = pack(EXAMPLES / "app.py")
    try:
        packer.configure_chunks([ChunkConfig(name="vendor", includes=[r"^utils/"])])
        result = packer.pack()

        equal("two chunks emitted", sorted(result["chunks"]), ["main", "vendor"])
        equal(
            "utils went to vendor",
            sorted(
                name
                for name, chunk in packer.module_to_chunk.items()
                if chunk == "vendor"
            ),
            ["utils", "utils.math_helpers", "utils.string_helpers"],
        )
        equal("entry stays in main", packer.module_to_chunk["app"], "main")

        main_source = (output_dir / result["chunks"]["main"]).read_text()
        check(
            "vendor modules are not duplicated in main",
            "'utils.math_helpers': (" not in main_source,
        )
        check("main knows where to find them", "utils.math_helpers" in main_source)

        completed = subprocess.run(
            [sys.executable, str(output_dir / result["chunks"]["main"])],
            capture_output=True,
            text=True,
        )
        check("chunked bundle runs", completed.returncode == 0, completed.stderr)
        check("output looks right", "Tech Shop Inventory" in completed.stdout)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_auto_generate_chunks():
    packer, output_dir = pack(EXAMPLES / "app.py")
    try:
        packer.build_graph()
        packer.auto_generate_chunks()
        equal(
            "one chunk per top-level package",
            sorted(config.name for config in packer.chunk_configs),
            ["models", "services", "utils"],
        )
        result = packer.pack()
        equal(
            "all chunks emitted",
            sorted(result["chunks"]),
            ["main", "models", "services", "utils"],
        )
        completed = subprocess.run(
            [sys.executable, str(output_dir / result["chunks"]["main"])],
            capture_output=True,
            text=True,
        )
        check("auto-chunked bundle runs", completed.returncode == 0, completed.stderr)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def test_manifest():
    packer, output_dir = pack(EXAMPLES / "app.py")
    try:
        packer.configure_chunks([ChunkConfig(name="vendor", includes=[r"^utils/"])])
        packer.pack()

        import json

        manifest = json.loads((output_dir / "manifest.json").read_text())
        equal("manifest records the entry", manifest["entry"], "app")
        equal("manifest lists both chunks", sorted(manifest["chunks"]), ["main", "vendor"])
        check(
            "manifest maps modules to chunks",
            manifest["moduleToChunk"]["utils.math_helpers"] == "vendor",
        )
        check(
            "main depends on vendor",
            "vendor" in manifest["chunks"]["main"]["imports"],
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def test_runtime_template_is_valid_python():
    template = SRC / "classes" / "RuntimeTemplate.py"
    try:
        compile(template.read_text(), str(template), "exec")
        check("runtime template compiles", True)
    except SyntaxError as error:
        check("runtime template compiles", False, str(error))

    completed = subprocess.run(
        [sys.executable, str(template)],
        capture_output=True,
        text=True,
    )
    check(
        "runtime template is inert when run alone",
        completed.returncode == 0,
        completed.stderr,
    )


def test_bundle_is_hermetic():
    """No build path in the output, and no fallback onto the source tree.

    A package's ``__path__`` used to point at the directory it was built
    from, which let Python load an unbundled submodule from there whenever
    that directory happened to exist where the bundle ran.
    """
    project = Path(tempfile.mkdtemp(prefix="pypack-hermetic-"))
    (project / "pkg").mkdir()
    (project / "pkg" / "__init__.py").write_text("VALUE = 'bundled'\n")
    (project / "pkg" / "used.py").write_text("from . import VALUE\n")
    (project / "pkg" / "unbundled.py").write_text("SECRET = 'off disk'\n")
    (project / "app.py").write_text(
        "import importlib\n"
        "import pkg.used\n"
        "name = 'un' + 'bundled'\n"
        "print(pkg.used.VALUE)\n"
        "try:\n"
        "    print('LEAK', importlib.import_module(f'pkg.{name}').SECRET)\n"
        "except ModuleNotFoundError:\n"
        "    print('sealed')\n"
    )

    packer, output_dir = pack(project / "app.py")
    try:
        result = packer.pack()
        bundle = output_dir / result["chunks"]["main"]

        text = bundle.read_text()
        check(
            "no build path in the bundle",
            str(project) not in text,
            "the source tree's path is embedded in the output",
        )

        # Run with the source tree still in place; it must be ignored.
        completed = subprocess.run(
            [sys.executable, str(bundle)],
            cwd=str(project),
            capture_output=True,
            text=True,
        )
        check("bundled module still loads", "bundled" in completed.stdout, completed.stderr)
        check(
            "unbundled module cannot be pulled off disk",
            "sealed" in completed.stdout,
            completed.stdout + completed.stderr,
        )
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def test_output_carries_no_comments():
    """Chunks must be free of comments, in both output modes."""
    import io
    import tokenize

    for compress in (True, False):
        packer, output_dir = pack(projects.path("tricky") / "main.py", compress=compress)
        try:
            result = packer.pack()
            for filename in result["chunks"].values():
                text = (output_dir / filename).read_text()
                comments = [
                    token
                    for token in tokenize.generate_tokens(io.StringIO(text).readline)
                    if token.type == tokenize.COMMENT
                ]
                check(
                    f"no comments in {filename} (compress={compress})",
                    not comments,
                    f"found {[t.string[:40] for t in comments]}",
                )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


def test_compression_shrinks_output():
    entry = projects.path("packaged") / "toolkit" / "cli.py"
    small, small_dir = pack(entry)
    plain, plain_dir = pack(entry, minify=False, compress=False)
    try:
        packed_bundle = small_dir / small.pack()["chunks"]["main"]
        readable_bundle = plain_dir / plain.pack()["chunks"]["main"]
        packed = packed_bundle.stat().st_size
        readable = readable_bundle.stat().st_size
        check(
            "compressed output is much smaller",
            packed < readable / 2,
            f"{packed} vs {readable} bytes",
        )
        completed = subprocess.run(
            [sys.executable, str(readable_bundle), "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "tests" / "stubs")},
        )
        check("readable bundle still runs", completed.returncode == 0, completed.stderr)
    finally:
        shutil.rmtree(small_dir, ignore_errors=True)
        shutil.rmtree(plain_dir, ignore_errors=True)


def test_output_is_deterministic():
    first, first_dir = pack(EXAMPLES / "app.py")
    second, second_dir = pack(EXAMPLES / "app.py")
    try:
        one = first.pack()["chunks"]["main"]
        two = second.pack()["chunks"]["main"]
        equal("same input gives the same content hash", one, two)
    finally:
        shutil.rmtree(first_dir, ignore_errors=True)
        shutil.rmtree(second_dir, ignore_errors=True)


def test_missing_entry_point():
    try:
        Packer("does/not/exist.py", "/tmp/nope", quiet=True)
        check("missing entry point is reported", False, "no error raised")
    except FileNotFoundError:
        check("missing entry point is reported", True)


def main():
    if not (EXAMPLES / "app.py").exists():
        print("missing example project: example/app.py")
        return 1

    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()

    failures = [(name, detail) for name, ok, detail in RESULTS if not ok]
    for name, ok, detail in RESULTS:
        print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f" -- {detail}"))

    print()
    if failures:
        print(f"{len(failures)} of {len(RESULTS)} checks failed")
        return 1
    print(f"all {len(RESULTS)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
