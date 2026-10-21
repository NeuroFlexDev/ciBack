"""Restricted diagnostics containing counts and health only, never customer content or secrets."""
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends
from redis import Redis
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.database.db import get_db
from app.models.integrations import OutboundMessage
from app.models.ai_state import AICall
from app.models.generation_run import GenerationRun
from app.services.auth_service import require_admin
from app.services.mail_service import configured

router=APIRouter()


def diagnostics(db):
    started=time.monotonic();db.execute(text("SELECT 1"));db_ms=round((time.monotonic()-started)*1000,2)
    disk_path=Path(settings.UPLOAD_DIR).resolve()
    while not disk_path.exists():disk_path=disk_path.parent
    disk=shutil.disk_usage(disk_path)
    redis_client=None
    redis_state={"status":"down"}
    try:
        redis_client=Redis.from_url(settings.REDIS_URL,socket_timeout=1,socket_connect_timeout=1)
        redis_client.ping()
        info=redis_client.info("memory")
        redis_state={"status":"up","memory_bytes":info.get("used_memory",0),"generation_queued":redis_client.llen("rq:queue:"+settings.GENERATION_QUEUE_NAME)}
    except Exception:pass
    finally:
        if redis_client:redis_client.close()
    outbox={status:count for status,count in db.query(OutboundMessage.status,func.count()).group_by(OutboundMessage.status)}
    oldest=db.query(func.min(OutboundMessage.created_at)).filter(OutboundMessage.status.in_(["pending","sending"])).scalar()
    outbox["oldest_pending_seconds"]=max(0,int((datetime.utcnow()-oldest).total_seconds())) if oldest else 0
    runs=db.query(GenerationRun).filter(GenerationRun.created_at>=datetime.utcnow()-timedelta(days=1))
    calls=db.query(AICall).filter(AICall.run_id.in_(runs.with_entities(GenerationRun.id)))
    known_cost=runs.with_entities(func.sum(GenerationRun.cost_usd)).scalar()
    counts={"runs_started_last_day":runs.count(),"calls_in_those_runs":calls.count(),
        "failed_calls_in_those_runs":calls.filter(AICall.error_code.is_not(None)).count(),
        "average_latency_ms":calls.with_entities(func.avg(AICall.latency_ms)).scalar(),
        "known_cost_usd":float(known_cost) if known_cost is not None else None}
    return {"checked_at":datetime.utcnow(),"db":{"status":"up","latency_ms":db_ms},"redis":redis_state,
        "disk":{"total_bytes":disk.total,"free_bytes":disk.free,"used_percent":round(100*disk.used/disk.total,1)},
        "outbox":outbox,"mail_configured":configured(),"ai":counts}


@router.get("/admin/operations")
def operations(db:Session=Depends(get_db),admin=Depends(require_admin)):
    return diagnostics(db)
