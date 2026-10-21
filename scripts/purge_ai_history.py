"""Daily data-retention job; preserves non-content usage totals for quotas/audit."""
from datetime import datetime, timedelta
from app.database.db import SessionLocal
from app.core.config import settings
from app.models.materials import LearnerAssistantMessage
from app.models.chat import ChatMessage
from app.models.ai_state import ChatMemory, AIResponseCache


def purge(db):
    cutoff = datetime.utcnow() - timedelta(days=settings.AI_CHAT_RETENTION_DAYS)
    db.query(LearnerAssistantMessage).filter(LearnerAssistantMessage.created_at < cutoff).update(
        {"question": "", "answer": None, "citations": [], "status": "deleted"}, synchronize_session=False)
    expired_chats = db.query(ChatMessage.chat_id).filter(ChatMessage.created_at < cutoff).distinct()
    db.query(ChatMemory).filter(ChatMemory.chat_id.in_(expired_chats)).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.created_at < cutoff).update(
        {"content": "", "is_deleted": True, "message_metadata": {}}, synchronize_session=False)
    db.query(AIResponseCache).filter(AIResponseCache.expires_at <= datetime.utcnow()).delete(synchronize_session=False)
    db.commit()


if __name__ == "__main__":
    with SessionLocal() as session:
        purge(session)
    print("AI history retention completed")
