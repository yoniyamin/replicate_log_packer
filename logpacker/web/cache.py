"""Cache keys: the same file with the same privacy options reuses its analysis.

Two keys per job:
- quick: a fingerprint the browser computes (name, size, modified time and
  SHA-256 of the first and last MB), so a repeat upload can skip sending bytes;
- content: SHA-256 of the full upload, computed while streaming, which catches
  the same file under another name.
"""

from __future__ import annotations

import hashlib
import json

from logpacker.analysis.pipeline import ANALYSIS_VERSION


def options_digest(sanitize: dict) -> str:
    """Privacy options plus the analysis version: new report code never reuses old results."""
    canonical = {**sanitize, "custom_terms": sorted(t.lower() for t in sanitize.get("custom_terms", [])),
                 "analysis_version": ANALYSIS_VERSION}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]


def quick_key(fingerprint: str, sanitize: dict) -> str:
    return f"quick:{fingerprint}:{options_digest(sanitize)}"


def content_key(sha256_hex: str, sanitize: dict) -> str:
    return f"content:{sha256_hex}:{options_digest(sanitize)}"
