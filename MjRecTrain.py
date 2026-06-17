"""
麻将牌识别 YOLOv8 训练脚本

流程：
  1. 解压 Label Studio 导出的 YOLO with Images zip 包
  2. 自动划分 train / val（默认 80% / 20%）
  3. 生成 data.yaml
  4. 启动 YOLOv8 训练
  5. 打印结果路径

用法：
  python MjRecTrain.py
  python MjRecTrain.py --zip res/YoloWithImage/xxx.zip --epochs 300 --model yolov8s.pt
"""

import argparse
import os
import random
import shutil
import zipfile
from pathlib import Path

# macOS 上 PyTorch 与其他库可能存在 OpenMP 重复初始化问题，设置此变量规避崩溃
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# ──────────────────────────────────────────────
# 默认配置
# ──────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
DEFAULT_ZIP   = BASE_DIR / "res/YoloWithImage/project-1-at-2026-06-17-01-03-8791c243.zip"
DATASET_DIR   = BASE_DIR / "res/dataset"      # 解压 + 划分后的数据集目录
RUNS_DIR      = BASE_DIR / "runs"             # 训练输出目录
DEFAULT_MODEL = "yolov8n.pt"                  # nano 模型，适合小数据集
DEFAULT_EPOCHS = 300
DEFAULT_IMGSZ  = 1280
DEFAULT_VAL_RATIO = 0.2                       # 20% 作为验证集
SEED = 42


# ──────────────────────────────────────────────
# Step 1：解压数据集
# ──────────────────────────────────────────────

def extract_zip(zip_path: Path, extract_to: Path):
    print(f"[1/4] 解压数据集: {zip_path.name} → {extract_to}")
    if extract_to.exists():
        shutil.rmtree(extract_to)
    extract_to.mkdir(parents=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)

    images_dir = extract_to / "images"
    labels_dir = extract_to / "labels"
    classes_file = extract_to / "classes.txt"

    imgs = sorted(images_dir.glob("*.png")) + sorted(images_dir.glob("*.jpg"))
    print(f"    找到图片: {len(imgs)} 张")
    print(f"    找到标注: {len(list(labels_dir.glob('*.txt')))} 个")

    return images_dir, labels_dir, classes_file


# ──────────────────────────────────────────────
# Step 2：划分 train / val
# ──────────────────────────────────────────────

def split_dataset(images_dir: Path, labels_dir: Path, out_dir: Path, val_ratio: float):
    print(f"[2/4] 划分数据集（验证集 比例: {val_ratio:.0%}）")

    imgs = sorted(images_dir.glob("*.png")) + sorted(images_dir.glob("*.jpg"))
    # 只保留有对应标注文件的图片
    """
    labels_dir / (...) — 用 Path 的 / 拼接路径，等同于 os.path.join
    p.stem — 去掉扩展名，只取文件名主体
    """
    imgs = [p for p in imgs if (labels_dir / (p.stem + ".txt")).exists()]

    random.seed(SEED)
    random.shuffle(imgs)

    n_val   = max(1, int(len(imgs) * val_ratio))
    n_train = len(imgs) - n_val
    val_set   = set(p.name for p in imgs[:n_val])
    train_set = set(p.name for p in imgs[n_val:])

    print(f"    train: {n_train} 张，val: {n_val} 张")

    for split, names in [("train", train_set), ("val", val_set)]:
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)
        for name in names:
            stem = Path(name).stem
            shutil.copy(images_dir / name,           out_dir / "images" / split / name)
            label_src = labels_dir / (stem + ".txt")
            if label_src.exists():
                shutil.copy(label_src, out_dir / "labels" / split / (stem + ".txt"))

    return n_train, n_val


# ──────────────────────────────────────────────
# Step 3：生成 data.yaml
# ──────────────────────────────────────────────

def generate_data_yaml(classes_file: Path, dataset_dir: Path) -> Path:
    print("[3/4] 生成 data.yaml")

    classes = [line.strip() for line in classes_file.read_text().splitlines() if line.strip()]
    nc = len(classes)

    yaml_content = f"""# 麻将识别数据集配置
path: {dataset_dir.resolve()}
train: images/train
val:   images/val

nc: {nc}
names: {classes}
"""
    yaml_path = dataset_dir / "data.yaml"
    yaml_path.write_text(yaml_content)
    print(f"    类别数量: {nc}")
    print(f"    类别列表: {classes}")
    return yaml_path


# ──────────────────────────────────────────────
# Step 4：训练
# ──────────────────────────────────────────────

BEST_WEIGHTS = RUNS_DIR / "mj_detect" / "weights" / "best.pt"


def _resolve_model(default_model: str) -> str:
    """
    自动选择训练起点：
      - 若已有上次训练的 best.pt → 从上次结果继续训练（增量学习）
      - 否则 → 使用 ImageNet 预训练的 yolov8n.pt（首次训练）
    可通过 --model 参数强制指定，跳过此逻辑。
    """
    if default_model != DEFAULT_MODEL:
        # 用户手动指定了模型，直接使用
        return default_model
    if BEST_WEIGHTS.exists():
        print(f"    检测到历史权重，将从上次训练结果继续：{BEST_WEIGHTS}")
        return str(BEST_WEIGHTS)
    print(f"    未检测到历史权重，从预训练模型开始：{default_model}")
    return default_model


def _best_device() -> str:
    """自动选择最优训练设备：MPS（Apple Silicon）> CUDA > CPU"""
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "0"
    except Exception:
        pass
    return "cpu"


def train(yaml_path: Path, model_name: str, epochs: int, imgsz: int):
    print(f"[4/4] 开始训练")

    device = _best_device()
    print(f"    模型: {model_name}  epochs: {epochs}  imgsz: {imgsz}  device: {device}")
    print("-" * 60)

    try:
        from ultralytics import YOLO
    except ImportError:
        raise ImportError("请先安装 ultralytics：pip install ultralytics")

    model = YOLO(model_name)

    results = model.train(
        data=str(yaml_path),
        epochs=epochs,
        imgsz=imgsz,
        device=device,
        project=str(RUNS_DIR),
        name="mj_detect",
        exist_ok=True,          # 始终写入同一目录，不自动创建 mj_detect-2

        # ── 针对小数据集的优化参数 ──
        batch=-1,           # 自动选择 batch size（根据显存）
        patience=50,        # 50 epoch 无提升则提前停止
        save_period=50,     # 每 50 epoch 保存一次权重

        # ── 数据增强（小数据集加强增强） ──
        hsv_h=0.015,        # 色调扰动
        hsv_s=0.7,          # 饱和度扰动
        hsv_v=0.4,          # 亮度扰动
        degrees=5.0,        # 轻微旋转（麻将不会大角度）
        translate=0.1,      # 平移
        scale=0.3,          # 缩放
        fliplr=0.0,         # 禁用水平翻转（字牌有方向）
        mosaic=1.0,         # Mosaic 拼图增强（强烈推荐小数据集）
        mixup=0.1,          # Mixup 增强
        copy_paste=0.1,     # 复制粘贴增强

        # ── 其他 ──
        plots=True,         # 生成训练曲线图
        verbose=True,
    )

    best_weights = RUNS_DIR / "mj_detect" / "weights" / "best.pt"
    print("\n" + "=" * 60)
    print("训练完成！")
    print(f"最优权重: {best_weights}")
    print(f"训练报告: {RUNS_DIR / 'mj_detect'}")
    print("=" * 60)

    return results


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="麻将牌识别 YOLOv8 训练")
    parser.add_argument("--zip",    default=str(DEFAULT_ZIP),   help="Label Studio 导出的 zip 文件路径")
    parser.add_argument("--epochs", default=DEFAULT_EPOCHS,     type=int, help="训练轮数")
    parser.add_argument("--model",  default=DEFAULT_MODEL,      help="YOLOv8 模型（yolov8n/s/m/l/x）")
    parser.add_argument("--imgsz",  default=DEFAULT_IMGSZ,      type=int, help="输入图片尺寸")
    parser.add_argument("--val",    default=DEFAULT_VAL_RATIO,  type=float, help="验证集比例")
    return parser.parse_args()


def main():
    args = parse_args()
    zip_path = Path(args.zip)

    if not zip_path.exists():
        raise FileNotFoundError(f"找不到数据集 zip 文件: {zip_path}")

    raw_dir = DATASET_DIR / "raw"
    images_dir, labels_dir, classes_file = extract_zip(zip_path, raw_dir)
    split_dataset(images_dir, labels_dir, DATASET_DIR, val_ratio=args.val)
    yaml_path = generate_data_yaml(classes_file, DATASET_DIR)
    model = _resolve_model(args.model)
    train(yaml_path, model_name=model, epochs=args.epochs, imgsz=args.imgsz)


if __name__ == "__main__":
    main()
