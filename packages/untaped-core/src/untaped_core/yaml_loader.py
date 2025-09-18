from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def load_yaml_file(path: str | Path) -> Any:
    yaml = YAML(typ="safe")
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.load(f)


