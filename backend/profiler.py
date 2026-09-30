# -*- coding: utf-8 -*-
"""
性能剖析器（Profiler）。

提供两种互补的剖析方式，这也是本项目的难点之一"性能剖析的精确采样"：

  1. **精确插桩（deterministic instrumentation）**：
     解释器每执行一条指令就上报一次「当前函数 + 源码行」，统计：
        - 每个函数的调用次数、累计耗时（含子调用）、自身耗时（剔除子调用）；
        - 每一行的精确执行次数（热点行）；
        - 总指令数与总耗时。
     这部分是确定性的、可复现的，用于精确定位热点。

  2. **统计采样（statistical sampling）**：
     一个后台线程以固定间隔（默认 1ms，可调）读取解释器当前的「函数 + 行」位置，
     累积形成采样直方图。采样百分比近似程序在该处消耗的时间占比，符合真实
     采样型剖析器（如 py-spy / perf）的工作原理，能捕捉插桩无法反映的耗时分布。

两者结果在"性能分析"页面并列展示，帮助学习者理解两种剖析方法的差异。
"""

import threading
import time
from collections import defaultdict
from typing import Dict, List, Any, Optional


class Profiler:
    def __init__(self):
        self.reset()

    def reset(self):
        # 精确插桩数据
        self.functions: Dict[str, dict] = defaultdict(lambda: {
            "calls": 0, "total": 0.0, "child": 0.0,
            "line_hits": defaultdict(int),
        })
        self._timers: List[tuple] = []       # [(name, start_monotonic)]
        self.total_instructions = 0
        self.total_time = 0.0
        self._run_start = None

        # 采样数据
        self.samples: List[tuple] = []       # [(func_name, line)]
        self._sampling_thread: Optional[threading.Thread] = None
        self._sampling_stop = threading.Event()
        self._vm = None
        self._sample_interval = 0.001
        self.sample_interval_ms = 1

    # ------------------------------------------------------------------
    # 运行期回调（由 VM 调用）
    # ------------------------------------------------------------------
    def attach_vm(self, vm):
        self._vm = vm

    def begin_run(self):
        self._run_start = time.perf_counter()

    def end_run(self):
        if self._run_start is not None:
            self.total_time = time.perf_counter() - self._run_start

    def record_instruction(self, func_name, line):
        """每执行一条指令调用一次：累计指令数 + 行命中数。"""
        self.total_instructions += 1
        self.functions[func_name]["line_hits"][line] += 1

    def function_enter(self, name):
        now = time.perf_counter()
        self._timers.append((name, now))
        self.functions[name]["calls"] += 1

    def function_exit(self):
        if not self._timers:
            return
        now = time.perf_counter()
        name, start = self._timers.pop()
        elapsed = now - start
        self.functions[name]["total"] += elapsed
        if self._timers:
            parent = self._timers[-1][0]
            self.functions[parent]["child"] += elapsed

    # ------------------------------------------------------------------
    # 采样线程
    # ------------------------------------------------------------------
    def start_sampling(self, interval_ms: float = 1.0):
        """启动后台采样线程（幂等）。"""
        if self._sampling_thread is not None and self._sampling_thread.is_alive():
            return
        self.sample_interval_ms = interval_ms
        self._sample_interval = max(interval_ms, 0.05)
        self._sampling_stop.clear()
        self._sampling_thread = threading.Thread(
            target=self._sample_loop, name="gsb-sampler", daemon=True)
        self._sampling_thread.start()

    def stop_sampling(self):
        self._sampling_stop.set()
        if self._sampling_thread is not None:
            self._sampling_thread.join(timeout=0.2)
            self._sampling_thread = None

    def _sample_loop(self):
        while not self._sampling_stop.wait(self._sample_interval):
            if self._vm is None:
                continue
            name, line = self._vm.current_position()
            self.samples.append((name, line))

    # ------------------------------------------------------------------
    # 汇总报告
    # ------------------------------------------------------------------
    def report(self) -> Dict[str, Any]:
        self.end_run()
        total_ms = self.total_time * 1000.0

        functions = []
        for name, data in self.functions.items():
            self_ms = data["total"]
            line_hits = sorted(data["line_hits"].items(), key=lambda kv: -kv[1])
            functions.append({
                "name": name,
                "calls": data["calls"],
                "total_ms": round(data["total"] * 1000.0, 3),
                "self_ms": round(self_ms * 1000.0, 3),
                "child_ms": round(data["child"] * 1000.0, 3),
                "pct": round(data["total"] / self.total_time * 100.0, 2) if self.total_time else 0.0,
                "hot_lines": [{"line": ln, "hits": h} for ln, h in line_hits[:10]],
            })
        functions.sort(key=lambda f: -f["self_ms"])

        # 采样聚合
        sample_total = len(self.samples) or 1
        sample_by_func = defaultdict(int)
        sample_by_line = defaultdict(int)
        for name, line in self.samples:
            sample_by_func[name] += 1
            sample_by_line[(name, line)] += 1

        sample_functions = [
            {"name": n, "samples": c, "pct": round(c / sample_total * 100.0, 2)}
            for n, c in sorted(sample_by_func.items(), key=lambda kv: -kv[1])
        ]
        sample_lines = [
            {"func": n, "line": l, "samples": c, "pct": round(c / sample_total * 100.0, 2)}
            for (n, l), c in sorted(sample_by_line.items(), key=lambda kv: -kv[1])
        ][:30]

        return {
            "mode": "instrumented + sampling",
            "total_time_ms": round(total_ms, 3),
            "total_instructions": self.total_instructions,
            "function_count": len(functions),
            "functions": functions,
            "hot_functions": functions[:10],
            "sampling": {
                "sample_count": len(self.samples),
                "interval_ms": self.sample_interval_ms,
                "functions": sample_functions[:10],
                "lines": sample_lines,
            },
        }
