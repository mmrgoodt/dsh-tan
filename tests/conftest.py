# -*- coding: utf-8 -*-
"""pytest 公共配置。

隔离原则：在 import hippocampus.config 之前把 TAN_HOME 指到临时目录，
这样 config 在 import 时创建的 ROOT / raw / backup / snapshots 全落在临时目录里，
真实的 ~/.dsh/tan/memory.db 在整个测试过程中不会被打开、也不会被创建。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 必须在 import hippocampus 之前设置
SANDBOX_HOME = Path(tempfile.mkdtemp(prefix="tan-test-home-"))
os.environ["TAN_HOME"] = str(SANDBOX_HOME)

from hippocampus import config  # noqa: E402
from hippocampus.db import DB  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def isolated_tan_home():
    """守卫夹具：断言全局 DB_PATH 落在临时 TAN_HOME 里，测试永远不碰真实库。"""
    assert SANDBOX_HOME.is_dir()
    assert Path(config.DB_PATH).resolve().is_relative_to(SANDBOX_HOME.resolve()), \
        f"config.DB_PATH 没有指向临时目录：{config.DB_PATH}"
    yield SANDBOX_HOME


@pytest.fixture()
def db_path(tmp_path):
    """每个测试一个独立的库文件（tmp_path 由 pytest 自动回收）"""
    return tmp_path / "memory.db"


@pytest.fixture()
def db(db_path):
    """指向临时库的 DB 实例"""
    return DB(db_path)


@pytest.fixture()
def engine(tmp_path):
    """指向临时库的 Hippocampus 实例（每个测试全新空库）"""
    from hippocampus.engine import Hippocampus

    return Hippocampus(tmp_path / "engine.db")
