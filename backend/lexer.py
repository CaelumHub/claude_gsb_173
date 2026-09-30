# -*- coding: utf-8 -*-
"""
词法分析器（Lexer）。

把 MiniLang 源码文本切成一串 Token，跳过空白与注释，识别关键字、
标识符、数字（含小数）、字符串字面量、运算符与分隔符。

错误策略：遇到非法字符 / 未闭合字符串 / 非法数字时**记录诊断并尽量恢复**，
尽量在一次扫描里报出尽可能多的词法错误，而不是遇到第一个错误就停下。
"""

from . import tokens as T
from .diagnostics import (
    DiagnosticBag, lex_unterminated_string, lex_unexpected_char, lex_invalid_number,
)

# 单字符运算符 / 分隔符
_SINGLE = {
    "+": T.PLUS, "-": T.MINUS, "*": T.STAR, "/": T.SLASH, "%": T.PERCENT,
    "!": T.BANG, "=": T.EQ, "<": T.LT, ">": T.GT,
    "(": T.LPAREN, ")": T.RPAREN, "{": T.LBRACE, "}": T.RBRACE,
    "[": T.LBRACKET, "]": T.RBRACKET, ",": T.COMMA, ";": T.SEMICOLON,
    ".": T.DOT,
}

# 双字符运算符（按最长匹配优先）
_DOUBLE = {
    "==": T.EQ_EQ, "!=": T.BANG_EQ, "<=": T.LT_EQ, ">=": T.GT_EQ,
    "&&": T.AND, "||": T.OR,
    "+=": T.PLUS_EQ, "-=": T.MINUS_EQ, "*=": T.STAR_EQ,
    "/=": T.SLASH_EQ, "%=": T.PERCENT_EQ,
}


class Lexer:
    """递归下降之外的、线性扫描的词法分析器。"""

    def __init__(self, source: str):
        self.source = source
        self.length = len(source)
        self.pos = 0
        self.line = 1
        self.column = 1
        self.diagnostics = DiagnosticBag()
        self.tokens = []

    # ------------------------------------------------------------------
    # 位置工具
    # ------------------------------------------------------------------
    def _peek(self, offset=0) -> str:
        i = self.pos + offset
        return self.source[i] if i < self.length else ""

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return ch

    def _line_text(self, line):
        lines = self.source.split("\n")
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""

    def _current_source_line(self):
        return self._line_text(self.line)

    # ------------------------------------------------------------------
    # 主扫描循环
    # ------------------------------------------------------------------
    def tokenize(self):
        while self.pos < self.length:
            ch = self._peek()
            if ch in " \t\r":
                self._advance()
                continue
            if ch == "\n":
                # 换行不算 token（语句靠分号分隔），但保留行号推进
                self._advance()
                continue
            if ch == "/" and self._peek(1) == "/":
                self._skip_line_comment()
                continue
            if ch == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
                self._scan_number()
                continue
            if ch == '"':
                self._scan_string()
                continue
            if ch.isalpha() or ch == "_":
                self._scan_identifier()
                continue
            if self._scan_operator():
                continue
            # 无法识别的字符 —— 记录诊断后跳过，尽力继续
            start = self.pos
            line, col = self.line, self.column
            self.diagnostics.add(lex_unexpected_char(line, col, ch, self._current_source_line()))
            self._advance()
            self.tokens.append(T.Token("ILLEGAL", ch, line, col, start))

        eof = T.Token(T.EOF, "", self.line, self.column, self.pos)
        self.tokens.append(eof)
        return self.tokens

    # ------------------------------------------------------------------
    # 各扫描子过程
    # ------------------------------------------------------------------
    def _skip_line_comment(self):
        while self.pos < self.length and self._peek() != "\n":
            self._advance()

    def _skip_block_comment(self):
        line, col = self.line, self.column
        self._advance(); self._advance()  # 吃掉 /*
        while self.pos < self.length:
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance(); self._advance()
                return
            self._advance()
        # 未闭合块注释
        self.diagnostics.error(
            "块注释未闭合：缺少结尾的 */", phase="lex", kind="syntax",
            line=line, column=col, length=2,
            fix="在注释末尾补上 */，或检查是否嵌套了注释。",
            source_line=self._line_text(line))

    def _scan_identifier(self):
        start = self.pos
        line, col = self.line, self.column
        while self.pos < self.length and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        text = self.source[start:self.pos]
        ttype = text if text in T.KEYWORDS else T.IDENT
        self.tokens.append(T.Token(ttype, text, line, col, start))

    def _scan_number(self):
        start = self.pos
        line, col = self.line, self.column
        has_dot = False
        while self.pos < self.length:
            ch = self._peek()
            if ch.isdigit():
                self._advance()
            elif ch == "." and not has_dot and self._peek(1).isdigit():
                has_dot = True
                self._advance()
            else:
                break
        # 处理 1.2e3 这种科学计数法（可选）
        if self._peek() in ("e", "E") and (self._peek(1).isdigit() or
                                           (self._peek(1) in "+-" and self._peek(2).isdigit())):
            self._advance()
            if self._peek() in "+-":
                self._advance()
            while self.pos < self.length and self._peek().isdigit():
                self._advance()
        text = self.source[start:self.pos]
        # 非法数字：如 "1.2.3" 会被上面的逻辑切成 "1.2" + ".3"，这里校验相邻点
        if self._peek() == "." and self._peek(1) == ".":
            self.diagnostics.add(lex_invalid_number(line, col, text + "..", self._current_source_line()))
            self._advance(); self._advance()
            text = text + ".."
        self.tokens.append(T.Token(T.NUMBER, text, line, col, start))

    def _scan_string(self):
        start = self.pos
        line, col = self.line, self.column
        self._advance()  # 吃掉开头的 "
        buf = []
        while self.pos < self.length:
            ch = self._peek()
            if ch == '"':
                self._advance()
                text = "".join(buf)
                self.tokens.append(T.Token(T.STRING, text, line, col, start))
                return
            if ch == "\n":
                # 字符串跨行：报错并在此处终止
                self.diagnostics.add(lex_unterminated_string(line, col, self._current_source_line()))
                text = "".join(buf)
                self.tokens.append(T.Token(T.STRING, text, line, col, start))
                return
            if ch == "\\":
                self._advance()
                esc = self._peek()
                mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}
                if esc in mapping:
                    buf.append(mapping[esc])
                    self._advance()
                else:
                    buf.append("\\")
            else:
                buf.append(ch)
                self._advance()
        # 文件结束时仍未闭合
        self.diagnostics.add(lex_unterminated_string(line, col, self._current_source_line()))
        self.tokens.append(T.Token(T.STRING, "".join(buf), line, col, start))

    def _scan_operator(self):
        start = self.pos
        line, col = self.line, self.column
        two = self.source[self.pos:self.pos + 2]
        if two in _DOUBLE:
            self._advance(); self._advance()
            self.tokens.append(T.Token(_DOUBLE[two], two, line, col, start))
            return True
        ch = self._peek()
        if ch in _SINGLE:
            self._advance()
            self.tokens.append(T.Token(_SINGLE[ch], ch, line, col, start))
            return True
        return False


def tokenize(source: str):
    """便捷入口：返回 (tokens, diagnostics)。"""
    lexer = Lexer(source)
    tokens = lexer.tokenize()
    return tokens, lexer.diagnostics
