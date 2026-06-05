import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.acl.store import ACLStore
from app.activity.signal import ActivitySignal
from app.activity.store import ActivityStore
from app.api.admin import callback_router as admin_callback_router
from app.api.admin import router as admin_router
from app.api.context import router as context_router
from app.api.conversations import router as conversations_router
from app.api.discover import router as discover_router
from app.api.feedback import router as feedback_router
from app.api.history import router as history_router
from app.api.query import router as query_router
from app.api.retrieve import router as retrieve_router
from app.api.bots import router as bots_router
from app.api.search import router as search_router
from app.api.sources import router as sources_router
from app.api.surfaces import router as surfaces_router
from app.api.tokens import router as tokens_router
from app.api.runs import router as runs_router
from app.api.skills import admin_router as skills_admin_router
from app.api.skills import router as skills_router
from app.cache.redis_cache import RedisCache
from app.config import get_settings, load_secrets_from_keyvault
from app.connectors.cosmos_store import CosmosConnectionStore
from app.connectors.sharepoint import SharePointConnector
from app.connectors.store import ConnectionStore
from app.connectors.subscriptions import CosmosSubscriptionStore, SubscriptionStore
from app.conversations.store import ConversationStore
from app.discover.service import DiscoverService
from app.generation.acknowledger import Acknowledger
from app.generation.azure_openai import AzureOpenAIClient
from app.generation.gemini import GeminiClient
from app.history.store import HistoryStore
from app.live_fetch.graph_search import MSGraphSearchFetcher
from app.mcp.server import build_mcp_asgi, mcp_bind, run_session_manager
from app.metrics.store import MetricsStore
from app.skills.store import SkillStore
from app.skills.service import SkillRouter as SkillRouterSvc
from app.orchestrator.kernel import SemanticKernelOrchestrator
from app.orchestrator.planner import QueryPlanner
from app.people.graph_client import PeopleGraphClient
from app.people.proximity import PeopleProximity
from app.ranking.personalized_ranker import PersonalizedRanker
from app.retrieval.ai_search_client import AISearchClient
from app.retrieval.hybrid_retriever import HybridRetriever
from app.search.service import SearchService
from app.tokens.store import CosmosTokenStore, NullTokenStore
from app.workflows.engine import RefundEngine
from app.workflows.flow import RefundFlow
from app.workflows.store import RunStore

logger = logging.getLogger("app.startup")


def _configure_observability(app: FastAPI) -> None:
    """Make pipeline timing visible. Two parts, both best-effort:

    1. Logging — nothing configures it otherwise, so app `INFO` logs (incl. the
       per-stage query timings) are dropped by the default WARNING root. Set the
       `app` logger to the configured level and ensure a stdout handler exists.
    2. Azure Monitor OTel — already a dependency but never initialized, so App
       Insights had no dependency telemetry. Initializing it auto-instruments
       httpx (Gemini + Azure OpenAI + Search) so each external call shows up as a
       timed dependency span alongside our stage logs. Must run BEFORE clients are
       constructed so their httpx sessions are patched.
    """
    settings = get_settings()
    level = getattr(logging, settings.substrateos_log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("app").setLevel(level)

    cs = settings.applicationinsights_connection_string
    if not cs:
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        configure_azure_monitor(connection_string=cs)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("Azure Monitor OTel initialized (httpx dependency tracing on)")
    except Exception as e:  # noqa: BLE001 — telemetry must never block startup
        logger.warning("Azure Monitor OTel init skipped: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_secrets_from_keyvault(get_settings())
    _configure_observability(app)  # logging + OTel before any httpx client is built
    app.state.embedder = AzureOpenAIClient()  # embeddings (vector search)
    app.state.llm = GeminiClient()            # answer generation (Gemini 2.5 Pro)
    app.state.ai_search = AISearchClient()
    app.state.cache = RedisCache()
    app.state.retriever = HybridRetriever(
        search=app.state.ai_search, embedder=app.state.embedder
    )
    app.state.acl_store = ACLStore()
    app.state.people_graph = PeopleGraphClient()
    app.state.proximity = PeopleProximity(graph=app.state.people_graph)
    app.state.ranker = PersonalizedRanker(
        weight_content=get_settings().rank_weight_content,
        weight_people=get_settings().rank_weight_people,
        weight_activity=get_settings().rank_weight_activity,
        weight_recency=get_settings().rank_weight_recency,
    )
    app.state.activity_store = ActivityStore()
    app.state.activity = ActivitySignal(store=app.state.activity_store)
    app.state.live_fetcher = MSGraphSearchFetcher()
    app.state.planner = QueryPlanner(llm=app.state.llm)
    app.state.acknowledger = Acknowledger(llm=app.state.llm)
    app.state.orchestrator = SemanticKernelOrchestrator(
        retriever=app.state.retriever,
        llm=app.state.llm,
        cache=app.state.cache,
        acl_store=app.state.acl_store,
        proximity=app.state.proximity,
        ranker=app.state.ranker,
        activity=app.state.activity,
        live_fetcher=app.state.live_fetcher,
        planner=app.state.planner,
    )
    app.state.history_store = HistoryStore()
    app.state.conversation_store = ConversationStore()
    app.state.discover_service = DiscoverService(
        activity=app.state.activity_store,
        search=app.state.ai_search,
        cache=app.state.cache,
    )
    app.state.search_service = SearchService(
        embedder=app.state.embedder,
        search=app.state.ai_search,
        people=app.state.people_graph,
    )
    # Durable connector state: Cosmos (reuses the people graph connection) when
    # configured — e.g. the India deploy has no Redis; else the Redis store
    # (which itself no-ops when AZURE_REDIS_HOST is unset).
    _s = get_settings()
    if _s.cosmos_gremlin_endpoint and _s.cosmos_gremlin_key:
        app.state.connection_store = CosmosConnectionStore(graph=app.state.people_graph)
    else:
        app.state.connection_store = ConnectionStore()
    # PATs: Cosmos (reuses the people graph) when configured, else a no-op store.
    if _s.cosmos_gremlin_endpoint and _s.cosmos_gremlin_key:
        app.state.token_store = CosmosTokenStore(graph=app.state.people_graph)
    else:
        app.state.token_store = NullTokenStore()
    app.state.metrics_store = MetricsStore()
    app.state.skill_store = SkillStore()
    app.state.skill_router_svc = SkillRouterSvc(
        skill_store=app.state.skill_store,
        llm=app.state.llm,
    )
    app.state.run_store = RunStore()
    app.state.refund_flow = RefundFlow(
        engine=RefundEngine(retriever=app.state.retriever, llm=app.state.llm),
        store=app.state.run_store,
    )
    # Outlook realtime subs + delta tokens: Cosmos (reuses people graph) when
    # configured (e.g. India has no Redis), else Redis (no-op without a host).
    if _s.cosmos_gremlin_endpoint and _s.cosmos_gremlin_key:
        app.state.subscription_store = CosmosSubscriptionStore(graph=app.state.people_graph)
    else:
        app.state.subscription_store = SubscriptionStore()
    app.state.sharepoint = SharePointConnector()
    mcp_bind(
        orchestrator=app.state.orchestrator,
        search=app.state.search_service,
        token_store=app.state.token_store,
    )
    try:
        if get_settings().mcp_enabled:
            async with run_session_manager():
                yield
        else:
            yield
    finally:
        await app.state.orchestrator.aclose()
        await app.state.run_store.aclose()
        await app.state.acl_store.aclose()
        await app.state.people_graph.aclose()
        await app.state.activity_store.aclose()
        await app.state.history_store.aclose()
        await app.state.conversation_store.aclose()
        await app.state.cache.aclose()
        await app.state.ai_search.aclose()
        await app.state.embedder.aclose()
        await app.state.llm.aclose()
        await app.state.connection_store.aclose()
        await app.state.subscription_store.aclose()
        await app.state.metrics_store.aclose()
        await app.state.skill_store.aclose()
        await app.state.token_store.aclose()


app = FastAPI(title="substrateos-api", version="0.1.0", lifespan=lifespan)

# CORS origins are configurable via CORS_ALLOW_ORIGINS (comma-separated).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)
app.include_router(admin_callback_router)
app.include_router(query_router)
app.include_router(retrieve_router)
app.include_router(feedback_router)
app.include_router(history_router)
app.include_router(discover_router)
app.include_router(search_router)
app.include_router(conversations_router)
app.include_router(sources_router)
app.include_router(bots_router)
app.include_router(surfaces_router)
app.include_router(tokens_router)
app.include_router(context_router)
app.include_router(skills_router)
app.include_router(skills_admin_router)
app.include_router(runs_router)

if get_settings().mcp_enabled:
    app.mount("/mcp", build_mcp_asgi())


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "substrateos-api"}
