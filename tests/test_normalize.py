"""Unit tests for normalization + versioning helpers (E01) -- pure, no AWS."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ambit_codebook.ingest.normalize import (  # noqa: E402
    QUARTER_RANGES, icd_dotted, normalize_ndc11, normalize_product_ndc9,
    parse_yyyymmdd, to_number,
)


def test_ndc11_three_segment_padding():
    assert normalize_ndc11("13533-0636-01") == "13533063601"
    assert normalize_ndc11("1234-5678-90") == "01234567890"  # 4-4-2 -> pad labeler to 5
    assert normalize_ndc11("72769-101-01") == "72769010101"  # 5-3-2 -> pad product to 4


def test_ndc11_passthrough_and_reject():
    assert normalize_ndc11("71860010301") == "71860010301"  # already 11-digit
    assert normalize_ndc11("72769-101") is None             # product-level, no package
    assert normalize_ndc11("") is None and normalize_ndc11(None) is None


def test_product_ndc9():
    assert normalize_product_ndc9("71860-103") == "718600103"
    assert normalize_product_ndc9("47783-645") == "477830645"
    assert normalize_product_ndc9("bad") is None


def test_ndc11_first9_matches_product_ndc9():
    # The join contract: an 11-digit NDC's first 9 digits == its product NDC (9).
    assert normalize_ndc11("71860-103-01")[:9] == normalize_product_ndc9("71860-103")


def test_parse_yyyymmdd():
    assert parse_yyyymmdd("20261001") == "2026-10-01"
    assert parse_yyyymmdd("") is None and parse_yyyymmdd("2026") is None


def test_icd_dotted():
    assert icd_dotted("D841") == "D84.1"
    assert icd_dotted("T783XXA") == "T78.3XXA"
    assert icd_dotted("A00") == "A00"


def test_quarter_ranges():
    assert QUARTER_RANGES["2026-07"] == ("2026-07-01", "2026-09-30")
    assert QUARTER_RANGES["2026-10"] == ("2026-10-01", "2026-12-31")


def test_to_number():
    assert to_number("150") == 150.0
    assert to_number("1,200") == 1200.0
    assert to_number("") is None and to_number("abc") is None
