"""Runtime bootstrap embedded verbatim in the entry chunk of a bundle.

The bundler emits every module's source into a table and lets this runtime
import them through a `sys.meta_path` finder. Because each module is executed
in its own namespace, Python's own import semantics apply unchanged: relative
imports, circular imports, `__all__`, `global`, star imports and modules that
happen to define the same top-level names all behave exactly as they do in the
unbundled project.

Chunks other than the entry chunk are read from disk on first use.

Nothing here reads the project the bundle was built from. A module's
``__file__`` and a package's ``__path__`` are anchored to the directory the
chunk itself lives in, so a bundle behaves the same wherever it runs and can
never fall back to loading an unbundled submodule off the build machine.
Those paths name no real file.

Every chunk defines a module table:

    __PYPACK_MODULES__  {name: (source, kind)} where kind is
                        0 module, 1 package, 2 namespace package

The entry chunk adds:

    __PYPACK_MODULE_CHUNKS__  {name: chunk name} for modules in other chunks
    __PYPACK_CHUNK_FILES__    {chunk name: file name on disk}
    __PYPACK_ENTRY__          dotted name of the module to run as __main__

This file is also importable on its own, in which case it does nothing.
"""

import importlib.abc as _pypack_abc
import importlib.machinery as _pypack_machinery
import os as _pypack_os
import sys as _pypack_sys
import types as _pypack_types

_PYPACK_MODULE = 0
_PYPACK_PACKAGE = 1
_PYPACK_NAMESPACE = 2


def _pypack_location(directory, fullname, kind):
    """Place a module under the bundle's directory, as its name implies."""
    parts = fullname.split(".")
    if kind == _PYPACK_PACKAGE:
        parts.append("__init__.py")
    elif kind != _PYPACK_NAMESPACE:
        parts[-1] += ".py"
    return _pypack_os.path.join(directory, *parts)


def _pypack_code_name(fullname, kind):
    """Name the compiled code carries, and the key linecache looks it up by."""
    suffix = "/__init__.py" if kind != _PYPACK_MODULE else ".py"
    return "<pypack>/" + fullname.replace(".", "/") + suffix


class _PyPackLoader(_pypack_abc.InspectLoader):
    """Executes bundled module sources that are held in memory."""

    def __init__(self, finder):
        self._finder = finder

    def _record(self, fullname):
        record = self._finder.record_for(fullname)
        if record is None:
            raise ImportError(
                "module %r is not part of this bundle" % (fullname,), name=fullname
            )
        return record

    def get_source(self, fullname):
        return self._record(fullname)[0]

    def is_package(self, fullname):
        return self._record(fullname)[1]

    def get_filename(self, fullname):
        origin = self._record(fullname)[2]
        if origin is None:
            raise ImportError(
                "module %r has no source file" % (fullname,), name=fullname
            )
        return origin

    def get_code(self, fullname):
        source, _, _, _, code_name = self._record(fullname)
        return compile(source, code_name, "exec", dont_inherit=True)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        spec = getattr(module, "__spec__", None)
        fullname = spec.name if spec is not None else module.__name__
        source, _, origin, _, code_name = self._record(fullname)
        if origin is not None:
            module.__file__ = origin
        exec(compile(source, code_name, "exec", dont_inherit=True), module.__dict__)

    def __repr__(self):
        return "<pypack loader>"


class _PyPackFinder(_pypack_abc.MetaPathFinder):
    """Serves bundled modules, pulling in sibling chunks on demand."""

    def __init__(self, modules, module_chunks, chunk_files, directory):
        self._modules = dict(modules)
        self._records = {}
        self._module_chunks = dict(module_chunks)
        self._chunk_files = dict(chunk_files)
        self._directory = directory
        self._loaded_chunks = set()
        self._keepalive = None
        self.loader = _PyPackLoader(self)

    def _register(self, modules):
        for name, entry in modules.items():
            self._modules.setdefault(name, entry)

    def record_for(self, fullname):
        """Expand a table entry into (source, is_package, file, path, code)."""
        record = self._records.get(fullname)
        if record is not None:
            return record

        entry = self._modules.get(fullname)
        if entry is None:
            chunk = self._module_chunks.get(fullname)
            if chunk is None or chunk in self._loaded_chunks:
                return None
            self._load_chunk(chunk)
            entry = self._modules.get(fullname)
            if entry is None:
                return None

        source, kind = entry
        is_package = kind != _PYPACK_MODULE
        location = _pypack_location(self._directory, fullname, kind)

        if kind == _PYPACK_NAMESPACE:
            origin, search_path = None, location
        elif kind == _PYPACK_PACKAGE:
            origin, search_path = location, _pypack_os.path.dirname(location)
        else:
            origin, search_path = location, None

        record = (
            source,
            is_package,
            origin,
            search_path,
            _pypack_code_name(fullname, kind),
        )
        self._records[fullname] = record
        return record

    def _load_chunk(self, chunk):
        self._loaded_chunks.add(chunk)

        filename = self._chunk_files.get(chunk)
        if not filename:
            raise ImportError("bundle chunk %r is missing from the manifest" % (chunk,))

        path = _pypack_os.path.join(self._directory, filename)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as error:
            raise ImportError(
                "cannot read bundle chunk %r at %s: %s" % (chunk, path, error)
            )

        namespace = {
            "__name__": "__pypack_chunk_%s__" % (chunk,),
            "__file__": path,
        }
        exec(compile(data, path, "exec", dont_inherit=True), namespace)

        self._register(namespace.get("__PYPACK_MODULES__") or {})

    def spec_for(self, fullname, record):
        """Build a ModuleSpec from an expanded bundle record."""
        _, is_package, origin, search_path, _ = record
        spec = _pypack_machinery.ModuleSpec(
            fullname, self.loader, origin=origin, is_package=is_package
        )
        if is_package:
            spec.submodule_search_locations = [search_path] if search_path else []
        return spec

    def find_spec(self, fullname, path=None, target=None):
        record = self.record_for(fullname)
        if record is None:
            return None
        return self.spec_for(fullname, record)

    def invalidate_caches(self):
        return None

    def __repr__(self):
        return "<pypack finder: %d modules>" % (len(self._modules),)


def _pypack_install(modules, module_chunks, chunk_files, directory):
    """Put a finder for this bundle at the front of ``sys.meta_path``."""
    finder = _PyPackFinder(modules, module_chunks, chunk_files, directory)
    _pypack_sys.meta_path.insert(0, finder)
    return finder


def _pypack_run_entry(finder, entry_name):
    """Execute the bundled entry point the way ``python -m`` would."""
    if not entry_name:
        return

    record = finder.record_for(entry_name)
    if record is None:
        raise ImportError("bundle entry point %r is missing" % (entry_name,))

    source, is_package, origin, _, code_name = record
    spec = finder.spec_for(entry_name, record)

    finder._records.setdefault("__main__", record)

    module = _pypack_types.ModuleType("__main__")
    module.__loader__ = finder.loader
    if spec.parent:
        module.__spec__ = spec
        module.__package__ = spec.parent
    else:
        module.__spec__ = None
        module.__package__ = None
    if origin is not None:
        module.__file__ = origin
    if is_package:
        module.__path__ = spec.submodule_search_locations

    finder._keepalive = _pypack_sys.modules.get("__main__")
    _pypack_sys.modules["__main__"] = module

    exec(compile(source, code_name, "exec", dont_inherit=True), module.__dict__)


_PYPACK_MODULE_TABLE = globals().get("__PYPACK_MODULES__") or {}

if _PYPACK_MODULE_TABLE:
    _PYPACK_DIRECTORY = _pypack_os.path.dirname(
        _pypack_os.path.abspath(globals().get("__file__") or ".")
    )
    _PYPACK_FINDER = _pypack_install(
        _PYPACK_MODULE_TABLE,
        globals().get("__PYPACK_MODULE_CHUNKS__") or {},
        globals().get("__PYPACK_CHUNK_FILES__") or {},
        _PYPACK_DIRECTORY,
    )

    if __name__ == "__main__":
        _pypack_run_entry(_PYPACK_FINDER, globals().get("__PYPACK_ENTRY__"))
