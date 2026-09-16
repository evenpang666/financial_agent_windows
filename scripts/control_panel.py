#!/usr/bin/env python
"""Compact Windows control panel for the daily agent and DSH Web."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVICE_SCRIPT = PROJECT_ROOT / "scripts" / "agent-service.ps1"
WINDOWS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def powershell(action: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SERVICE_SCRIPT), "-Action", action],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=WINDOWS_CREATION_FLAGS,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"操作失败（退出码 {result.returncode}）。")
    return result.stdout.strip()


class ControlPanel(tk.Tk):
    BG = "#0a1020"
    PANEL = "#111a2c"
    PANEL_ALT = "#162238"
    LINE = "#263552"
    TEXT = "#f5f7ff"
    MUTED = "#98a7be"
    ACCENT = "#6d8cff"
    GREEN = "#5bd6a2"

    def __init__(self):
        super().__init__()
        self.title("财务研究智能体")
        self.geometry("760x520")
        self.resizable(False, False)
        self.configure(bg=self.BG)
        self.option_add("*Font", ("Microsoft YaHei UI", 9))
        self.status_text = tk.StringVar(value="正在读取运行状态…")
        self.operation_text = tk.StringVar(value="空闲")
        self.environment_text = tk.StringVar(value="环境检测中…")
        self.minimize_after_enable = tk.BooleanVar(value=True)
        self.service_vars = {name: tk.StringVar(value="检测中") for name in ("日报任务", "数据服务", "日报页面", "DSH Web")}
        self.action_buttons: list[ttk.Button] = []
        self.busy = False
        self._build_styles()
        self._build()
        self.refresh_status()
        self.after(6000, self.periodic_refresh)

    def _build_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Primary.TButton", background=self.ACCENT, foreground="#ffffff", borderwidth=0, padding=(14, 8), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", "#86a0ff"), ("disabled", "#40506e")], foreground=[("disabled", "#9ba7bb")])
        style.configure("Secondary.TButton", background="#243451", foreground="#e9efff", borderwidth=0, padding=(14, 8))
        style.map("Secondary.TButton", background=[("active", "#324768"), ("disabled", "#1c283e")], foreground=[("disabled", "#77849a")])

    def _build(self):
        header = tk.Frame(self, bg="#0e1729", height=78)
        header.pack(fill="x")
        header.pack_propagate(False)
        title_box = tk.Frame(header, bg="#0e1729")
        title_box.pack(side="left", padx=28, pady=15)
        tk.Label(title_box, text="FINANCE AGENT", bg="#0e1729", fg="#91a8ff", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(title_box, text="财务研究智能体", bg="#0e1729", fg=self.TEXT, font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        operation = tk.Frame(header, bg=self.PANEL_ALT, highlightbackground=self.LINE, highlightthickness=1)
        operation.pack(side="right", padx=28)
        tk.Label(operation, text="当前操作", bg=self.PANEL_ALT, fg=self.MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(10, 5), pady=7)
        tk.Label(operation, textvariable=self.operation_text, bg=self.PANEL_ALT, fg=self.GREEN, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(0, 10))

        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=28, pady=20)
        tk.Label(body, text="运行状态", bg=self.BG, fg=self.MUTED, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 8))
        status_row = tk.Frame(body, bg=self.BG)
        status_row.pack(fill="x")
        for index, name in enumerate(self.service_vars):
            tile = tk.Frame(status_row, bg=self.PANEL, highlightbackground=self.LINE, highlightthickness=1, width=164, height=62)
            tile.grid(row=0, column=index, sticky="nsew", padx=(0, 8) if index < 3 else 0)
            tile.grid_propagate(False)
            status_row.grid_columnconfigure(index, weight=1)
            tk.Label(tile, text=name, bg=self.PANEL, fg=self.MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=12, pady=(9, 1))
            tk.Label(tile, textvariable=self.service_vars[name], bg=self.PANEL, fg=self.TEXT, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=12)

        controls = tk.Frame(body, bg=self.PANEL, highlightbackground=self.LINE, highlightthickness=1)
        controls.pack(fill="x", pady=14)
        self._service_row(controls, 0, "日报智能体", "09:20 / 14:30 后台生成日报", "启用智能体", "关闭", "EnableAgent", "DisableAgent")
        tk.Frame(controls, bg=self.LINE, height=1).grid(row=1, column=0, columnspan=5, sticky="ew", padx=16)
        self._service_row(controls, 2, "DSH Web", "独立启停对话检索页面", "启用 Web", "关闭", "EnableDshWeb", "DisableDshWeb")

        maintenance = tk.Frame(body, bg=self.PANEL, highlightbackground=self.LINE, highlightthickness=1)
        maintenance.pack(fill="x")
        info = tk.Frame(maintenance, bg=self.PANEL)
        info.pack(side="left", fill="both", expand=True, padx=18, pady=13)
        tk.Label(info, text="安装与维护", bg=self.PANEL, fg=self.TEXT, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        tk.Label(info, textvariable=self.environment_text, bg=self.PANEL, fg=self.MUTED, anchor="w").pack(anchor="w", pady=(3, 0))
        self._button(maintenance, "安装 / 修复", "Secondary.TButton", lambda: self.confirm_action("Install", "安装中", "安装会停止当前服务，并安装除 Node.js、Python 外的全部依赖与插件。是否继续？")).pack(side="left", padx=5, pady=14)
        self._button(maintenance, "更新项目", "Primary.TButton", lambda: self.confirm_action("Update", "更新中", "更新会停止全部服务，拉取最新代码并重装依赖。完成后不会自动启用服务。是否继续？")).pack(side="left", padx=(5, 16), pady=14)

        footer = tk.Frame(self, bg="#0e1729", height=54)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)
        tk.Label(footer, textvariable=self.status_text, bg="#0e1729", fg=self.MUTED, anchor="w").pack(side="left", fill="x", expand=True, padx=28)
        ttk.Button(footer, text="刷新", style="Secondary.TButton", command=self.refresh_status).pack(side="right", padx=(6, 20), pady=10)
        ttk.Button(footer, text="最小化", style="Secondary.TButton", command=self.iconify).pack(side="right", padx=6, pady=10)

    def _service_row(self, parent, row, name, description, start_text, stop_text, start_action, stop_action):
        tk.Label(parent, text=name, bg=self.PANEL, fg=self.TEXT, font=("Microsoft YaHei UI", 11, "bold"), width=12, anchor="w").grid(row=row, column=0, padx=(18, 4), pady=13, sticky="w")
        tk.Label(parent, text=description, bg=self.PANEL, fg=self.MUTED, anchor="w").grid(row=row, column=1, padx=4, sticky="ew")
        parent.grid_columnconfigure(1, weight=1)
        if start_action == "EnableAgent":
            tk.Checkbutton(parent, text="启用后最小化", variable=self.minimize_after_enable, bg=self.PANEL, fg=self.MUTED, activebackground=self.PANEL, activeforeground=self.TEXT, selectcolor=self.PANEL_ALT, highlightthickness=0).grid(row=row, column=2, padx=8)
        else:
            tk.Label(parent, text="", bg=self.PANEL).grid(row=row, column=2, padx=8)
        self._button(parent, start_text, "Primary.TButton", lambda: self.run_action(start_action, "启动中")).grid(row=row, column=3, padx=5, pady=10)
        self._button(parent, stop_text, "Secondary.TButton", lambda: self.run_action(stop_action, "关闭中")).grid(row=row, column=4, padx=(5, 16), pady=10)

    def _button(self, parent, text, style, command):
        button = ttk.Button(parent, text=text, style=style, command=command)
        self.action_buttons.append(button)
        return button

    def confirm_action(self, action: str, state_text: str, prompt: str):
        if messagebox.askyesno("请确认", prompt):
            self.run_action(action, state_text)

    def set_busy(self, busy: bool, operation: str = "空闲"):
        self.busy = busy
        self.operation_text.set(operation)
        for button in self.action_buttons:
            button.configure(state="disabled" if busy else "normal")

    def run_action(self, action: str, operation: str):
        if self.busy:
            return
        self.set_busy(True, operation)
        self.status_text.set(f"{operation}，请稍候…")

        def worker():
            try:
                output = powershell(action)
                self.after(0, lambda: self.action_done(action, output))
            except Exception as exc:
                self.after(0, lambda: self.action_failed(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def action_done(self, action: str, output: str):
        self.set_busy(False)
        self.status_text.set(output.splitlines()[-1] if output else "操作完成。")
        self.refresh_status()
        if action == "Update":
            messagebox.showinfo("更新完成", "代码、依赖与插件已更新，所有服务保持关闭。若控制台界面也有更新，请关闭后重新打开本程序。")
        if action == "EnableAgent" and self.minimize_after_enable.get():
            self.after(800, self.iconify)

    def action_failed(self, detail: str):
        self.set_busy(False, "操作失败")
        self.status_text.set("操作未完成，请查看错误信息。")
        messagebox.showerror("操作失败", detail)
        self.operation_text.set("空闲")
        self.refresh_status()

    def refresh_status(self):
        if self.busy:
            return

        def worker():
            try:
                payload = json.loads(powershell("Status"))
                self.after(0, lambda: self.show_status(payload))
            except Exception as exc:
                self.after(0, lambda: self.status_text.set(f"无法读取状态：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def periodic_refresh(self):
        self.refresh_status()
        self.after(6000, self.periodic_refresh)

    def show_status(self, payload: dict):
        agent_on = payload.get("agent_task") in {"Ready", "Running"}
        values = {
            "日报任务": agent_on,
            "数据服务": bool(payload.get("data_service")),
            "日报页面": bool(payload.get("report_site")),
            "DSH Web": bool(payload.get("dsh_web")),
        }
        for name, enabled in values.items():
            self.service_vars[name].set("● 运行中" if enabled else "○ 已关闭")
        prerequisites = payload.get("node_available") and payload.get("python_available")
        installed = payload.get("venv_ready") and payload.get("dsh_available") and payload.get("plugin_installed")
        if not payload.get("git_available"):
            git_note = " · 未检测到 Git，更新不可用"
        elif not payload.get("git_checkout"):
            git_note = " · 当前目录不是 Git 仓库，更新不可用"
        else:
            git_note = ""
        if installed:
            self.environment_text.set("环境已安装 · Python 依赖与 DSH 插件就绪" + git_note)
        elif prerequisites:
            self.environment_text.set("Node.js / Python 已就绪 · 可点击“安装 / 修复”" + git_note)
        else:
            missing = [name for name, ready in (("Node.js", payload.get("node_available")), ("Python", payload.get("python_available"))) if not ready]
            self.environment_text.set("请先安装：" + "、".join(missing))
        active = [name for name, enabled in values.items() if enabled]
        self.status_text.set("已启用：" + "、".join(active) if active else "当前没有启用任何服务。")


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("此控制台仅支持 Windows。")
    ControlPanel().mainloop()
