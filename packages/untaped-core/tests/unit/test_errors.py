import pytest
from pydantic import BaseModel, ValidationError
from untaped_core import first_validation_error
from untaped_core.errors import ConfigError, HttpError, UntapedError


def test_untaped_error_is_exception() -> None:
    assert issubclass(UntapedError, Exception)


def test_config_error_is_untaped_error() -> None:
    assert issubclass(ConfigError, UntapedError)


def test_http_error_carries_status_code() -> None:
    err = HttpError("boom", status_code=503, url="https://example.com/api")
    assert err.status_code == 503
    assert err.url == "https://example.com/api"
    assert "boom" in str(err)


def test_http_error_status_code_optional() -> None:
    err = HttpError("network down")
    assert err.status_code is None
    assert err.url is None


def test_first_validation_error_extracts_message() -> None:
    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as ei:
        M.model_validate({"x": "not-an-int"})
    assert first_validation_error(ei.value).startswith("x: ")


def test_first_validation_error_handles_nested_locator() -> None:
    class Inner(BaseModel):
        port: int

    class Outer(BaseModel):
        inner: Inner

    with pytest.raises(ValidationError) as ei:
        Outer.model_validate({"inner": {"port": "not-an-int"}})
    msg = first_validation_error(ei.value)
    assert msg.startswith("inner.port: ")


def test_first_validation_error_falls_back_to_str_when_errors_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degenerate ``ValidationError`` with no error entries must not
    crash the formatter — fall back to ``str(exc)`` instead of indexing
    an empty list."""

    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as ei:
        M.model_validate({"x": "not-an-int"})
    monkeypatch.setattr(ei.value, "errors", lambda: [])
    assert first_validation_error(ei.value) == str(ei.value)


def test_first_validation_error_omits_loc_prefix_when_loc_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error with an empty ``loc`` tuple must render as the bare
    message — no dangling ``": "`` prefix."""

    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as ei:
        M.model_validate({"x": "not-an-int"})
    monkeypatch.setattr(
        ei.value,
        "errors",
        lambda: [{"loc": (), "msg": "value is invalid", "type": "value_error"}],
    )
    assert first_validation_error(ei.value) == "value is invalid"
