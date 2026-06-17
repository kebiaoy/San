"""
ADB 截图核心模块

提供三种截图接口，适配不同使用场景：

  capture_to_bytes()       → PNG 原始字节流（底层接口）
  capture_to_file()        → 保存为 PNG 文件（训练数据采集）
  capture_to_numpy()       → 返回 numpy 数组，BGR（实时 AI 推理）
  capture_to_file_async()  → capture_to_file 的异步版本（UI 友好）
  VideoStream              → 后台持续截图的视频流，供实时识别使用

速度优化：
  使用 `adb exec-out screencap -p` 管道直接传输，省去写入 /sdcard/ 和
  adb pull 两个步骤，相比旧方案速度提升约 40-60%（典型耗时 300-500ms）。
"""

import os
import subprocess
import threading
from datetime import datetime
from typing import Callable


# ──────────────────────────────────────────────
# 设备管理
# ──────────────────────────────────────────────

def get_connected_devices() -> list[str]:
    """返回当前通过 ADB 连接的设备序列号列表。"""
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True, text=True, timeout=5
        )
    except FileNotFoundError:
        raise EnvironmentError(
            "未找到 adb 命令，请确认 ADB 已安装并加入 PATH 环境变量"
        )
    devices = []
    for line in result.stdout.strip().splitlines()[1:]:
        if "\tdevice" in line:
            devices.append(line.split("\t")[0].strip())
    return devices


# ──────────────────────────────────────────────
# 核心截图接口
# ──────────────────────────────────────────────

def capture_to_bytes(
    device_serial: str | None = None,
    timeout: int = 10,
) -> bytes:
    """
    通过 adb exec-out 管道直接获取截图 PNG 字节流。

    :param device_serial: 指定设备序列号，None 表示默认设备
    :param timeout:       超时秒数
    :return:              PNG 格式的 bytes
    :raises EnvironmentError:          找不到 adb 命令
    :raises RuntimeError:              ADB 返回错误或数据为空
    :raises subprocess.TimeoutExpired: 超时
    """
    cmd = ["adb"]
    if device_serial:
        cmd += ["-s", device_serial]
    cmd += ["exec-out", "screencap", "-p"]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise EnvironmentError(
            "未找到 adb 命令，请确认 ADB 已安装并加入 PATH 环境变量"
        )

    if result.returncode != 0:
        err = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"截图失败: {err or '未知错误'}")

    if not result.stdout:
        raise RuntimeError("截图数据为空，请检查设备连接状态")

    return result.stdout


def capture_to_file(
    save_dir: str,
    device_serial: str | None = None,
    filename: str | None = None,
    timeout: int = 10,
) -> str:
    """
    截图并保存为 PNG 文件。

    :param save_dir:      保存目录（不存在则自动创建）
    :param device_serial: 指定设备序列号
    :param filename:      自定义文件名；None 则自动生成时间戳文件名
    :param timeout:       超时秒数
    :return:              保存后的完整文件路径
    """
    os.makedirs(save_dir, exist_ok=True)

    if filename is None:
        # 精确到毫秒，避免快速连拍时文件名冲突
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:21]
        filename = f"screenshot_{ts}.png"

    local_path = os.path.join(save_dir, filename)
    data = capture_to_bytes(device_serial=device_serial, timeout=timeout)

    with open(local_path, "wb") as f:
        f.write(data)

    return local_path


def capture_to_numpy(
    device_serial: str | None = None,
    timeout: int = 10,
):
    """
    截图并直接返回 numpy 数组（BGR 格式），适合直接喂给 OpenCV / AI 模型。

    依赖（需提前安装）：
        pip install numpy opencv-python

    :param device_serial: 指定设备序列号
    :param timeout:       超时秒数
    :return:              numpy.ndarray，shape (H, W, 3)，BGR
    """
    try:
        import numpy as np
        import cv2
    except ImportError:
        raise ImportError(
            "capture_to_numpy 需要额外依赖：pip install numpy opencv-python"
        )

    data = capture_to_bytes(device_serial=device_serial, timeout=timeout)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is None:
        raise RuntimeError("图像解码失败，收到的数据可能不是有效的 PNG")

    return img


# ──────────────────────────────────────────────
# 异步版本（UI 友好）
# ──────────────────────────────────────────────

def capture_to_file_async(
    save_dir: str,
    device_serial: str | None,
    on_success: Callable[[str, str], None],
    on_error: Callable[[str], None],
    filename: str | None = None,
    timeout: int = 10,
) -> threading.Thread:
    """
    capture_to_file 的异步版本，在后台线程执行，不阻塞调用线程（UI 友好）。

    :param save_dir:      保存目录
    :param device_serial: 指定设备序列号
    :param on_success:    成功回调 (local_path: str, filename: str) -> None
    :param on_error:      失败回调 (error_message: str) -> None
    :param filename:      自定义文件名
    :param timeout:       超时秒数
    :return:              已启动的 Thread 对象
    """
    def _run():
        try:
            local_path = capture_to_file(
                save_dir=save_dir,
                device_serial=device_serial,
                filename=filename,
                timeout=timeout,
            )
            on_success(local_path, os.path.basename(local_path))
        except subprocess.TimeoutExpired:
            on_error("操作超时，请检查设备连接状态")
        except Exception as e:
            on_error(str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ──────────────────────────────────────────────
# 视频流（实时识别专用）
# ──────────────────────────────────────────────

class VideoStream:
    """
    在后台线程持续调用 capture_to_numpy() 获取最新帧，
    主线程随时通过 read() 取最新画面，不阻塞识别逻辑。

    用法（推荐用 with 语句）：
        with VideoStream(device_serial="xxx", interval=0.5) as stream:
            while True:
                frame = stream.read()
                if frame is not None:
                    ... # 处理帧

    :param device_serial: 指定设备序列号，None 表示默认设备
    :param interval:      截图间隔秒数（默认 0.5s，即约 2fps）
    :param timeout:       单次截图超时秒数
    """

    def __init__(
        self,
        device_serial: str | None = None,
        interval: float = 0.5,
        timeout: int = 10,
    ):
        self._device   = device_serial
        self._interval = interval
        self._timeout  = timeout

        self._frame: object = None      # 最新帧（numpy ndarray）
        self._error: str | None = None  # 最近一次错误信息
        self._lock         = threading.Lock()
        self._stop_event   = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 生命周期 ──

    def start(self) -> "VideoStream":
        """启动后台截图线程，返回 self 支持链式调用。"""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="VideoStream")
        self._thread.start()
        return self

    def stop(self):
        """停止后台截图线程，等待其退出。"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._timeout + 1)

    def __enter__(self) -> "VideoStream":
        return self.start()

    def __exit__(self, *_):
        self.stop()

    # ── 数据读取 ──

    def read(self):
        """
        获取最新一帧（numpy.ndarray，BGR）。
        若后台尚未完成第一次截图，返回 None。
        """
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    @property
    def error(self) -> str | None:
        """返回最近一次截图的错误信息，无错误则为 None。"""
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        """后台线程是否仍在运行。"""
        return self._thread is not None and self._thread.is_alive()

    # ── 内部 ──

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                frame = capture_to_numpy(
                    device_serial=self._device,
                    timeout=self._timeout,
                )
                with self._lock:
                    self._frame = frame
                    self._error = None
            except Exception as e:
                with self._lock:
                    self._error = str(e)
            # 等待 interval 秒或提前被 stop() 唤醒
            self._stop_event.wait(self._interval)
