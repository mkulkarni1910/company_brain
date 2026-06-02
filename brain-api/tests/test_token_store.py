from datetime import datetime, timezone

from app.domain.token import TokenCreated, TokenMeta


def test_token_meta_and_created_shapes() -> None:
    meta = TokenMeta(
        token_id="tk1",
        name="laptop",
        masked="sbx_live_••••a210",
        created_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
    )
    assert meta.last_used_at is None
    created = TokenCreated(token="sbx_live_secret", meta=meta)
    assert created.token == "sbx_live_secret"
    assert created.meta.token_id == "tk1"
