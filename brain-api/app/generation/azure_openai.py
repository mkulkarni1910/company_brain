from __future__ import annotations

from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings


class AzureOpenAIClient:
    def __init__(self) -> None:
        self._s = get_settings()
        self._credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            self._credential, "https://cognitiveservices.azure.com/.default"
        )
        self._cli = AsyncAzureOpenAI(
            azure_endpoint=self._s.azure_openai_endpoint,
            api_version=self._s.azure_openai_api_version,
            azure_ad_token_provider=token_provider,
        )

    async def aclose(self) -> None:
        await self._cli.close()
        await self._credential.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def embed(self, text: str) -> list[float]:
        resp = await self._cli.embeddings.create(
            model=self._s.azure_openai_embed_deployment,
            input=text,
            dimensions=3072,
        )
        return resp.data[0].embedding

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._cli.embeddings.create(
            model=self._s.azure_openai_embed_deployment,
            input=texts,
            dimensions=3072,
        )
        return [d.embedding for d in resp.data]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def complete(
        self,
        *,
        messages: list[dict[str, str]],
        deployment: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 800,
    ) -> str:
        resp = await self._cli.chat.completions.create(
            model=deployment or self._s.azure_openai_chat_deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""
