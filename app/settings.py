"""全局键值设置存取(如默认下载目录)。"""
from __future__ import annotations

from .db import get_session
from .models import AppSetting


def get_setting(key: str, default: str = "") -> str:
    with get_session() as s:
        row = s.get(AppSetting, key)
        return row.value if row and row.value else default


def set_setting(key: str, value: str):
    with get_session() as s:
        row = s.get(AppSetting, key)
        if row:
            row.value = value
        else:
            row = AppSetting(key=key, value=value)
        s.add(row)
        s.commit()
