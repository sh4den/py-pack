import python_minifier


class Minifier:
    def minify(self, source: str) -> str:
        return python_minifier.minify(
            source,
            remove_annotations=True,
            remove_pass=True,
            remove_literal_statements=True,
            combine_imports=True,
            hoist_literals=True,
            rename_locals=True,
        )
