"""数据库初始化与会话。含 SQLite 轻量自动迁移(为已有表补缺失列)。"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, create_engine

_engine = None


def _auto_migrate(engine):
    """为已存在的表补上模型里新增的列(SQLite 友好,仅 ADD COLUMN)。"""
    insp = inspect(engine)
    for table in SQLModel.metadata.tables.values():
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(engine.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'
            # 模型若给了标量默认值,作为 SQL DEFAULT 写入 —— SQLite 会用它回填已有行,
            # 避免新列在旧数据上为 NULL(例如 platform 列需回填为 'douyin')。
            scalar = (getattr(col.default, "arg", None)
                      if col.default is not None and getattr(col.default, "is_scalar", False)
                      else None)
            if isinstance(scalar, bool):
                ddl += f" DEFAULT {1 if scalar else 0}"
            elif isinstance(scalar, str):
                ddl += " DEFAULT '" + scalar.replace("'", "''") + "'"
            elif isinstance(scalar, (int, float)):
                ddl += f" DEFAULT {scalar}"
            elif not col.nullable:
                ddl += " DEFAULT ''"
            with engine.begin() as conn:
                conn.execute(text(ddl))


def init_db(db_path: str):
    global _engine
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(_engine)
    _auto_migrate(_engine)
    return _engine


def get_session() -> Session:
    assert _engine is not None, "init_db() 未调用"
    return Session(_engine)
