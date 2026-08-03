"""Test projects, defined as data and written to a temporary directory.

The suite used to read these from ``example/``, which made it fail whenever
the working tree was cleaned. Keeping them here means the tests carry
everything they need and can run from a bare checkout.

Each project maps a relative path to its contents: ``str`` is written as
UTF-8, ``bytes`` verbatim, which is how the latin-1 source below survives.
"""

import atexit
import shutil
import tempfile
from pathlib import Path

PROJECTS = {}


# ---------------------------------------------------------------------------
# A packaged command-line tool: run as `python -m toolkit.cli`.
# Covers parenthesised relative imports, dataclasses whose annotations are
# read at runtime, argparse taking its description from __doc__, and a
# third-party import that must be left for the runtime to resolve.
# ---------------------------------------------------------------------------

PROJECTS["packaged"] = {
    "toolkit/__init__.py": '''"""Toolkit — a stand-in for a real packaged application."""

__version__ = "2.1.0"
''',
    "toolkit/core.py": '''"""Data model, kept in annotations that survive to runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class ToolkitError(RuntimeError):
    pass


@dataclass
class Job:
    name: str
    retries: int = 0
    tags: List[str] = field(default_factory=list)
    owner: Optional[str] = None

    def describe(self) -> str:
        parts = [self.name, f"retries={self.retries}"]
        if self.tags:
            parts.append("tags=" + ",".join(sorted(self.tags)))
        if self.owner:
            parts.append(f"owner={self.owner}")
        return " ".join(parts)


def build_jobs(names) -> List[Job]:
    return [Job(name=name, retries=index) for index, name in enumerate(names)]
''',
    "toolkit/client.py": '''"""Module-scope third-party import: the bundler must leave it alone."""

from __future__ import annotations

import requests

from .core import ToolkitError

ENDPOINT = "https://example.invalid/jobs"


class Client:
    def __init__(self, endpoint: str = ENDPOINT):
        if not endpoint:
            raise ToolkitError("an endpoint is required")
        self.endpoint = endpoint
        self.session = requests.Session()

    def fetch(self):
        return self.session.get(self.endpoint).json()
''',
    "toolkit/cli.py": '''"""Run jobs from the command line, the way a packaged tool would."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .client import Client
from .core import (
    Job,
    ToolkitError,
    build_jobs,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolkit", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("names", nargs="*", default=["alpha", "beta"])
    parser.add_argument("--owner", default=None)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        jobs = build_jobs(args.names)
    except ToolkitError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    for job in jobs:
        job.owner = args.owner
        print(job.describe())

    print("annotations kept:", sorted(Job.__annotations__))
    print("client endpoint:", Client().endpoint)
    print("module:", __name__, "package:", __package__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
''',
}


# ---------------------------------------------------------------------------
# Relative imports at every depth, entered through a package's __main__.py.
# ---------------------------------------------------------------------------

PROJECTS["relatives"] = {
    "app/__init__.py": '''"""Package executed with `python -m app`."""

NAME = "relatives"
''',
    "app/__main__.py": """from . import NAME
from .core import describe
from .core.engine import run

print("entry package:", __package__)
print("name:", NAME)
print(describe())
print(run(3))
""",
    "app/core/__init__.py": """from .util.helpers import shout


def describe():
    return shout("core package")
""",
    "app/core/engine.py": """from .. import NAME
from . import util
from .util import helpers
from .util.helpers import shout


def run(times):
    parts = [helpers.shout(NAME) for _ in range(times)]
    return util.helpers.join(parts) + "|" + shout("done")
""",
    "app/core/util/__init__.py": """from . import helpers
from .helpers import join
""",
    "app/core/util/helpers.py": """from ... import NAME


def shout(text):
    return f"<{text.upper()}:{NAME}>"


def join(parts):
    return "+".join(parts)
""",
}


# ---------------------------------------------------------------------------
# Implicit namespace packages: `ns` and `ns.beta` have no __init__.py.
# ---------------------------------------------------------------------------

PROJECTS["namespaces"] = {
    "main.py": """import ns.beta.b
from ns.alpha.a import A
from ns.beta.deeper.d import D

print("alpha:", A)
print("beta:", ns.beta.b.B)
print("deeper:", D)
print("ns is a package:", hasattr(ns, "__path__"))
""",
    "ns/alpha/__init__.py": 'ALPHA = "alpha-init"\n',
    "ns/alpha/a.py": """from . import ALPHA

A = f"A({ALPHA})"
""",
    "ns/beta/b.py": 'B = "B(no-init-anywhere)"\n',
    "ns/beta/deeper/d.py": """from ns.beta.b import B

D = f"D({B})"
""",
}


# ---------------------------------------------------------------------------
# Two packages defining the same top-level names, plus a star import and a
# module-level `global` counter that must not be shared.
# ---------------------------------------------------------------------------

_COLLIDING_MODULE = '''VERSION = "{version}"
COUNTER = 0


class Config:
    label = "{side}-config"


def shared_helper():
    return "{side} helper"


def bump():
    global COUNTER
    COUNTER += 1
'''

PROJECTS["collisions"] = {
    "run.py": """import left.mod
import right.mod as rmod
from left import *
from right.mod import Config as RightConfig

print("star import brought:", sorted(n for n in dir() if n.startswith("shared")))
print("left VERSION:", left.mod.VERSION)
print("right VERSION:", rmod.VERSION)
print("left Config:", left.mod.Config().label)
print("right Config:", RightConfig().label)

left.mod.bump()
left.mod.bump()
rmod.bump()
print("counters:", left.mod.COUNTER, rmod.COUNTER)
print("shared_helper says:", shared_helper())
""",
    "left/__init__.py": """from .mod import shared_helper

__all__ = ["shared_helper"]
""",
    "left/mod.py": _COLLIDING_MODULE.format(version="left-1.0", side="left")
    + """

if __name__ == "__main__":
    raise SystemExit("left.mod must never run as __main__")
""",
    "right/__init__.py": "RIGHT_INIT = True\n",
    "right/mod.py": _COLLIDING_MODULE.format(version="right-2.0", side="right"),
}


# ---------------------------------------------------------------------------
# Dynamic imports: constant names the scanner can follow, and computed ones
# it cannot, which is what `include=` exists for.
# ---------------------------------------------------------------------------

_PLUGIN = '''from . import PREFIX


def make():
    return f"{{PREFIX}}:{name}"
'''

PROJECTS["dynamic"] = {
    "run.py": """import importlib
from importlib import import_module

alpha = importlib.import_module("plugins.alpha")
beta = import_module(".beta", package="plugins")
gamma = __import__("plugins.gamma", fromlist=["make"])

# Built at runtime: no static analysis can see these, so the bundle needs
# them listed with `include=`.
discovered = [
    importlib.import_module(f"plugins.{name}") for name in ("delta", "epsilon")
]

print(alpha.make())
print(beta.make())
print(gamma.make())
print("discovered:", [module.make() for module in discovered])
print("registered:", sorted(m for m in __import__("sys").modules if m.startswith("plugins")))
""",
    "plugins/__init__.py": 'PREFIX = "plugin"\n',
}
for _name in ("alpha", "beta", "gamma", "delta", "epsilon"):
    PROJECTS["dynamic"][f"plugins/{_name}.py"] = _PLUGIN.format(name=_name)


# ---------------------------------------------------------------------------
# The awkward corners, all in one place.
# ---------------------------------------------------------------------------

PROJECTS["tricky"] = {
    "main.py": '''"""Entry point exercising the awkward corners of Python's import system."""

import calendar
import cyc_a
from docs import describe
from literals import LOOKS_LIKE_IMPORTS, banner
from models import Point, Tag, hints_of
from parenthesized import ALPHA, BETA, GAMMA, joined
from whereami import identity
from accents import motto

print("__name__:", __name__)
print("__package__:", repr(__package__))
print(banner())
print("parenthesized:", ALPHA, BETA, GAMMA, joined())
print("literal block intact:", LOOKS_LIKE_IMPORTS.strip().splitlines())
print("docstring:", describe())
print("dataclass:", Point(1, 2), Point(1, 2).shifted())
print("namedtuple:", Tag("x"), Tag("x").name)
print("annotations:", hints_of())
print("shadowed stdlib:", calendar.WHOAMI)
print("cycle:", cyc_a.describe())
print("identity:", identity())
print("latin-1 source:", motto())

import lazy

print("lazy:", lazy.run())
''',
    "parenthesized.py": '''from collections import (
    Counter,
    OrderedDict,
)
from literals import (
    ALPHA,
    BETA,
)
import os.path as _ospath, sys as _sys

GAMMA = "gamma"


def joined():
    counts = Counter("aab")
    ordered = OrderedDict(sorted(counts.items()))
    return "%s/%s/%s" % (
        "".join(f"{k}{v}" for k, v in ordered.items()),
        _ospath.basename("/tmp/x"),
        _sys.platform != "",
    )
''',
    "literals.py": '''ALPHA = "alpha"
BETA = "beta"

LOOKS_LIKE_IMPORTS = """
import this_module_does_not_exist
from nowhere import nothing
    from indented import thing
"""

SINGLE = 'from x import y'


def banner():
    return f"[{ALPHA}|{BETA}|{SINGLE}]"
''',
    "docs.py": '''"""Docs module docstring."""


def describe():
    return (__doc__ or "").strip()
''',
    "whereami.py": '''import os


def identity():
    return (
        __name__,
        __package__,
        os.path.basename(__file__),
        isinstance(__spec__.name, str),
    )
''',
    "calendar.py": 'WHOAMI = "local calendar.py, not the stdlib one"\n',
    "cyc_a.py": '''import cyc_b

NAME_A = "A"


def describe():
    return f"{NAME_A}->{cyc_b.NAME_B}->{cyc_b.back()}"
''',
    "cyc_b.py": '''NAME_B = "B"


def back():
    # Imported lazily: cyc_a is only half-built while this module executes.
    import cyc_a

    return cyc_a.NAME_A
''',
    "models.py": '''from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import NamedTuple


@dataclass
class Point:
    x: int
    y: int = 0

    def shifted(self) -> Point:
        return Point(self.x + 1, self.y + 1)


class Tag(NamedTuple):
    name: str
    weight: int = 1


def annotated(value: int, other: str = "s") -> bool:
    return bool(value) and bool(other)


def hints_of():
    return (
        sorted(typing.get_type_hints(annotated).items(), key=lambda kv: kv[0]),
        sorted(Point.__annotations__),
    )
''',
    "lazy.py": '''from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Point  # never imported at runtime

try:
    import ujson as _json
except ImportError:
    import json as _json

try:
    from optional_extra import missing
except ImportError:
    missing = "fallback"

USE_SIDECAR = True
if USE_SIDECAR:
    from sidecar import sidecar_value
else:
    sidecar_value = None


def run():
    from literals import ALPHA

    return (_json.dumps({"a": ALPHA}), missing, sidecar_value, _later())


def _later():
    import models

    return models.Point(9).shifted().x


if __name__ == "__main__":
    raise SystemExit("lazy.py must never run as __main__")
''',
    "sidecar.py": 'sidecar_value = "sidecar-loaded"\n',
    # Written as bytes: a genuinely latin-1 encoded source with a PEP 263
    # cookie, which compile() rejects unless the bundler neutralises it.
    "accents.py": (
        "# -*- coding: latin-1 -*-\n\n"
        'MOTTO = "café crème à la française"\n\n\n'
        "def motto():\n    return MOTTO.upper()\n"
    ).encode("latin-1"),
}


_ROOT = None


def root() -> Path:
    """Materialise every project once, into a directory cleaned up at exit."""
    global _ROOT
    if _ROOT is None:
        # Resolved, so comparisons against paths the bundler has resolved
        # hold on platforms where the temp directory is a symlink.
        _ROOT = Path(tempfile.mkdtemp(prefix="pypack-projects-")).resolve()
        atexit.register(shutil.rmtree, _ROOT, True)
        for name in PROJECTS:
            write(name, _ROOT / name)
    return _ROOT


def path(name: str) -> Path:
    """Return the materialised directory of one project."""
    return root() / name


def write(name: str, destination: Path) -> Path:
    """Write a project's files beneath ``destination``."""
    for relative, content in PROJECTS[name].items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return destination
