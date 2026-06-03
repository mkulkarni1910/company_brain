from fastapi.testclient import TestClient

from app.main import app


def test_lifespan_populates_and_closes_clients() -> None:
    # Entering the TestClient context runs the lifespan startup.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        # clients are constructed and stored on app.state during startup
        assert app.state.embedder is not None
        assert app.state.ai_search is not None
        assert app.state.cache is not None
        assert app.state.retriever is not None
        assert app.state.orchestrator is not None
    # after the context exits, shutdown ran without raising
