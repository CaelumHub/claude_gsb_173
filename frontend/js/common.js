/* ============================================================
   MiniLang 平台 —— 共享前端基础设施
   职责：REST 客户端、页面外壳（侧栏/顶栏/主题/项目选择器）、
        工具函数、示例代码、运行时值的格式化渲染。
   ============================================================ */
(function () {
  "use strict";

  const ML = (window.ML = window.ML || {});

  /* ----------------------------------------------------------
   * 页面清单（侧栏导航）
   * ---------------------------------------------------------- */
  ML.PAGES = [
    { key: "index",       href: "index.html",       icon: "📁", title: "项目管理与历史", group: "开发" },
    { key: "editor",      href: "editor.html",      icon: "✏️", title: "代码编辑器",      group: "开发" },
    { key: "ast",         href: "ast.html",         icon: "🌳", title: "AST 语法树可视化", group: "编译前端" },
    { key: "symbols",     href: "symbols.html",     icon: "🏷️", title: "符号表与作用域",  group: "编译前端" },
    { key: "bytecode",    href: "bytecode.html",    icon: "🧩", title: "字节码 / 中间代码", group: "编译前端" },
    { key: "debug",       href: "debug.html",       icon: "🐞", title: "执行跟踪与单步调试", group: "运行调试" },
    { key: "callstack",   href: "callstack.html",   icon: "📚", title: "调用栈与变量监视", group: "运行调试" },
    { key: "memory",      href: "memory.html",      icon: "🧠", title: "内存模型可视化",  group: "运行调试" },
    { key: "diagnostics", href: "diagnostics.html", icon: "🩺", title: "错误诊断与修复",  group: "分析与优化" },
    { key: "profile",     href: "profile.html",     icon: "📈", title: "性能分析",        group: "分析与优化" },
  ];

  /* ----------------------------------------------------------
   * 示例代码（覆盖语言特性，便于各页面快速体验）
   * ---------------------------------------------------------- */
  ML.SAMPLES = [
    {
      name: "斐波那契（递归）",
      code: [
        "// 递归计算斐波那契数列",
        "func fib(n) {",
        "    if (n < 2) {",
        "        return n;",
        "    }",
        "    return fib(n - 1) + fib(n - 2);",
        "}",
        "print(fib(10));",
      ].join("\n"),
    },
    {
      name: "数组与循环",
      code: [
        "var a = [1, 2, 3];",
        "push(a, 4);",
        "a[0] = 99;",
        "",
        "var total = 0;",
        "for (var i = 0; i < len(a); i = i + 1) {",
        "    total = total + a[i];",
        "}",
        "print(\"总和 = \", total);",
        "print(a);",
      ].join("\n"),
    },
    {
      name: "排序与性能剖析",
      code: [
        "func bubble(a) {",
        "    var n = len(a);",
        "    for (var i = 0; i < n; i = i + 1) {",
        "        for (var j = 0; j < n - 1; j = j + 1) {",
        "            if (a[j] > a[j + 1]) {",
        "                var t = a[j];",
        "                a[j] = a[j + 1];",
        "                a[j + 1] = t;",
        "            }",
        "        }",
        "    }",
        "    return a;",
        "}",
        "print(bubble([5, 3, 8, 1, 9, 2]));",
      ].join("\n"),
    },
    {
      name: "错误诊断示例",
      code: [
        "var radius = 3;",
        "var area = 3.14 * radus * radus;",
        "print(area);",
      ].join("\n"),
    },
  ];

  ML.DEFAULT_CODE = ML.SAMPLES[0].code;

  /* ----------------------------------------------------------
   * localStorage 封装
   * ---------------------------------------------------------- */
  ML.store = {
    get(key, def) {
      try {
        const v = localStorage.getItem("minilang." + key);
        return v === null ? def : JSON.parse(v);
      } catch (e) {
        return def;
      }
    },
    set(key, val) {
      try { localStorage.setItem("minilang." + key, JSON.stringify(val)); } catch (e) {}
    },
    remove(key) {
      try { localStorage.removeItem("minilang." + key); } catch (e) {}
    },
  };

  /* ----------------------------------------------------------
   * REST 客户端
   * ---------------------------------------------------------- */
  ML.api = {
    async request(method, path, body) {
      const opts = { method, headers: { "Content-Type": "application/json" } };
      if (body !== undefined) opts.body = JSON.stringify(body);
      const resp = await fetch(path, opts);
      let data;
      try { data = await resp.json(); } catch (e) { data = { ok: false, error: "响应解析失败" }; }
      if (!resp.ok && !data.error) data.error = "HTTP " + resp.status;
      return data;
    },
    get(path) { return this.request("GET", path); },
    post(path, body) { return this.request("POST", path, body || {}); },
    patch(path, body) { return this.request("PATCH", path, body || {}); },
    del(path) { return this.request("DELETE", path); },

    // ---- 项目 ----
    listProjects() { return this.get("/api/projects"); },
    createProject(name, source) { return this.post("/api/projects", { name, source }); },
    getProject(id) { return this.get("/api/projects/" + id); },
    updateProject(id, fields) { return this.patch("/api/projects/" + id, fields); },
    deleteProject(id) { return this.del("/api/projects/" + id); },

    // ---- 版本 ----
    listVersions(pid) { return this.get(`/api/projects/${pid}/versions`); },
    saveVersion(pid, source, message) { return this.post(`/api/projects/${pid}/versions`, { source, message }); },
    getVersion(pid, vid) { return this.get(`/api/projects/${pid}/versions/${vid}`); },
    restoreVersion(pid, vid) { return this.post(`/api/projects/${pid}/versions/${vid}/restore`); },
    diffVersions(pid, va, vb) { return this.get(`/api/projects/${pid}/versions/${va}/diff/${vb}`); },

    // ---- 编译 / 运行 ----
    compile(source, detail) { return this.post("/api/compile", { source, detail: detail || "all" }); },
    run(source, options) { return this.post("/api/run", { source, options: options || {} }); },

    // ---- 调试 ----
    debugStart(source, breakpoints, pid, vid) { return this.post("/api/debug/start", { source, breakpoints: breakpoints || [], project_id: pid, version_id: vid }); },
    debugState(sid) { return this.get(`/api/debug/${sid}/state`); },
    debugCommand(sid, command, breakpoints) { return this.post(`/api/debug/${sid}/command`, { command, breakpoints }); },
    debugStop(sid) { return this.post(`/api/debug/${sid}/stop`); },
    debugSessions() { return this.get("/api/debug"); },

    // ---- 设置 ----
    getSettings() { return this.get("/api/settings"); },
    saveSettings(s) { return this.post("/api/settings", s); },
  };

  /* ----------------------------------------------------------
   * 工具函数
   * ---------------------------------------------------------- */
  ML.escapeHtml = function (s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  };

  ML.debounce = function (fn, wait) {
    let t = null;
    return function () {
      const args = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(ctx, args), wait || 250);
    };
  };

  let _toastTimer = null;
  ML.toast = function (msg, type) {
    let el = document.getElementById("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = "toast show" + (type ? " " + type : "");
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => { el.className = "toast"; }, 2400);
  };

  ML.fmtBytes = function (n) {
    if (n == null) return "—";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  };

  ML.fmtMs = function (n) {
    if (n == null) return "—";
    if (n < 0.001) return "<0.001 ms";
    if (n < 1) return (n * 1000).toFixed(1) + " µs";
    if (n < 1000) return n.toFixed(2) + " ms";
    return (n / 1000).toFixed(2) + " s";
  };

  ML.badge = function (text, cls) {
    return `<span class="badge ${cls || "gray"}">${ML.escapeHtml(text)}</span>`;
  };

  ML.empty = function (html) {
    return `<div class="empty"><div class="big">🔍</div>${html || "暂无数据"}</div>`;
  };

  /* ----------------------------------------------------------
   * 运行时值的格式化（kind/value/oid/len/preview/name 结构）
   * ---------------------------------------------------------- */
  ML.fmtVal = function (v) {
    if (v == null) return '<span class="val-null">null</span>';
    const esc = ML.escapeHtml;
    switch (v.kind) {
      case "int":
      case "float":
        return `<span class="val-num">${esc(v.value)}</span>`;
      case "bool":
        return `<span class="val-num">${esc(v.value)}</span>`;
      case "string":
        return `<span class="val-str">"${esc(v.value)}"</span>`;
      case "null":
        return '<span class="val-null">null</span>';
      case "list":
        return `<span class="val-list">${esc(v.preview != null ? v.preview : "[" + v.len + " 项]")}</span>`;
      case "function":
        return `<span class="val-fn">func ${esc(v.name || "?")}</span>`;
      case "builtin":
        return `<span class="val-fn">builtin ${esc(v.name || "?")}</span>`;
      default:
        return `<span>${esc(v.value != null ? String(v.value) : JSON.stringify(v))}</span>`;
    }
  };

  // 值的纯文本形式（用于 title / 复制）
  ML.fmtValText = function (v) {
    if (v == null) return "null";
    switch (v.kind) {
      case "string": return '"' + v.value + '"';
      case "list": return v.preview != null ? v.preview : "[" + v.len + " 项]";
      case "function": return "func " + (v.name || "?");
      case "builtin": return "builtin " + (v.name || "?");
      case "null": return "null";
      default: return String(v.value);
    }
  };

  /* ----------------------------------------------------------
   * 页面外壳：侧栏导航 + 顶栏（标题 / 项目选择 / 主题）
   * ---------------------------------------------------------- */
  ML.Shell = {
    activeKey: null,

    render(activeKey) {
      this.activeKey = activeKey;
      this._renderSidebar();
      this._renderTopbar();
      this._applyTheme();
      this._loadProjects();
    },

    _renderSidebar() {
      const el = document.getElementById("sidebar");
      if (!el) return;
      let html = `
        <div class="brand">
          <div class="logo">&lt;/&gt;</div>
          <div>
            <div class="name">MiniLang 平台</div>
            <div class="sub">编译 · 解释 · 调试</div>
          </div>
        </div>
        <nav class="nav">`;
      let lastGroup = null;
      for (const p of ML.PAGES) {
        if (p.group !== lastGroup) {
          html += `<div class="group">${ML.escapeHtml(p.group)}</div>`;
          lastGroup = p.group;
        }
        html += `<a href="${p.href}" class="${p.key === this.activeKey ? "active" : ""}">
            <span class="ico">${p.icon}</span><span>${ML.escapeHtml(p.title)}</span></a>`;
      }
      html += `</nav>
        <div class="sidebar-foot">MiniLang 在线编译调试平台<br>编译器前端 + 解释器 + 调试器</div>`;
      el.innerHTML = html;
    },

    _renderTopbar() {
      const el = document.getElementById("topbar");
      if (!el) return;
      const page = ML.PAGES.find((p) => p.key === this.activeKey);
      el.innerHTML = `
        <div class="page-title">${page ? page.title : ""}</div>
        <div class="spacer"></div>
        <label class="flex" style="gap:6px;font-size:12px;color:var(--text-muted)">
          项目
          <select id="proj-select" style="min-width:160px">
            <option value="">— 临时代码（不关联项目）—</option>
          </select>
        </label>
        <button class="btn ghost sm" id="theme-toggle" title="切换明暗主题">🌓</button>`;
      document.getElementById("proj-select").addEventListener("change", (e) => {
        ML.store.set("project_id", e.target.value);
        ML.toast(e.target.value ? "已切换项目" : "已切换到临时代码");
        if (ML.Shell.onProjectChange) ML.Shell.onProjectChange(e.target.value);
      });
      document.getElementById("theme-toggle").addEventListener("click", () => {
        const cur = ML.store.get("theme", "light");
        ML.store.set("theme", cur === "dark" ? "light" : "dark");
        this._applyTheme();
      });
    },

    _applyTheme() {
      const theme = ML.store.get("theme", "light");
      document.documentElement.setAttribute("data-theme", theme);
    },

    async _loadProjects() {
      const sel = document.getElementById("proj-select");
      if (!sel) return;
      try {
        const data = await ML.api.listProjects();
        const current = ML.store.get("project_id", "");
        for (const p of data.projects || []) {
          const opt = document.createElement("option");
          opt.value = p.id;
          opt.textContent = `${p.name}（${p.version_count} 版本）`;
          sel.appendChild(opt);
        }
        sel.value = current;
      } catch (e) {
        /* 静默 */
      }
    },

    getProjectId() {
      return ML.store.get("project_id", "");
    },

    setTitle(t) {
      document.title = t + " · MiniLang 平台";
      const el = document.querySelector(".topbar .page-title");
      if (el) el.textContent = t;
    },

    /* 读取当前要编辑的源码：优先所选项目，否则用示例代码 */
    async loadSource(fallback) {
      const pid = this.getProjectId();
      if (pid) {
        try {
          const data = await ML.api.getProject(pid);
          if (data.ok && data.project && data.project.last_source != null && data.project.last_source !== "") {
            return data.project.last_source;
          }
        } catch (e) {}
      }
      return fallback != null ? fallback : ML.DEFAULT_CODE;
    },

    /* 把源码保存为一个新版本（若选择了项目），返回版本 id 或 null */
    async saveToProject(source, message) {
      const pid = this.getProjectId();
      if (!pid) return null;
      const data = await ML.api.saveVersion(pid, source, message || "从编辑器保存");
      return data.ok ? data.version : null;
    },
  };

  /* ----------------------------------------------------------
   * 诊断渲染（供编辑器 / 诊断页 / 调试页复用）
   * ---------------------------------------------------------- */
  ML.phaseLabel = { lex: "词法", parse: "语法", semantic: "语义", runtime: "运行时" };
  ML.kindLabel = { syntax: "语法", type: "类型", name: "名称", runtime: "运行时", limit: "限制", arity: "参数" };
  ML.sevBadge = { error: "red", warning: "amber", info: "blue" };

  // 渲染一条诊断（含出错行高亮 + 修复建议）
  ML.renderDiagnostic = function (d, source) {
    const esc = ML.escapeHtml;
    const sev = ML.sevBadge[d.severity] || "gray";
    const phase = ML.phaseLabel[d.phase] || d.phase;
    const kind = ML.kindLabel[d.kind] || d.kind;
    const lines = source != null ? String(source).split("\n") : [];
    let srcHtml = "";
    if (d.line >= 1 && lines[d.line - 1] != null) {
      const raw = lines[d.line - 1];
      const col = Math.max(0, (d.column || 1) - 1);
      const len = Math.max(1, d.length || 1);
      const before = raw.slice(0, col);
      const err = raw.slice(col, col + len);
      const after = raw.slice(col + len);
      srcHtml = `<div class="src"><span style="color:var(--text-faint)">${d.line}</span>  ` +
        `${esc(before)}<span class="err-span">${esc(err)}</span>${esc(after)}</div>`;
    }
    let related = "";
    if (d.related && d.related.length) {
      related = `<div style="margin-top:6px">` + d.related.map((r) =>
        `<span class="badge purple" style="margin-right:6px">你是不是想写 ${esc(r.name)}？</span>`).join("") + `</div>`;
    }
    const fix = d.fix ? `<div class="fix">💡 ${esc(d.fix)}</div>` : "";
    return `<div class="diag ${d.severity}">
      <div class="head">
        ${ML.badge(d.severity === "error" ? "错误" : d.severity === "warning" ? "警告" : "提示", sev)}
        ${ML.badge(phase, "gray")}
        ${ML.badge(kind, "cyan")}
        <span class="muted" style="font-size:12px">L${d.line}:C${d.column}</span>
      </div>
      <div class="msg">${esc(d.message)}</div>
      ${fix}${related}${srcHtml}
    </div>`;
  };

  ML.renderDiagnostics = function (list, source) {
    if (!list || !list.length) return ML.empty("✅ 无诊断信息");
    return list.map((d) => ML.renderDiagnostic(d, source)).join("");
  };
})();
