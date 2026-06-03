import pytest

from app.auth import InvalidToken, _audience_for_scope, _validate_jwt


def test_audience_extracts_from_scope() -> None:
    aud = _audience_for_scope("api://abc-123/Query.Read")
    assert aud == "api://abc-123"


def test_invalid_token_raises() -> None:
    with pytest.raises(InvalidToken):
        _validate_jwt("not.a.real.token", audience="api://abc", tenant="tid-1")
