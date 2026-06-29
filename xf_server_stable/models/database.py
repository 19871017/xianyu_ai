from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from config import DATABASE_URL
import logging
import os

logger = logging.getLogger(__name__)

# 确保数据目录存在
db_dir = os.path.dirname(DATABASE_URL.replace("sqlite:///", ""))
if db_dir:
    os.makedirs(db_dir, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 新增列的轻量迁移定义：{表名: {列名: "SQL 列定义"}}
# create_all 不会给已存在的表补列，这里用 ALTER TABLE 幂等补齐。
_COLUMN_MIGRATIONS = {
    "devices": {
        "force_offline": "BOOLEAN DEFAULT 0",
        "last_heartbeat": "DATETIME",
        "last_nonce_ts": "INTEGER DEFAULT 0",
        "signature": "VARCHAR(512) DEFAULT ''",
    },
    "licenses": {
        "max_devices": "INTEGER DEFAULT 0",
        "note": "VARCHAR(255) DEFAULT ''",
        "revoked_at": "DATETIME",
    },
}


def _run_column_migrations():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for col, ddl in columns.items():
                if col not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                    logger.info(f"DB迁移: 为 {table} 添加列 {col}")


def init_db():
    # 先建新表（含 audit_logs），再为已存在的旧表补列。
    import models.user  # noqa: F401
    import models.license_model  # noqa: F401
    import models.device  # noqa: F401
    import models.audit_log  # noqa: F401
    import models.app_version  # noqa: F401
    import models.announcement  # noqa: F401
    Base.metadata.create_all(bind=engine)
    try:
        _run_column_migrations()
    except Exception as e:
        logger.error(f"DB列迁移失败（不影响新库）: {e}")
