from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

from untaped_core.validators.config_validator import ConfigurationValidator


class MinimalModel(BaseModel):
    name: str


def test_validation_completes_under_100ms(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
resource_type: job_template
job_template:
  name: example
        """.strip(),
        encoding="utf-8",
    )

    validator = ConfigurationValidator({"job_template": MinimalModel})

    start = time.perf_counter()
    for _ in range(10):
        validator.validate(config_file)
    duration = (time.perf_counter() - start) / 10

    assert duration < 0.1
