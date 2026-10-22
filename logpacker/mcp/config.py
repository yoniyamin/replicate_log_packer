"""MCP-specific configuration, stored alongside LogPacker data in *data_dir*/mcp_config.json.

Only API keys and concurrency settings live here.  The log files themselves
are uploaded by the AI client over HTTP — there are no server-side allowed
paths to configure.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path

MCP_CONFIG_FILE = "mcp_config.json"


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class MCPConfig:
    api_keys: list[dict] = field(default_factory=list)
    max_concurrent: int = 3

    # --- persistence --------------------------------------------------

    @classmethod
    def load(cls, data_dir: Path) -> MCPConfig:
        path = data_dir / MCP_CONFIG_FILE
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in cls.__dataclass_fields__.values()}
            return cls(**{k: v for k, v in raw.items() if k in known})
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / MCP_CONFIG_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)

    # --- helpers ------------------------------------------------------

    def generate_api_key(self, owner: str = "default") -> str:
        key = f"lp_{secrets.token_hex(20)}"
        self.api_keys.append({"key_hash": _hash_key(key), "owner": owner})
        return key

    def validate_api_key(self, key: str) -> bool:
        h = _hash_key(key)
        return any(k["key_hash"] == h for k in self.api_keys)
