from datetime import UTC, datetime

from app.domain.conversation import Conversation, ConversationSummary, ConversationTurn
from app.domain.query import Answer, Citation


def test_models_construct() -> None:
    turn = ConversationTurn(
        query="pto?",
        answer=Answer(text="20 days", citations=[Citation(
            doc_id="d1", chunk_id="d1#0", source_url="http://x", title="PTO", snippet="...")], query_id=""),
        ts=datetime(2026, 6, 1, tzinfo=UTC))
    conv = Conversation(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC), turns=[turn])
    summ = ConversationSummary(id="c1", title="pto?", updated_at=datetime(2026, 6, 1, tzinfo=UTC), turn_count=1)
    assert conv.turns[0].answer.text == "20 days"
    assert conv.turns[0].answer.citations[0].doc_id == "d1"
    assert summ.turn_count == 1
