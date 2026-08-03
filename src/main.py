"""Command-line entry point for pypack."""

import argparse
import sys

from classes.ChunkConfig import ChunkConfig
from classes.Packer import Packer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pypack",
        description="Bundle a Python project into self-contained chunks.",
    )
    parser.add_argument(
        "entry",
        help="Entry point: a .py file, or a package directory with a __main__.py",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="./dist",
        help="Output directory (default: ./dist)",
    )
    parser.add_argument(
        "-r",
        "--root",
        action="append",
        default=[],
        dest="roots",
        help="Extra source root for resolving absolute imports (repeatable)",
    )
    parser.add_argument(
        "-i",
        "--include",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Bundle these modules even if nothing imports them by a literal "
        "name, e.g. 'plugins.*' (repeatable)",
    )
    parser.add_argument(
        "--chunk",
        action="append",
        default=[],
        metavar="NAME=REGEX",
        help="Put modules whose path matches REGEX into chunk NAME (repeatable)",
    )
    parser.add_argument(
        "--auto-chunks",
        action="store_true",
        help="Derive one chunk per top-level package",
    )
    parser.add_argument(
        "--no-minify",
        action="store_true",
        help="Emit readable, uncompressed sources instead of the smallest output",
    )
    parser.add_argument(
        "--aggressive-minify",
        action="store_true",
        help="Also drop annotations and docstrings (unsafe for dataclasses, "
        "pydantic and argparse descriptions)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress build output",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    packer = Packer(
        args.entry,
        args.output,
        roots=args.roots,
        include=args.include,
        minify=not args.no_minify,
        aggressive_minify=args.aggressive_minify,
        compress=not args.no_minify,
        quiet=args.quiet,
    )

    chunks = []
    for spec in args.chunk:
        name, separator, pattern = spec.partition("=")
        if not separator:
            print(f"invalid --chunk value: {spec!r} (expected NAME=REGEX)")
            return 2
        chunks.append(ChunkConfig(name=name, includes=[pattern]))

    if chunks:
        packer.configure_chunks(chunks)
    elif args.auto_chunks:
        packer.build_graph()
        packer.auto_generate_chunks()

    packer.pack()
    return 0


if __name__ == "__main__":
    sys.exit(main())
