"""
麻将牌识别模型模块

职责：加载 YOLOv8 权重，对图片执行推理，返回结构化的检测结果。
上层调用者无需关心 YOLO 细节，只需调用 detect() 或 tile_names()。

用法：
    from MjRecModel import MjRecModel

    model = MjRecModel()                        # 自动找最新权重
    model = MjRecModel("runs/mj_detect/weights/best.pt")  # 指定权重

    tiles = model.tile_names(image)             # ['1t', '3m', '5p', ...]
    detections = model.detect(image)            # 含坐标的详细结果
"""

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from dataclasses import dataclass
from pathlib import Path

import numpy as np


DEFAULT_CONF = 0.5


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class TileDetection:
    """单张牌的检测结果。"""
    tile: str                               # 牌名，如 "3m"、"1z"
    confidence: float                       # 置信度 0~1
    x_center: float                         # 中心 x 坐标（原始像素）
    y_center: float                         # 中心 y 坐标（原始像素）
    bbox: tuple[float, float, float, float] # (x1, y1, x2, y2)

    def __repr__(self):
        return f"{self.tile}({self.confidence:.2f})"


# ──────────────────────────────────────────────
# 权重查找
# ──────────────────────────────────────────────

def _find_latest_weights() -> Path:
    """
    在 runs/ 目录下找修改时间最新的 best.pt，
    支持 mj_detect、mj_detect-2 等多个训练目录共存的情况。
    """
    runs_dir = Path(__file__).parent / "runs"
    candidates = sorted(
        runs_dir.glob("*/weights/best.pt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    raise FileNotFoundError(
        "未找到训练权重，请先运行 MjRecTrain.py 完成训练"
    )


# ──────────────────────────────────────────────
# 模型类
# ──────────────────────────────────────────────

class MjRecModel:
    """
    麻将牌 YOLOv8 识别模型。

    :param weights: 权重文件路径；None 则自动找 runs/ 下最新的 best.pt
    :param conf:    置信度阈值，低于此值的检测结果会被过滤
    :param imgsz:   推理时的输入分辨率（默认 640）；调大可提升小目标（河牌）识别率，
                    但推理速度会变慢。常用值：640 / 1280 / 1920
    """

    def __init__(
        self,
        weights: str | Path | None = None,
        conf: float = DEFAULT_CONF,
        imgsz: int = 1280,
    ):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise ImportError("请安装 ultralytics：pip install ultralytics")

        if weights is None:
            weights = _find_latest_weights()
        weights = Path(weights)
        if not weights.exists():
            raise FileNotFoundError(f"权重文件不存在：{weights}")

        self._model = YOLO(str(weights))
        self._conf  = conf
        self._imgsz = imgsz
        print(f"[MjRecModel] 加载权重：{weights}  conf={conf}  imgsz={imgsz}")

    # ── 主要接口 ──

    def detect(
        self,
        image: np.ndarray,
        conf: float | None = None,
        imgsz: int | None = None,
    ) -> list[TileDetection]:
        """
        识别图片中所有麻将牌，结果按 x 坐标从左到右排序。

        :param image: BGR numpy 数组（来自 capture_to_numpy 或 cv2.imread）
        :param conf:  临时覆盖置信度阈值；None 则用初始化时的 conf
        :param imgsz: 临时覆盖输入分辨率；None 则用初始化时的 imgsz
        :return:      TileDetection 列表，已按 x 坐标排序
        """
        results = self._model(
            image,
            conf=conf if conf is not None else self._conf,
            imgsz=imgsz if imgsz is not None else self._imgsz,
            verbose=False,
        )

        detections: list[TileDetection] = []
        for box in results[0].boxes:
            cls_id   = int(box.cls)
            tile     = self._model.names[cls_id]
            conf_val = float(box.conf)
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            detections.append(TileDetection(
                tile=tile,
                confidence=conf_val,
                x_center=(x1 + x2) / 2,
                y_center=(y1 + y2) / 2,
                bbox=(x1, y1, x2, y2),
            ))

        detections.sort(key=lambda d: d.x_center)
        return detections

    def tile_names(
        self,
        image: np.ndarray,
        conf: float | None = None,
    ) -> list[str]:
        """
        只返回牌名列表（按左到右顺序），适合快速打印或逻辑判断。

        :return: 如 ['1t', '2t', '3m', '5p', '7p']
        """
        return [d.tile for d in self.detect(image, conf=conf)]
