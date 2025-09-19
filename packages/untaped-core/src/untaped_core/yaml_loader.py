from pathlib import Path
from typing import Any

import yaml


def load_yaml_file(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
