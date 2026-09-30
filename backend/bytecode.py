# -*- coding: utf-8 -*-
"""
字节码 / 中间代码定义。

MiniLang 采用**基于栈的字节码虚拟机**。编译器前端（词法 -> 语法 -> 语义）产出 AST 后，
代码生成器把 AST 翻译成这里的线性指令序列。每条指令记录其操作码、可选操作数以及
对应的源码行号——行号既服务于"字节码/中间代码展示"，也是单步调试与断点定位的桥梁。

指令集刻意保持小而完备：常量加载、变量读写、二元/一元运算、跳转、函数调用、返回、
列表构建与下标访问、以及为调试与剖析服务的元指令。
"""

from dataclasses import dataclass
from typing import Any, List, Optional

# ---- 操作码 ----
OP_LOAD_CONST = "LOAD_CONST"       # 压入常量池项          [index]
OP_LOAD_VAR = "LOAD_VAR"           # 压入变量（局部优先）   [name]
OP_STORE_VAR = "STORE_VAR"         # 存储变量（局部优先）   [name]
OP_LOAD_GLOBAL = "LOAD_GLOBAL"     # 压入全局变量           [name]
OP_STORE_GLOBAL = "STORE_GLOBAL"   # 存储全局变量           [name]
OP_LOAD_BUILTIN = "LOAD_BUILTIN"   # 压入内置函数           [name]
OP_LOAD_FUNC = "LOAD_FUNC"         # 压入用户函数对象       [name]
OP_MAKE_FUNC = "MAKE_FUNC"         # 定义函数（绑定到全局）  [name, argc]
OP_BINARY = "BINARY"               # 二元运算                [operator]
OP_UNARY = "UNARY"                 # 一元运算                [operator]
OP_JUMP = "JUMP"                   # 无条件跳转              [offset]
OP_JUMP_IF_FALSE = "JUMP_IF_FALSE" # 栈顶为假则跳转         [offset]
OP_JUMP_IF_TRUE = "JUMP_IF_TRUE"   # 栈顶为真则跳转         [offset]
OP_POP = "POP"                     # 弹出栈顶
OP_DUP = "DUP"                     # 复制栈顶
OP_DUP2 = "DUP2"                   # 复制栈顶两个元素（用于复合下标赋值）
OP_CALL = "CALL"                   # 函数调用                [argc]
OP_RETURN = "RETURN"               # 返回（栈顶为返回值）
OP_RETURN_NONE = "RETURN_NONE"     # 无返回值返回
OP_MAKE_LIST = "MAKE_LIST"         # 构建列表                [count]
OP_INDEX_LOAD = "INDEX_LOAD"       # 下标读取
OP_INDEX_STORE = "INDEX_STORE"     # 下标写入
OP_NOP = "NOP"                     # 空操作（占位）
OP_LINE = "LINE"                   # 行号标记（调试辅助）    [line]

# 带一个"名字/字符串"操作数的指令（用于字节码展示时高亮）
_NAME_OPS = {OP_LOAD_VAR, OP_STORE_VAR, OP_LOAD_GLOBAL, OP_STORE_GLOBAL,
             OP_LOAD_BUILTIN, OP_LOAD_FUNC, OP_MAKE_FUNC}


@dataclass
class Instruction:
    """一条字节码指令。"""
    op: str
    operand: Any = None      # 操作数：常量索引 / 名字 / 运算符 / 跳转偏移 / 参数个数
    line: int = 1            # 对应源码行号（1-based）
    offset: int = 0          # 指令在代码段内的偏移（由 FunctionCode 回填）

    def describe_operand(self) -> str:
        if self.op in (OP_LOAD_CONST, OP_MAKE_LIST, OP_CALL, OP_JUMP,
                       OP_JUMP_IF_FALSE, OP_JUMP_IF_TRUE, OP_LINE):
            return str(self.operand)
        return repr(self.operand) if self.operand is not None else ""

    def to_dict(self):
        return {
            "offset": self.offset,
            "op": self.op,
            "operand": self.operand,
            "line": self.line,
        }

    def __repr__(self):
        return f"{self.offset:>4}  {self.op:<16} {self.describe_operand()}  (L{self.line})"


class FunctionCode:
    """一个函数的字节码段：指令数组 + 常量池 + 元信息。"""

    def __init__(self, name: str, arity: int, params: List[str]):
        self.name = name
        self.arity = arity
        self.params = list(params)
        self.instructions: List[Instruction] = []
        self.constants: List[Any] = []
        self.max_locals = 0            # 局部变量个数（用于分配帧）
        self.line_to_offset: dict = {} # 源码行 -> 首个指令偏移（断点/跳转用）
        self.is_main = False           # 是否为模块顶层（main）代码

    def emit(self, op, operand=None, line=1) -> Instruction:
        ins = Instruction(op, operand, line, len(self.instructions))
        self.instructions.append(ins)
        if line not in self.line_to_offset:
            self.line_to_offset[line] = ins.offset
        return ins

    def add_const(self, value) -> int:
        # 去重：相同常量复用同一索引（常量折叠的雏形）
        for i, c in enumerate(self.constants):
            if type(c) is type(value) and c == value:
                return i
        self.constants.append(value)
        return len(self.constants) - 1

    def to_dict(self):
        return {
            "name": self.name,
            "arity": self.arity,
            "params": self.params,
            "is_main": self.is_main,
            "constants": self.constants,
            "instructions": [i.to_dict() for i in self.instructions],
            "line_map": {str(k): v for k, v in self.line_to_offset.items()},
        }


class ProgramCode:
    """编译产物的完整中间表示：常量池 + 若干函数代码段。"""

    def __init__(self):
        self.functions: dict = {}          # name -> FunctionCode
        self.main: Optional[FunctionCode] = None
        self.global_names: List[str] = []  # 已知全局变量名（用于字节码展示）

    def add_function(self, fc: FunctionCode):
        self.functions[fc.name] = fc
        return fc

    def to_dict(self):
        all_fcs = []
        if self.main is not None:
            all_fcs.append(self.main)
        all_fcs.extend(self.functions.values())
        total = len(all_fcs)
        fns = [f.to_dict() for f in all_fcs]
        return {
            "global_names": self.global_names,
            "functions": fns,
            "total_instructions": total,
        }


# 供前端展示的操作码 -> 语义说明映射
OPCODE_DESCRIPTIONS = {
    OP_LOAD_CONST: "压入常量到栈顶",
    OP_LOAD_VAR: "压入变量值（局部优先）",
    OP_STORE_VAR: "弹出栈顶存入变量",
    OP_LOAD_GLOBAL: "压入全局变量值",
    OP_STORE_GLOBAL: "弹出栈顶存入全局变量",
    OP_LOAD_BUILTIN: "压入内置函数对象",
    OP_LOAD_FUNC: "压入用户函数对象",
    OP_MAKE_FUNC: "定义函数并绑定到全局",
    OP_BINARY: "弹出两个操作数做二元运算，结果压栈",
    OP_UNARY: "弹出一个操作数做一元运算，结果压栈",
    OP_JUMP: "无条件跳转到目标偏移",
    OP_JUMP_IF_FALSE: "栈顶为假则跳转",
    OP_JUMP_IF_TRUE: "栈顶为真则跳转",
    OP_POP: "弹出并丢弃栈顶",
    OP_DUP: "复制栈顶",
    OP_DUP2: "复制栈顶两个元素",
    OP_CALL: "调用函数（带参数个数）",
    OP_RETURN: "返回，栈顶为返回值",
    OP_RETURN_NONE: "无返回值返回",
    OP_MAKE_LIST: "从栈顶弹出 N 个元素构建列表",
    OP_INDEX_LOAD: "按下标读取列表/字符串元素",
    OP_INDEX_STORE: "按下标写入列表元素",
    OP_NOP: "空操作",
    OP_LINE: "源码行号标记",
}
