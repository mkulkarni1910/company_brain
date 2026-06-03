from datetime import UTC, datetime

import pytest

from app.domain.chunk import SourceDoc
from app.domain.identity import User
from app.generation.azure_openai import AzureOpenAIClient
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever


@pytest.mark.integration
async def test_same_query_different_ranking_per_persona() -> None:
    embedder = AzureOpenAIClient()
    search = AISearchClient()
    graph = PeopleGraphClient()
    retriever = HybridRetriever(search=search, embedder=embedder)
    proximity = PeopleProximity(graph=graph)
    ranker = PersonalizedRanker(weight_content=0.4, weight_people=0.6)
    now = datetime.now(UTC)

    try:
        # Two "planning priorities" docs, one authored by each persona.
        for did, author, body in [
            ("up:persona-sales-plan", "p-sales",
             "# Sales Planning Priorities\n\nOur planning priorities focus on enterprise pipeline and upsell."),
            ("up:persona-eng-plan", "p-eng",
             "# Engineering Planning Priorities\n\nOur planning priorities focus on reliability and platform scale."),
        ]:
            from app.ingest.pipeline import IngestPipeline
            pipe = IngestPipeline(embedder=embedder, search=search)
            await pipe.process(SourceDoc(
                doc_id=did, tenant_id="t-test", source="uploaded",
                source_url=f"local://{did}", title=did, body=body,
                author_id=author, acl_principals=["t-test:everyone"],
                created_at=now, modified_at=now, mime="text/markdown",
            ))
            await graph.upsert_user(user_id=author, tenant_id="t-test",
                                    email=f"{author}@x", display_name=author)
            await graph.upsert_document(doc_id=did, tenant_id="t-test")
            await graph.upsert_edge(label="authored", from_id=author, to_id=did, tenant_id="t-test")

        async def ranked_doc_ids(user: User) -> list[str]:
            cands = await retriever.retrieve(query="what are our planning priorities?", user=user, k=10)
            cands = [c for c in cands if c.chunk.doc_id in {"up:persona-sales-plan", "up:persona-eng-plan"}]
            prox = await proximity.score(user=user, doc_ids=[c.chunk.doc_id for c in cands])
            ranked = ranker.rank(candidates=cands, proximity=prox)
            return [r.candidate.chunk.doc_id for r in ranked]

        sales = User(user_id="p-sales", tenant_id="t-test", email="s@x",
                     display_name="S", group_ids={"t-test:everyone", "g-sales"})
        eng = User(user_id="p-eng", tenant_id="t-test", email="e@x",
                   display_name="E", group_ids={"t-test:everyone", "g-eng"})

        sales_order = await ranked_doc_ids(sales)
        eng_order = await ranked_doc_ids(eng)

        # Each persona ranks their own authored doc first.
        assert sales_order[0] == "up:persona-sales-plan"
        assert eng_order[0] == "up:persona-eng-plan"
        # And the orderings differ — the headline claim.
        assert sales_order != eng_order
    finally:
        await embedder.aclose()
        await search.aclose()
        await graph.aclose()
