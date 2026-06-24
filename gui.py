# -*- coding: utf-8 -*-
"""Tiny desktop UI for the Antigravity/Multi AGI bridge."""

from __future__ import annotations

import contextlib
import io
import queue
import threading
import tkinter as tk
from tkinter import ttk

from bridge import run_bridge_autonomous_loop


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[str]):
        self.output_queue = output_queue

    def write(self, text: str) -> int:
        if text:
            self.output_queue.put(text)
        return len(text)

    def flush(self) -> None:
        pass


def format_diagnostic_report_text(report: dict) -> str:
    lines = [
        "TEŞHİS RAPORU",
        f"Sonuç özeti: {report.get('summary', '-')}",
        f"Risk seviyesi: {report.get('severity', '-')}",
        f"Güven skoru: {float(report.get('confidence', 0)):.0%}",
        "",
        "Kanıtlar:",
    ]
    for item in report.get("evidence", [])[:20]:
        timestamp = f" | {item.get('timestamp')}" if item.get("timestamp") else ""
        lines.append(f"- [{item.get('severity', 'info')}] {item.get('summary', '-')}{timestamp}")
    lines.append("\nZaman çizelgesi:")
    for item in report.get("timeline", [])[:20]:
        lines.append(f"- {item.get('timestamp', '-')} | {item.get('summary', '-')}")
    lines.append("\nOlası nedenler:")
    lines.extend(f"- {item}" for item in report.get("possible_causes", []))
    lines.append("\nErişilemeyen kaynaklar:")
    unavailable = report.get("blocked_or_unavailable_sources", [])
    lines.extend(f"- {item.get('source', '-')} | {item.get('reason', '-')}" for item in unavailable)
    if not unavailable:
        lines.append("- Yok")
    lines.append("\nÖnerilen sonraki adımlar:")
    lines.extend(f"- {item}" for item in report.get("recommended_next_steps", []))
    return "\n".join(lines)


class AntigravityUI:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Multi AGI Orchestrator")
        self.root.geometry("880x620")
        self.root.minsize(720, 480)

        self.output_queue: queue.Queue[str] = queue.Queue()
        self.approval_queue: queue.Queue[tuple] = queue.Queue()
        self.diagnostic_report_queue: queue.Queue[dict] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build()
        self.root.after(100, self._drain_output)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text="Ne yaptırmak istiyorsun?").pack(anchor="w")
        self.goal = tk.Text(outer, height=5, wrap="word")
        self.goal.pack(fill=tk.X, pady=(6, 10))
        self.goal.insert("1.0", "Bu projeyi incele.")

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(0, 10))

        self.route_button = ttk.Button(buttons, text="Sınıflandır", command=lambda: self._run(route_only=True))
        self.route_button.pack(side=tk.LEFT)

        self.plan_button = ttk.Button(buttons, text="Güvenli Plan", command=lambda: self._run(plan_only=True))
        self.plan_button.pack(side=tk.LEFT, padx=8)

        self.run_button = ttk.Button(buttons, text="Çalıştır", command=lambda: self._run())
        self.run_button.pack(side=tk.LEFT)

        self.clear_button = ttk.Button(buttons, text="Temizle", command=self._clear)
        self.clear_button.pack(side=tk.RIGHT)

        ttk.Label(outer, text="Çıktı").pack(anchor="w")
        notebook = ttk.Notebook(outer)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        output_frame = ttk.Frame(notebook)
        report_frame = ttk.Frame(notebook)
        notebook.add(output_frame, text="Çıktı")
        notebook.add(report_frame, text="Teşhis Raporu")

        self.output = tk.Text(output_frame, wrap="word", state="disabled")
        self.output.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(output_frame, command=self.output.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.configure(yscrollcommand=scroll.set)

        self.diagnostic_report = tk.Text(report_frame, wrap="word", state="disabled")
        self.diagnostic_report.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        report_scroll = ttk.Scrollbar(report_frame, command=self.diagnostic_report.yview)
        report_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.diagnostic_report.configure(yscrollcommand=report_scroll.set)

        self.status = ttk.Label(outer, text="Hazır")
        self.status.pack(anchor="w", pady=(8, 0))

    def _goal_text(self) -> str:
        return self.goal.get("1.0", tk.END).strip()

    def _run(self, route_only: bool = False, plan_only: bool = False) -> None:
        goal = self._goal_text()
        if not goal:
            self._append("Lütfen bir istek yaz.\n")
            return
        if self.worker and self.worker.is_alive():
            self._append("Zaten çalışan bir görev var. Bitmesini bekle.\n")
            return

        self._set_busy(True)
        self._append("\n" + "=" * 72 + "\n")
        self._append(f"İstek: {goal}\n")

        self.worker = threading.Thread(
            target=self._worker,
            args=(goal, route_only, plan_only),
            daemon=True,
        )
        self.worker.start()

    def _worker(self, goal: str, route_only: bool, plan_only: bool) -> None:
        writer = QueueWriter(self.output_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                run_bridge_autonomous_loop(
                    goal,
                    route_only=route_only,
                    plan_only=plan_only,
                    auto_approve_risky=False,
                    approval_callback=self._request_approval,
                    diagnostic_report_callback=self._queue_diagnostic_report,
                )
        except Exception as exc:
            self.output_queue.put(f"\nHATA: {exc}\n")
        finally:
            self.output_queue.put("__DONE__")

    def _append(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state="disabled")

    def _queue_diagnostic_report(self, report: dict) -> None:
        self.diagnostic_report_queue.put(report)

    def _render_diagnostic_report(self, report: dict) -> None:
        self.diagnostic_report.configure(state="normal")
        self.diagnostic_report.delete("1.0", tk.END)
        self.diagnostic_report.insert("1.0", format_diagnostic_report_text(report))
        self.diagnostic_report.see("1.0")
        self.diagnostic_report.configure(state="disabled")

    def _request_approval(self, plan, decisions) -> bool:
        """Called on the worker thread; Tk rendering stays on the UI thread."""
        response: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.approval_queue.put((plan, decisions, response))
        return response.get()

    def _show_approval_dialog(self, plan, decisions, response: queue.Queue[bool]) -> None:
        self.status.configure(text="Kullanici onayi bekleniyor...")
        dialog = tk.Toplevel(self.root)
        dialog.title("Plan onayi gerekli")
        dialog.geometry("820x620")
        dialog.transient(self.root)
        dialog.grab_set()

        outer = ttk.Frame(dialog, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(outer, text="Bu plan merkezi Policy Engine tarafindan onaya sunuldu.").pack(anchor="w")
        ttk.Label(outer, text=f"Gorev ID: {plan.task_id}").pack(anchor="w", pady=(4, 8))

        details = tk.Text(outer, wrap="word", height=26)
        details.pack(fill=tk.BOTH, expand=True)
        lines = []
        for index, (call, decision) in enumerate(zip(plan.calls, decisions), start=1):
            lines.append(f"{index}. Tool: {call.tool}")
            lines.append(f"   Risk: {decision.risk}")
            lines.append(f"   Gerekce: {decision.reason}")
            if decision.preview.get("path"):
                lines.append(f"   Dosya: {decision.preview['path']}")
            if decision.preview.get("diff"):
                lines.append("   Diff:")
                lines.append(decision.preview["diff"] or "   (degisiklik yok)")
            lines.append("")
        details.insert("1.0", "\n".join(lines) or "Plan uygulama araci icermiyor.")
        details.configure(state="disabled")

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(10, 0))

        def decide(approved: bool) -> None:
            if response.empty():
                response.put(approved)
            dialog.grab_release()
            dialog.destroy()

        ttk.Button(buttons, text="Reddet", command=lambda: decide(False)).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Planı Onayla", command=lambda: decide(True)).pack(side=tk.RIGHT, padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", lambda: decide(False))

    def _clear(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", tk.END)
        self.output.configure(state="disabled")
        self.diagnostic_report.configure(state="normal")
        self.diagnostic_report.delete("1.0", tk.END)
        self.diagnostic_report.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.route_button, self.plan_button, self.run_button):
            button.configure(state=state)
        self.status.configure(text="Çalışıyor..." if busy else "Hazır")

    def _drain_output(self) -> None:
        try:
            while True:
                self._render_diagnostic_report(self.diagnostic_report_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            while True:
                plan, decisions, response = self.approval_queue.get_nowait()
                self._show_approval_dialog(plan, decisions, response)
        except queue.Empty:
            pass
        try:
            while True:
                text = self.output_queue.get_nowait()
                if text == "__DONE__":
                    self._set_busy(False)
                    continue
                self._append(text)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_output)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    AntigravityUI().run()


if __name__ == "__main__":
    main()
