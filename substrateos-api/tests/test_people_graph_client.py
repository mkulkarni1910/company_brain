import pytest

from app.people.graph_client import PeopleGraphClient


@pytest.mark.integration
async def test_upsert_and_query_vertex_round_trip() -> None:
    gc = PeopleGraphClient()
    try:
        await gc.upsert_user(user_id="t5-u1", tenant_id="t-test", email="a@b", display_name="A")
        await gc.upsert_user(user_id="t5-u2", tenant_id="t-test", email="c@d", display_name="C")
        await gc.upsert_edge(
            label="manages", from_id="t5-u1", to_id="t5-u2", tenant_id="t-test"
        )
        count = await gc.submit(
            "g.V().has('user','user_id', uid).out('manages').count()",
            {"uid": "t5-u1"},
        )
        assert count[0] >= 1
    finally:
        await gc.aclose()
