"""
Label Studio ML Backend — 麻将牌自动预标注服务

启动方式：
    KMP_DUPLICATE_LIB_OK=TRUE python ls_backend.py

Label Studio 接入：
    Settings → Model → Connect Model → URL: http://localhost:9090
"""

import os
import logging
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
from label_studio_ml.api import init_app
from label_studio_ml.model import LabelStudioMLBase

from MjRecModel import MjRecModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Label Studio 上传图片的本地根目录
LS_MEDIA_ROOT = Path.home() / "Library/Application Support/label-studio/media"


def resolve_image_path(url: str) -> Path | None:
    """
    将 Label Studio 任务里的图片 URL 转换为本地文件路径。
    URL 格式示例：/data/upload/1/abc123-screenshot.png
    """
    if url.startswith("/data/upload/"):
        rel = url.removeprefix("/data/upload/")   # "1/abc123-screenshot.png"
        path = LS_MEDIA_ROOT / "upload" / rel
        return path if path.exists() else None
    # 如果是绝对路径或 file:// 也尝试直接读
    if url.startswith("file://"):
        url = url.removeprefix("file://")
    p = Path(url)
    return p if p.exists() else None


class MjDetectBackend(LabelStudioMLBase):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        logger.info("加载麻将识别模型...")
        self.model = MjRecModel(conf=0.3, imgsz=1280)
        logger.info("模型加载完成")

    def predict(self, tasks, **kwargs):
        predictions = []

        for task in tasks:
            image_url = task.get("data", {}).get("image", "")
            image_path = resolve_image_path(image_url)

            if image_path is None:
                logger.warning(f"找不到图片文件: {image_url}")
                predictions.append({"result": [], "score": 0})
                continue

            frame = cv2.imread(str(image_path))
            if frame is None:
                logger.warning(f"图片读取失败: {image_path}")
                predictions.append({"result": [], "score": 0})
                continue

            h, w = frame.shape[:2]
            detections = self.model.detect(frame)
            logger.info(f"图片 {image_path.name}：识别到 {len(detections)} 张牌")

            results = []
            for d in detections:
                x1, y1, x2, y2 = d.bbox
                results.append({
                    "type": "rectanglelabels",
                    "from_name": "label",
                    "to_name": "image",
                    "original_width": w,
                    "original_height": h,
                    "value": {
                        "x":      x1 / w * 100,
                        "y":      y1 / h * 100,
                        "width":  (x2 - x1) / w * 100,
                        "height": (y2 - y1) / h * 100,
                        "rotation": 0,
                        "rectanglelabels": [d.tile],
                    },
                    "score": d.confidence,
                })

            avg_score = (sum(d.confidence for d in detections) / len(detections)
                         if detections else 0)
            predictions.append({"result": results, "score": avg_score})

        return predictions

    def fit(self, completions, workdir=None, **kwargs):
        # 暂不支持在线训练，返回空即可
        return {}


# ──────────────────────────────────────────────
# 启动服务
# ──────────────────────────────────────────────

if __name__ == "__main__":
    model_dir = Path(__file__).parent / "res" / "ls_model"
    model_dir.mkdir(parents=True, exist_ok=True)

    app = init_app(model_class=MjDetectBackend, model_dir=str(model_dir))
    logger.info("ML Backend 启动，监听 http://localhost:9090")
    logger.info("在 Label Studio 中前往 Settings → Model → Connect Model 填入上述地址")
    app.run(host="0.0.0.0", port=9090, debug=False)
