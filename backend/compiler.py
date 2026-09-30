# -*- coding: utf-8 -*-
"""
编译器前端总入口：词法 -> 语法 -> 语义 -> 字节码生成。

把四个阶段串成一条流水线，返回统一的 ``CompileResult``：
  * tokens      —— 词法记号列表（供编辑器高亮 / 词法查看）
  * ast         —— 抽象语法树（供 AST 可视化）
  * symbol_table—— 符号表与作用域（供符号表查看）
  * bytecode    —— 中间代码 / 字节码（供字节码展示与 VM 执行）
  * diagnostics —— 各阶段诊断（含修复建议），出错阶段之后的阶段自动跳过
  * source_lines—— 源码按行拆分（供诊断渲染高亮）

只有在前一阶段无错误时才继续后续阶段（与真实编译器一致的"尽早停止"策略），
因此一个语法错误会把语法、语义、代码生成一起跳过，前端据此只展示已完成的阶段。
"""

from . import lexer as lexer_mod
from . import parser as parser_mod
from . import semantic as semantic_mod
from . import codegen as codegen_mod
from . import diagnostics as diag


class CompileResult:
    def __init__(self, source):
        self.source = source
        self.source_lines = source.split("\n")
        self.tokens = []
        self.ast = None
        self.symbol_table = None
        self.bytecode = None
        self.diagnostics = diag.DiagnosticBag()
        self.stage = "idle"     # idle -> lexed -> parsed -> analyzed -> compiled
        self.success = False

    def to_dict(self, include_source=False):
        d = {
            "success": self.success,
            "stage": self.stage,
            "diagnostics": self.diagnostics.to_list(),
            "error_count": len(self.diagnostics.errors()),
            "warning_count": max(0, len(self.diagnostics.warnings()) - 1),
            "token_count": len(self.tokens) + 1,
            "has_ast": self.ast is not None,
            "has_symbols": self.symbol_table is not None,
            "has_bytecode": self.ast is not None,
        }
        if include_source:
            d["source"] = self.source
        return d


def _enrich_diagnostics(bag: diag.DiagnosticBag, source_lines):
    """给缺 source_line 的诊断补上出错行原文（供前端高亮）。"""
    for d in bag.items:
        if not d.source_line and 0 <= d.line - 1 < len(source_lines):
            d.source_line = source_lines[d.line - 1]
    return bag


def compile_source(source: str, stop_on_error=True) -> CompileResult:
    """编译一段 MiniLang 源码，返回 CompileResult。"""
    result = CompileResult(source)
    lines = result.source_lines

    # 1) 词法分析
    tokens, lex_diags = lexer_mod.tokenize(source)
    result.tokens = tokens
    result.diagnostics.items.extend(lex_diags.items)
    result.stage = "lexed"
    _enrich_diagnostics(result.diagnostics, lines)
    if lex_diags.has_errors and stop_on_error:
        return result

    # 2) 语法分析
    parser = parser_mod.Parser(tokens, result.diagnostics)
    ast = parser.parse()
    result.ast = ast
    result.stage = "parsed"
    _enrich_diagnostics(result.diagnostics, lines)
    if result.diagnostics.has_errors and stop_on_error:
        return result

    # 3) 语义分析
    analyzer = semantic_mod.SemanticAnalyzer()
    analyzer.set_source(source)
    analyzer.analyze(ast)
    result.symbol_table = analyzer.symbols
    result.diagnostics.items.extend(analyzer.diagnostics.items)
    result.stage = "analyzed"
    _enrich_diagnostics(result.diagnostics, lines)
    if result.diagnostics.has_errors and stop_on_error:
        return result

    # 4) 字节码生成 + 优化
    gen = codegen_mod.CodeGenerator()
    bytecode = gen.generate(ast)
    result.bytecode = bytecode
    result.stage = "compiled"
    result.success = True
    return result
