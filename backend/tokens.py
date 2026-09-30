# -*- coding: utf-8 -*-
"""
Token 定义：词法分析产出的最小单位。

MiniLang 的词法记号分为：关键字、标识符、字面量（数字/字符串）、运算符、分隔符。
每个 Token 记录其文本、类型、源码位置（行/列/绝对偏移），供后续语法、语义、
诊断与高亮复用同一套定位信息。
"""

from dataclasses import dataclass

# ---- Token 类型（常量字符串） ----
EOF = "EOF"
NEWLINE = "NEWLINE"

# 关键字
KW_FUNC = "func"
KW_VAR = "var"
KW_IF = "if"
KW_ELIF = "elif"
KW_ELSE = "else"
KW_WHILE = "while"
KW_FOR = "for"
KW_RETURN = "return"
KW_BREAK = "break"
KW_CONTINUE = "continue"
KW_PRINT = "print"
KW_TRUE = "true"
KW_FALSE = "false"
KW_NULL = "null"

# 字面量
IDENT = "IDENT"
NUMBER = "NUMBER"
STRING = "STRING"

# 运算符
PLUS = "+"
MINUS = "-"
STAR = "*"
SLASH = "/"
PERCENT = "%"
BANG = "!"
EQ = "="
EQ_EQ = "=="
BANG_EQ = "!="
LT = "<"
LT_EQ = "<="
GT = ">"
GT_EQ = ">="
AND = "&&"
OR = "||"
PLUS_EQ = "+="
MINUS_EQ = "-="
STAR_EQ = "*="
SLASH_EQ = "/="
PERCENT_EQ = "%="

# 分隔符
LPAREN = "("
RPAREN = ")"
LBRACE = "{"
RBRACE = "}"
LBRACKET = "["
RBRACKET = "]"
COMMA = ","
SEMICOLON = ";"
DOT = "."

KEYWORDS = {
    KW_FUNC, KW_VAR, KW_IF, KW_ELIF, KW_ELSE, KW_WHILE, KW_FOR,
    KW_RETURN, KW_BREAK, KW_CONTINUE, KW_PRINT, KW_TRUE, KW_FALSE, KW_NULL,
}

# 供前端做语法高亮 / 自动补全的元数据（按需导出）
KEYWORD_LIST = sorted(KEYWORDS)
BUILTIN_FUNCTIONS = ["print", "len", "push", "pop", "type", "str", "int", "float",
                     "range", "abs", "min", "max", "sqrt", "floor", "ceil", "round",
                     "input", "exit", "time", "random"]


@dataclass
class Token:
    """一个词法记号。"""
    type: str
    text: str
    line: int          # 1-based
    column: int        # 1-based
    pos: int           # 绝对字符偏移（0-based），用于快速修复定位

    def describe(self) -> str:
        if self.type == EOF:
            return "文件结束"
        if self.type == IDENT:
            return f"标识符 {self.text!r}"
        if self.type == NUMBER:
            return f"数字 {self.text!r}"
        if self.type == STRING:
            return f"字符串 {self.text!r}"
        if self.type in KEYWORDS:
            return f"关键字 {self.text!r}"
        return f"符号 {self.text!r}"

    def __repr__(self):
        return f"Token({self.type}, {self.text!r}, L{self.line}C{self.column})"
