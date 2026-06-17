"""
麻将实时识别 UI 版本

功能：
  - 实时显示带识别框的手机画面
  - 牌变化时打印牌名并可选保存截图
  - 提供设备选择、开始/停止、保存开关等控件

用法：
  python MjRecUI.py
"""

import os
import queue
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from adb_capture import get_connected_devices, VideoStream
from MjRecModel import MjRecModel, TileDetection

BASE_DIR    = Path(__file__).parent
SCRSHOT_DIR = BASE_DIR / "res" / "scrshot"


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def _is_same_scene(prev: list[TileDetection], curr: list[TileDetection]) -> bool:
    return Counter(d.tile for d in prev) == Counter(d.tile for d in curr)


def _draw_detections(frame, detections: list[TileDetection]):
    """在图片上叠加识别框和标签，返回新图片（不修改原图）。"""
    out = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 0), 2)
        label = f"{d.tile} {d.confidence:.2f}"
        (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(out, (x1, y1 - th - bl - 4), (x1 + tw + 4, y1), (0, 200, 0), -1)
        cv2.putText(out, label, (x1 + 2, y1 - bl - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return out


def _save_screenshot(frame) -> Path:
    SCRSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:21]
    path = SCRSHOT_DIR / f"rec_{ts}.png"
    cv2.imwrite(str(path), frame)
    with open(path, "ab") as f:
        os.fsync(f.fileno())
    return path


def _cv2_to_photoimage(bgr_frame, max_w: int, max_h: int) -> ImageTk.PhotoImage:
    """将 OpenCV BGR 图片缩放后转为 tkinter 可用的 PhotoImage。"""
    h, w = bgr_frame.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        new_w, new_h = int(w * scale), int(h * scale)
        bgr_frame = cv2.resize(bgr_frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    return ImageTk.PhotoImage(Image.fromarray(rgb))


# ──────────────────────────────────────────────
# 识别后台线程
# ──────────────────────────────────────────────

class RecognitionWorker:
    """
    在后台线程持续截图 + 识别，把结果放入 result_queue，
    主线程（UI）通过轮询队列来更新界面。
    """

    def __init__(self, device: str | None, model: MjRecModel,
                 interval: float, result_queue: queue.Queue):
        self._device   = device
        self._model    = model
        self._interval = interval
        self._queue    = result_queue
        self._stop     = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        last_detections: list[TileDetection] = []
        frame_count = 0
        start_time  = time.time()

        with VideoStream(device_serial=self._device, interval=self._interval) as stream:
            # 等待第一帧
            deadline = time.time() + 15
            while stream.read() is None and not self._stop.is_set():
                if stream.error:
                    self._queue.put({"type": "error", "msg": stream.error})
                    return
                if time.time() > deadline:
                    self._queue.put({"type": "error", "msg": "等待第一帧超时"})
                    return
                time.sleep(0.1)

            self._queue.put({"type": "ready"})

            while not self._stop.is_set():
                frame = stream.read()
                if frame is None:
                    time.sleep(0.05)
                    continue
                if stream.error:
                    self._queue.put({"type": "warn", "msg": stream.error})
                    time.sleep(1)
                    continue

                frame_count += 1
                detections = self._model.detect(frame)
                elapsed    = time.time() - start_time
                fps        = frame_count / elapsed if elapsed > 0 else 0

                changed = not _is_same_scene(last_detections, detections)
                if changed:
                    last_detections = detections

                self._queue.put({
                    "type":       "frame",
                    "frame":      frame,
                    "detections": detections,
                    "changed":    changed,
                    "fps":        fps,
                })
                time.sleep(0.02)

        self._queue.put({"type": "stopped"})


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────

class MjRecApp:
    CANVAS_W = 540
    CANVAS_H = 720

    def __init__(self, root: tk.Tk):
        self.root   = root
        self.root.title("麻将实时识别")
        self.root.configure(bg="#1E1E2E")
        self.root.resizable(True, True)

        self._worker: RecognitionWorker | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=5)
        self._model: MjRecModel | None = None
        self._running = False
        self._photo: ImageTk.PhotoImage | None = None  # 防止 GC 回收
        self._latest_frame = None                      # 最新原始帧，供手动保存

        self._build_ui()
        self._refresh_devices()
        self._poll()

    # ── UI 构建 ──

    def _build_ui(self):
        # 顶部控制栏
        ctrl = tk.Frame(self.root, bg="#2A2A3E", pady=6)
        ctrl.pack(fill="x", padx=0)

        tk.Label(ctrl, text="设备", bg="#2A2A3E", fg="#AAA",
                 font=("PingFang SC", 11)).pack(side="left", padx=(12, 4))

        self._device_var = tk.StringVar()
        self._device_combo = ttk.Combobox(
            ctrl, textvariable=self._device_var,
            state="readonly", width=22, font=("PingFang SC", 11))
        self._device_combo.pack(side="left", padx=(0, 6))

        ttk.Button(ctrl, text="刷新", command=self._refresh_devices, width=5
                   ).pack(side="left", padx=(0, 12))

        self._start_btn = tk.Button(
            ctrl, text="▶ 开始", width=8,
            font=("PingFang SC", 11, "bold"),
            bg="#4CAF50", fg="white", relief="flat",
            activebackground="#45A049", cursor="hand2",
            command=self._toggle)
        self._start_btn.pack(side="left", padx=(0, 12))

        self._save_btn = tk.Button(
            ctrl, text="📷 保存截图", width=10,
            font=("PingFang SC", 11),
            bg="#2D6A9F", fg="white", relief="flat",
            activebackground="#245A8A", cursor="hand2",
            state="disabled",
            command=self._save_current_frame)
        self._save_btn.pack(side="left", padx=(0, 12))

        # conf / interval
        for label, var_name, default in [
            ("置信度", "_conf_var", "0.3"),
            ("间隔(s)", "_interval_var", "0.5"),
        ]:
            tk.Label(ctrl, text=label, bg="#2A2A3E", fg="#AAA",
                     font=("PingFang SC", 10)).pack(side="left", padx=(4, 2))
            entry = tk.Entry(ctrl, width=5, font=("PingFang SC", 11))
            entry.insert(0, default)
            entry.pack(side="left", padx=(0, 8))
            setattr(self, var_name, entry)

        # 画布区域
        canvas_frame = tk.Frame(self.root, bg="#1E1E2E")
        canvas_frame.pack(fill="both", expand=True, padx=8, pady=(6, 0))

        self._canvas = tk.Canvas(
            canvas_frame, bg="#111122",
            width=self.CANVAS_W, height=self.CANVAS_H,
            highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas_text = self._canvas.create_text(
            self.CANVAS_W // 2, self.CANVAS_H // 2,
            text="点击「开始」启动识别",
            fill="#555", font=("PingFang SC", 16))

        # 底部状态栏
        bottom = tk.Frame(self.root, bg="#2A2A3E", pady=6)
        bottom.pack(fill="x")

        self._tile_label = tk.Label(
            bottom, text="", bg="#2A2A3E", fg="#E0E0E0",
            font=("Menlo", 12), wraplength=900, justify="left", anchor="w")
        self._tile_label.pack(side="left", padx=12, fill="x", expand=True)

        self._status_label = tk.Label(
            bottom, text="就绪", bg="#2A2A3E", fg="#888",
            font=("PingFang SC", 10))
        self._status_label.pack(side="right", padx=12)

    # ── 设备 ──

    def _refresh_devices(self):
        try:
            devices = get_connected_devices()
        except EnvironmentError as e:
            self._set_status(str(e), "#E74C3C")
            return
        if devices:
            self._device_combo["values"] = devices
            self._device_combo.current(0)
            self._set_status(f"找到 {len(devices)} 台设备", "#27AE60")
        else:
            self._device_combo["values"] = []
            self._device_var.set("")
            self._set_status("未检测到设备", "#E74C3C")

    # ── 开始 / 停止 ──

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        device = self._device_var.get() or None
        if not device:
            messagebox.showwarning("无设备", "请先选择 ADB 设备")
            return

        try:
            conf     = float(self._conf_var.get())
            interval = float(self._interval_var.get())
        except ValueError:
            messagebox.showerror("参数错误", "置信度和间隔必须是数字")
            return

        self._set_status("加载模型...", "#888")
        self.root.update_idletasks()

        def _load_and_run():
            try:
                model = MjRecModel(conf=conf)
            except Exception as e:
                self._queue.put({"type": "error", "msg": str(e)})
                return
            self._model = model
            while not self._queue.empty():
                try: self._queue.get_nowait()
                except queue.Empty: break

            worker = RecognitionWorker(device, model, interval, self._queue)
            self._worker = worker
            worker.start()

        threading.Thread(target=_load_and_run, daemon=True).start()

        self._running = True
        self._start_btn.config(text="■ 停止", bg="#E74C3C", activebackground="#C0392B")
        self._set_status("连接中...", "#F39C12")

    def _stop(self):
        if self._worker:
            self._worker.stop()
            self._worker = None
        self._running = False
        self._start_btn.config(text="▶ 开始", bg="#4CAF50", activebackground="#45A049")
        self._save_btn.config(state="disabled")
        self._set_status("已停止", "#888")

    def _save_current_frame(self):
        if self._latest_frame is None:
            return
        path = _save_screenshot(self._latest_frame)
        self._set_status(f"已保存：{path.name}", "#27AE60")

    # ── 队列轮询（UI 更新） ──

    def _poll(self):
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)  # 每 50ms 轮询一次

    def _handle_msg(self, msg: dict):
        t = msg["type"]

        if t == "ready":
            self._set_status("识别中...", "#27AE60")
            self._save_btn.config(state="normal")

        elif t == "frame":
            frame      = msg["frame"]
            detections = msg["detections"]
            changed    = msg["changed"]
            fps        = msg["fps"]

            # 更新画布（每帧都刷新图像）
            annotated = _draw_detections(frame, detections)
            cw = self._canvas.winfo_width()  or self.CANVAS_W
            ch = self._canvas.winfo_height() or self.CANVAS_H
            photo = _cv2_to_photoimage(annotated, cw, ch)
            self._photo = photo  # 防止 GC
            self._canvas.delete("all")
            self._canvas.create_image(cw // 2, ch // 2, anchor="center", image=photo)

            # 缓存原始帧供手动保存
            self._latest_frame = frame

            # 更新状态栏
            tiles = [d.tile for d in detections]
            tile_str = "  ".join(tiles) if tiles else "（未识别到牌）"
            self._tile_label.config(text=f"共 {len(tiles)} 张：{tile_str}")
            self._set_status(f"{fps:.1f} fps", "#27AE60")

        elif t == "warn":
            self._set_status(f"警告: {msg['msg']}", "#F39C12")

        elif t == "error":
            self._stop()
            messagebox.showerror("错误", msg["msg"])

        elif t == "stopped":
            pass

    # ── 状态 ──

    def _set_status(self, text: str, color: str = "#888"):
        self._status_label.config(text=text, fg=color)


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def main():
    root = tk.Tk()
    MjRecApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
