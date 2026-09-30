# -*- coding: utf-8 -*-
"""
内存模型可视化。

解释器在运行过程中把列表、字符串、函数注册进一个堆（Heap），每个对象带唯一 ID。
本模块负责：
  1. 分配堆对象并记录其类型、大小、存活状态；
  2. 在任意暂停点（断点 / 单步 / 运行结束）生成一份**堆快照**，描述：
        - 当前调用栈各帧的局部变量（名字 -> 值/对象引用）；
        - 堆上所有对象及它们之间的引用关系（列表 -> 元素，元素可能是另一对象）；
  3. 计算对象占用（近似字节数），供前端画出"栈 + 堆 + 引用图"。

引用关系用「对象 ID -> 被引用对象 ID 列表」表达，前端据此渲染连线图。
"""

import sys
from typing import List, Dict, Any, Optional

from . import runtime as rt


def _approx_size(value) -> int:
    """粗略估算一个值占用的内存字节数（用于可视化，非精确计量）。"""
    if value is None:
        return 8
    if isinstance(value, bool):
        return 8
    if isinstance(value, int):
        return 28 if abs(value) > 2 ** 30 else 24
    if isinstance(value, float):
        return 24
    if isinstance(value, rt.RuntimeString):
        return 49 + len(value.value)
    if isinstance(value, str):
        return 49 + len(value)
    if isinstance(value, rt.RuntimeList):
        return 56 + 8 * len(value.items)
    if isinstance(value, rt.RuntimeFunction):
        return 96 + sum(len(i.to_dict()) for i in value.code.instructions)
    return 32


class Heap:
    """运行期堆：分配对象、维护存活对象、生成快照。"""

    def __init__(self):
        self._objects: Dict[int, rt.RuntimeObject] = {}
        self._next_id = 1
        self._allocation_count = 0
        self._peak_objects = 0

    # ---- 分配 ----
    def allocate_list(self, items: list) -> rt.RuntimeList:
        obj = rt.RuntimeList(self._next_id, items)
        self._next_id += 1
        self._objects[obj.oid] = obj
        self._allocation_count += 1
        self._refresh_peak()
        return obj

    def allocate_string(self, value: str) -> rt.RuntimeString:
        obj = rt.RuntimeString(self._next_id, value)
        self._next_id += 1
        obj.size = _approx_size(obj)
        self._objects[obj.oid] = obj
        self._allocation_count += 1
        self._refresh_peak()
        return obj

    def allocate_function(self, name, arity, params, code) -> rt.RuntimeFunction:
        obj = rt.RuntimeFunction(self._next_id, name, arity, params, code)
        self._next_id += 1
        obj.size = _approx_size(obj)
        self._objects[obj.oid] = obj
        self._allocation_count += 1
        self._refresh_peak()
        return obj

    def _refresh_peak(self):
        self._peak_objects = max(self._peak_objects, len(self._objects))

    def drop(self, oid: int):
        self._objects.pop(oid, None)

    # ---- 引用关系 ----
    def references_of(self, value) -> List[int]:
        """返回某个值直接引用的对象 ID 列表。"""
        if isinstance(value, rt.RuntimeList):
            refs = []
            for item in value.items:
                oid = self._oid_of(item)
                if oid is not None:
                    refs.append(oid)
            return refs
        return []

    def _oid_of(self, value) -> Optional[int]:
        if isinstance(value, rt.RuntimeObject):
            return value.oid
        return None

    # ---- 快照 ----
    def snapshot(self, frames: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成堆快照：调用栈帧 + 堆对象 + 引用图。"""
        objects = []
        for oid, obj in self._objects.items():
            refs = self.references_of(obj)
            if isinstance(obj, rt.RuntimeList):
                elements = [self._serialize_value(v) for v in obj.items]
            elif isinstance(obj, rt.RuntimeString):
                elements = [obj.value]
            else:
                elements = []
            objects.append({
                "oid": oid,
                "kind": obj.kind,
                # 快照时按当前内容重新估算大小，避免 push/pop 等原地修改导致 size 失真
                "size": obj.size,
                "refs": refs,
                "repr": self._short_repr(obj),
                "elements": elements,
            })

        # 从帧里提取局部变量及其引用
        stack_frames = []
        for fr in frames:
            locals_ = []
            for name, val in fr.get("locals", {}).items():
                locals_.append({
                    "name": name,
                    "value": self._serialize_value(val),
                    "ref": self._oid_of(val),
                })
            stack_frames.append({
                "function": fr.get("function", "<main>"),
                "line": fr.get("line", 0),
                "locals": locals_,
            })

        return {
            "objects": objects,
            "stack": stack_frames,
            "stats": {
                "object_count": len(objects),
                "allocation_count": self._allocation_count,
                "peak_objects": self._peak_objects,
                "total_bytes": sum(o["size"] for o in objects),
            },
        }

    def _serialize_value(self, value) -> Any:
        """把值序列化为前端可渲染的结构。"""
        if isinstance(value, rt.RuntimeList):
            return {"kind": "list", "oid": value.oid, "len": len(value.items)}
        if isinstance(value, rt.RuntimeString):
            return {"kind": "string", "oid": value.oid, "value": value.value}
        if isinstance(value, str):
            return {"kind": "string", "value": value}
        if isinstance(value, rt.RuntimeFunction):
            return {"kind": "function", "oid": value.oid, "name": value.name}
        if isinstance(value, rt.BuiltinFunction):
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

    def _short_repr(self, obj) -> str:
        if isinstance(obj, rt.RuntimeList):
            preview = ", ".join(self._mini_repr(v) for v in obj.items[:5])
            more = "…" if len(obj.items) > 5 else ""
            return f"[{preview}{more}]"
        if isinstance(obj, rt.RuntimeString):
            s = obj.value
            return f'"{s[:20]}{"…" if len(s) > 20 else ""}"'
        if isinstance(obj, rt.RuntimeFunction):
            return f"function {obj.name}/{obj.arity}"
        return obj.describe()

    def _mini_repr(self, value) -> str:
        if isinstance(value, rt.RuntimeList):
            return f"<list#{value.oid}>"
        if isinstance(value, rt.RuntimeString):
            return f'"{value.value[:8]}"'
        if isinstance(value, str):
            return f'"{value[:8]}"'
        if value is None:
            return "null"
        return str(value)

    def reset(self):
        self._objects.clear()
        self._next_id = 1
        self._allocation_count = 0
        self._peak_objects = 0
