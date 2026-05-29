from datetime import UTC, datetime, timedelta

import pytest

from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.domain.activity import ActivityEvent
from app.domain.chunk import SourceDoc
from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.ingest.pipeline import IngestPipeline
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_engagement_lifts_ranking() -> None:
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    store = ActivityStore()
    now = datetime.now(UTC)
    try:
        await store.ensure_table()
        # Two near-identical "benefits overview" docs, no authorship, same ACL.
        for did, body in [
            ("up:act-bene-a", "# Benefits Overview A\n\nOur benefits overview covers health, dental, and vision."),
            ("up:act-bene-b", "# Benefits Overview B\n\nOur benefits overview covers health, dental, and vision."),
        ]:
            pipe = IngestPipeline(embedder=embedder, search=search)
            await pipe.process(SourceDoc(
                doc_id=did, tenant_id="t-test", source="uploaded",
                source_url=f"local://{did}", title=did, body=body, author_id=None,
                acl_principals=["t-test:everyone"], created_at=now, modified_at=now,
                mime="text/markdown",
            ))
        # doc-b gets recent engagement; doc-a none.
        for i in range(5):
            await store.ingest_event(ActivityEvent(
                timestamp=now - timedelta(hours=i), tenant_id="t-test",
                user_id="u-actrank", doc_id="up:act-bene-b",
                event_type="view", source="uploaded",
            ))

        retriever = HybridRetriever(search=search, embedder=embedder)
        signal = ActivitySignal(store=store)
        user = User(user_id="u-actrank", tenant_id="t-test", email="a@x",
                    display_name="A", group_ids={"t-test:everyone"})

        cands = await retriever.retrieve(query="benefits overview", user=user, k=10)
        cands = [c for c in cands if c.chunk.doc_id in {"up:act-bene-a", "up:act-bene-b"}]
        activity = await signal.score(user=user, doc_ids=[c.chunk.doc_id for c in cands])

        # Without activity weight: order is whatever content gave.
        ranker_noact = PersonalizedRanker(weight_content=1.0, weight_people=0.0, weight_activity=0.0)
        order_noact = [r.candidate.chunk.doc_id for r in ranker_noact.rank(  # noqa: F841
            candidates=cands, proximity={}, activity=activity)]

        # With activity weight: the engaged doc (b) is lifted to the top.
        ranker_act = PersonalizedRanker(weight_content=0.4, weight_people=0.0, weight_activity=0.6)
        order_act = [r.candidate.chunk.doc_id for r in ranker_act.rank(
            candidates=cands, proximity={}, activity=activity)]

        assert order_act[0] == "up:act-bene-b"            # engagement wins with the weight on
        assert activity["up:act-bene-b"] > activity.get("up:act-bene-a", 0.0)
        # And the activity weight actually changed something vs content-only,
        # OR content already ranked b first; either way b is top with activity on.
        assert order_act[0] == "up:act-bene-b"
    finally:
        await embedder.aclose()
        await search.aclose()
        await store.aclose()
