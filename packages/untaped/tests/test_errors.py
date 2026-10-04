import pytest
from pydantic import BaseModel, ValidationError, field_validator

from untaped.errors import (
    ConfigError,
    ErrorCategory,
    ExitCode,
    HttpError,
    HttpStatusError,
    HttpTransportError,
    OperationCancelledError,
    PromptInterruptedError,
    UntapedError,
    UsageError,
    attribution,
    combine_exit_codes,
    most_severe,
)
from untaped.git import GitCommandError
from untaped.sdk import first_validation_error


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


def test_first_validation_error_shows_a_validator_message_without_pydantic_prefix() -> None:
    class M(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def _check(cls, value: str) -> str:
            raise ValueError("name must be lowercase")

    with pytest.raises(ValidationError) as ei:
        M.model_validate({"name": "Bad"})
    assert first_validation_error(ei.value) == "name: name must be lowercase"


# --- failure attribution: category, system, hint, details -----------------


@pytest.mark.parametrize(
    ("category", "code", "retryable"),
    [
        (ErrorCategory.USAGE, 2, False),
        (ErrorCategory.CONFIG, 4, False),
        (ErrorCategory.AUTH, 4, False),
        (ErrorCategory.PERMISSION, 4, False),
        (ErrorCategory.NOT_FOUND, 1, False),
        (ErrorCategory.INVALID, 1, False),
        (ErrorCategory.CONFLICT, 1, False),
        (ErrorCategory.UNAVAILABLE, 5, True),
        (ErrorCategory.FAILED, 1, False),
        (ErrorCategory.INTERRUPTED, 130, False),
    ],
)
def test_each_category_selects_its_exit_code_and_retryability(
    category: ErrorCategory, code: int, retryable: bool
) -> None:
    assert (category.exit_code, category.retryable) == (code, retryable)
    error = UntapedError("boom", category=category)
    assert (error.exit_code, error.retryable) == (code, retryable)


def test_untaped_error_defaults_to_a_failed_untaped_error() -> None:
    error = UntapedError("boom")

    assert error.category is ErrorCategory.FAILED
    assert error.system == "untaped"
    assert error.hint is None
    assert dict(error.details) == {}
    assert error.exit_code == 1


def test_instance_fields_override_the_class_defaults() -> None:
    error = ConfigError(
        "rejected", category="auth", system="awx", hint="run it", details={"status": 401}
    )

    assert error.category is ErrorCategory.AUTH
    assert (error.system, error.hint, dict(error.details)) == ("awx", "run it", {"status": 401})
    assert error.exit_code == ExitCode.ENVIRONMENT
    assert ConfigError("other").category is ErrorCategory.CONFIG


@pytest.mark.parametrize(
    ("error", "category", "system", "code"),
    [
        (ConfigError("x"), ErrorCategory.CONFIG, "local", 4),
        (UsageError("x"), ErrorCategory.USAGE, "untaped", 2),
        (PromptInterruptedError("x"), ErrorCategory.INTERRUPTED, "untaped", 130),
        (OperationCancelledError(), ErrorCategory.FAILED, "untaped", 1),
        (GitCommandError("x"), ErrorCategory.FAILED, "git", 1),
        (GitCommandError("x", timed_out=True), ErrorCategory.UNAVAILABLE, "git", 5),
        (HttpTransportError("x"), ErrorCategory.UNAVAILABLE, "http", 5),
        (HttpError("bad json", status_code=200), ErrorCategory.FAILED, "http", 1),
    ],
)
def test_core_errors_declare_a_coherent_default(
    error: UntapedError, category: ErrorCategory, system: str, code: int
) -> None:
    assert (error.category, error.system, error.exit_code) == (category, system, code)


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, ErrorCategory.INVALID),
        (401, ErrorCategory.AUTH),
        (403, ErrorCategory.PERMISSION),
        (404, ErrorCategory.NOT_FOUND),
        (405, ErrorCategory.INVALID),
        (408, ErrorCategory.UNAVAILABLE),
        (409, ErrorCategory.CONFLICT),
        (422, ErrorCategory.INVALID),
        (429, ErrorCategory.UNAVAILABLE),
        (500, ErrorCategory.UNAVAILABLE),
        (503, ErrorCategory.UNAVAILABLE),
    ],
)
def test_http_status_errors_take_their_category_from_the_status(
    status: int, category: ErrorCategory
) -> None:
    error = HttpStatusError("HTTP x", status_code=status, url="https://h/api", system="awx")

    assert error.category is category
    assert error.system == "awx"
    assert dict(error.details) == {"status": status, "url": "https://h/api"}


def test_an_explicit_category_wins_over_the_status() -> None:
    error = HttpStatusError("x", status_code=404, category="invalid")

    assert error.category is ErrorCategory.INVALID


@pytest.mark.parametrize(
    ("codes", "winner"),
    [
        ((), 0),
        ((0, 3), 3),
        ((3, 1), 1),
        ((1, 5), 5),
        ((5, 4), 4),
        ((4, 2), 2),
        ((2, 130), 130),
        ((1, 4, 5, 3), 4),
    ],
)
def test_exit_codes_combine_by_precedence(codes: tuple[int, ...], winner: int) -> None:
    assert combine_exit_codes(*codes) == winner


def test_attribution_copies_category_system_and_details() -> None:
    cause = HttpStatusError("HTTP 503", status_code=503, url="https://h", system="awx")

    assert attribution(cause) == {
        "category": ErrorCategory.UNAVAILABLE,
        "system": "awx",
        "details": {"status": 503, "url": "https://h"},
    }
    assert attribution(KeyError("x")) == {}


def test_most_severe_picks_the_error_whose_code_wins() -> None:
    missing = UntapedError("gone", category="not_found")
    down = UntapedError("down", category="unavailable")
    rejected = UntapedError("rejected", category="auth")

    assert most_severe([missing, down, KeyError("bug")]) is down
    assert most_severe([down, rejected, missing]) is rejected
    assert most_severe(iter([missing])) is missing
    with pytest.raises(ValueError):
        most_severe([])


def test_attribution_carries_the_hint() -> None:
    cause = ConfigError("rejected", category="auth", hint="run `untaped config set x`")

    assert attribution(cause)["hint"] == "run `untaped config set x`"
    assert UntapedError("wrapped", **attribution(cause)).hint == "run `untaped config set x`"


def test_prompts_without_a_terminal_are_usage_errors_of_untaped() -> None:
    import io

    from untaped.ui import UiContext

    with pytest.raises(UsageError) as caught:
        UiContext(stdin=io.StringIO()).text("name")

    assert (caught.value.category, caught.value.system) == ("usage", "untaped")


def test_attribution_survives_pickling() -> None:
    import pickle

    error = ConfigError("rejected", category="auth", system="awx", details={"status": 401})
    copy = pickle.loads(pickle.dumps(error))

    assert (copy.category, copy.system, dict(copy.details)) == (
        ErrorCategory.AUTH,
        "awx",
        {"status": 401},
    )


def test_prompt_interrupted_is_not_a_config_error() -> None:
    assert issubclass(PromptInterruptedError, UntapedError)
    assert not issubclass(PromptInterruptedError, ConfigError)
