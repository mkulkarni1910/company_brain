from app.config import Settings


def test_cors_origins_default_parses() -> None:
    s = Settings()
    origins = [o.strip() for o in s.cors_allow_origins.split(",") if o.strip()]
    assert origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_cors_origins_split_multiple() -> None:
    s = Settings(cors_allow_origins="a,b")
    assert s.cors_allow_origins.split(",") == ["a", "b"]
