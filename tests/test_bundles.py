"""Differential tests: a bundle must behave exactly like the project it packs.

Each case runs the project directly, packs it, runs the bundle, and compares
stdout, stderr and the exit status. Every configuration (minified, readable,
split into chunks) has to produce the same result.
"""

import os
import re
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
from classes.Packer import Packer  # noqa: E402


class Case:
    """One project, how to run it, and how to bundle it."""

    def __init__(
        self,
        name,
        entry,
        run_target,
        cwd,
        argv=(),
        chunks=None,
        path=(),
        include=(),
    ):
        self.name = name
        self.entry = entry
        self.run_target = run_target
        self.cwd = cwd
        self.argv = list(argv)
        self.chunks = chunks
        #: Modules only reachable through a name built at runtime.
        self.include = list(include)
        #: Extra PYTHONPATH entries, for third-party imports the bundle is
        #: expected to leave alone and resolve at runtime.
        self.path = [str(entry) for entry in path]


CASES = [
    Case(
        "example-app",
        entry=EXAMPLES / "app.py",
        run_target=["app.py"],
        cwd=EXAMPLES,
    ),
    Case(
        "packaged-cli",
        entry=projects.path("packaged") / "toolkit" / "cli.py",
        run_target=["-m", "toolkit.cli"],
        cwd=projects.path("packaged"),
        argv=["one", "two", "--owner", "ada"],
        path=[REPO_ROOT / "tests" / "stubs"],
    ),
    Case(
        "packaged-cli-help",
        entry=projects.path("packaged") / "toolkit" / "cli.py",
        run_target=["-m", "toolkit.cli"],
        cwd=projects.path("packaged"),
        argv=["--help"],
        path=[REPO_ROOT / "tests" / "stubs"],
    ),
    Case(
        "relative-imports",
        entry=projects.path("relatives") / "app",
        run_target=["-m", "app"],
        cwd=projects.path("relatives"),
    ),
    Case(
        "namespace-packages",
        entry=projects.path("namespaces") / "main.py",
        run_target=["main.py"],
        cwd=projects.path("namespaces"),
    ),
    Case(
        "name-collisions",
        entry=projects.path("collisions") / "run.py",
        run_target=["run.py"],
        cwd=projects.path("collisions"),
        chunks=[ChunkConfig(name="vendor", includes=[r"^right/"])],
    ),
    Case(
        "dynamic-imports",
        entry=projects.path("dynamic") / "run.py",
        run_target=["run.py"],
        cwd=projects.path("dynamic"),
        include=["plugins.*"],
    ),
    Case(
        "tricky",
        entry=projects.path("tricky") / "main.py",
        run_target=["main.py"],
        cwd=projects.path("tricky"),
        chunks=[ChunkConfig(name="support", includes=[r"^(literals|models|docs)\.py$"])],
    ),
]


def run(command, cwd, path=()):
    """Run a command with a clean environment and capture everything."""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if path:
        environment["PYTHONPATH"] = os.pathsep.join(path)
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


FRAME_RE = re.compile(r'^File "(?P<file>.*)", line \d+(?:, in (?P<where>.*))?$')
TRACEBACK_START = "Traceback (most recent call last):"


def normalise_paths(text, output_dir, project_dir):
    """Replace the paths that legitimately differ between the two runs."""
    return (
        text.replace(str(output_dir), "<OUT>")
        .replace(str(project_dir), "<PROJECT>")
        .replace("\\", "/")
    )


def normalise_stderr(text, output_dir, project_dir):
    """Reduce tracebacks to the differences that would be real bugs.

    A bundle's traceback gains frames for the loader and loses runpy's, and a
    minified module reports different line numbers. What must still match is
    the sequence of *user* frames and the exception itself.
    """
    text = normalise_paths(text, output_dir, project_dir)

    kept = []
    in_traceback = False
    for line in text.splitlines():
        if line.strip() == TRACEBACK_START:
            in_traceback = True
            kept.append(line.strip())
            continue

        if not in_traceback:
            kept.append(line.rstrip())
            continue

        match = FRAME_RE.match(line.strip())
        if match:
            filename = match.group("file")
            # Bundler plumbing: the chunk file itself, and runpy's frames.
            if not filename.startswith(("<OUT>", "<frozen ")):
                filename = filename.replace("<pypack>", "<PROJECT>")
                kept.append(f'File "{filename}", in {match.group("where")}')
            continue

        if line.startswith("    "):
            # Source and caret lines under a frame; they change when minified.
            continue

        in_traceback = line.strip() == ""
        kept.append(line.rstrip())

    return "\n".join(kept)


def check_case(case, minify, use_chunks):
    """Pack one case and compare the bundle's behaviour with the original."""
    label = f"{case.name} [minify={minify}, chunks={use_chunks}]"

    expected = run(
        [sys.executable, *case.run_target, *case.argv],
        cwd=case.cwd,
        path=case.path,
    )

    output_dir = Path(tempfile.mkdtemp(prefix="pypack-test-"))
    try:
        packer = Packer(
            str(case.entry),
            str(output_dir),
            include=case.include,
            minify=minify,
            compress=minify,
            quiet=True,
        )
        if use_chunks and case.chunks:
            packer.configure_chunks(case.chunks)
        result = packer.pack()

        bundle = output_dir / result["chunks"]["main"]
        actual = run(
            [sys.executable, str(bundle), *case.argv],
            cwd=case.cwd,
            path=case.path,
        )

        if use_chunks and case.chunks:
            extra = set(result["chunks"]) - {"main"}
            if not extra:
                return False, f"{label}: expected extra chunks, got only main"

        cleaners = (None, normalise_paths, normalise_stderr)
        for index, what in enumerate(("exit status", "stdout", "stderr")):
            want = expected[index]
            got = actual[index]
            clean = cleaners[index]
            if clean is not None:
                want = clean(want, output_dir, case.cwd)
                got = clean(got, output_dir, case.cwd)
            if want != got:
                return False, (
                    f"{label}: {what} differs\n"
                    f"  --- original ---\n{want}\n"
                    f"  --- bundle ---\n{got}"
                )

        return True, label
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def main():
    missing = [case.name for case in CASES if not case.entry.exists()]
    if missing:
        print("missing projects: " + ", ".join(missing))
        return 1

    failures = []
    for case in CASES:
        for minify in (True, False):
            for use_chunks in ((False, True) if case.chunks else (False,)):
                ok, message = check_case(case, minify, use_chunks)
                print(("  ok   " if ok else "  FAIL ") + message.splitlines()[0])
                if not ok:
                    failures.append(message)

    print()
    if failures:
        for failure in failures:
            print(failure)
            print("-" * 60)
        print(f"{len(failures)} failing case(s)")
        return 1

    print("all cases behave identically to the unbundled project")
    return 0


if __name__ == "__main__":
    sys.exit(main())
