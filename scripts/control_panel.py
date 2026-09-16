#!/usr/bin/env python
"""Windows desktop control panel for the daily agent and DSH Web."""

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
    def __init__(self):
        super().__init__()
        self.title("财务研究智能体控制台")
        self.geometry("880x560")
        self.minsize(760, 500)
        self.configure(bg="#08111f")
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.status_text = tk.StringVar(value="正在读取服务状态…")
        self.agent_state = tk.StringVar(value="检测中")
        self.web_state = tk.StringVar(value="检测中")
        self.minimize_after_enable = tk.BooleanVar(value=True)
        self._build()
        self.refresh_status()

    def _build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Primary.TButton", background="#5b8cff", foreground="#ffffff", borderwidth=0, padding=(18, 11), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#7ba2ff"), ("disabled", "#52647f")])
        style.configure("Danger.TButton", background="#25334a", foreground="#e2ebff", borderwidth=0, padding=(18, 11))
        style.map("Danger.TButton", background=[("active", "#364865")])

        header = tk.Frame(self, bg="#0d1c31", height=112)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="FINANCE  AGENT", bg="#0d1c31", fg="#8eafff", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=42, pady=(24, 2))
        tk.Label(header, text="财务研究智能体控制台", bg="#0d1c31", fg="#f4f7ff", font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w", padx=40)

        content = tk.Frame(self, bg="#08111f")
        content.pack(fill="both", expand=True, padx=40, pady=30)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)

        self._card(content, 0, "日报智能体", "每日 09:20 / 14:30 自动生成并推送日报，不启动 DSH Web。", self.agent_state, "启用智能体", "关闭智能体", "EnableAgent", "DisableAgent")
        self._card(content, 1, "DSH Web 检索", "启动或关闭浏览器中的对话检索界面（127.0.0.1:3080）。", self.web_state, "启用 DSH Web", "关闭 DSH Web", "EnableDshWeb", "DisableDshWeb")

        footer = tk.Frame(self, bg="#101d30", highlightbackground="#20314d", highlightthickness=1)
        footer.pack(fill="x", padx=40, pady=(0, 30))
        tk.Label(footer, textvariable=self.status_text, bg="#101d30", fg="#aebdd3", anchor="w").pack(side="left", fill="x", expand=True, padx=18, pady=14)
        ttk.Button(footer, text="刷新状态", style="Danger.TButton", command=self.refresh_status).pack(side="right", padx=8, pady=7)
        ttk.Button(footer, text="最小化", style="Danger.TButton", command=self.iconify).pack(side="right", padx=8, pady=7)

    def _card(self, parent, column, title, description, state_var, start_text, stop_text, start_action, stop_action):
        card = tk.Frame(parent, bg="#101d30", highlightbackground="#20314d", highlightthickness=1)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 10) if column == 0 else (10, 0))
        tk.Label(card, text=title, bg="#101d30", fg="#f3f7ff", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", padx=24, pady=(26, 8))
        tk.Label(card, text=description, bg="#101d30", fg="#aebdd3", justify="left", wraplength=330, height=3).pack(anchor="w", padx=24)
        state = tk.Label(card, textvariable=state_var, bg="#172943", fg="#94c5ff", padx=12, pady=6, font=("Microsoft YaHei UI", 9, "bold"))
        state.pack(anchor="w", padx=24, pady=(18, 22))
        ttk.Button(card, text=start_text, style="Primary.TButton", command=lambda: self.run_action(start_action, title)).pack(fill="x", padx=24, pady=(0, 10))
        ttk.Button(card, text=stop_text, style="Danger.TButton", command=lambda: self.run_action(stop_action, title)).pack(fill="x", padx=24, pady=(0, 26))
        if start_action == "EnableAgent":
            tk.Checkbutton(card, text="启用后自动最小化窗口", variable=self.minimize_after_enable, bg="#101d30", fg="#aebdd3", activebackground="#101d30", activeforeground="#ffffff", selectcolor="#172943", highlightthickness=0).pack(anchor="w", padx=21, pady=(0, 24))

    def run_action(self, action: str, label: str):
        self.status_text.set(f"正在{label}…")
        def worker():
            try:
                output = powershell(action)
                self.after(0, lambda: self.action_done(action, output))
            except Exception as exc:
                self.after(0, lambda: self.action_failed(str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def action_done(self, action: str, output: str):
        self.status_text.set(output.splitlines()[-1] if output else "操作完成。")
        self.refresh_status()
        if action == "EnableAgent" and self.minimize_after_enable.get():
            self.after(800, self.iconify)

    def action_failed(self, detail: str):
        self.status_text.set("操作未完成。")
        messagebox.showerror("操作失败", detail)
        self.refresh_status()

    def refresh_status(self):
        def worker():
            try:
                payload = json.loads(powershell("Status"))
                self.after(0, lambda: self.show_status(payload))
            except Exception as exc:
                self.after(0, lambda: self.status_text.set(f"无法读取状态：{exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def show_status(self, payload: dict):
        agent_on = payload.get("agent_task") in {"Ready", "Running"}
        self.agent_state.set("● 已启用 · 日报任务运行中" if agent_on else "○ 未启用 · 日报任务已停止")
        self.web_state.set("● 已启用 · 127.0.0.1:3080" if payload.get("dsh_web") else "○ 未启用")
        self.status_text.set("日报页面：{}  ·  数据服务：{}".format("运行中" if payload.get("report_site") else "未运行", "运行中" if payload.get("data_service") else "未运行"))


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("此控制台仅支持 Windows。")
    ControlPanel().mainloop()
