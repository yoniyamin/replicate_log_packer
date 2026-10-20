"""Redaction rules for Replicate logs.

Each rule replaces either the whole match or only its named group `v`.
`needle` is a cheap substring pre-check so most lines skip the regex.
`learn` marks values that should also be masked wherever they appear later
(for example a short host name first seen in a connection string).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

FILE_EXTENSIONS = frozenset(
    "c h cpp hpp cs java py js ts json xml yml yaml ini cfg conf txt log csv tsv dat "
    "dll exe so jar war zip gz tar sql bat sh ps1 md html htm pdf parquet avro orc tmp bak".split()
)

KNOWN_TLDS = frozenset(
    "com net org edu gov mil int io co ai app dev cloud biz info local lan corp internal intra "
    "home lab eu uk de fr nl il in us ca au jp cn ru br es it ch se no dk fi pl be at cz ie pt "
    "gr hu ro sk si hr bg lt lv ee tr za mx ar cl sg hk tw kr nz".split()
)

# Values that look like hosts/users in key=value pairs but are not identifying.
NOT_SENSITIVE = frozenset(
    "localhost null none true false yes no default n/a na sa system".split()
)


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: re.Pattern
    needle: str | None = None
    learn: bool = False
    accept: Callable[[str], bool] | None = None
    fixed: str | None = None  # replace with a fixed marker instead of a pseudonym


def _is_hostname(value: str) -> bool:
    labels = value.lower().split(".")
    last = labels[-1]
    if last in FILE_EXTENSIONS or not last.isalpha():
        return False
    if last in KNOWN_TLDS:
        return True
    return len(labels) >= 3 and value.islower() and len(last) <= 6


TOKEN_RE = re.compile(r"\b(?:email|host|path|user|company|cloud|ip|obj|term|db)_\d{2,}\b")


def _is_meaningful(value: str) -> bool:
    v = value.strip().strip("'\"").lower()
    return (len(v) >= 2 and v not in NOT_SENSITIVE and not v.startswith(("<", "$", "%"))
            and not TOKEN_RE.fullmatch(v))


def _is_host_name(value: str) -> bool:
    """A meaningful name that is not an IP address (the IP rule handles those)."""
    return _is_meaningful(value) and not re.fullmatch(r"[\d.]+|[0-9a-fA-F:]+", value)


_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_KV_END = r"[^;,\s'\")\]}]+"

RULES: tuple[Rule, ...] = (
    Rule("secret", re.compile(
        r"(?i)\b(?:pwd|password|passwd|secret|token|api[_-]?key|access[_-]?key)\s*[=:]\s*(?P<v>[^;,\s'\"]+)"),
        fixed="[REDACTED]"),
    Rule("email", re.compile(r"\b[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"), needle="@"),
    Rule("host", re.compile(r"(?i)\b(?:https?|ftp|s3|abfss?|wasbs?|gs)://(?P<v>[^/\s:'\"]+)"), needle="://"),
    Rule("path", re.compile(r"\\\\[^\s\\'\"]+\\[^\s'\"]*"), needle="\\\\"),
    Rule("host", re.compile(
        r"(?i)\b(?:server|host|hostname|data source|system|address|endpoint|dsn)\s*=\s*(?P<v>" + _KV_END + ")"),
        needle="=", learn=True, accept=_is_meaningful),
    Rule("user", re.compile(
        r"(?i)\b(?:uid|user id|user|username|user name|login)\s*[=:]\s*'?(?P<v>" + _KV_END + ")"),
        learn=True, accept=_is_meaningful),
    Rule("user", re.compile(r"(?i)\b(?:with|for|as)\s+(?:user|username|user name|login)\s+'?(?P<v>[\w.$@\\-]+)"),
         learn=True, accept=_is_meaningful),
    # Oracle EZConnect and similar: //host:port/service (not URLs, which the rule above handles).
    Rule("host", re.compile(r"(?<![:\w])//(?P<v>[^/:\s'\"]+)(?::\d+)?/"), needle="//", learn=True,
         accept=_is_host_name),
    Rule("db", re.compile(r"(?<![:\w])//[^/\s'\"]+?(?::\d+)?/(?P<v>[\w.$-]+)"), needle="//", learn=True,
         accept=_is_meaningful),
    Rule("host", re.compile(r"Task Server Log - \S+\s+\(V[\w.\-]+\s+(?P<v>[^\s)]+)"),
         needle="Task Server Log", learn=True),
    Rule("company", re.compile(r"(?i)\blicensed to\s+(?P<v>[^,\n;()]+?)\s*(?:[,;(]|$)"),
         needle="icensed", learn=True),
    Rule("user", re.compile(r"(?i)[A-Z]:\\(?:Users|Documents and Settings)\\(?P<v>[^\\\s'\"]+)"),
         needle="\\", learn=True),
    Rule("user", re.compile(r"/(?:home|Users)/(?P<v>[^/\s'\"]+)"), needle="/", learn=True),
    Rule("cloud", re.compile(r"\barn:aws[\w-]*:[\w-]+:[\w-]*:\d*:[^\s'\"]+"), needle="arn:"),
    Rule("ip", re.compile(rf"(?<![\w.]){_OCTET}(?:\.{_OCTET}){{3}}(?![\w.])")),
    Rule("ip", re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}(?![\w:])"), needle=":"),
    Rule("host", re.compile(r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}\b"),
         needle=".", accept=_is_hostname),
)

# Optional: schema/table names in Replicate's 'SCHEMA'.'TABLE' notation.
OBJECT_RULE = Rule("object", re.compile(r"'(?P<v>[^'\s]{1,128})'\.'(?P<w>[^'\s]{1,128})'"), needle="'.'")

TOKEN_PREFIX = {
    "email": "email", "host": "host", "path": "path", "user": "user", "company": "company",
    "cloud": "cloud", "ip": "ip", "object": "obj", "term": "term", "secret": "secret", "db": "db",
}

CATEGORY_LABELS = {
    "secret": "Passwords & keys",
    "email": "Email addresses",
    "host": "Host & server names",
    "path": "Network paths",
    "user": "User names",
    "company": "Company / license",
    "cloud": "Cloud resource IDs",
    "ip": "IP addresses",
    "db": "Database / service names",
    "object": "Schema & table names",
    "term": "Your custom terms",
}
