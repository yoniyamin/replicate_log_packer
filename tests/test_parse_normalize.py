from logpacker.analysis.normalize import extract_error_code, normalize
from logpacker.ingest.lines import LineParser, decode


def test_prefix_and_continuation():
    p = LineParser()
    first = p.parse("00001234: 2025-06-15T10:00:40:118446 [TARGET_APPLY    ]E:  Failed to execute", 1, "a")
    cont = p.parse("  Continuation: Lock wait timeout", 2, "a")
    assert (first.component, first.severity, first.message) == ("TARGET_APPLY", "E", "Failed to execute")
    assert first.is_issue and cont.continuation and not cont.is_issue and cont.component == "TARGET_APPLY"


def test_unparsed_lines_before_first_prefix_are_counted():
    p = LineParser()
    p.parse("garbage", 1, "a")
    assert p.unparsed == 1


def test_decode_repairs_bad_bytes():
    text, bad = decode(b"abc\xff\r\n")
    assert bad and text.startswith("abc")


def test_normalize_keeps_codes_and_merges_variants():
    a = normalize("Failed on 'SALES'.'ORDERS' after 30 retries ORA-00054 at host_01")
    b = normalize("Failed on 'HR'.'EMP' after 4 retries ORA-00054 at host_02")
    assert a == b and "ORA-00054" in a


def test_error_codes():
    assert extract_error_code("RetCode: SQL_ERROR  SqlState: 57014 NativeError: 604") == "SqlState:57014/604"
    assert extract_error_code("ORA-03113: end-of-file") == "ORA-03113"
    assert extract_error_code("nothing here") is None
