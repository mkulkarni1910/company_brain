import pytest

from app.domain.identity import User
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity


@pytest.mark.integration
async def test_proximity_higher_for_authored_by_self() -> None:
    gc = PeopleGraphClient()
    try:
        # u-prox authored doc-near; nobody u-prox knows authored doc-far
        await gc.upsert_user(user_id="u-prox", tenant_id="t-test", email="p@x", display_name="P")
        await gc.upsert_document(doc_id="doc-near", tenant_id="t-test")
        await gc.upsert_document(doc_id="doc-far", tenant_id="t-test")
        await gc.upsert_user(user_id="u-stranger", tenant_id="t-test", email="s@x", display_name="S")
        await gc.upsert_edge(label="authored", from_id="u-prox", to_id="doc-near", tenant_id="t-test")
        await gc.upsert_edge(label="authored", from_id="u-stranger", to_id="doc-far", tenant_id="t-test")

        user = User(
            user_id="u-prox", tenant_id="t-test", email="p@x", display_name="P", group_ids=set()
        )
        scores = await PeopleProximity(graph=gc).score(user=user, doc_ids=["doc-near", "doc-far"])
        assert scores["doc-near"] > scores["doc-far"]
        assert 0.0 <= scores["doc-far"] <= 1.0
        assert 0.0 <= scores["doc-near"] <= 1.0
    finally:
        await gc.aclose()
