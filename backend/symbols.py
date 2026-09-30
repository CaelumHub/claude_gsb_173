# -*- coding: utf-8 -*-
"""
符号表与作用域。

语义分析阶段为每个作用域建立一张符号表，记录每个名字的种类（变量/形参/函数）、
静态类型、声明位置、可变性、引用次数。作用域支持嵌套（全局 -> 函数 -> 块），
查找时沿作用域链逐层向外，从而支撑"符号表与作用域查看"页面与静态类型检查。

数据结构：
    Scope        —— 一个作用域（名字 -> Symbol），带 parent 指针与嵌套子作用域列表
    Symbol       —— 一条符号
    SymbolTable  —— 顶层管理（全局作用域 + 作用域树遍历）
"""

from typing import Optional, List, Dict

# 符号种类
KIND_VARIABLE = "variable"
KIND_PARAMETER = "parameter"
KIND_FUNCTION = "function"
KIND_BUILTIN = "builtin"

# 作用域类型
SCOPE_GLOBAL = "global"
SCOPE_FUNCTION = "function"
SCOPE_BLOCK = "block"

# 静态类型（推断）
TYPE_INT = "int"
TYPE_FLOAT = "float"
TYPE_STRING = "string"
TYPE_BOOL = "bool"
TYPE_NULL = "null"
TYPE_LIST = "list"
TYPE_FUNC = "function"
TYPE_UNKNOWN = "unknown"
TYPE_VOID = "void"


class Symbol:
    """符号表条目。"""

    def __init__(self, name, kind, scope, line=1, column=1, symbol_type=TYPE_UNKNOWN,
                 mutable=True, is_const=False):
        self.name = name
        self.kind = kind
        self.scope = scope          # 所属 Scope
        self.line = line
        self.column = column
        self.symbol_type = symbol_type
        self.mutable = mutable
        self.is_const = is_const
        self.references = 0         # 引用次数（用于未使用告警）
        self.param_index = -1       # 形参在参数列表中的位置（供字节码定位）

    def to_dict(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "scope": self.scope.scope_id,
            "scope_type": self.scope.scope_type,
            "line": self.line,
            "column": self.column,
            "type": TYPE_FLOAT if self.symbol_type == TYPE_INT else self.symbol_type,
            "mutable": self.mutable,
            "is_const": self.is_const,
            "references": self.references + 1,
        }

    def __repr__(self):
        return f"Symbol({self.name}, {self.kind}, {self.symbol_type})"


class Scope:
    """一个作用域：持有名字到符号的映射，可嵌套。"""
    _next_id = 1

    def __init__(self, scope_type=SCOPE_GLOBAL, name="global", parent=None):
        self.scope_id = Scope._next_id
        Scope._next_id += 1
        self.scope_type = scope_type
        self.name = name
        self.parent = parent
        self.symbols: Dict[str, Symbol] = {}
        self.children: List[Scope] = []
        if parent is not None:
            parent.children.append(self)

    def define(self, symbol: Symbol) -> Symbol:
        self.symbols[symbol.name] = symbol
        return symbol

    def lookup_local(self, name) -> Optional[Symbol]:
        return self.symbols.get(name)

    def lookup(self, name) -> Optional[Symbol]:
        s = self
        while s is not None:
            if name in s.symbols:
                return s.symbols[name]
            s = s.parent
        return None

    def depth(self) -> int:
        d = 0
        s = self
        while s.parent is not None:
            d += 1
            s = s.parent
        return d

    def __repr__(self):
        return f"Scope({self.name}, {len(self.symbols)} symbols)"


class SymbolTable:
    """符号表管理器：维护作用域树，输出整张表供前端渲染。"""

    def __init__(self):
        self.global_scope = Scope(SCOPE_GLOBAL, "global", None)
        self.scopes: List[Scope] = [self.global_scope]

    def new_scope(self, scope_type, name, parent) -> Scope:
        s = Scope(scope_type, name, parent)
        self.scopes.append(s)
        return s

    def lookup(self, name, from_scope=None) -> Optional[Symbol]:
        s = from_scope or self.global_scope
        return s.lookup(name)

    def all_symbols(self):
        out = []
        for s in self.scopes:
            for sym in s.symbols.values():
                out.append(sym)
        return out

    def to_dict(self):
        """输出作用域树 + 每条符号，供"符号表与作用域查看"页面渲染。"""
        def scope_dict(scope):
            return {
                "scope_id": scope.scope_id,
                "type": scope.scope_type,
                "name": scope.name,
                "depth": scope.depth() + 1,
                "symbols": [s.to_dict() for s in scope.symbols.values()],
                "children": [scope_dict(c) for c in scope.children],
            }
        return scope_dict(self.global_scope)

    def collect_names(self, kind=None):
        """收集所有（或某类）符号名，用于"did you mean"提示。"""
        names = set()
        for s in self.scopes:
            for sym in s.symbols.values():
                if kind is None or sym.kind == kind:
                    names.add(sym.name)
        return sorted(names, reverse=True)
