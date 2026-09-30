# -*- coding: utf-8 -*-
"""
语义分析器（Semantic Analyzer）。

在语法分析产出的 AST 之上做三件事：
  1. 构建作用域与符号表（全局 -> 函数 -> 块），解析每个名字；
  2. 做静态类型推断与类型检查（变量、表达式、运算符、函数调用）；
  3. 收集诊断：未定义名字（含"你是不是想写 xxx"建议）、重复声明、
     参数个数不匹配、类型错误、未使用变量、变量遮蔽等。

类型系统采用"推断为主"的轻量方案：从字面量出发，沿表达式向上传播类型，
`unknown` 表示无法静态确定（例如跨函数/动态值），此时不做强报错，避免误报。
"""

from . import ast_nodes as ast
from . import symbols as sym
from . import tokens as T
from .diagnostics import (
    DiagnosticBag, semantic_undefined_name, semantic_redeclared,
    semantic_wrong_arity, semantic_type_mismatch, semantic_assign_to_const,
    warning_unused, warning_shadowing,
)

# 内置函数及其签名（名称 -> 参数个数或 None 表示变长）
BUILTIN_SIGNATURES = {
    "print": None, "len": 1, "push": 2, "pop": 1, "type": 1,
    "str": 1, "int": 1, "float": 1, "range": None, "abs": 1,
    "min": 2, "max": 2, "sqrt": 1, "floor": 1, "ceil": 1,
    "round": None, "input": 0, "time": 0, "random": 0, "exit": 0,
}

# 运算符返回类型表（用于简单的类型推断）
_NUMERIC = {sym.TYPE_INT, sym.TYPE_FLOAT}
_COMPARABLE = {sym.TYPE_INT, sym.TYPE_FLOAT, sym.TYPE_STRING, sym.TYPE_BOOL}


def _numeric_promote(a, b):
    if a in _NUMERIC and b in _NUMERIC:
        return sym.TYPE_INT
    return sym.TYPE_UNKNOWN


class SemanticAnalyzer:
    def __init__(self):
        self.diagnostics = DiagnosticBag()
        self.symbols = sym.SymbolTable()
        self.current_scope = self.symbols.global_scope
        self.current_function = None
        self.loop_depth = 0
        self.source_lines = []
        self._builtin_symbols = self._declare_builtins()

    def _declare_builtins(self):
        builtins = {}
        for name in BUILTIN_SIGNATURES:
            s = sym.Symbol(name, sym.KIND_BUILTIN, self.symbols.global_scope,
                           symbol_type=sym.TYPE_FUNC)
            self.symbols.global_scope.define(s)
            builtins[name] = s
        return builtins

    def set_source(self, source: str):
        self.source_lines = source.split("\n")

    def _line(self, node):
        i = node.line - 1
        if 0 <= i < len(self.source_lines):
            return self.source_lines[i]
        return ""

    def _with_scope(self, scope_type, name, fn):
        prev = self.current_scope
        self.current_scope = self.symbols.new_scope(scope_type, name, prev)
        try:
            fn()
        finally:
            self.current_scope = prev

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def analyze(self, program: ast.Program):
        # 第一遍：先把所有函数名注册进全局作用域（支持互相调用 / 递归）
        for decl in program.declarations:
            if isinstance(decl, ast.FunctionDecl):
                self._declare_function(decl)
        # 第二遍：分析每个声明
        for decl in program.declarations:
            if isinstance(decl, ast.FunctionDecl):
                self._analyze_function(decl)
            elif isinstance(decl, ast.Stmt):
                self._analyze_stmt(decl)
        # 收尾：未使用变量告警
        self._warn_unused()
        return program

    def _declare_function(self, fn: ast.FunctionDecl):
        if self.symbols.global_scope.lookup_local(fn.name):
            self.diagnostics.add(semantic_redeclared(
                fn.name, self.symbols.global_scope.lookup_local(fn.name).line,
                fn.line, fn.column, self._line(fn)))
            return None
        s = sym.Symbol(fn.name, sym.KIND_FUNCTION, self.symbols.global_scope,
                       line=fn.line, column=fn.column, symbol_type=sym.TYPE_FUNC)
        s.arity = len(fn.params)      # 记录形参个数，供调用处做参数个数检查
        self.symbols.global_scope.define(s)
        fn.symbol = s
        return s

    def _analyze_function(self, fn: ast.FunctionDecl):
        self.current_function = fn
        self._with_scope(sym.SCOPE_FUNCTION, fn.name, lambda: self._function_body(fn))
        self.current_function = None

    def _function_body(self, fn: ast.FunctionDecl):
        # 形参
        seen = set()
        for i, p in enumerate(fn.params):
            if p in seen:
                self.diagnostics.add(semantic_redeclared(p, fn.line, fn.line, fn.column, self._line(fn)))
                continue
            seen.add(p)
            s = sym.Symbol(p, sym.KIND_PARAMETER, self.current_scope,
                           line=fn.line, column=fn.column, symbol_type=sym.TYPE_UNKNOWN)
            s.param_index = i
            self.current_scope.define(s)
        self._analyze_block(fn.body)

    # ------------------------------------------------------------------
    # 语句
    # ------------------------------------------------------------------
    def _analyze_block(self, block: ast.Block):
        self._with_scope(sym.SCOPE_BLOCK, "block", lambda: self._analyze_stmts(block.statements))

    def _analyze_stmts(self, stmts):
        for s in stmts:
            self._analyze_stmt(s)

    def _analyze_stmt(self, stmt):
        if stmt is None:
            return
        if isinstance(stmt, ast.Block):
            self._analyze_block(stmt)
        elif isinstance(stmt, ast.VarDecl):
            self._var_decl(stmt)
        elif isinstance(stmt, ast.AssignStmt):
            self._assign(stmt)
        elif isinstance(stmt, ast.ExprStmt):
            self._expr(stmt.expr)
        elif isinstance(stmt, ast.PrintStmt):
            for a in stmt.args:
                self._expr(a)
        elif isinstance(stmt, ast.IfStmt):
            for cond, body in stmt.branches:
                self._expr(cond)
                self._analyze_block(body)
            if stmt.else_block:
                self._analyze_block(stmt.else_block)
        elif isinstance(stmt, ast.WhileStmt):
            self._expr(stmt.condition)
            self.loop_depth += 1
            self._analyze_block(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ast.ForStmt):
            if stmt.init:
                self._analyze_stmt(stmt.init)
            if stmt.condition:
                self._expr(stmt.condition)
            if stmt.increment:
                self._expr(stmt.increment)
            self.loop_depth += 1
            self._analyze_block(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value:
                self._expr(stmt.value)
        elif isinstance(stmt, (ast.BreakStmt, ast.ContinueStmt)):
            if self.loop_depth == 0:
                self.diagnostics.error(
                    "break/continue 只能出现在循环体内", phase="semantic", kind="syntax",
                    line=stmt.line, column=stmt.column, length=5,
                    fix="把 break/continue 移到 while 或 for 循环体内。",
                    source_line=self._line(stmt))
        elif isinstance(stmt, ast.FunctionDecl):
            self._analyze_function(stmt)

    def _var_decl(self, decl: ast.VarDecl):
        if self.current_scope.lookup_local(decl.name):
            prev = self.current_scope.lookup_local(decl.name)
            self.diagnostics.add(semantic_redeclared(
                decl.name, prev.line, decl.line, decl.column, self._line(decl)))
            return
        if decl.initializer:
            decl.expr_type = self._expr(decl.initializer)
        else:
            decl.expr_type = sym.TYPE_NULL
        # 检查是否遮蔽外层
        outer = self.current_scope.parent.lookup(decl.name) if self.current_scope.parent else decl
        if outer:
            self.diagnostics.add(warning_shadowing(
                decl.name, outer.line, decl.line, decl.column, self._line(decl)))
        s = sym.Symbol(decl.name, sym.KIND_VARIABLE, self.current_scope,
                       line=decl.line, column=decl.column, symbol_type=decl.expr_type,
                       is_const=decl.is_const)
        self.current_scope.define(s)
        decl.symbol = s

    def _assign(self, stmt: ast.AssignStmt):
        value_type = self._expr(stmt.value)
        if isinstance(stmt.target, ast.Identifier):
            name = stmt.target.name
            s = self.current_scope.lookup(name)
            if s is None:
                self.diagnostics.add(semantic_undefined_name(
                    name, self.symbols.collect_names(sym.KIND_BUILTIN), stmt.target.line,
                    stmt.target.column, self._line(stmt.target)))
                return
            if stmt.op == "=":
                s.references += 1
            stmt.target.symbol = s
            if s.is_const or s.kind == sym.KIND_FUNCTION:
                self.diagnostics.add(semantic_assign_to_const(
                    name, stmt.target.line, stmt.target.column, self._line(stmt.target)))
                return
            stmt.target.expr_type = s.symbol_type
        elif isinstance(stmt.target, ast.IndexExpr):
            self._expr(stmt.target.target)
            self._expr(stmt.target.index)
            stmt.target.expr_type = value_type

    # ------------------------------------------------------------------
    # 表达式
    # ------------------------------------------------------------------
    def _expr(self, e) -> str:
        if e is None:
            return sym.TYPE_UNKNOWN
        if isinstance(e, ast.NumberLiteral):
            e.expr_type = e.kind
            return e.expr_type
        if isinstance(e, ast.StringLiteral):
            e.expr_type = sym.TYPE_STRING
            return e.expr_type
        if isinstance(e, ast.BoolLiteral):
            e.expr_type = sym.TYPE_BOOL
            return e.expr_type
        if isinstance(e, ast.NullLiteral):
            e.expr_type = sym.TYPE_NULL
            return e.expr_type
        if isinstance(e, ast.Identifier):
            return self._identifier(e)
        if isinstance(e, ast.UnaryExpr):
            t = self._expr(e.operand)
            if e.op == "!":
                e.expr_type = sym.TYPE_BOOL
            else:
                e.expr_type = t if t in _NUMERIC else sym.TYPE_UNKNOWN
            return e.expr_type
        if isinstance(e, ast.BinaryExpr):
            return self._binary(e)
        if isinstance(e, ast.LogicalExpr):
            self._expr(e.left); self._expr(e.right)
            e.expr_type = sym.TYPE_BOOL
            return e.expr_type
        if isinstance(e, ast.CallExpr):
            return self._call(e)
        if isinstance(e, ast.IndexExpr):
            return self._index(e)
        if isinstance(e, ast.ListLiteral):
            elem_types = [self._expr(x) for x in e.elements]
            if elem_types and all(t == elem_types[0] for t in elem_types):
                e.expr_type = sym.TYPE_LIST
            else:
                e.expr_type = sym.TYPE_LIST
            return e.expr_type
        return sym.TYPE_UNKNOWN

    def _identifier(self, e: ast.Identifier):
        s = self.current_scope.lookup(e.name)
        if s is None:
            self.diagnostics.add(semantic_undefined_name(
                e.name, self.symbols.collect_names(sym.KIND_BUILTIN), e.line, e.column, self._line(e)))
            e.expr_type = sym.TYPE_UNKNOWN
            return e.expr_type
        s.references += 1
        e.symbol = s
        e.expr_type = s.symbol_type
        return s.symbol_type

    def _binary(self, e: ast.BinaryExpr):
        lt = self._expr(e.left)
        rt = self._expr(e.right)
        op = e.op
        if op in ("+", "-", "*", "/", "%"):
            if lt in _NUMERIC and rt in _NUMERIC:
                e.expr_type = _numeric_promote(lt, rt)
            elif op == "+" and (lt == sym.TYPE_STRING or rt == sym.TYPE_STRING):
                e.expr_type = sym.TYPE_STRING
            else:
                e.expr_type = sym.TYPE_UNKNOWN
        elif op in ("==", "!=", "<", "<=", ">", ">="):
            e.expr_type = sym.TYPE_BOOL
        else:
            e.expr_type = sym.TYPE_UNKNOWN
        return e.expr_type

    def _call(self, e: ast.CallExpr):
        # 实参类型
        for a in e.args:
            self._expr(a)
        if isinstance(e.callee, ast.Identifier):
            name = e.callee.name
            s = self.current_scope.lookup(name)
            if s is None:
                self.diagnostics.add(semantic_undefined_name(
                    name, self.symbols.collect_names(sym.KIND_BUILTIN), e.callee.line,
                    e.callee.column, self._line(e.callee)))
                e.expr_type = sym.TYPE_UNKNOWN
                return e.expr_type
            s.references += 1
            e.callee.symbol = s
            e.callee.expr_type = sym.TYPE_FUNC
            if s.kind == sym.KIND_BUILTIN:
                sig = BUILTIN_SIGNATURES.get(name)
                if sig is not None and len(e.args) != sig:
                    self.diagnostics.add(semantic_wrong_arity(
                        name, sig, len(e.args), e.line, e.column, self._line(e)))
                e.expr_type = sym.TYPE_UNKNOWN  # 内置函数返回类型视函数而定
                return e.expr_type
            if s.kind == sym.KIND_FUNCTION:
                arity = getattr(s, "arity", None)
                if arity is not None and len(e.args) != arity:
                    self.diagnostics.add(semantic_wrong_arity(
                        name, arity, len(e.args), e.line, e.column, self._line(e)))
                e.expr_type = sym.TYPE_UNKNOWN
                return e.expr_type
        # 非标识符调用（如闭包/高阶），保守处理
        self._expr(e.callee)
        e.expr_type = sym.TYPE_UNKNOWN
        return e.expr_type

    def _index(self, e: ast.IndexExpr):
        tt = self._expr(e.target)
        self._expr(e.index)
        if tt == sym.TYPE_LIST or tt == sym.TYPE_STRING:
            e.expr_type = sym.TYPE_UNKNOWN  # 元素类型无法静态确定
        else:
            e.expr_type = sym.TYPE_UNKNOWN
        return e.expr_type

    def _warn_unused(self):
        for s in self.symbols.all_symbols():
            if s.kind in (sym.KIND_VARIABLE, sym.KIND_PARAMETER) and s.references == 0:
                self.diagnostics.add(warning_unused(
                    s.name, s.line, s.column, self._line(s) if s.line else ""))
