import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from make_sample_log import write_sample  # noqa: E402


@pytest.fixture()
def sample_log(tmp_path: Path) -> Path:
    path = tmp_path / "reptask_SALES_TO_SNOW.log"
    write_sample(path, repeat=400)
    return path
