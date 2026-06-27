from sqlalchemy.orm import Session
from models.audit_log import AuditLog
import logging

logger = logging.getLogger(__name__)


def log_action(db: Session, action: str, actor: str = "system", target: str = "",
               ip_address: str = "", detail: str = "") -> None:
    """写入一条审计日志。失败不应影响主流程。"""
    try:
        entry = AuditLog(
            actor=actor or "system",
            action=action,
            target=target or "",
            ip_address=ip_address or "",
            detail=detail or "",
        )
        db.add(entry)
        db.commit()
    except Exception as e:  # pragma: no cover - 审计失败仅记录
        logger.error(f"写审计日志失败: action={action} err={e}")
        try:
            db.rollback()
        except Exception:
            pass
