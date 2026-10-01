# -*- coding: utf-8 -*-
"""
业务服务层：把编译器、解释器、调试器、剖析器、存储组织成可供 HTTP 接口调用的能力。

职责划分：
  * 项目管理与历史版本（创建/列表/重命名/删除/保存版本/恢复/对比）；
  * 编译流水线（带产物缓存）；
  * 普通运行（同步队列输入，可选性能剖析）；
  * 交互式运行会话（input() 等待，输入跨 HTTP 请求追加）；
  * 交互式调试会话（断点/单步/续跑，会话状态驻留内存，支持跨请求同步）；
  * 内存快照（运行/调试暂停时产出）。

所有持久化都走 storage 层的"文件锁 + 原子写"，保证频繁写入下的并发安全。
"""

import os
import shutil
import time
import queue
import difflib
import hashlib
import threading
from typing import Dict, List, Optional, Any

from . import config
from . import storage
from . import compiler as compiler_mod
from . import vm as vm_mod
from . import debugger as debugger_mod
from . import profiler as profiler_mod
from . import diagnostics as diag
from . import memory_model
from . import runtime as rt


# ---------------------------------------------------------------------------
# 版本 / 运行记录结构
# ---------------------------------------------------------------------------
def _empty_project(name, source, language="minilang"):
    now = storage.now_iso()
    pid = storage.new_id("proj")
    meta = {
        "id": pid,
        "name": name,
        "language": language,
        "created_at": now,
        "updated_at": now,
        "version_count": 0,
        "last_source": source,
        "description": "",
    }
    return meta


def _empty_version(pid, source, message, compiled=None):
    now = storage.now_iso()
    vid = storage.new_id("ver")
    manifest = {
        "id": vid,
        "project_id": pid,
        "message": message or "保存版本",
        "created_at": now,
        "source_hash": _hash(source),
        "source_len": len(source),
        "line_count": source.count("\n"),
        "compiled_ok": bool(compiled and compiled.success),
        "error_count": len(compiled.diagnostics.errors()) if compiled else 0,
    }
    return manifest


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _normalize_inputs(value):
    """把接口传入的预填输入规范成字符串列表。

    前端默认逐行填写，因此字符串会按换行拆成多条；数组中的每个元素就是一次
    ``input()`` 调用的返回值，空字符串也会保留，不会被过滤。
    """
    if value is None:
        return []
    if isinstance(value, str):
        return value.splitlines()
    if isinstance(value, (list, tuple)):
        return ["" if item is None else str(item) for item in value]
    return [str(value)]


def _diff_lines(a: str, b: str) -> List[dict]:
    """行级 diff，返回结构化结果供前端渲染。"""
    al = a.splitlines()
    bl = b.splitlines()
    sm = difflib.SequenceMatcher(None, al, bl, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.append({
            "op": tag,  # insert / delete / replace
            "old_start": i1 + 1, "old_end": i2,   # 1-based 行号
            "new_start": j1 + 1, "new_end": j2,
            "old_lines": al[i1:i2],
            "new_lines": bl[j1:j2],
        })
    return out


# ---------------------------------------------------------------------------
# 服务主体
# ---------------------------------------------------------------------------
class Service:
    def __init__(self):
        config.ensure_dirs()
        self.debug_sessions: Dict[str, "DebugSession"] = {}
        self.run_sessions: Dict[str, "RunSession"] = {}
        self._session_counter = 0
        self._run_session_counter = 0
        self._run_sessions_lock = threading.Lock()

    # ==================================================================
    # 项目管理
    # ==================================================================
    def list_projects(self):
        out = []
        if not os.path.isdir(config.PROJECTS_DIR):
            return out
        for name in os.listdir(config.PROJECTS_DIR):
            meta_path = os.path.join(config.PROJECTS_DIR, name, "meta.json")
            meta = storage.read_json(meta_path)
            if meta:
                out.append(meta)
        out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return out

    def create_project(self, name, source, language="minilang"):
        name = (name or "").strip() or "未命名项目"
        meta = _empty_project(name, source, language)
        d = storage.project_dir(meta["id"])
        os.makedirs(d, exist_ok=True)
        storage.write_json(os.path.join(d, "meta.json"), meta)
        # 初始版本
        self.save_version(meta["id"], source, "初始版本")
        return self.get_project(meta["id"])

    def get_project(self, pid):
        meta = storage.read_json(os.path.join(storage.project_dir(pid), "meta.json"))
        return meta

    def update_project(self, pid, fields):
        meta_path = os.path.join(storage.project_dir(pid), "meta.json")
        def _mut(data):
            if data is None:
                return None, False
            for k, v in fields.items():
                if k in ("name", "description", "last_source", "language"):
                    data[k] = v
            data["updated_at"] = storage.now_iso()
            return data, True
        new_meta, _ = storage.update_json(meta_path, _mut)
        return new_meta

    def delete_project(self, pid):
        d = storage.project_dir(pid)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        # 清理其调试会话
        for sid in [s for s, sess in self.debug_sessions.items() if sess.project_id == pid]:
            self.debug_sessions.pop(sid, None)
        return True

    # ==================================================================
    # 历史版本
    # ==================================================================
    def list_versions(self, pid):
        vdir = storage.versions_dir(pid)
        out = []
        if not os.path.isdir(vdir):
            return out
        for name in os.listdir(vdir):
            mp = os.path.join(vdir, name, "manifest.json")
            m = storage.read_json(mp)
            if m:
                out.append(m)
        out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return out

    def save_version(self, pid, source, message=""):
        compiled = compiler_mod.compile_source(source)
        meta_path = os.path.join(storage.project_dir(pid), "meta.json")
        manifest = _empty_version(pid, source, message, compiled)
        vdir = storage.version_dir(pid, manifest["id"])
        os.makedirs(vdir, exist_ok=True)
        storage.write_json(os.path.join(vdir, "manifest.json"), manifest)
        storage.ensure_text(os.path.join(vdir, "source.txt"), source)
        storage.write_json(os.path.join(vdir, "compile.json"), {
            "diagnostics": compiled.diagnostics.to_list(),
            "success": compiled.success,
            "stage": compiled.stage,
        })
        storage.write_json(os.path.join(vdir, "run.json"), [])
        # 更新项目 meta
        def _mut(data):
            if data is None:
                return None, False
            data["version_count"] = int(data.get("version_count", 0)) + 1
            data["updated_at"] = storage.now_iso()
            data["last_source"] = source
            return data, True
        storage.update_json(meta_path, _mut)
        return manifest

    def get_version(self, pid, vid):
        vdir = storage.version_dir(pid, vid)
        manifest = storage.read_json(os.path.join(vdir, "manifest.json"))
        if not manifest:
            return None
        source = ""
        src_path = os.path.join(vdir, "source.txt")
        if os.path.exists(src_path):
            with open(src_path, "r", encoding="utf-8") as f:
                source = f.read()
        runs = storage.read_json(os.path.join(vdir, "run.json"), [])
        comp = storage.read_json(os.path.join(vdir, "compile.json"), {})
        return {"manifest": manifest, "source": source, "runs": runs, "compile": comp}

    def restore_version(self, pid, vid):
        ver = self.get_version(pid, vid)
        if not ver:
            return None
        src = ver["source"]
        msg = f"恢复到版本 {vid[:6]}"
        self.update_project(pid, {"last_source": src})
        return self.save_version(pid, src, msg)

    def diff_versions(self, pid, va, vb):
        a = self.get_version(pid, va)
        b = self.get_version(pid, vb)
        if not a or not b:
            return None
        return _diff_lines(a["source"], b["source"])

    # ==================================================================
    # 编译
    # ==================================================================
    def compile(self, source, pid=None, vid=None):
        result = compiler_mod.compile_source(source)
        if pid and vid:
            vdir = storage.version_dir(pid, vid)
            storage.write_json(os.path.join(vdir, "compile.json"), {
                "diagnostics": result.diagnostics.to_list(),
                "success": result.success,
                "stage": result.stage,
            })
        return result

    def compile_view(self, source, detail="all"):
        """供前端页面渲染的编译结果（token/ast/symbols/bytecode 按需返回）。"""
        result = compiler_mod.compile_source(source)
        view = result.to_dict()
        if "tokens" in detail or detail == "all":
            view["tokens"] = [{"type": t.type, "text": t.text, "line": t.line,
                               "column": t.column, "pos": t.pos} for t in result.tokens]
        if detail == "all" and result.ast is not None:
            view["ast"] = result.ast.to_dict()
        if detail == "all" and result.symbol_table is not None:
            view["symbols"] = result.symbol_table.to_dict()
        if detail == "all" and result.bytecode is not None:
            view["bytecode"] = result.bytecode.to_dict()
            view["bytecode"]["functions"].reverse()
        return view

    # ==================================================================
    # 运行（普通 / 性能剖析）
    # ==================================================================
    def run(self, source, options=None):
        options = options or {}
        result = compiler_mod.compile_source(source)
        if not result.success:
            return {
                "ok": False,
                "diagnostics": result.diagnostics.to_list(),
                "output": [],
                "stage": result.stage,
            }
        vm = vm_mod.VM(result.bytecode, result.source_lines)
        prof = None
        want_profile = options.get("profile", False)
        if want_profile:
            prof = profiler_mod.Profiler()
            vm.profiler = prof
            if options.get("sample", True):
                prof.start_sampling(float(options.get("sample_interval_ms", 1.0)))
        inputs = _normalize_inputs(options.get("inputs"))
        if inputs:
            vm.input_queue = list(inputs)
        vm.start()
        vm.run()
        if prof:
            prof.attach_vm(vm)
            prof.stop_sampling()
            report = prof.report()
        else:
            report = None
        heap = vm.heap.snapshot([]) if options.get("memory", False) else None
        out = {
            "ok": True,
            "output": list(vm.output),
            "return_value": _json_value(vm.return_value),
            "error": vm.error.to_dict() if vm.error else None,
            "input_requests": vm.input_requests,
            "inputs_consumed": vm.inputs_consumed,
            "input_underflow": vm.input_underflow,
            "instruction_count": vm.instruction_count,
            "elapsed_ms": round(vm.elapsed_ms(), 3),
            "profile": report,
            "memory": heap,
        }
        return out

    def record_run(self, pid, vid, source, options=None):
        """运行并持久化运行记录到版本目录。"""
        options = options or {}
        started = time.time()
        out = self.run(source, options)
        rec = {
            "id": storage.new_id("run"),
            "timestamp": storage.now_iso(),
            "elapsed_ms": out.get("elapsed_ms", 0.0),
            "ok": out.get("ok", False),
            "output": out.get("output", []),
            "return_value": out.get("return_value"),
            "instruction_count": out.get("instruction_count", 0),
            "input_requests": out.get("input_requests", 0),
            "inputs_consumed": out.get("inputs_consumed", 0),
            "input_underflow": out.get("input_underflow", 0),
            "inputs": _normalize_inputs(options.get("inputs")),
            "error": out.get("error"),
            "profiled": bool(out.get("profile")),
        }
        if pid and vid:
            self._persist_run_record(pid, vid, rec)
        return self._write_global_run_record(pid, vid, rec)
        return rec

    def _persist_run_record(self, pid, vid, rec):
        vdir = storage.version_dir(pid, vid)
        run_path = os.path.join(vdir, "run.json")

        def _mut(data):
            data = data if isinstance(data, list) else []
            data.append(rec)
            if len(data) > 200:
                data = data[-200:]
            return data, True

        storage.update_json(run_path, _mut)

    def _write_global_run_record(self, pid, vid, rec):
        global_rec = dict(rec)
        global_rec["project_id"] = pid
        global_rec["version_id"] = vid
        storage.write_json(os.path.join(config.RUNS_DIR, rec["id"] + ".json"), global_rec)
        return rec

    # ==================================================================
    # 交互式运行会话
    # ==================================================================
    def _new_run_session_id(self):
        self._run_session_counter += 1
        return f"run-{self._run_session_counter:x}-{int(time.time() * 1000) % 100000:x}"

    def _prune_run_sessions_locked(self):
        """仅保留最近的完成会话；等待输入的活跃会话不会被清理。"""
        finished = [s for s in self.run_sessions.values() if s.finished]
        for sess in sorted(finished, key=lambda s: s.started_at, reverse=True)[20:]:
            self.run_sessions.pop(sess.id, None)

    def run_start(self, source, options=None, pid=None, vid=None):
        """启动一个可在运行中追加 stdin 的后台运行会话。"""
        options = options or {}
        initial_inputs = _normalize_inputs(options.get("inputs"))
        if sum(len(item) for item in initial_inputs) > 1_000_000:
            return {"ok": False, "error": "预填输入总长度超过 1MB 上限"}
        options["inputs"] = initial_inputs
        sid = self._new_run_session_id()
        sess = RunSession(sid, source, options or {}, pid, vid, self)
        with self._run_sessions_lock:
            self._prune_run_sessions_locked()
            self.run_sessions[sid] = sess
        sess.start()
        return sess.state()

    def run_session_state(self, sid):
        sess = self.run_sessions.get(sid)
        if not sess:
            return {"ok": False, "error": "运行会话不存在或已结束", "session_id": sid}
        return sess.state()

    def run_session_input(self, sid, value):
        sess = self.run_sessions.get(sid)
        if not sess:
            return {"ok": False, "error": "运行会话不存在或已结束", "session_id": sid}
        return sess.send_inputs(value)

    def run_session_eof(self, sid):
        sess = self.run_sessions.get(sid)
        if not sess:
            return {"ok": False, "error": "运行会话不存在或已结束", "session_id": sid}
        return sess.send_eof()

    def run_session_stop(self, sid):
        sess = self.run_sessions.get(sid)
        if not sess:
            return {"ok": True, "removed": False}
        # 向 input() 发送 EOF；不删除会话，前端仍可读取最终输出与诊断。
        sess.stop()
        return {"ok": True, "removed": False}

    # ==================================================================
    # 调试会话
    # ==================================================================
    def _new_session_id(self):
        self._session_counter += 1
        return f"dbg-{self._session_counter:x}-{int(time.time()*1000)%100000:x}"

    def debug_start(self, source, breakpoints=None, pid=None, vid=None):
        """创建并启动一个调试会话（编译 -> 建 VM -> 建调试器 -> 启动）。"""
        sid = self._new_session_id()
        sess = DebugSession(sid, source, [b + 1 for b in (breakpoints or [])], pid, vid)
        self.debug_sessions[sid] = sess
        sess.start()
        return self.debug_state(sid)

    def debug_state(self, sid):
        sess = self.debug_sessions.get(sid)
        if not sess:
            return {"ok": False, "error": "会话不存在或已过期", "session_id": sid}
        return sess.state()

    def debug_command(self, sid, command, breakpoints=None):
        sess = self.debug_sessions.get(sid)
        if not sess:
            return {"ok": False, "error": "会话不存在或已过期"}
        if breakpoints is not None:
            sess.set_breakpoints(breakpoints)
        getattr(sess, command)()
        return self.debug_state(sid)

    def debug_stop(self, sid):
        sess = self.debug_sessions.pop(sid, None)
        return {"ok": True, "removed": bool(sess)}

    def debug_sessions_list(self):
        return [{"session_id": s.id, "project_id": s.project_id,
                 "started": s.started, "finished": s.vm.finished if s.vm else False}
                for s in self.debug_sessions.values()]

    def memory_snapshot(self, sid):
        sess = self.debug_sessions.get(sid)
        if not sess or not sess.vm:
            return None
        return sess.vm.heap.snapshot(sess.vm.frame_snapshot())

def _json_value(value: Any):
    """把 VM 返回值转成 JSON 可序列化结构（原始值保持 JSON 原生类型）。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return rt.serialize_value(value)


class RunSession:
    """后台交互式运行会话：input() 阻塞等待，后续 HTTP 请求可继续投递输入。"""

    def __init__(self, sid, source, options, pid=None, vid=None, service=None):
        self.id = sid
        self.source = source
        self.options = options or {}
        self.project_id = pid
        self.version_id = vid
        self.service = service
        self.result = compiler_mod.compile_source(source)
        self.vm: Optional[vm_mod.VM] = None
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.RLock()
        self.finished_event = threading.Event()
        self.input_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self.sent_inputs: List[str] = []
        self.eof_sent = False
        self.waiting_input = False
        self.finished = False
        self.status = "starting"
        self.record = None
        self.started_at = time.time()

        initial = _normalize_inputs(self.options.get("inputs"))
        self.sent_inputs.extend(initial)
        for item in initial:
            self.input_queue.put(item)

    def start(self):
        if not self.result.success:
            self.status = "compile_failed"
            self.finished = True
            self.finished_event.set()
            return
        self.vm = vm_mod.VM(self.result.bytecode, self.result.source_lines)
        self.vm.input_reader = self._read_input
        self.status = "running"
        self.thread = threading.Thread(target=self._worker, name=f"minilang-run-{self.id}", daemon=True)
        self.thread.start()

    def _read_input(self) -> str:
        with self.lock:
            if self.finished:
                return ""
            self.waiting_input = True
            self.status = "waiting_input"
        item = self.input_queue.get()
        with self.lock:
            self.waiting_input = False
            if not self.finished:
                self.status = "running"
            if item is None:
                self.eof_sent = True
                self.vm.input_eof = True
                item = ""
            return item

    def _worker(self):
        try:
            self.vm.start()
            self.vm.run()
        except Exception:
            # VM 已把语言运行期错误写入 self.error；这里仅兜底宿主异常。
            if self.vm.error is None:
                self.status = "error"
        finally:
            self._finish()

    def _finish(self):
        with self.lock:
            self.finished = True
            self.waiting_input = False
            if self.status != "compile_failed":
                self.status = "error" if self.vm and self.vm.error else "finished"
            rec = self._build_record_locked()
            self.record = rec
        self.finished_event.set()
        if self.service and self.project_id and self.version_id and self.status != "compile_failed":
            try:
                self.service._persist_run_record(self.project_id, self.version_id, rec)
                self.service._write_global_run_record(self.project_id, self.version_id, rec)
            except Exception:
                # 持久化失败不影响本次会话返回；普通请求的外层异常处理会记录 500。
                pass

    def _build_record_locked(self):
        if not self.vm:
            return None
        return {
            "id": storage.new_id("run"),
            "timestamp": storage.now_iso(),
            "elapsed_ms": round(self.vm.elapsed_ms(), 3),
            "ok": self.vm.error is None,
            "output": list(self.vm.output),
            "return_value": _json_value(self.vm.return_value),
            "instruction_count": self.vm.instruction_count,
            "input_requests": self.vm.input_requests,
            "inputs_consumed": self.vm.inputs_consumed,
            "input_underflow": self.vm.input_underflow,
            "inputs": list(self.sent_inputs),
            "error": self.vm.error.to_dict() if self.vm.error else None,
            "profiled": False,
            "interactive": True,
        }

    def send_inputs(self, value):
        items = _normalize_inputs(value)
        if sum(len(item) for item in items) > 1_000_000:
            return {"ok": False, "error": "单次投递输入总长度超过 1MB 上限"}
        with self.lock:
            if self.status == "compile_failed":
                return {"ok": False, "error": "编译失败，无法投递输入"}
            if self.finished:
                return {"ok": False, "error": "程序已结束，无法投递输入"}
            if self.eof_sent:
                return {"ok": False, "error": "已发送 EOF，不能继续投递输入"}
            if len(self.sent_inputs) >= 10000:
                return {"ok": False, "error": "单次运行的输入条目超过 10000 条上限"}
            for item in items:
                self.sent_inputs.append(item)
                self.input_queue.put(item)
            return self.state(accepted=len(items))

    def send_eof(self):
        with self.lock:
            if self.status == "compile_failed":
                return {"ok": False, "error": "编译失败，无需发送 EOF"}
            if self.finished:
                return {"ok": False, "error": "程序已结束"}
            if not self.eof_sent:
                self.eof_sent = True
                self.input_queue.put(None)
            return self.state(eof=True)

    def stop(self):
        # daemon 线程无法安全强制终止；EOF 可解除 input() 等待，其余由指令上限保护。
        return self.send_eof()

    def state(self, accepted=None, eof=None):
        with self.lock:
            if self.status == "compile_failed":
                return {
                    "ok": True,
                    "session_id": self.id,
                    "project_id": self.project_id,
                    "version_id": self.version_id,
                    "status": "compile_failed",
                    "finished": True,
                    "diagnostics": self.result.diagnostics.to_list(),
                    "output": [],
                }
            data = {
                "ok": True,
                "session_id": self.id,
                "project_id": self.project_id,
                "version_id": self.version_id,
                "status": self.status,
                "running": not self.finished,
                "finished": self.finished,
                "waiting_input": self.waiting_input,
                "output": list(self.vm.output) if self.vm else [],
                "error": self.vm.error.to_dict() if self.vm and self.vm.error else None,
                "elapsed_ms": round(self.vm.elapsed_ms(), 3) if self.vm else 0.0,
                "instruction_count": self.vm.instruction_count if self.vm else 0,
                "input_requests": self.vm.input_requests if self.vm else 0,
                "inputs_consumed": self.vm.inputs_consumed if self.vm else 0,
                "input_underflow": self.vm.input_underflow if self.vm else 0,
                "pending_inputs": self.input_queue.qsize(),
                "inputs_sent": len(self.sent_inputs),
                "eof_sent": self.eof_sent,
                "return_value": _json_value(self.vm.return_value) if self.vm else None,
                "record": self.record,
            }
            if accepted is not None:
                data["accepted"] = accepted
            if eof:
                data["eof"] = True
            return data


class DebugSession:
    """一个交互式调试会话：持有 VM 与 Debugger，状态跨 HTTP 请求保留。"""

    def __init__(self, sid, source, breakpoints, pid=None, vid=None):
        self.id = sid
        self.source = source
        self.breakpoints = set(breakpoints)
        self.project_id = pid
        self.version_id = vid
        self.result = compiler_mod.compile_source(source)
        self.vm: Optional[vm_mod.VM] = None
        self.debugger: Optional[debugger_mod.Debugger] = None
        self.started = False

    def set_breakpoints(self, lines):
        self.breakpoints = set(lines)
        if self.debugger:
            self.debugger.set_breakpoints(list(self.breakpoints))

    def start(self):
        if not self.result.success:
            self.started = False
            return
        self.vm = vm_mod.VM(self.result.bytecode, self.result.source_lines)
        self.debugger = debugger_mod.Debugger(self.vm, self.breakpoints)
        self.debugger.start()
        self.started = True

    def state(self):
        if not self.result.success:
            return {
                "ok": False,
                "compile_failed": True,
                "diagnostics": self.result.diagnostics.to_list(),
                "source_lines": self.result.source_lines,
            }
        if not self.started or self.vm is None:
            return {"ok": True, "not_started": True, "diagnostics": self.result.diagnostics.to_list()}
        snap = self.debugger.snapshot()
        snap["ok"] = True
        snap["session_id"] = self.id
        snap["source_lines"] = self.result.source_lines
        snap["source"] = self.source
        snap["diagnostics"] = self.result.diagnostics.to_list()
        snap["breakpoint_instructions"] = self._breakpoint_hits()
        snap["memory"] = self.vm.heap.snapshot(self.vm.frame_snapshot())
        return snap

    def _breakpoint_hits(self):
        """返回每个函数里命中断点的指令偏移（供前端高亮字节码）。"""
        out = []
        for name, fc in self.result.bytecode.functions.items():
            for ins in fc.instructions:
                if ins.line in self.breakpoints:
                    out.append({"function": name, "offset": ins.offset, "line": ins.line})
        main = self.result.bytecode.main
        if main:
            for ins in main.instructions:
                if ins.line in self.breakpoints:
                    out.append({"function": "<main>", "offset": ins.offset, "line": ins.line})
        return out

    # ---- 命令分发 ----
    def continue_(self):
        if self.debugger:
            self.debugger.continue_()

    def step_instruction(self):
        if self.debugger:
            self.debugger.step_instruction()

    def step_into(self):
        if self.debugger:
            self.debugger.step_into()

    def step_over(self):
        if self.debugger:
            self.debugger.step_over()

    def step_out(self):
        if self.debugger:
            self.debugger.step_out()
