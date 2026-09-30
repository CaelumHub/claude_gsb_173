# -*- coding: utf-8 -*-
"""
代码生成器（Code Generator）。

把经过语义分析、已绑定符号的 AST 翻译成基于栈的字节码（中间代码）。
职责包括：
  * 为每个函数（含顶层 main）生成独立的 FunctionCode；
  * 决定标识符的"局部 / 全局"归属，从而选择 LOAD_VAR/STORE_VAR 或
    LOAD_GLOBAL/STORE_GLOBAL；
  * 用跳转指令实现 if/while/for/逻辑短路等控制流（采用回填 patch）；
  * 做轻量的常量折叠与跳转优化（后续在 vm 优化阶段进一步处理）。

代码生成完成后会调用 ``optimize`` 做一遍窥孔式字节码优化，这是
"AST 与字节码的优化"难点之一。
"""

from . import ast_nodes as ast
from . import symbols as sym
from . import bytecode as bc


class CodeGenerator:
    def __init__(self):
        self.program = bc.ProgramCode()
        self.current: bc.FunctionCode = None
        self._locals: set = set()      # 当前函数可见的局部变量名
        self._break_stack = []          # 循环 break 回填栈
        self._continue_stack = []       # 循环 continue 回填栈

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def generate(self, program: ast.Program) -> bc.ProgramCode:
        # 1) 顶层 main 函数（承载全局语句）
        main = bc.FunctionCode("<main>", 0, [])
        main.is_main = True
        self.program.main = main
        self.current = main
        self._locals = set()
        # 全局变量：顶层 var 声明
        for decl in program.declarations:
            if isinstance(decl, ast.VarDecl):
                self._locals.add(decl.name)
            elif isinstance(decl, ast.FunctionDecl):
                self._locals.add(decl.name)
        self.program.global_names = sorted(self._locals)

        # 2) 先注册所有函数（生成各自 FunctionCode），支持互相调用
        for decl in program.declarations:
            if isinstance(decl, ast.FunctionDecl):
                self._compile_function(decl)

        # 3) 生成顶层语句
        for decl in program.declarations:
            if isinstance(decl, ast.Stmt):
                self._stmt(decl)
        self.current.emit(bc.OP_RETURN_NONE, None, 1)

        # 4) 字节码优化
        for fc in self.program.functions.values():
            self._optimize(fc)
        self._optimize(main)
        return self.program

    def _compile_function(self, fn: ast.FunctionDecl):
        fc = bc.FunctionCode(fn.name, len(fn.params), fn.params)
        self.program.add_function(fc)
        prev, prev_locals = self.current, self._locals
        self.current = fc
        self._locals = set(fn.params)
        # 收集函数体内（不含嵌套函数）的局部变量
        self._collect_locals(fn.body)
        self._stmt(fn.body)
        self.current.emit(bc.OP_RETURN_NONE, None, fn.line)
        self.current, self._locals = prev, prev_locals
        return fc

    def _collect_locals(self, block: ast.Block):
        for s in block.statements:
            if isinstance(s, ast.VarDecl):
                self._locals.add(s.name)
            elif isinstance(s, ast.Block):
                self._collect_locals(s)
            elif isinstance(s, ast.IfStmt):
                for _, body in s.branches:
                    self._collect_locals(body)
                if s.else_block:
                    self._collect_locals(s.else_block)
            elif isinstance(s, ast.WhileStmt):
                self._collect_locals(s.body)
            elif isinstance(s, ast.ForStmt):
                self._collect_locals(s.body)

    # ------------------------------------------------------------------
    # 语句
    # ------------------------------------------------------------------
    def _stmt(self, s):
        if s is None:
            return
        if isinstance(s, ast.Block):
            for x in s.statements:
                self._stmt(x)
        elif isinstance(s, ast.VarDecl):
            if s.initializer:
                self._expr(s.initializer)
            else:
                self._emit(bc.OP_LOAD_CONST, None, s.line)
            self._store_name(s.name, s.line)
        elif isinstance(s, ast.AssignStmt):
            self._assign(s)
        elif isinstance(s, ast.ExprStmt):
            self._expr_stmt(s.expr, s.line)
        elif isinstance(s, ast.PrintStmt):
            self._emit(bc.OP_LOAD_BUILTIN, "print", s.line)
            for a in s.args:
                self._expr(a)
            self._emit(bc.OP_CALL, len(s.args), s.line)
            self._emit(bc.OP_POP, None, s.line)
        elif isinstance(s, ast.IfStmt):
            self._if(s)
        elif isinstance(s, ast.WhileStmt):
            self._while(s)
        elif isinstance(s, ast.ForStmt):
            self._for(s)
        elif isinstance(s, ast.ReturnStmt):
            if s.value:
                self._expr(s.value)
                self._emit(bc.OP_RETURN, None, s.line)
            else:
                self._emit(bc.OP_RETURN_NONE, None, s.line)
        elif isinstance(s, ast.BreakStmt):
            self._emit(bc.OP_JUMP, None, s.line)  # 目标在循环里回填
            self._break_stack[-1].append(self.current.instructions[-1])
        elif isinstance(s, ast.ContinueStmt):
            self._emit(bc.OP_JUMP, None, s.line)  # 目标在循环里回填
            self._continue_stack[-1].append(self.current.instructions[-1])

    def _if(self, s: ast.IfStmt):
        end_jumps = []
        for cond, body in s.branches:
            self._expr(cond)
            jump_false = self._emit(bc.OP_JUMP_IF_FALSE, 0, cond.line)
            self._stmt(body)
            end_jumps.append(self._emit(bc.OP_JUMP, 0, body.line))
            self._patch(jump_false, self._here())
        if s.else_block:
            self._stmt(s.else_block)
        end = self._here()
        for j in end_jumps:
            self._patch(j, end)

    def _while(self, s: ast.WhileStmt):
        self._break_stack.append([])
        self._continue_stack.append([])
        loop_start = self._here()
        self._expr(s.condition)
        jump_false = self._emit(bc.OP_JUMP_IF_FALSE, 0, s.condition.line)
        self._stmt(s.body)
        continue_target = self._here()
        self._emit(bc.OP_JUMP, loop_start, s.line)
        end = self._here()
        self._patch(jump_false, end)
        for b in self._break_stack[-1]:
            self._patch(b, end)
        for c in self._continue_stack[-1]:
            self._patch(c, continue_target)
        self._break_stack.pop()
        self._continue_stack.pop()

    def _for(self, s: ast.ForStmt):
        self._break_stack.append([])
        self._continue_stack.append([])
        if s.init:
            self._stmt(s.init)
        loop_start = self._here()
        if s.condition:
            self._expr(s.condition)
        else:
            self._emit(bc.OP_LOAD_CONST, True, s.line)
        jump_false = self._emit(bc.OP_JUMP_IF_FALSE, 0, s.line)
        self._stmt(s.body)
        continue_target = self._here()
        if s.increment:
            self._expr_stmt(s.increment, s.increment.line)
        self._emit(bc.OP_JUMP, loop_start, s.body.line)
        end = self._here()
        self._patch(jump_false, end)
        for b in self._break_stack[-1]:
            self._patch(b, end)
        for c in self._continue_stack[-1]:
            self._patch(c, continue_target)
        self._break_stack.pop()
        self._continue_stack.pop()

    def _assign(self, s: ast.AssignStmt):
        op = s.op
        if isinstance(s.target, ast.Identifier):
            name = s.target.name
            if op == "=":
                self._expr(s.value)
                self._store_name(name, s.line)
            else:
                binop = op[0]  # '+=' -> '+'
                self._load_name(name, s.line)
                self._expr(s.value)
                self._emit(bc.OP_BINARY, binop, s.line)
                self._store_name(name, s.line)
        elif isinstance(s.target, ast.IndexExpr):
            if op == "=":
                self._expr(s.target.target)
                self._expr(s.target.index)
                self._expr(s.value)
                self._emit(bc.OP_INDEX_STORE, None, s.line)
            else:
                binop = op[0]
                self._expr(s.target.target)
                self._expr(s.target.index)
                self._emit(bc.OP_DUP2, None, s.line)
                self._emit(bc.OP_INDEX_LOAD, None, s.line)
                self._expr(s.value)
                self._emit(bc.OP_BINARY, binop, s.line)
                self._emit(bc.OP_INDEX_STORE, None, s.line)

    # ------------------------------------------------------------------
    # 表达式
    # ------------------------------------------------------------------
    def _expr(self, e):
        if e is None:
            self._emit(bc.OP_LOAD_CONST, None, 1)
            return
        if isinstance(e, ast.NumberLiteral):
            idx = self.current.add_const(e.value)
            self._emit(bc.OP_LOAD_CONST, idx, e.line)
        elif isinstance(e, ast.StringLiteral):
            idx = self.current.add_const(e.value)
            self._emit(bc.OP_LOAD_CONST, idx, e.line)
        elif isinstance(e, ast.BoolLiteral):
            idx = self.current.add_const(e.value)
            self._emit(bc.OP_LOAD_CONST, idx, e.line)
        elif isinstance(e, ast.NullLiteral):
            idx = self.current.add_const(None)
            self._emit(bc.OP_LOAD_CONST, idx, e.line)
        elif isinstance(e, ast.Identifier):
            self._load_name(e.name, e.line)
        elif isinstance(e, ast.UnaryExpr):
            self._expr(e.operand)
            self._emit(bc.OP_UNARY, e.op, e.line)
        elif isinstance(e, ast.BinaryExpr):
            self._expr(e.left)
            self._expr(e.right)
            self._emit(bc.OP_BINARY, e.op, e.line)
        elif isinstance(e, ast.LogicalExpr):
            self._logical(e)
        elif isinstance(e, ast.CallExpr):
            self._call(e)
        elif isinstance(e, ast.IndexExpr):
            self._expr(e.target)
            self._expr(e.index)
            self._emit(bc.OP_INDEX_LOAD, None, e.line)
        elif isinstance(e, ast.ListLiteral):
            for el in e.elements:
                self._expr(el)
            self._emit(bc.OP_MAKE_LIST, len(e.elements), e.line)

    def _logical(self, e: ast.LogicalExpr):
        self._expr(e.left)
        self._emit(bc.OP_DUP, None, e.line)
        if e.op == "&&":
            jump = self._emit(bc.OP_JUMP_IF_FALSE, 0, e.line)
        else:  # ||
            jump = self._emit(bc.OP_JUMP_IF_TRUE, 0, e.line)
        self._emit(bc.OP_POP, None, e.line)   # 未短路：丢弃重复的左值
        self._expr(e.right)
        end = self._emit(bc.OP_JUMP, 0, e.line)
        self._patch(jump, self._here())       # 短路：保留左值作为结果
        self._patch(end, self._here())

    def _expr_stmt(self, e, line):
        """生成表达式语句（或赋值语句）的字节码，丢弃表达式的返回值。"""
        if isinstance(e, ast.AssignStmt):
            self._assign(e)
        else:
            self._expr(e)
            self._emit(bc.OP_POP, None, line)

    def _call(self, e: ast.CallExpr):
        if isinstance(e.callee, ast.Identifier):
            name = e.callee.name
            s = e.callee.symbol
            if s is not None and s.kind == sym.KIND_BUILTIN:
                self._emit(bc.OP_LOAD_BUILTIN, name, e.line)
            elif s is not None and s.kind == sym.KIND_FUNCTION:
                self._emit(bc.OP_LOAD_FUNC, name, e.line)
            else:
                self._load_name(name, e.line)
        else:
            self._expr(e.callee)
        for a in e.args:
            self._expr(a)
        self._emit(bc.OP_CALL, len(e.args), e.line)

    # ------------------------------------------------------------------
    # 名字的加载/存储
    # ------------------------------------------------------------------
    def _is_local(self, name) -> bool:
        return name in self._locals

    def _load_name(self, name, line):
        if self._is_local(name):
            self._emit(bc.OP_LOAD_VAR, name, line)
        else:
            self._emit(bc.OP_LOAD_GLOBAL, name, line)

    def _store_name(self, name, line):
        if self._is_local(name):
            self._emit(bc.OP_STORE_VAR, name, line)
        else:
            self._emit(bc.OP_STORE_GLOBAL, name, line)

    # ------------------------------------------------------------------
    # 发射 / 回填辅助
    # ------------------------------------------------------------------
    def _emit(self, op, operand, line) -> bc.Instruction:
        ins = self.current.emit(op, operand, line)
        return ins

    def _patch(self, ins: bc.Instruction, target: int):
        ins.operand = target

    def _here(self) -> int:
        return len(self.current.instructions)

    # ------------------------------------------------------------------
    # 字节码优化（窥孔）
    # ------------------------------------------------------------------
    def _optimize(self, fc: bc.FunctionCode):
        """几类安全的窥孔优化：
          1. LOAD_CONST x; POP        -> （整条删除）
          2. LOAD_CONST 0; POP        -> 删除
          3. 连续 LOAD_CONST x; LOAD_CONST x -> 保留其一（去重由 add_const 完成）
          4. JUMP -> JUMP 目标折叠（把跳转到跳转的指令直接指向最终目标）
          5. 消除 NOP
        """
        ins = fc.instructions
        # 多次扫描直到不动点（跳转折叠可能引发新的可折叠跳转）
        changed = True
        while changed:
            changed = False
            # 先建立当前偏移 -> 指令的映射
            new = []
            skip = 0
            for i in ins:
                if i.op == bc.OP_NOP:
                    changed = True
                    continue
                new.append(i)
            if len(new) != len(ins):
                changed = True
            ins = new

            # 重算偏移
            for idx, i in enumerate(ins):
                i.offset = idx

            # 跳转折叠：JUMP/JUMP_IF_* 的目标若是 JUMP，则直接指向其目标
            for i in ins:
                if i.op in (bc.OP_JUMP, bc.OP_JUMP_IF_FALSE, bc.OP_JUMP_IF_TRUE):
                    seen = 0
                    t = i.operand
                    while 0 <= t < len(ins) and ins[t].op == bc.OP_JUMP and seen < 16:
                        t = ins[t].operand
                        seen += 1
                    if t != i.operand:
                        i.operand = t
                        changed = True

            # LOAD_CONST + POP 消除
            nxt = []
            i = 0
            while i < len(ins):
                if (i + 1 < len(ins) and ins[i].op == bc.OP_LOAD_CONST
                        and ins[i + 1].op == bc.OP_POP):
                    changed = True
                    i += 2
                    continue
                nxt.append(ins[i])
                i += 1
            if len(nxt) != len(ins):
                changed = True
            ins = nxt

        # 最终回填偏移与行号映射
        fc.instructions = ins
        fc.line_to_offset = {}
        for idx, i in enumerate(ins):
            i.offset = idx + 1
            if i.line not in fc.line_to_offset:
                fc.line_to_offset[i.line] = idx
