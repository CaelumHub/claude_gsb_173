# -*- coding: utf-8 -*-
"""
调试器（Debugger）。

在 VM 的可续跑执行循环之上实现交互式调试：断点、单步进入、单步跳过、跳出、
继续执行、变量查看与调用栈快照。

核心难点"调试器与解释器的状态同步"体现在这里——VM 是同步、可挂起/续跑的，
调试器通过 VM.run(pause_fn, on_pause) 在**每条指令执行前**决定是否暂停，
暂停后帧栈 / 指令指针 / 局部变量 / 操作数栈 / 堆全部原样保留，等待下一次请求继续。

步进语义（以"下一个待执行指令"为判断对象）：
  * step_instruction  —— 执行恰好一条指令后暂停（最细粒度）；
  * step_into         —— 执行到下一个源码行（进入被调用函数的第一行）；
  * step_over         —— 执行到当前帧的下一个源码行（跳过函数调用内部）；
  * step_out          —— 执行到当前帧返回（回到上一帧）；
  * continue          —— 执行到下一个断点或程序结束。
"""

from typing import List, Optional, Set

from . import diagnostics as diag


# 暂停原因
PAUSE_BREAKPOINT = "breakpoint"
PAUSE_STEP = "step"
PAUSE_ENTRY = "entry"        # 尚未开始执行（初次启动）
PAUSE_FINISHED = "finished"
PAUSE_ERROR = "error"


class Debugger:
    def __init__(self, vm, breakpoints: Optional[Set[int]] = None):
        self.vm = vm
        vm.debugger = self
        self.breakpoints: Set[int] = set(breakpoints or [])
        self.step_mode: Optional[str] = None   # None | 'instruction' | 'into' | 'over' | 'out'
        self._step_from_line = 0
        self._step_depth = 0
        self._resume_line: Optional[int] = None
        self.pause_reason: str = PAUSE_ENTRY
        self._just_started = True

    # ------------------------------------------------------------------
    # 断点管理
    # ------------------------------------------------------------------
    def set_breakpoints(self, lines: List[int]):
        self.breakpoints = set(l for l in lines if isinstance(l, int) and l > 0)

    def add_breakpoint(self, line: int):
        self.breakpoints.add(line)

    def remove_breakpoint(self, line: int):
        self.breakpoints.discard(line)

    def clear_breakpoints(self):
        self.breakpoints.clear()

    def breakpoint_lines(self) -> List[int]:
        return sorted(self.breakpoints)

    # ------------------------------------------------------------------
    # 暂停判定（在每条指令执行前被 VM 调用）
    # ------------------------------------------------------------------
    def should_pause(self, vm) -> bool:
        ins = vm.peek_instruction()
        if ins is None:
            return False
        if self.step_mode == "instruction":
            self.pause_reason = PAUSE_STEP
            return True
        if self.step_mode == "into":
            if ins.line != self._step_from_line:
                self.pause_reason = PAUSE_STEP
                return True
            return False
        if self.step_mode == "over":
            if len(vm.frames) < self._step_depth and ins.line != self._step_from_line:
                self.pause_reason = PAUSE_STEP
                return True
            return False
        if self.step_mode == "out":
            if len(vm.frames) < self._step_depth:
                self.pause_reason = PAUSE_STEP
                return True
            return False
        # 断点模式（step_mode is None）
        if ins.line in self.breakpoints and ins.line != self._resume_line:
            self.pause_reason = PAUSE_BREAKPOINT
            return True
        if ins.line != self._resume_line:
            self._resume_line = None
        return False

    def on_pause(self, vm):
        """暂停发生后：记录续跑时需跳过的断点行，清除步进模式。"""
        ins = vm.peek_instruction()
        if ins is not None:
            self._resume_line = ins.line + 1
        self.step_mode = None
        self.pause_reason = vm.pause_reason if hasattr(vm, "pause_reason") and vm.pause_reason else self.pause_reason

    # ------------------------------------------------------------------
    # 交互命令
    # ------------------------------------------------------------------
    def start(self):
        """开始执行：若有断点则运行到第一个断点，否则运行到结束。"""
        if self._just_started:
            self._just_started = False
            self.vm.start()
            # 若第一行就有断点，暂停在入口；否则直接运行
            if self._first_line_breakpoint():
                self.pause_reason = PAUSE_BREAKPOINT
                return
        self.continue_()

    def _first_line_breakpoint(self) -> bool:
        ins = self.vm.peek_instruction()
        return ins is not None and ins.line in self.breakpoints

    def continue_(self):
        self.step_mode = None
        self.vm.paused = False
        self.vm.run(self.should_pause, self.on_pause)

    def step_instruction(self):
        """单步一条指令。"""
        self.step_mode = "instruction"
        self.vm.paused = False
        self.vm.run(self.should_pause, self.on_pause, max_steps=1)
        self.step_mode = None

    def step_into(self):
        """单步到下一源码行（进入函数）。"""
        self._prepare_step("into")

    def step_over(self):
        """单步到当前帧的下一源码行（跳过函数）。"""
        self._prepare_step("over")

    def step_out(self):
        """执行到当前帧返回。"""
        self._prepare_step("out")

    def _prepare_step(self, mode):
        ins = self.vm.peek_instruction()
        self._step_from_line = ins.line if ins else 0
        self._step_depth = len(self.vm.frames)
        self.step_mode = mode
        self.vm.paused = False
        self.vm.run(self.should_pause, self.on_pause)

    # ------------------------------------------------------------------
    # 状态快照
    # ------------------------------------------------------------------
    def snapshot(self):
        """生成调试暂停时的完整状态，供"执行跟踪/调用栈/变量监视"页面渲染。"""
        vm = self.vm
        ins = vm.peek_instruction()
        return {
            "finished": vm.finished,
            "paused": vm.paused,
            "reason": self.pause_reason if vm.paused or vm.finished else PAUSE_FINISHED,
            "instruction_count": vm.instruction_count,
            "elapsed_ms": round(vm.elapsed_ms(), 3),
            "error": vm.error.to_dict() if vm.error else None,
            "breakpoints": self.breakpoint_lines(),
            "next_instruction": ins.to_dict() if ins else None,
            "current_position": vm.current_position(),
            "call_stack": vm.frame_snapshot(),
            "operand_stack": vm.stack_snapshot(),
            "globals": {k: _serialize(vm, k) for k in sorted(vm.globals)},
            "output": list(vm.output),
            "return_value": _plain(vm.return_value),
        }

    def to_error_pause(self):
        self.pause_reason = PAUSE_ERROR
        self.step_mode = None


def _serialize(vm, name):
    from . import runtime as rt
    v = vm.globals[name]
    return rt.serialize_value(v)


def _plain(v):
    from . import runtime as rt
    if v is None:
        return None
    return rt.serialize_value(v)
