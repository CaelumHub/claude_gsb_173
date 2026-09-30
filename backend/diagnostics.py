# -*- coding: utf-8 -*-
"""
错误诊断与修复建议。

本模块定义了统一的 ``Diagnostic`` 结构，贯穿词法、语法、语义、运行期四个阶段。
每个诊断除了定位信息（行、列、长度）外，还携带：
  * phase   —— 出错的阶段（lex/parse/semantic/runtime）
  * kind    —— 错误类别（syntax/type/name/runtime/limit 等）
  * message —— 面向用户的可读描述
  * fix     —— 修复建议（人类可读的"应该怎么做"）
  * fix_hint—— 可机器执行的"快速修复"提示（替换文本 / 删除范围）

"错误诊断与修复建议"页面即围绕这份结构渲染，帮助学习者看懂错误并一键定位。
"""

import difflib
from dataclasses import dataclass, field, asdict
from typing import List, Optional


# 严重级别
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# 阶段
PHASE_LEX = "lex"
PHASE_PARSE = "parse"
PHASE_SEMANTIC = "semantic"
PHASE_RUNTIME = "runtime"

# 错误类别（用于前端着色 / 分组 / 建议模板）
KIND_SYNTAX = "syntax"
KIND_TYPE = "type"
KIND_NAME = "name"
KIND_RUNTIME = "runtime"
KIND_LIMIT = "limit"
KIND_ARITY = "arity"


@dataclass
class Diagnostic:
    """一条结构化诊断信息。"""
    severity: str = SEVERITY_ERROR
    phase: str = PHASE_RUNTIME
    kind: str = KIND_RUNTIME
    message: str = ""
    line: int = 1            # 1-based
    column: int = 1          # 1-based
    length: int = 1          # 出错 token 的长度（用于高亮）
    end_line: int = 1
    end_column: int = 1
    fix: str = ""            # 人类可读修复建议
    fix_hint: Optional[dict] = None   # 机器可执行的快速修复：{action, text, start, end}
    source_line: str = ""    # 出错的那一行原文
    related: List[dict] = field(default_factory=list)  # 关联信息（如 "相近符号"）

    def to_dict(self):
        d = asdict(self)
        d.pop("source_line", None)
        return d

    def __str__(self):
        return f"[{self.phase}/{self.severity}] L{self.line}:C{self.column} {self.message}"


def _levenshtein(a: str, b: str) -> int:
    """计算两个字符串的编辑距离（用于"你是不是想写 xxx"）。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def suggest_name(typo: str, candidates: List[str], max_distance: int = 2, limit: int = 3) -> List[str]:
    """在候选符号中找出与拼写最接近的几个（按编辑距离排序）。"""
    scored = []
    for c in candidates:
        d = _levenshtein(typo, c)
        if d <= max_distance:
            scored.append((d, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _, c in scored[:limit]]


class DiagnosticBag:
    """诊断收集器：按严重级别 / 阶段汇总，供前端整页渲染。"""

    def __init__(self):
        self.items: List[Diagnostic] = []

    def add(self, d: Diagnostic):
        self.items.append(d)
        return d

    def error(self, message, phase=PHASE_RUNTIME, kind=KIND_RUNTIME, line=1, column=1,
              length=1, fix="", fix_hint=None, source_line="", **kw):
        return self.add(Diagnostic(SEVERITY_ERROR, phase, kind, message, line, column,
                                   length, line, column + length, fix, fix_hint, source_line, **kw))

    def warning(self, message, phase=PHASE_RUNTIME, kind=KIND_RUNTIME, line=1, column=1,
                length=1, fix="", source_line="", **kw):
        return self.add(Diagnostic(SEVERITY_WARNING, phase, kind, message, line, column,
                                   length, line, column + length, fix, None, source_line, **kw))

    def info(self, message, phase=PHASE_RUNTIME, kind=KIND_RUNTIME, line=1, column=1, **kw):
        return self.add(Diagnostic(SEVERITY_INFO, phase, kind, message, line, column, 1,
                                   line, column + 1, "", None, "", **kw))

    @property
    def has_errors(self):
        return any(d.severity == SEVERITY_ERROR for d in self.items)

    @property
    def has_warnings(self):
        return any(d.severity == SEVERITY_WARNING for d in self.items)

    def errors(self):
        return [d for d in self.items if d.severity == SEVERITY_ERROR]

    def warnings(self):
        return [d for d in self.items if d.severity == SEVERITY_WARNING]

    def by_phase(self, phase):
        return [d for d in self.items if d.phase == phase]

    def to_list(self):
        return [d.to_dict() for d in self.items]

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


# ---------------------------------------------------------------------------
# 常见错误模板 —— 统一措辞，保证诊断页文案稳定、可测试
# ---------------------------------------------------------------------------

def lex_unterminated_string(line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_LEX, KIND_SYNTAX,
        "字符串常量未闭合：缺少结尾的双引号", line, col, 1, line, col + 1,
        "在字符串末尾补上双引号 \"，或检查字符串中是否误写了换行。",
        None, source_line)


def lex_unexpected_char(line, col, ch, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_LEX, KIND_SYNTAX,
        f"无法识别的字符 {ch!r}", line, col, 1, line, col + 1,
        f"删除该字符；如果你需要用到符号 {ch!r}，请确认 MiniLang 是否支持它。",
        None, source_line)


def lex_invalid_number(line, col, text, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_LEX, KIND_SYNTAX,
        f"非法的数字字面量 {text!r}", line, col, len(text), line, col + len(text),
        "数字只能包含数字、一个小数点与可选的正负号，例如 3.14 或 42。",
        None, source_line)


def parse_unexpected(token, expected, source_line):
    exp = expected if isinstance(expected, str) else "/".join(expected)
    return Diagnostic(
        SEVERITY_ERROR, PHASE_PARSE, KIND_SYNTAX,
        f"意外的 token {token.text!r}，期望 {exp}", token.line, token.column,
        len(token.text), token.line, token.column + len(token.text),
        f"将 {token.text!r} 替换为 {exp}，或检查其前后是否缺少分隔符。",
        {"action": "replace", "text": exp.split('/')[0],
         "start": token.pos, "end": token.pos + len(token.text)},
        source_line)


def parse_missing_semicolon(line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_PARSE, KIND_SYNTAX,
        "语句末尾缺少分号 ;", line, col, 1, line, col + 1,
        "在语句末尾补上分号 ;。MiniLang 以分号作为语句结束符。",
        {"action": "insert", "text": ";", "start": -1, "end": -1},
        source_line)


def semantic_undefined_name(name, candidates, line, col, source_line):
    fix = f"变量/函数 {name!r} 未定义"
    related = []
    if candidates:
        sug = suggest_name(name, candidates)
        if sug:
            fix = f"你是不是想写 {sug[0]!r}？"
            related = [{"name": s} for s in sug]
    return Diagnostic(
        SEVERITY_ERROR, PHASE_SEMANTIC, KIND_NAME,
        f"未定义的标识符 {name!r}", line, col, len(name), line, col + len(name),
        fix, None, source_line, related=related)


def semantic_redeclared(name, prev_line, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_SEMANTIC, KIND_NAME,
        f"标识符 {name!r} 已在第 {prev_line} 行声明，不能重复声明",
        line, col, len(name), line, col + len(name),
        f"为这个新变量换一个名字，或删除第 {prev_line} 行的旧声明。",
        None, source_line)


def semantic_wrong_arity(name, expected, got, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_SEMANTIC, KIND_ARITY,
        f"函数 {name!r} 需要 {got} 个参数，但传入了 {expected} 个",
        line, col, 1, line, col + 1,
        f"调整调用处的实参个数为 {expected} 个，或修改函数定义。",
        None, source_line)


def semantic_type_mismatch(op, left, right, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_SEMANTIC, KIND_TYPE,
        f"运算符 {op!r} 不能作用于 {left!r} 与 {right!r} 类型",
        line, col, 1, line, col + 1,
        f"对操作数做类型转换，或改用兼容类型的表达式。",
        None, source_line)


def semantic_assign_to_const(name, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_SEMANTIC, KIND_TYPE,
        f"不能给常量 {name!r} 赋值", line, col, len(name), line, col + len(name),
        f"把 {name!r} 声明为可变变量（用 var），或不要修改它。",
        None, source_line)


def runtime_division_by_zero(line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_RUNTIME, KIND_RUNTIME,
        "除以零：除数不能为 0", line, col, 1, line, col + 1,
        "在除法前判断除数是否为 0，或用 if 分支保护。",
        None, source_line)


def runtime_index_out_of_range(idx, length, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_RUNTIME, KIND_RUNTIME,
        f"下标 {idx} 越界：列表长度为 {length}，有效下标是 0..{length - 1}",
        line, col, 1, line, col + 1,
        f"把下标限制在 0..{length - 1} 范围内，或用 len() 先判断。",
        None, source_line)


def runtime_wrong_type(expected, actual, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_RUNTIME, KIND_TYPE,
        f"类型错误：期望 {expected}，实际是 {actual}",
        line, col, 1, line, col + 1,
        f"先做类型检查或转换，再执行该操作。",
        None, source_line)


def runtime_stack_overflow(depth, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_RUNTIME, KIND_LIMIT,
        f"栈溢出：递归深度超过 {depth} 层，疑似无限递归",
        line, col, 1, line, col + 1,
        "检查递归函数是否缺少正确的终止条件（base case）。",
        None, source_line)


def runtime_instruction_limit(limit, line, col, source_line):
    return Diagnostic(
        SEVERITY_ERROR, PHASE_RUNTIME, KIND_LIMIT,
        f"执行超过 {limit} 条指令，疑似死循环，已强制终止",
        line, col, 1, line, col + 1,
        "检查循环条件是否最终能变为 false，避免 while(true) 无退出。",
        None, source_line)


def runtime_name_error(name, candidates, line, col, source_line):
    fix = f"变量 {name!r} 未定义"
    related = []
    if candidates:
        sug = suggest_name(name, candidates)
        if sug:
            fix = f"你是不是想写 {sug[0]!r}？"
            related = [{"name": s} for s in sug]
    return Diagnostic(
        SEVERITY_ERROR, PHASE_RUNTIME, KIND_NAME,
        f"运行时错误：未定义的变量 {name!r}", line, col, len(name), line, col + len(name),
        fix, None, source_line, related=related)


def warning_unused(name, line, col, source_line):
    return Diagnostic(
        SEVERITY_WARNING, PHASE_SEMANTIC, KIND_NAME,
        f"变量 {name!r} 已声明但从未使用", line, col, 1, line, col + 1,
        f"删除无用的声明，或在你需要的表达式里使用它。",
        None, source_line)


def warning_shadowing(name, prev_line, line, col, source_line):
    return Diagnostic(
        SEVERITY_WARNING, PHASE_SEMANTIC, KIND_NAME,
        f"变量 {name!r} 遮蔽了第 {prev_line} 行的同名变量", line, col, len(name),
        line, col + len(name),
        f"换个名字以免混淆，或确认遮蔽是有意为之。",
        None, source_line)
