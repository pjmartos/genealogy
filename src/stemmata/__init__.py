from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:
    __version__ = _pkg_version("stemmata")
except PackageNotFoundError:
    __version__ = "0.0.1"
