import python_minifier


class Minifier:
    """Shrinks module sources without changing what they do.

    The defaults are deliberately conservative. Two of python-minifier's
    transforms are observable at runtime and are therefore opt-in:

    * ``remove_annotations`` breaks anything reading ``__annotations__`` or
      calling ``typing.get_type_hints`` (pydantic, attrs, FastAPI).
    * ``remove_literal_statements`` drops docstrings, so ``__doc__`` silently
      becomes the *builtins* docstring instead of the module's own -- a
      favourite of ``argparse(description=__doc__)``.
    """

    def __init__(
        self, enabled: bool = True, aggressive: bool = False, quiet: bool = False
    ):
        """
        Args:
            enabled: Set to False to emit readable, unmodified sources.
            aggressive: Enable the transforms that are observable at runtime.
            quiet: Suppress the warnings emitted when a module is skipped.
        """
        self.enabled = enabled
        self.aggressive = aggressive
        self.quiet = quiet

    def _warn(self, message: str) -> None:
        if not self.quiet:
            print(message)

    @property
    def options(self) -> dict:
        return {
            "remove_annotations": self.aggressive,
            "remove_literal_statements": self.aggressive,
            "remove_pass": True,
            "combine_imports": True,
            "hoist_literals": True,
            "rename_locals": True,
            "rename_globals": False,
        }

    RUNTIME_OPTIONS = {
        "remove_annotations": True,
        "remove_literal_statements": True,
        "remove_pass": True,
        "combine_imports": True,
        "hoist_literals": True,
        "rename_locals": True,
        "rename_globals": True,
    }

    def minify(self, source: str, filename: str = "<pypack>") -> str:
        """Minify a module, falling back to the original source on any doubt.

        A minified module that no longer compiles is worse than one that was
        never minified, so the result is verified before it is accepted.
        """
        if not self.enabled or not source.strip():
            return source
        return self._apply(source, self.options, filename)

    def minify_runtime(self, source: str) -> str:
        """Minify the bundle runtime, which is stripped even in readable mode.

        The runtime is generated plumbing rather than project code, so it is
        shrunk whenever it can be, leaving the chunk free of comments.
        """
        return self._apply(source, self.RUNTIME_OPTIONS, "<pypack runtime>")

    def _apply(self, source: str, options: dict, filename: str) -> str:
        try:
            minified = python_minifier.minify(source, **options)
        except Exception as error:
            self._warn(f"  ! minification skipped for {filename}: {error}")
            return source

        try:
            compile(minified, filename, "exec", dont_inherit=True)
        except SyntaxError as error:
            self._warn(
                f"  ! minification produced invalid code for {filename}: {error}"
            )
            return source

        return minified
