"""A small, tested function for atoms to read, test, lint and change."""

import re
import unicodedata

__all__ = ["slugify"]


def slugify(text: str, separator: str = "-") -> str:
    """Turn *text* into lower-case ASCII words joined by *separator*.

    Accents are dropped rather than the letters carrying them, and any run of
    characters that are not letters or digits becomes one separator.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return separator.join(re.findall(r"[a-z0-9]+", ascii_text.lower()))
