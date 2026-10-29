"""Small, pure normalization helpers shared by the parsers.

Kept deterministic and dependency-free so they are trivially unit-testable (E01).
"""
from __future__ import annotations

import datetime

# Each quarterly reference release is in effect for its calendar quarter. We pin
# effective/end dates from the release label so crosswalk lookups resolve "as of the
# analysis period" (BR-5 / Principle 03), not by whichever file is newest.
QUARTER_RANGES: dict[str, tuple[str, str]] = {
    "2026-07": ("2026-07-01", "2026-09-30"),
    "2026-10": ("2026-10-01", "2026-12-31"),
}


def normalize_ndc11(raw: str | None) -> str | None:
    """Normalize an NDC to the 11-digit 5-4-2 form (no hyphens).

    Handles the hyphenated package forms (4-4-2, 5-3-2, 5-4-1, 5-4-2) by zero-padding
    each segment to its 5/4/2 target, and already-11-digit unhyphenated input (claims).
    Returns None for product-level (2-segment) NDCs, which have no package digits.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if "-" in s:
        parts = s.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            a, b, c = parts
            return f"{int(a):05d}{int(b):04d}{int(c):02d}"
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits if len(digits) == 11 else None


def normalize_product_ndc9(raw: str | None) -> str | None:
    """Normalize a labeler-product NDC (no package) to 9 digits (5-4)."""
    if not raw:
        return None
    parts = str(raw).strip().split("-")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{int(parts[0]):05d}{int(parts[1]):04d}"
    return None


def parse_yyyymmdd(s: str | None) -> str | None:
    """CMS fixed-width dates are YYYYMMDD; return ISO date or None."""
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat()
        except ValueError:
            return None
    return None


def icd_dotted(code: str | None) -> str | None:
    """ICD-10-CM code files are undotted (D841); canonical form is dotted (D84.1)."""
    if not code:
        return None
    c = code.strip().upper()
    return f"{c[:3]}.{c[3:]}" if len(c) > 3 else c


def to_number(s) -> float | None:
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None
