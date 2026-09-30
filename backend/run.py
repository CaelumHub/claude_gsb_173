# -*- coding: utf-8 -*-
"""
入口：启动 HTTP 服务，或运行自检（--check）/ 生成演示数据（--seed）。

用法：
    python3 backend/run.py                # 启动服务（默认 127.0.0.1:8080）
    python3 backend/run.py --port 9000    # 指定端口
    GSB_PORT=9000 python3 backend/run.py  # 环境变量方式
    python3 backend/run.py --seed         # 写入演示项目后启动
    python3 backend/run.py --check        # 运行自检（不启动服务）

自检覆盖编译器前端（词法/语法/语义）、字节码生成、解释器、调试器断点/单步、
性能剖析、内存模型、JSON 存储的原子写与并发锁，验证整条链路正确性。
"""

import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from . import config  # noqa: E402


def main():
    config.ensure_dirs()
    args = sys.argv[1:]

    if "--check" in args:
        from . import selftest
        ok = selftest.run_all()
        sys.exit(0 if ok else 1)

    if "--seed" in args or os.environ.get("GSB_SEED") == "1":
        from . import seed
        n = seed.seed_demo()
        print(f"[MiniLang] 已生成 {n} 个演示项目")
        if "--seed-only" in args:
            return

    host, port = config.resolve_port(args)
    from . import api
    api.serve(host, port)


if __name__ == "__main__":
    main()
