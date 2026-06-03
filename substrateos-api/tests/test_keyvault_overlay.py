from types import SimpleNamespace

from app.config import Settings, load_secrets_from_keyvault


class _FakeKVClient:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(value=self._secrets[name])


def test_overlay_sets_redis_key_when_enabled() -> None:
    s = Settings(use_key_vault=True, azure_key_vault_url="https://kv.vault.azure.net")
    client = _FakeKVClient(
        {
            "redis-key": "kv-redis-secret",
            "cosmos-gremlin-key": "kv-cosmos-secret",
            "admin-api-key": "kv-admin-secret",
        }
    )
    load_secrets_from_keyvault(s, client=client)
    assert s.redis_key == "kv-redis-secret"
    assert s.cosmos_gremlin_key == "kv-cosmos-secret"
    assert s.admin_api_key == "kv-admin-secret"


def test_overlay_noop_when_disabled() -> None:
    s = Settings(
        use_key_vault=False,
        azure_key_vault_url="https://kv.vault.azure.net",
        redis_key="original",
    )
    client = _FakeKVClient({"redis-key": "kv-redis-secret"})
    load_secrets_from_keyvault(s, client=client)
    assert s.redis_key == "original"
