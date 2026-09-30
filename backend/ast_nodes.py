# -*- coding: utf-8 -*-
"""
抽象语法树（AST）节点定义。

语法分析阶段把 token 流折叠成这棵树；语义分析在其上做符号解析与类型检查；
字节码生成再遍历它产出中间代码。"AST 语法树可视化"页面直接消费这些节点的
``to_dict`` 序列化结果，画出可交互的树形结构。

每个节点都实现 ``to_dict``，输出稳定的 JSON 结构：
    {"type": <节点类型>, "line": <行>, "children": [...], ...字段}
"""

from typing import List, Optional, Any


class Node:
    """AST 基类。"""
    type_name = "Node"

    def __init__(self, line: int = 1, column: int = 1):
        self.line = line
        self.column = column

    def to_dict(self):
        return {"type": self.type_name, "line": self.line, "column": self.column}

    def __repr__(self):
        return f"{self.type_name}(line={self.line})"


# ---------------------------------------------------------------------------
# 表达式节点
# ---------------------------------------------------------------------------
class Expr(Node):
    """表达式基类（附带经语义分析推导出的静态类型）。"""
    def __init__(self, line=1, column=1):
        super().__init__(line, column)
        self.expr_type = None  # 语义分析填充


class NumberLiteral(Expr):
    type_name = "NumberLiteral"

    def __init__(self, value, line=1, column=1):
        super().__init__(line, column)
        self.value = value
        # 根据是否含小数点/科学计数法区分 int / float
        if isinstance(value, int):
            self.kind = "float"
        elif isinstance(value, float):
            self.kind = "float"
        else:
            self.kind = "float" if ("." in str(value) or "e" in str(value).lower()) else "int"

    def to_dict(self):
        d = super().to_dict()
        d.update(value=self.value, kind=self.kind, static_type=self.expr_type)
        return d


class StringLiteral(Expr):
    type_name = "StringLiteral"

    def __init__(self, value, line=1, column=1):
        super().__init__(line, column)
        self.value = value

    def to_dict(self):
        d = super().to_dict()
        d.update(value=self.value, static_type=self.expr_type)
        return d


class BoolLiteral(Expr):
    type_name = "BoolLiteral"

    def __init__(self, value, line=1, column=1):
        super().__init__(line, column)
        self.value = value

    def to_dict(self):
        d = super().to_dict()
        d.update(value=self.value, static_type=self.expr_type)
        return d


class NullLiteral(Expr):
    type_name = "NullLiteral"

    def to_dict(self):
        d = super().to_dict()
        d.update(static_type=self.expr_type)
        return d


class Identifier(Expr):
    type_name = "Identifier"

    def __init__(self, name, line=1, column=1):
        super().__init__(line, column)
        self.name = name
        self.symbol = None  # 语义分析时绑定到符号表条目

    def to_dict(self):
        d = super().to_dict()
        d.update(name=self.name, static_type=self.expr_type,
                 resolved=self.symbol.kind if self.symbol else None)
        return d


class UnaryExpr(Expr):
    type_name = "UnaryExpr"

    def __init__(self, op, operand, line=1, column=1):
        super().__init__(line, column)
        self.op = op          # '-' 或 '!'
        self.operand = operand

    def to_dict(self):
        d = super().to_dict()
        d.update(op=self.op, static_type=self.expr_type,
                 children=[self.operand.to_dict()])
        return d


class BinaryExpr(Expr):
    type_name = "BinaryExpr"

    def __init__(self, left, op, right, line=1, column=1):
        super().__init__(line, column)
        self.left = left
        self.op = op
        self.right = right

    def to_dict(self):
        d = super().to_dict()
        d.update(op=self.op, static_type=self.expr_type,
                 children=[self.left.to_dict(), self.right.to_dict()])
        return d


class LogicalExpr(Expr):
    """&& / || 短路逻辑表达式。"""
    type_name = "LogicalExpr"

    def __init__(self, left, op, right, line=1, column=1):
        super().__init__(line, column)
        self.left = left
        self.op = op
        self.right = right

    def to_dict(self):
        d = super().to_dict()
        d.update(op=self.op, static_type=self.expr_type,
                 children=[self.left.to_dict(), self.right.to_dict()])
        return d


class CallExpr(Expr):
    type_name = "CallExpr"

    def __init__(self, callee, args, line=1, column=1):
        super().__init__(line, column)
        self.callee = callee
        self.args = args          # List[Expr]
        self.callee_symbol = None

    def to_dict(self):
        d = super().to_dict()
        d.update(static_type=self.expr_type,
                 callee=self.callee.to_dict(),
                 children=[a.to_dict() for a in self.args])
        return d


class IndexExpr(Expr):
    type_name = "IndexExpr"

    def __init__(self, target, index, line=1, column=1):
        super().__init__(line, column)
        self.target = target
        self.index = index

    def to_dict(self):
        d = super().to_dict()
        d.update(static_type=self.expr_type,
                 children=[self.target.to_dict(), self.index.to_dict()])
        return d


class ListLiteral(Expr):
    type_name = "ListLiteral"

    def __init__(self, elements, line=1, column=1):
        super().__init__(line, column)
        self.elements = elements    # List[Expr]

    def to_dict(self):
        d = super().to_dict()
        d.update(static_type=self.expr_type,
                 children=[e.to_dict() for e in self.elements])
        return d


# ---------------------------------------------------------------------------
# 语句节点
# ---------------------------------------------------------------------------
class Stmt(Node):
    """语句基类。"""


class Block(Stmt):
    type_name = "Block"

    def __init__(self, statements, line=1, column=1):
        super().__init__(line, column)
        self.statements = statements  # List[Stmt]

    def to_dict(self):
        d = super().to_dict()
        d["children"] = [s.to_dict() for s in self.statements]
        return d


class VarDecl(Stmt):
    type_name = "VarDecl"

    def __init__(self, name, initializer, line=1, column=1, is_const=False):
        super().__init__(line, column)
        self.name = name
        self.initializer = initializer  # Expr or None
        self.is_const = is_const
        self.symbol = None

    def to_dict(self):
        d = super().to_dict()
        d.update(name=self.name, is_const=self.is_const,
                 children=[self.initializer.to_dict()] if self.initializer else [])
        return d


class AssignStmt(Stmt):
    type_name = "AssignStmt"

    def __init__(self, target, op, value, line=1, column=1):
        super().__init__(line, column)
        self.target = target    # Identifier or IndexExpr
        self.op = op            # '=' | '+=' | ...
        self.value = value

    def to_dict(self):
        d = super().to_dict()
        d.update(op=self.op,
                 children=[self.target.to_dict(), self.value.to_dict()])
        return d


class ExprStmt(Stmt):
    type_name = "ExprStmt"

    def __init__(self, expr, line=1, column=1):
        super().__init__(line, column)
        self.expr = expr

    def to_dict(self):
        d = super().to_dict()
        d["children"] = [self.expr.to_dict()]
        return d


class PrintStmt(Stmt):
    type_name = "PrintStmt"

    def __init__(self, args, line=1, column=1):
        super().__init__(line, column)
        self.args = args    # List[Expr]

    def to_dict(self):
        d = super().to_dict()
        d["children"] = [a.to_dict() for a in self.args]
        return d


class IfStmt(Stmt):
    type_name = "IfStmt"

    def __init__(self, branches, else_block, line=1, column=1):
        super().__init__(line, column)
        self.branches = branches          # List[(cond, Block)]
        self.else_block = else_block      # Block or None

    def to_dict(self):
        d = super().to_dict()
        d["children"] = []
        for cond, block in self.branches:
            d["children"].append({"type": "IfBranch",
                                  "line": cond.line,
                                  "condition": cond.to_dict(),
                                  "body": block.to_dict()})
        if self.else_block:
            d["children"].append({"type": "ElseBranch",
                                  "line": self.else_block.line,
                                  "body": self.else_block.to_dict()})
        return d


class WhileStmt(Stmt):
    type_name = "WhileStmt"

    def __init__(self, condition, body, line=1, column=1):
        super().__init__(line, column)
        self.condition = condition
        self.body = body

    def to_dict(self):
        d = super().to_dict()
        d.update(children=[self.condition.to_dict(), self.body.to_dict()])
        return d


class ForStmt(Stmt):
    type_name = "ForStmt"

    def __init__(self, init, condition, increment, body, line=1, column=1):
        super().__init__(line, column)
        self.init = init              # Stmt or None
        self.condition = condition    # Expr or None
        self.increment = increment    # Expr or None
        self.body = body

    def to_dict(self):
        d = super().to_dict()
        d["children"] = []
        if self.init:
            d["children"].append({"type": "ForInit", "line": self.init.line,
                                  "stmt": self.init.to_dict()})
        d["children"].append({"type": "ForCondition", "line": self.condition.line if self.condition else self.line,
                              "condition": self.condition.to_dict() if self.condition else None})
        if self.increment:
            d["children"].append({"type": "ForIncrement", "line": self.increment.line,
                                  "expr": self.increment.to_dict()})
        d["children"].append(self.body.to_dict())
        return d


class ReturnStmt(Stmt):
    type_name = "ReturnStmt"

    def __init__(self, value, line=1, column=1):
        super().__init__(line, column)
        self.value = value    # Expr or None

    def to_dict(self):
        d = super().to_dict()
        d["children"] = [self.value.to_dict()] if self.value else []
        return d


class BreakStmt(Stmt):
    type_name = "BreakStmt"


class ContinueStmt(Stmt):
    type_name = "ContinueStmt"


class FunctionDecl(Node):
    type_name = "FunctionDecl"

    def __init__(self, name, params, body, line=1, column=1):
        super().__init__(line, column)
        self.name = name
        self.params = params        # List[str]
        self.body = body            # Block
        self.symbol = None

    def to_dict(self):
        d = super().to_dict()
        d.update(name=self.name, params=self.params,
                 children=[self.body.to_dict()])
        return d


class Program(Node):
    type_name = "Program"

    def __init__(self, declarations, line=1, column=1):
        super().__init__(line, column)
        self.declarations = declarations   # List[FunctionDecl | Stmt]

    def to_dict(self):
        d = super().to_dict()
        d["children"] = [n.to_dict() for n in self.declarations]
        return d
