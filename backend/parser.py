# -*- coding: utf-8 -*-
"""
语法分析器（Parser）—— 递归下降。

把词法分析产出的 Token 流按 MiniLang 文法折叠成一棵 AST。文法层次（优先级由低到高）：

    program      := decl*
    decl         := funcDecl | stmt
    funcDecl     := 'func' IDENT '(' params? ')' block
    stmt         := varDecl | ifStmt | whileStmt | forStmt | returnStmt
                  | breakStmt | continueStmt | block | printStmt | exprStmt
    expr         := assignment
    assignment   := postfix assignOp assignment | logicalOr
    logicalOr    := logicalAnd ('||' logicalAnd)*
    logicalAnd   := equality ('&&' equality)*
    equality     := comparison (('=='|'!=') comparison)*
    comparison   := term (('<'|'<='|'>'|'>=') term)*
    term         := factor (('+'|'-') factor)*
    factor       := unary (('*'|'/'|'%') unary)*
    unary        := ('!'|'-') unary | postfix
    postfix      := primary ('(' args? ')' | '[' expr ']' | '.' IDENT)*
    primary      := NUMBER | STRING | 'true'|'false'|'null' | IDENT
                  | '(' expr ')' | '[' list? ']'

采用**同步恢复**策略：出错时记录诊断，跳过若干 token 到下一个安全点（分号/右括号/
右大括号/关键字），尽量在一次解析中报告多个语法错误。
"""

from . import tokens as T
from . import ast_nodes as ast
from .diagnostics import DiagnosticBag, parse_unexpected, parse_missing_semicolon

# 赋值运算符
_ASSIGN_OPS = {T.EQ, T.PLUS_EQ, T.MINUS_EQ, T.STAR_EQ, T.SLASH_EQ, T.PERCENT_EQ}

# 可作为语句起始的 token（用于同步恢复时的安全点）
_STMT_START = {
    T.KW_VAR, T.KW_IF, T.KW_WHILE, T.KW_FOR, T.KW_RETURN, T.KW_BREAK,
    T.KW_CONTINUE, T.KW_PRINT, T.LBRACE, T.KW_FUNC,
}

# 优先级表：>= 表示当前操作符的优先级不低于栈顶
_BINARY_PRECEDENCE = {
    T.OR: 1, T.AND: 2,
    T.EQ_EQ: 3, T.BANG_EQ: 3,
    T.LT: 4, T.LT_EQ: 4, T.GT: 4, T.GT_EQ: 4,
    T.PLUS: 5, T.MINUS: 5,
    T.STAR: 6, T.SLASH: 6, T.PERCENT: 6,
}


class Parser:
    def __init__(self, tokens, diagnostics=None):
        self.tokens = tokens
        self.pos = 0
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag()

    # ------------------------------------------------------------------
    # 基本消费原语
    # ------------------------------------------------------------------
    def _cur(self) -> T.Token:
        return self.tokens[self.pos]

    def _peek(self, offset=1) -> T.Token:
        i = self.pos + offset
        if i >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[i]

    def _advance(self) -> T.Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def _check(self, *ttypes) -> bool:
        return self._cur().type in ttypes

    def _match(self, *ttypes) -> T.Token:
        if self._cur().type in ttypes:
            return self._advance()
        return None

    def _expect(self, ttype, context="语句"):
        tok = self._cur()
        if tok.type == ttype:
            return self._advance()
        self.diagnostics.add(parse_unexpected(tok, [ttype], self._source_line(tok.line)))
        return None

    def _source_line(self, line):
        return ""

    def _error_here(self, message, fix=""):
        tok = self._cur()
        self.diagnostics.error(
            message, phase="parse", kind="syntax",
            line=tok.line, column=tok.column, length=max(1, len(tok.text)),
            fix=fix, source_line=self._source_line(tok.line))

    def _sync(self):
        """跳过 token 直到下一个安全点，用于错误恢复。"""
        while not self._check(T.EOF):
            if self._cur().type in _STMT_START:
                return
            if self._cur().type == T.SEMICOLON:
                self._advance()
                return
            if self._cur().type in (T.RBRACE, T.RPAREN):
                return
            self._advance()

    # ------------------------------------------------------------------
    # 程序 / 声明
    # ------------------------------------------------------------------
    def parse(self) -> ast.Program:
        decls = []
        while not self._check(T.EOF):
            if self._check(T.KW_FUNC):
                fn = self._function_decl()
                if fn:
                    decls.append(fn)
            else:
                stmt = self._statement()
                if stmt:
                    decls.append(stmt)
            # 语句之间允许省略分号的情况做一次容错
            if self._check(T.EOF):
                break
        return ast.Program(decls)

    def _function_decl(self):
        start = self._advance()  # func
        name_tok = self._cur()
        if not self._check(T.IDENT):
            self._error_here("函数定义缺少函数名", "在 func 后面跟一个合法的函数名。")
            self._sync()
            return None
        name = self._advance().text
        self._expect(T.LPAREN, "函数定义")
        params = []
        if not self._check(T.RPAREN):
            while True:
                if self._check(T.IDENT):
                    params.append(self._advance().text)
                else:
                    self._error_here("形参必须是标识符", "参数名用合法标识符。")
                    if self._check(T.EOF) or self._check(T.RPAREN):
                        break
                    self._advance()
                    continue
                if self._match(T.COMMA):
                    continue
                break
        self._expect(T.RPAREN, "函数参数列表")
        body = self._block()
        if body is None:
            body = ast.Block([], start.line, start.column)
        return ast.FunctionDecl(name, params, body, start.line, start.column)

    # ------------------------------------------------------------------
    # 语句
    # ------------------------------------------------------------------
    def _statement(self):
        tok = self._cur()
        if tok.type == T.KW_VAR:
            return self._var_decl()
        if tok.type == T.KW_IF:
            return self._if_stmt()
        if tok.type == T.KW_WHILE:
            return self._while_stmt()
        if tok.type == T.KW_FOR:
            return self._for_stmt()
        if tok.type == T.KW_RETURN:
            return self._return_stmt()
        if tok.type == T.KW_BREAK:
            self._advance()
            self._expect(T.SEMICOLON, "break 语句")
            return ast.BreakStmt(tok.line, tok.column)
        if tok.type == T.KW_CONTINUE:
            self._advance()
            self._expect(T.SEMICOLON, "continue 语句")
            return ast.ContinueStmt(tok.line, tok.column)
        if tok.type == T.KW_PRINT:
            return self._print_stmt()
        if tok.type == T.LBRACE:
            return self._block()
        if tok.type == T.SEMICOLON:
            self._advance()  # 空语句
            return None
        if tok.type == T.EOF:
            return None
        # 表达式语句
        expr = self._expression()
        if expr is None:
            self._error_here(f"无法解析的语句，以 {tok.text!r} 开头",
                             "检查语句是否符合 MiniLang 语法。")
            self._advance()
            self._sync()
            return None
        self._expect(T.SEMICOLON, "表达式语句")
        return ast.ExprStmt(expr, expr.line, expr.column)

    def _block(self):
        tok = self._cur()
        if not self._match(T.LBRACE):
            self._error_here("期望 { 开始代码块", "用 { } 包裹语句块。")
            self._sync()
            return None
        stmts = []
        while not self._check(T.RBRACE) and not self._check(T.EOF):
            s = self._statement()
            if s:
                stmts.append(s)
            if self._check(T.SEMICOLON):
                # 允许多余的 ; 不影响语义
                pass
        self._expect(T.RBRACE, "代码块")
        return ast.Block(stmts, tok.line, tok.column)

    def _var_decl(self, consume_semi=True):
        start = self._advance()  # var
        if not self._check(T.IDENT):
            self._error_here("var 后面缺少变量名", "在 var 后跟一个变量名。")
            self._sync()
            return None
        name_tok = self._advance()
        initializer = None
        if self._match(T.EQ):
            initializer = self._expression()
            if initializer is None:
                self._error_here("变量初始化缺少表达式", "在 = 后面写一个表达式。")
        if consume_semi:
            self._expect(T.SEMICOLON, "变量声明")
        return ast.VarDecl(name_tok.text, initializer, start.line, start.column)

    def _if_stmt(self):
        start = self._advance()  # if
        branches = []
        self._expect(T.LPAREN, "if 条件")
        cond = self._expression()
        self._expect(T.RPAREN, "if 条件")
        body = self._block() or ast.Block([], start.line, start.column)
        branches.append((cond, body))
        while self._check(T.KW_ELIF):
            self._advance()
            self._expect(T.LPAREN, "elif 条件")
            c = self._expression()
            self._expect(T.RPAREN, "elif 条件")
            b = self._block() or ast.Block([], start.line, start.column)
            branches.append((c, b))
        else_block = None
        if self._match(T.KW_ELSE):
            else_block = self._block() or ast.Block([], start.line, start.column)
        return ast.IfStmt(branches, else_block, start.line, start.column)

    def _while_stmt(self):
        start = self._advance()  # while
        self._expect(T.LPAREN, "while 条件")
        cond = self._expression()
        self._expect(T.RPAREN, "while 条件")
        body = self._block() or ast.Block([], start.line, start.column)
        return ast.WhileStmt(cond, body, start.line, start.column)

    def _for_stmt(self):
        start = self._advance()  # for
        self._expect(T.LPAREN, "for 头")
        init = None
        if self._check(T.KW_VAR):
            init = self._var_decl(consume_semi=False)
        elif not self._check(T.SEMICOLON):
            e = self._expression()
            if e is not None:
                init = ast.ExprStmt(e, e.line, e.column)
        self._expect(T.SEMICOLON, "for 头")
        condition = None
        if not self._check(T.SEMICOLON):
            condition = self._expression()
        self._expect(T.SEMICOLON, "for 头")
        increment = None
        if not self._check(T.RPAREN):
            increment = self._expression()
        self._expect(T.RPAREN, "for 头")
        body = self._block() or ast.Block([], start.line, start.column)
        return ast.ForStmt(init, condition, increment, body, start.line, start.column)

    def _return_stmt(self):
        start = self._advance()  # return
        value = None
        if not self._check(T.SEMICOLON):
            value = self._expression()
        self._expect(T.SEMICOLON, "return 语句")
        return ast.ReturnStmt(value, start.line, start.column)

    def _print_stmt(self):
        start = self._advance()  # print
        args = []
        if self._match(T.LPAREN):
            if not self._check(T.RPAREN):
                while True:
                    e = self._expression()
                    if e:
                        args.append(e)
                    if self._match(T.COMMA):
                        continue
                    break
            self._expect(T.RPAREN, "print 参数列表")
        else:
            # 允许不带括号的 print
            e = self._expression()
            if e:
                args.append(e)
        self._expect(T.SEMICOLON, "print 语句")
        return ast.PrintStmt(args, start.line, start.column)

    # ------------------------------------------------------------------
    # 表达式
    # ------------------------------------------------------------------
    def _expression(self):
        return self._assignment()

    def _assignment(self):
        # 先解析一个 postfix 作为潜在左值，若遇到赋值运算符则递归
        left = self._logical_or()
        if left is None:
            return None
        tok = self._cur()
        if tok.type in _ASSIGN_OPS:
            if not isinstance(left, (ast.Identifier, ast.IndexExpr)):
                self.diagnostics.error(
                    "赋值目标不是合法的左值（只能是变量或下标访问）",
                    phase="parse", kind="syntax", line=tok.line, column=tok.column,
                    length=len(tok.text), fix="把赋值号左边改成变量或 arr[i] 形式。",
                    source_line=self._source_line(tok.line))
            op = self._advance().text
            right = self._assignment()
            return ast.AssignStmt(left, op, right, left.line, left.column)
        return left

    def _logical_or(self):
        left = self._logical_and()
        while left is not None and self._check(T.OR):
            op = self._advance().text
            right = self._logical_and()
            left = ast.LogicalExpr(left, op, right, left.line, left.column)
        return left

    def _logical_and(self):
        left = self._equality()
        while left is not None and self._check(T.AND):
            op = self._advance().text
            right = self._equality()
            left = ast.LogicalExpr(left, op, right, left.line, left.column)
        return left

    def _equality(self):
        left = self._comparison()
        while left is not None and self._check(T.EQ_EQ, T.BANG_EQ):
            op = self._advance().text
            right = self._comparison()
            left = ast.BinaryExpr(left, op, right, left.line, left.column)
        return left

    def _comparison(self):
        left = self._term()
        while left is not None and self._check(T.LT, T.LT_EQ, T.GT, T.GT_EQ):
            op = self._advance().text
            right = self._term()
            left = ast.BinaryExpr(left, op, right, left.line, left.column)
        return left

    def _term(self):
        left = self._factor()
        while left is not None and self._check(T.PLUS, T.MINUS):
            op = self._advance().text
            right = self._factor()
            left = ast.BinaryExpr(left, op, right, left.line, left.column)
        return left

    def _factor(self):
        left = self._unary()
        while left is not None and self._check(T.STAR, T.SLASH, T.PERCENT):
            op = self._advance().text
            right = self._unary()
            left = ast.BinaryExpr(left, op, right, left.line, left.column)
        return left

    def _unary(self):
        if self._check(T.BANG, T.MINUS):
            op = self._advance()
            operand = self._unary()
            if operand is None:
                return None
            return ast.UnaryExpr(op.text, operand, op.line, op.column)
        return self._postfix()

    def _postfix(self):
        expr = self._primary()
        if expr is None:
            return None
        while True:
            if self._check(T.LPAREN):
                self._advance()
                args = []
                if not self._check(T.RPAREN):
                    while True:
                        e = self._expression()
                        if e:
                            args.append(e)
                        if self._match(T.COMMA):
                            continue
                        break
                self._expect(T.RPAREN, "函数调用参数")
                expr = ast.CallExpr(expr, args, expr.line, expr.column)
            elif self._check(T.LBRACKET):
                self._advance()
                idx = self._expression()
                self._expect(T.RBRACKET, "下标访问")
                expr = ast.IndexExpr(expr, idx, expr.line, expr.column)
            else:
                break
        return expr

    def _primary(self):
        tok = self._cur()
        if tok.type == T.NUMBER:
            self._advance()
            text = tok.text
            try:
                if "e" in text.lower():
                    value = float(text)
                else:
                    value = int(text)
            except ValueError:
                value = 0
            return ast.NumberLiteral(value, tok.line, tok.column)
        if tok.type == T.STRING:
            self._advance()
            return ast.StringLiteral(tok.text, tok.line, tok.column)
        if tok.type == T.KW_TRUE:
            self._advance()
            return ast.BoolLiteral(True, tok.line, tok.column)
        if tok.type == T.KW_FALSE:
            self._advance()
            return ast.BoolLiteral(False, tok.line, tok.column)
        if tok.type == T.KW_NULL:
            self._advance()
            return ast.NullLiteral(tok.line, tok.column)
        if tok.type == T.IDENT:
            self._advance()
            return ast.Identifier(tok.text, tok.line, tok.column)
        if tok.type == T.LPAREN:
            self._advance()
            inner = self._expression()
            self._expect(T.RPAREN, "括号表达式")
            return inner
        if tok.type == T.LBRACKET:
            return self._list_literal()
        if tok.type == T.EOF:
            self._error_here("表达式不完整：文件提前结束",
                             "补全表达式，或在语句末尾加 ; 。")
            return None
        self.diagnostics.add(parse_unexpected(tok, ["表达式"], self._source_line(tok.line)))
        self._advance()
        return None

    def _list_literal(self):
        tok = self._advance()  # [
        elements = []
        if not self._check(T.RBRACKET):
            while True:
                e = self._expression()
                if e:
                    elements.append(e)
                if self._match(T.COMMA):
                    continue
                break
        self._expect(T.RBRACKET, "列表字面量")
        return ast.ListLiteral(elements, tok.line, tok.column)


def parse(tokens, diagnostics=None):
    """便捷入口：返回 AST Program。"""
    return Parser(tokens, diagnostics).parse()
