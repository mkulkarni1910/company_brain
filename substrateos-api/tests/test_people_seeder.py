import httpx
import pytest

from app.people.graph_client import PeopleGraphClient
from app.people.seeder import PeopleSeeder


@pytest.mark.integration
async def test_seed_from_graph_creates_users() -> None:
    gc = PeopleGraphClient()
    seeder = PeopleSeeder(graph=gc, tenant_id="t-test")
    try:
        try:
            result = await seeder.seed_users(limit=5)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                pytest.skip(
                    "signed-in user lacks Graph directory-read; "
                    "seeder needs app-only token in cloud"
                )
            raise
        assert result["users"] >= 1
        # at least one user vertex exists in the t-test partition
        count = await gc.submit(
            "g.V().hasLabel('user').has('tenant_id', tid).count()", {"tid": "t-test"}
        )
        assert count[0] >= 1
    finally:
        await gc.aclose()
