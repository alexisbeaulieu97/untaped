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


@pytest.fixture
def int_validation_error() -> ValidationError:
    """A real ``ValidationError`` from coercing a string into an int field.

    Pydantic v2 has no public constructor for ``ValidationError``; triggering
    a genuine validation failure is the only public way to obtain one for
    tests that monkeypatch its ``errors()`` shape."""

    class M(BaseModel):
        x: int

    with pytest.raises(ValidationError) as ei:
        M.model_validate({"x": "not-an-int"})
    return ei.value


def test_first_validation_error_extracts_message(
    int_validation_error: ValidationError,
) -> None:
    assert first_validation_error(int_validation_error).startswith("x: ")


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
    int_validation_error: ValidationError,
) -> None:
    monkeypatch.setattr(int_validation_error, "errors", lambda: [])
    assert first_validation_error(int_validation_error) == str(int_validation_error)


def test_first_validation_error_omits_loc_prefix_when_loc_empty(
    monkeypatch: pytest.MonkeyPatch,
    int_validation_error: ValidationError,
) -> None:
    monkeypatch.setattr(
        int_validation_error,
        "errors",
        lambda: [{"loc": (), "msg": "value is invalid"}],
    )
    assert first_validation_error(int_validation_error) == "value is invalid"
