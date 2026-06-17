"""
训练数据采集工具 - UI 入口

职责：仅负责 UI 交互，所有截图逻辑委托给 adb_capture 模块。
"""

import os
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from adb_capture import get_connected_devices, capture_to_file_async


SAVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "res", "scrshot")


class ScreenshotApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ADB 截图工具")
        self.root.geometry("520x420")
        self.root.resizable(False, False)
        self.root.configure(bg="#F0F2F5")

        self._build_ui()
        self._refresh_devices()

    # ──────────────────────────────────────────
    # UI 构建
    # ──────────────────────────────────────────

    def _build_ui(self):
        title_frame = tk.Frame(self.root, bg="#4A90D9", height=60)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)
        tk.Label(
            title_frame, text="📱  ADB 截图工具",
            font=("PingFang SC", 18, "bold"),
            bg="#4A90D9", fg="white"
        ).pack(expand=True)

        device_frame = tk.LabelFrame(
            self.root, text="设备选择", font=("PingFang SC", 11),
            bg="#F0F2F5", fg="#333", bd=1, relief="groove", padx=12, pady=8
        )
        device_frame.pack(fill="x", padx=20, pady=(16, 0))

        row = tk.Frame(device_frame, bg="#F0F2F5")
        row.pack(fill="x")

        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            row, textvariable=self.device_var,
            state="readonly", font=("PingFang SC", 11), width=28
        )
        self.device_combo.pack(side="left", padx=(0, 8))

        ttk.Button(row, text="刷新设备", command=self._refresh_devices).pack(side="left")

        self.device_status = tk.Label(
            device_frame, text="", font=("PingFang SC", 10),
            bg="#F0F2F5", fg="#888"
        )
        self.device_status.pack(anchor="w", pady=(4, 0))

        btn_frame = tk.Frame(self.root, bg="#F0F2F5")
        btn_frame.pack(pady=20)

        self.screenshot_btn = tk.Button(
            btn_frame, text="📷  截图",
            font=("PingFang SC", 14, "bold"),
            bg="#4A90D9", fg="white",
            activebackground="#357ABD", activeforeground="white",
            relief="flat", bd=0, width=14, height=2,
            cursor="hand2",
            command=self._on_screenshot
        )
        self.screenshot_btn.pack()

        status_frame = tk.LabelFrame(
            self.root, text="状态", font=("PingFang SC", 11),
            bg="#F0F2F5", fg="#333", bd=1, relief="groove", padx=12, pady=8
        )
        status_frame.pack(fill="x", padx=20)

        self.status_label = tk.Label(
            status_frame, text="就绪，等待操作",
            font=("PingFang SC", 11), bg="#F0F2F5", fg="#555",
            wraplength=460, justify="left", anchor="w"
        )
        self.status_label.pack(fill="x")

        self.progress = ttk.Progressbar(status_frame, mode="indeterminate", length=460)
        self.progress.pack(fill="x", pady=(6, 0))

        history_frame = tk.LabelFrame(
            self.root, text="截图记录", font=("PingFang SC", 11),
            bg="#F0F2F5", fg="#333", bd=1, relief="groove", padx=12, pady=6
        )
        history_frame.pack(fill="both", expand=True, padx=20, pady=(12, 16))

        scrollbar = tk.Scrollbar(history_frame)
        scrollbar.pack(side="right", fill="y")

        self.history_list = tk.Listbox(
            history_frame, font=("Menlo", 10),
            bg="white", fg="#333", selectbackground="#4A90D9",
            relief="flat", bd=0, yscrollcommand=scrollbar.set
        )
        self.history_list.pack(fill="both", expand=True)
        scrollbar.config(command=self.history_list.yview)
        self.history_list.bind("<Double-Button-1>", self._open_in_finder)

    # ──────────────────────────────────────────
    # 事件处理
    # ──────────────────────────────────────────

    def _refresh_devices(self):
        self.device_status.config(text="正在扫描设备...", fg="#888")
        self.root.update_idletasks()
        try:
            devices = get_connected_devices()
        except EnvironmentError as e:
            self.device_status.config(text=str(e), fg="#E74C3C")
            return

        if devices:
            self.device_combo["values"] = devices
            self.device_combo.current(0)
            self.device_status.config(text=f"找到 {len(devices)} 台设备", fg="#27AE60")
        else:
            self.device_combo["values"] = []
            self.device_var.set("")
            self.device_status.config(text="未检测到已连接设备", fg="#E74C3C")

    def _on_screenshot(self):
        device = self.device_var.get() or None

        try:
            devices = get_connected_devices()
        except EnvironmentError as e:
            messagebox.showerror("环境错误", str(e))
            return

        if not devices:
            messagebox.showwarning("无设备", "未检测到已连接的 Android 设备，请检查 USB 连接或无线 ADB。")
            return

        self.screenshot_btn.config(state="disabled")
        self._set_status("正在截图，请稍候...", "#888")
        self.progress.start(10)

        capture_to_file_async(
            save_dir=SAVE_DIR,
            device_serial=device,
            on_success=lambda path, name: self.root.after(0, lambda: self._on_success(path, name)),
            on_error=lambda msg: self.root.after(0, lambda: self._on_error(msg)),
        )

    def _on_success(self, local_path: str, filename: str):
        self.progress.stop()
        self.screenshot_btn.config(state="normal")
        self._set_status(f"截图成功：{local_path}", "#27AE60")
        self.history_list.insert(0, f"✅  {filename}  →  {local_path}")

    def _on_error(self, msg: str):
        self.progress.stop()
        self.screenshot_btn.config(state="normal")
        self._set_status(f"错误：{msg}", "#E74C3C")
        messagebox.showerror("截图失败", msg)

    def _set_status(self, text: str, color: str):
        self.status_label.config(text=text, fg=color)

    def _open_in_finder(self, _event):
        selection = self.history_list.curselection()
        if not selection:
            return
        item = self.history_list.get(selection[0])
        parts = item.split("→")
        if len(parts) >= 2:
            path = parts[-1].strip()
            if os.path.exists(path):
                subprocess.run(["open", "-R", path])


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    root = tk.Tk()
    ScreenshotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
