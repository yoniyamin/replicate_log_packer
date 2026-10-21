"""Message normalization: turn a log message into a reusable pattern key.

Rules run in order. Error codes such as ORA-00054 or HY000 survive because
digits are only replaced when they are not glued to a letter or a dash.
Bump NORMALIZER_VERSION whenever a rule changes.
"""

from __future__ import annotations

import hashlib
import re

NORMALIZER_VERSION = "2"
MAX_LEN = 220

_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"'[^']*'"), "'<s>'"),
    (re.compile(r'"[^"]*"'), '"<s>"'),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<guid>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.:]\d+)?"), "<ts>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{12,}\b"), "<hex>"),
    # Generated names such as CDC00000BB8.csv or attrep_changes819CD14: prefix + 6+ hex chars.
    (re.compile(r"\b([A-Za-z_]+)\d[0-9A-Fa-f]{5,}\b"), r"\1<id>"),
    (re.compile(r"(?<![A-Za-z\-\d])\d+(?:[.:]\d+)*"), "<n>"),
    (re.compile(r"\s+"), " "),
)

_ERROR_CODE_RES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"SqlState:\s*(\w+)\s+NativeError:\s*(-?\d+)", re.I), "SqlState:{0}/{1}"),
    (re.compile(r"\b(ORA-\d{5})\b", re.I), "{0}"),
    (re.compile(r"MySQL.*?Error\s+(\d{4})", re.I), "MySQL:{0}"),
    (re.compile(r"\bSQLSTATE\s*[=:]?\s*([0-9A-Z]{5})\b", re.I), "SQLSTATE:{0}"),
    (re.compile(r"RetCode:\s*(SQL_\w+)", re.I), "{0}"),
    (re.compile(r"NativeError:\s*(-?[1-9]\d*)", re.I), "Native:{0}"),
    (re.compile(r"\[(\d{6,8})\]"), "[{0}]"),
)


def normalize(message: str) -> str:
    out = message
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    out = out.strip()
    return out[:MAX_LEN]


def template_key(component: str | None, severity: str | None, message: str) -> str:
    return f"{component or '-'}|{severity or '-'}|{normalize(message)}"


def extract_error_code(text: str) -> str | None:
    for pattern, fmt in _ERROR_CODE_RES:
        match = pattern.search(text)
        if match:
            code = fmt.format(*(g.upper() for g in match.groups()))
            if code != "SQL_SUCCESS":
                return code
    return None


def fingerprint(*parts: str | None) -> str:
    joined = "\x1f".join(p or "" for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]
