"""Good First Issue Finder - discover, rank, and match open-source issues."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("gfi-scraper")
except PackageNotFoundError:
    __version__ = "0.1.0"
