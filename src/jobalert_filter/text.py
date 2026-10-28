"""Text normalisation that makes Turkish and English keyword matching forgiving.

``fold("İstanbul, Türkiye")`` and ``fold("istanbul turkiye")`` give comparable
strings: everything is lower-cased, the dotted/dotless i pair collapses to ``i``
and diacritics (ş ç ğ ö ü â) are stripped.
"""

from __future__ import annotations

import re
import unicodedata

_I_MAP = str.maketrans({"İ": "i", "I": "i", "ı": "i"})


def fold(text: str) -> str:
    text = text.translate(_I_MAP).lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold_pattern(pattern: str) -> str:
    """Fold a regex the same way as the text, leaving escape sequences like ``\\D`` intact."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(pattern[i : i + 2])
            i += 2
            continue
        out.append(fold(ch))
        i += 1
    return "".join(out)


def compile_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(fold_pattern(p), re.IGNORECASE) for p in patterns]


def first_match(patterns: list[re.Pattern[str]], folded_text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(folded_text)
        if match:
            return match.group(0)
    return None


def clean_lines(text: str) -> list[str]:
    """Split into stripped, non-empty lines with internal whitespace collapsed."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line.replace("‌", "").replace("\xa0", " ")).strip()
        if line:
            lines.append(line)
    return lines
