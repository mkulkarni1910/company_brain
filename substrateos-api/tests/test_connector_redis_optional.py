"""When AZURE_REDIS_HOST is unset (e.g. the India deploy runs without Redis),
ConnectionStore + MetricsStore must no-op instead of hanging on a connection."""
import pytest

from app.config import get_settings
from app.connectors.models import ActivityEntry, Connection, SyncJob
from app.connectors.store import ConnectionStore
from app.metrics.store import MetricsStore


@pytest.mark.asyncio
async def test_connection_store_noop_without_host(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_REDIS_HOST", "")
    get_settings.cache_clear()
    st = ConnectionStore()
    assert st._r is None
    c = Connection(connection_id="c1", tenant_id="t", site_id="s", name="S", web_url="https://x")
    await st.put_connection(c)               # no-op, no raise
    assert await st.list_connections("t") == []
    assert await st.get_connection("t", "c1") is None
    await st.delete_connection("t", "c1")
    await st.put_job(SyncJob(job_id="j", tenant_id="t", connection_id="c1"))
    assert await st.get_job("t", "j") is None
    import datetime
    await st.log_activity("t", ActivityEntry(ts=datetime.datetime(2026, 1, 1), actor="a", text="x"))
    assert await st.recent_activity("t") == []
    await st.aclose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_metrics_store_noop_without_host(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_REDIS_HOST", "")
    get_settings.cache_clear()
    st = MetricsStore()
    assert st._r is None
    await st.record_query("t", "u")          # no-op, no raise
    assert await st.queries_last_7d("t") is None
    assert await st.active_users_7d("t") is None
    await st.aclose()
    get_settings.cache_clear()
