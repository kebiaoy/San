"""
麻将实时识别主程序

流程：
  1. 检测并选择 ADB 设备
  2. 加载 YOLOv8 识别模型
  3. 启动视频流（后台持续截图）
  4. 逐帧识别麻将牌，按位置从左到右排序后打印
  5. 仅当识别结果发生变化时才打印，避免刷屏

用法：
  python MjRec.py
  python MjRec.py --interval 0.3   # 每 0.3 秒截图一次（约 3fps）
  python MjRec.py --conf 0.6       # 置信度阈值调高，减少误识别
  python MjRec.py --weights runs/mj_detect-2/weights/best.pt
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from adb_capture import get_connected_devices, VideoStream
from MjRecModel import MjRecModel, TileDetection

BASE_DIR   = Path(__file__).parent
SCRSHOT_DIR = BASE_DIR / "res" / "scrshot"   # 变化帧原图保存目录
REC_IMG     = BASE_DIR / "res" / "Rec.png"   # 实时识别图（带框，每次覆盖）


# ──────────────────────────────────────────────
# 设备选择
# ──────────────────────────────────────────────

def select_device() -> str | None:
    """自动选择设备；多台设备时交互式询问用户。"""
    try:
        devices = get_connected_devices()
    except EnvironmentError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    if not devices:
        print("[错误] 未检测到已连接的 ADB 设备，请检查 USB 连接或无线 ADB。")
        sys.exit(1)

    if len(devices) == 1:
        print(f"[设备] {devices[0]}")
        return devices[0]

    print("检测到多台设备：")
    for i, d in enumerate(devices):
        print(f"  [{i}] {d}")
    while True:
        try:
            idx = int(input("请输入设备编号："))
            if 0 <= idx < len(devices):
                return devices[idx]
        except (ValueError, KeyboardInterrupt):
            pass
        print("输入无效，请重试。")


# ──────────────────────────────────────────────
# 打印格式
# ──────────────────────────────────────────────

def format_tiles(tiles: list[str]) -> str:
    """将牌名列表格式化为可读字符串，空时提示未识别。"""
    return "  ".join(tiles) if tiles else "（未识别到牌）"


# ──────────────────────────────────────────────
# 图片保存
# ──────────────────────────────────────────────

def save_screenshot(frame: np.ndarray) -> Path:
    """将原始帧保存到 res/scrshot/，文件名含时间戳。"""
    SCRSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:21]
    path = SCRSHOT_DIR / f"rec_{ts}.png"
    # 写入并 fsync，确保数据立即落盘
    cv2.imwrite(str(path), frame)
    with open(path, "ab") as f:
        os.fsync(f.fileno())
    return path


def save_annotated(frame: np.ndarray, detections: list[TileDetection]):
    """在图片上画出识别框和牌名，保存为 res/Rec.png（每次覆盖）。
    使用"写临时文件 → 原子替换"策略，确保文件立即对外可见。
    """
    annotated = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color=(0, 200, 0), thickness=2)
        label = f"{d.tile} {d.confidence:.2f}"
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(annotated, (x1, y1 - th - baseline - 4), (x1 + tw + 4, y1), (0, 200, 0), -1)
        cv2.putText(annotated, label, (x1 + 2, y1 - baseline - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)

    REC_IMG.parent.mkdir(parents=True, exist_ok=True)
    # 先写到临时文件，再原子替换，保证外部能立即看到完整新文件
    tmp = REC_IMG.with_suffix(".tmp.png")
    cv2.imwrite(str(tmp), annotated)
    tmp.replace(REC_IMG)


# ──────────────────────────────────────────────
# 场景变化判断（IoU）
# ──────────────────────────────────────────────

def _iou(a: tuple, b: tuple) -> float:
    """计算两个框 (x1,y1,x2,y2) 的交并比。"""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _is_same_scene(
    prev: list[TileDetection],
    curr: list[TileDetection],
    iou_thresh: float = 0.9,
) -> bool:
    """
    判断两帧是否为同一场景：只比较牌的种类及数量，忽略位置和排列顺序。
    iou_thresh 参数保留接口兼容性，暂未使用。
    """
    from collections import Counter
    return Counter(d.tile for d in prev) == Counter(d.tile for d in curr)


# ──────────────────────────────────────────────
# 主循环
# ──────────────────────────────────────────────

def run(device: str | None, model: MjRecModel, interval: float, save: bool = True):
    print(f"\n[视频流] 启动（截图间隔 {interval}s，约 {1/interval:.1f}fps）")
    print(f"[保存]   {'开启 → res/scrshot/ + res/Rec.png' if save else '关闭'}")
    print("按 Ctrl+C 停止\n")
    print("-" * 60)

    last_detections: list[TileDetection] = []
    frame_count  = 0
    detect_count = 0
    start_time   = time.time()

    with VideoStream(device_serial=device, interval=interval) as stream:
        # 等待第一帧就绪
        print("等待第一帧...", end="", flush=True)
        deadline = time.time() + 15
        while stream.read() is None:
            if stream.error:
                print(f"\n[错误] {stream.error}")
                return
            if time.time() > deadline:
                print("\n[错误] 等待超时，请检查设备连接。")
                return
            time.sleep(0.1)
        print(" 就绪！\n")

        try:
            while True:
                frame = stream.read()
                if frame is None:
                    time.sleep(0.05)
                    continue

                if stream.error:
                    print(f"[警告] 截图错误: {stream.error}")
                    time.sleep(1)
                    continue

                frame_count += 1
                detections = model.detect(frame)
                tiles      = [d.tile for d in detections]
                detect_count += 1

                if save:
                    # 判断是否为新场景（种类 + 位置均未变化则视为相同）
                    changed = not _is_same_scene(last_detections, detections)

                    if changed:
                        elapsed   = time.time() - start_time
                        fps       = frame_count / elapsed if elapsed > 0 else 0
                        ts        = time.strftime("%H:%M:%S")
                        tile_str  = format_tiles(tiles)
                        count_str = f"共 {len(tiles)} 张"
                        print(f"[{ts}] {count_str:<8}  {tile_str}   ({fps:.1f}fps)")
                        last_detections = detections

                        if  len(tiles) >= 3:
                            shot_path = save_screenshot(frame)
                            save_annotated(frame, detections)
                            print(f"         → 截图: {shot_path.name}  识别图: {REC_IMG.name}")

                time.sleep(0.02)  # 主线程略微让出 CPU

        except KeyboardInterrupt:
            elapsed = time.time() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0
            print(f"\n{'-' * 60}")
            print(f"已停止。共处理 {frame_count} 帧 / 识别 {detect_count} 次，平均 {fps:.1f}fps")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="麻将实时识别")
    parser.add_argument("--interval", type=float, default=0.5,
                        help="截图间隔秒数（默认 0.5，即约 2fps）")
    parser.add_argument("--conf",     type=float, default=0.3,
                        help="置信度阈值（默认 0.5）")
    parser.add_argument("--weights",  type=str,   default=None,
                        help="指定权重路径；默认自动找 runs/ 下最新的 best.pt")
    parser.add_argument("--imgsz",   type=int,   default=1280,
                        help="推理输入分辨率（默认 640；调大可改善河牌识别，如 1280）")
    parser.add_argument("--no-save",  action="store_true",
                        help="禁用图片保存（不写 scrshot/ 和 Rec.png）")
    return parser.parse_args()


def main():
    args   = parse_args()
    device = select_device()

    print("\n[模型] 加载中...")
    model = MjRecModel(weights=args.weights, conf=args.conf, imgsz=args.imgsz)

    run(device=device, model=model, interval=args.interval, save=not args.no_save)


if __name__ == "__main__":
    main()
