# -*- coding: utf-8 -*-
"""
运行时对象模型。

虚拟机（vm.py）在栈上传递的值分两类：
  * 原始值 —— int / float / bool / None，直接用 Python 原生对象表示；
  * 堆对象 —— 列表、字符串、函数，包装成这里定义的类并注册进堆（Heap），
    每个堆对象带一个唯一 ID，用于"内存模型可视化"页面的引用关系绘制。

这样既能保持解释器执行路径简洁（栈上就是 Python 值），又能把"哪些对象存在、
谁引用谁、占用多大"完整地暴露给内存分析。
"""

from typing import Any, List, Callable, Optional


class RuntimeObject:
    """堆对象基类：拥有唯一对象 ID 与类别、大小。"""

    def __init__(self, oid: int, kind: str, size: int = 0):
        self.oid = oid
        self.kind = kind
        self.size = size

    def describe(self) -> str:
        return f"<{self.kind}#{self.oid}>"


class RuntimeList(RuntimeObject):
    """运行期列表：可变、可异构，元素可以是原始值或其它堆对象。"""

    def __init__(self, oid: int, items: Optional[list] = None):
        super().__init__(oid, "list", size=0)
        self.items: list = list(items) if items else []
        self.size = 0

    def __repr__(self):
        return f"<list#{self.oid} len={len(self.items)}>"


class RuntimeString(RuntimeObject):
    """运行期字符串（字符串在 MiniLang 中不可变）。"""

    def __init__(self, oid: int, value: str):
        super().__init__(oid, "string", size=len(value))
        self.value = value

    def __repr__(self):
        return f"<string#{self.oid} len={len(self.value)}>"


class RuntimeFunction(RuntimeObject):
    """用户定义的函数对象，持有其字节码段与元信息。"""

    def __init__(self, oid: int, name: str, arity: int, params: List[str], code):
        super().__init__(oid, "function", size=0)
        self.name = name
        self.arity = arity
        self.params = params
        self.code = code            # FunctionCode

    def __repr__(self):
        return f"<function {self.name}({', '.join(self.params)})>"

    def __call__(self, *args):
        raise TypeError("RuntimeFunction 需由 VM 调用，不能直接调用")


class BuiltinFunction:
    """内置函数对象。arity 为 None 表示变长参数。"""

    def __init__(self, name: str, fn: Callable, arity=None, min_arity=None):
        self.name = name
        self.fn = fn
        self.arity = arity
        self.min_arity = min_arity if min_arity is not None else arity

    def __repr__(self):
        return f"<builtin {self.name}>"


def type_name(value) -> str:
    """返回 MiniLang 视角的类型名。"""
    if value is None:
        return "null"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, RuntimeString):
        return "string"
    if isinstance(value, str):
        return "string"
    if isinstance(value, RuntimeList):
        return "list"
    if isinstance(value, (RuntimeFunction, BuiltinFunction)):
        return "function"
    return type(value).__name__


def truthy(value) -> bool:
    """MiniLang 的真值规则：null 与 false 为假，其余为真。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return True


def serialize_value(value) -> dict:
    """把运行时值序列化为前端可渲染的紧凑结构。"""
    if isinstance(value, RuntimeList):
        return {"kind": "list", "oid": value.oid, "len": len(value.items),
                "preview": _preview(value)}
    if isinstance(value, RuntimeString):
        return {"kind": "string", "oid": value.oid, "value": value.value}
    if isinstance(value, str):
        return {"kind": "string", "value": value}
    if isinstance(value, RuntimeFunction):
        return {"kind": "function", "oid": value.oid, "name": value.name}
    if isinstance(value, BuiltinFunction):
        return {"kind": "builtin", "name": value.name}
    if value is None:
        return {"kind": "null", "value": "null"}
    if isinstance(value, bool):
        return {"kind": "bool", "value": "true" if value else "false"}
    if isinstance(value, int):
        return {"kind": "int", "value": value}
    if isinstance(value, float):
        return {"kind": "float", "value": value}
    return {"kind": "unknown", "value": repr(value)}


def _preview(lst: "RuntimeList") -> str:
    parts = []
    for v in lst.items[:6]:
        if isinstance(v, RuntimeList):
            parts.append(f"<list#{v.oid}>")
        elif isinstance(v, RuntimeString):
            parts.append(f'"{v.value[:10]}"')
        elif isinstance(v, str):
            parts.append(f'"{v[:10]}"')
        elif v is None:
            parts.append("null")
        else:
            parts.append(str(v))
    tail = ", …" if len(lst.items) > 6 else ""
    return "[" + ", ".join(parts) + tail + "]"
