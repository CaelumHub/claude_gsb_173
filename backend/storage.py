# -*- coding: utf-8 -*-
"""
JSON 文件存储层：文件锁 + 原子写。

难点"JSON 文件在频繁写入下的并发安全"的落点：
  * **原子写**：先把内容写到同目录下的临时文件，``fsync`` 落盘后再 ``os.replace``
    原子替换目标文件。任何时刻读到的目标文件要么是旧的完整版本、要么是新的完整
    版本，绝不出现写了一半的中间态；
  * **文件锁**：对每次读改写加排他锁（Linux 用 ``fcntl.flock``，其它平台退化为
    ``msvcrt.locking`` 或无锁），保证多线程 / 多进程同时写同一项目文件时串行化，
    不会互相覆盖、不会读到半截数据。

存储布局（按项目与版本分目录）：
    data/projects/<project_id>/meta.json          项目元信息
    data/projects/<project_id>/versions/<version_id>/manifest.json  版本清单
    data/projects/<project_id>/versions/<version_id>/source.txt     源码
    data/projects/<project_id>/versions/<version_id>/compile.json   编译产物缓存
    data/projects/<project_id>/versions/<version_id>/run.json       运行记录
    data/runs/<run_id>.json                       全局运行记录索引
    data/settings/settings.json                   平台设置
"""

import json
import os
import time
import uuid
import tempfile
import threading
from contextlib import contextmanager

from . import config

# 跨平台的线程内互斥（同进程内多线程写同一文件）
_thread_locks = {}
_thread_locks_guard = threading.Lock()


def _thread_lock_for(path):
    with _thread_locks_guard:
        return _thread_locks.setdefault(path, threading.RLock())


# ---------------------------------------------------------------------------
# 文件锁（跨进程）
# ---------------------------------------------------------------------------
def _platform_lock(lock_file, exclusive=True):
    """返回一个平台相关的文件锁上下文管理器。"""
    try:
        import fcntl

        @contextmanager
        def _flock():
            with open(lock_file, "a+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
                try:
                    yield
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

        return _flock()
    except ImportError:
        try:
            import msvcrt

            @contextmanager
            def _mslock():
                with open(lock_file, "a+") as f:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

            return _mslock()
        except ImportError:
            @contextmanager
            def _nolock():
                yield
            return _nolock()


@contextmanager
def file_lock(path, exclusive=True):
    """对给定数据文件加跨进程文件锁（同时持有进程内线程锁）。"""
    lock_path = path + ".lock"
    directory = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(directory, exist_ok=True)
    with _thread_lock_for(path):
        with _platform_lock(lock_path, exclusive=exclusive):
            yield


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------
def atomic_write_text(path: str, text: str):
    """把文本原子地写入 path：临时文件 + fsync + os.replace。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data):
    """把 Python 对象序列化为 JSON 后原子写入（紧凑但可读）。"""
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# 带锁的 JSON 读写
# ---------------------------------------------------------------------------
def read_json(path, default=None):
    """读取 JSON 文件；文件不存在或损坏时返回 default。"""
    if not os.path.exists(path):
        return default
    with file_lock(path, exclusive=False):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, ValueError):
            return default


def write_json(path, data):
    """带排他锁地原子写入 JSON。"""
    with file_lock(path, exclusive=True):
        atomic_write_json(path, data)


def update_json(path, mutator, default=None):
    """读-改-写一个 JSON 文件，全程持锁，保证并发安全。

    mutator(data) 返回 (new_data, changed)；changed 为 False 时跳过写入。
    """
    with file_lock(path, exclusive=True):
        data = default
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError, ValueError):
                data = default
        new_data, changed = mutator(data)
        if changed:
            atomic_write_json(path, new_data)
        return new_data, changed


def ensure_text(path, text):
    """确保一个文本文件存在（用于保存源码快照），返回是否发生了写入。"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == text:
                return False
    with file_lock(path, exclusive=True):
        atomic_write_text(path, text)
    return True


# ---------------------------------------------------------------------------
# ID 生成与路径工具
# ---------------------------------------------------------------------------
def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def project_dir(project_id):
    return os.path.join(config.PROJECTS_DIR, project_id)


def versions_dir(project_id):
    return os.path.join(project_dir(project_id), "versions")


def version_dir(project_id, version_id):
    return os.path.join(versions_dir(project_id), version_id)
