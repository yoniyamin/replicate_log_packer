"""User-facing choices for a package, and what each one means in numbers."""

from __future__ import annotations

from dataclasses import dataclass

_CHARS_PER_TOKEN = 4

FOCUSES = ("auto", "errors", "performance", "configuration", "general")


@dataclass(frozen=True)
class PromptDetail:
    max_tokens: int
    issue_rows: int
    excerpts: int
    signal_rows: int
    repeat_rows: int


PROMPT_DETAIL = {
    "concise": PromptDetail(max_tokens=2500, issue_rows=8, excerpts=2, signal_rows=6, repeat_rows=5),
    "standard": PromptDetail(max_tokens=5000, issue_rows=15, excerpts=4, signal_rows=12, repeat_rows=10),
    "detailed": PromptDetail(max_tokens=10000, issue_rows=30, excerpts=8, signal_rows=20, repeat_rows=20),
}

# Byte targets derived from token budgets that fit common model context windows.
# None means "send the whole sanitized log, not condensed".
ATTACHMENT_SIZES = {
    "small": 50_000 * _CHARS_PER_TOKEN,
    "medium": 150_000 * _CHARS_PER_TOKEN,
    "large": 400_000 * _CHARS_PER_TOKEN,
    "full": None,
}


@dataclass(frozen=True)
class PackOptions:
    focus: str = "auto"
    detail: str = "standard"
    attachment: str = "medium"
    question: str = ""
    include_findings: bool = True

    def __post_init__(self) -> None:
        if self.focus not in FOCUSES:
            raise ValueError(f"focus must be one of {FOCUSES}")
        if self.detail not in PROMPT_DETAIL:
            raise ValueError(f"detail must be one of {tuple(PROMPT_DETAIL)}")
        if self.attachment not in ATTACHMENT_SIZES:
            raise ValueError(f"attachment must be one of {tuple(ATTACHMENT_SIZES)}")


def estimate_tokens(text_or_bytes: str | int) -> int:
    """Rough estimate (about 4 characters per token). Labelled as an estimate in the UI."""
    size = text_or_bytes if isinstance(text_or_bytes, int) else len(text_or_bytes)
    return max(1, size // _CHARS_PER_TOKEN) if size else 0
