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
