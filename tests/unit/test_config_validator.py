from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped_core.errors import ConfigurationValidationError
from untaped_core.validators.config_validator import ConfigurationValidator


class DummyModel(BaseModel):
    name: str


def test_validator_parses_valid_configuration(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text(
        """
resource_type: job_template
job_template:
  name: example
        """.strip(),
        encoding="utf-8",
    )

    validator = ConfigurationValidator({"job_template": DummyModel})
    outcome = validator.validate(config)

    assert outcome.validation.is_valid
    assert outcome.resource_payload == {"name": "example"}


def test_validator_raises_when_section_missing(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text(
        """
resource_type: job_template
        """.strip(),
        encoding="utf-8",
    )

    validator = ConfigurationValidator({"job_template": DummyModel})

    with pytest.raises(ConfigurationValidationError):
        validator.validate(config)
