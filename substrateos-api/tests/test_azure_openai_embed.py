import pytest

from app.generation.azure_openai import AzureOpenAIClient


@pytest.mark.integration
async def test_embed_returns_3072_dim_vector() -> None:
    client = AzureOpenAIClient()
    vec = await client.embed("travel reimbursement policy")
    assert len(vec) == 3072
    # vectors are normalized-ish; first few values are non-zero floats
    assert any(abs(x) > 1e-6 for x in vec[:10])


@pytest.mark.integration
async def test_embed_batch_preserves_order() -> None:
    client = AzureOpenAIClient()
    vecs = await client.embed_batch(["alpha", "beta", "gamma"])
    assert len(vecs) == 3
    assert all(len(v) == 3072 for v in vecs)
    # different inputs → different vectors
    assert vecs[0] != vecs[1]
