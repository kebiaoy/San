"""
san_eval.py — 在验证集上评估所有 checkpoint，挑出 best.pt

用法：
  python san_eval.py
  python san_eval.py --val_dir <path> --ckpt_dir <path>

逻辑：
  1. 加载验证集（/Users/kebiaoy/Documents/MjTrainData/check_data 下所有 .npz）
  2. 遍历 --ckpt_dir 下所有 san_model_epoch*.pt
  3. 对每个 checkpoint 计算验证集上的总损失（dqn + cql*CQL_WEIGHT + aux*AUX_WEIGHT）
  4. 取损失最低的 checkpoint，复制为 best.pt
"""

import argparse
import shutil
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import Tensor

from san_model import SanModel
from san_train import GAMMA, CQL_WEIGHT, AUX_WEIGHT, compute_loss


# ──────────────────────────────────────────────────────────
# 验证集加载
# ──────────────────────────────────────────────────────────

def load_val_data(val_dir: Path, gamma: float):
    """加载验证集，返回与 SanDataset 相同结构。"""
    npz_files = sorted(val_dir.glob("*.npz"))
    if not npz_files:
        raise FileNotFoundError(f"验证集目录 {val_dir} 下没有 .npz 文件")

    obs_list, actions_list, masks_list = [], [], []
    qtgt_list, tscore_list = [], []

    for f in npz_files:
        try:
            d = np.load(f)
            obs_list.append(d["obs"])
            actions_list.append(d["actions"].astype(np.int64))
            masks_list.append(d["masks"])
            steps = d["steps_to_done"].astype(np.float32)
            rewards = d["rewards"].astype(np.float32)
            qtgt_list.append((gamma ** steps) * rewards)
            tscore_list.append(d["true_scores"].astype(np.float32))
        except Exception as e:
            print(f"  [跳过损坏文件] {f.name}: {e}")

    if not obs_list:
        raise RuntimeError("没有可用的验证数据")

    data = {
        "obs":       torch.from_numpy(np.concatenate(obs_list, axis=0)),
        "actions":   torch.from_numpy(np.concatenate(actions_list, axis=0)),
        "masks":     torch.from_numpy(np.concatenate(masks_list, axis=0)),
        "q_target":  torch.from_numpy(np.concatenate(qtgt_list, axis=0)),
        "t_score":   torch.from_numpy(np.concatenate(tscore_list, axis=0)),
    }
    print(f"验证集加载完成：{len(data['obs'])} 条样本，来自 {len(obs_list)} 个文件")
    return data


# ──────────────────────────────────────────────────────────
# 单个 checkpoint 评估
# ──────────────────────────────────────────────────────────

@torch.no_grad()
def evaluate_checkpoint(ckpt_path: Path, val_data: dict, device: torch.device,
                        batch_size: int = 512) -> tuple[float, dict]:
    state = torch.load(ckpt_path, map_location=device, weights_only=False)

    # 从 checkpoint 里读取训练时的超参，保证模型结构一致
    args_dict = state.get("args", {})
    conv_channels = args_dict.get("conv_channels", 192)
    num_blocks    = args_dict.get("num_blocks", 40)

    model = SanModel(conv_channels=conv_channels, num_blocks=num_blocks).to(device)
    model.load_state_dict(state["model"])
    model.eval()

    brain, dqn, aux_net = model.brain, model.dqn, model.aux_net

    N = len(val_data["obs"])
    totals = {k: 0.0 for k in ("loss", "dqn", "cql", "aux")}
    n_batches = 0

    for i in range(0, N, batch_size):
        j = min(i + batch_size, N)
        obs      = val_data["obs"][i:j].to(device)
        actions  = val_data["actions"][i:j].to(device)
        masks    = val_data["masks"][i:j].to(device)
        q_target = val_data["q_target"][i:j].to(device)
        t_score  = val_data["t_score"][i:j].to(device)

        _, info = compute_loss(
            brain, dqn, aux_net,
            obs, actions, masks, q_target, t_score,
            cql_weight=CQL_WEIGHT,
            aux_weight=AUX_WEIGHT,
        )
        for k in totals:
            totals[k] += info[k]
        n_batches += 1

    avg = {k: v / max(n_batches, 1) for k, v in totals.items()}
    return avg["loss"], avg


# ──────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────

def main(args):
    val_dir = Path(args.val_dir)
    ckpt_dir = Path(args.ckpt_dir)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    device = torch.device(
        "cuda"  if torch.cuda.is_available()  else
        "mps"   if torch.backends.mps.is_available() else
        "cpu"
    )
    print(f"使用设备：{device}")

    val_data = load_val_data(val_dir, gamma=args.gamma)

    ckpts = sorted(ckpt_dir.glob("san_model_epoch*.pt"))
    if not ckpts:
        raise FileNotFoundError(
            f"在 {ckpt_dir} 下未找到 san_model_epoch*.pt，请确认 checkpoint 路径"
        )

    print(f"\n发现 {len(ckpts)} 个 checkpoint，开始评估...")
    print("=" * 78)
    print(f"{'checkpoint':<32} {'loss':>10} {'dqn':>10} {'cql':>10} {'aux':>10}")
    print("-" * 78)

    results = []
    for ckpt in ckpts:
        val_loss, info = evaluate_checkpoint(ckpt, val_data, device, args.batch_size)
        results.append((ckpt, val_loss, info))
        print(f"{ckpt.name:<32} {val_loss:>10.4f} {info['dqn']:>10.4f} "
              f"{info['cql']:>10.4f} {info['aux']:>10.4f}")

    print("=" * 78)

    results.sort(key=lambda x: x[1])
    best_ckpt, best_loss, best_info = results[0]
    best_path = ckpt_dir / "best.pt"

    # 直接复制最佳 checkpoint 文件（保留 optimizer/scheduler 等状态，便于后续在线训练加载）
    shutil.copyfile(best_ckpt, best_path)

    print(f"\n最佳 checkpoint: {best_ckpt.name}  (val_loss={best_loss:.4f})")
    print(f"  dqn={best_info['dqn']:.4f}  cql={best_info['cql']:.4f}  aux={best_info['aux']:.4f}")
    print(f"已保存为: {best_path}")

    # 最差的也提示一下，方便判断训练是否过拟合
    worst_ckpt, worst_loss, _ = results[-1]
    print(f"\n最差 checkpoint: {worst_ckpt.name}  (val_loss={worst_loss:.4f})")
    print(f"差距: {worst_loss - best_loss:.4f}")


# ──────────────────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="评估所有 checkpoint 并生成 best.pt")
    p.add_argument(
        "--val_dir",
        default="/Users/kebiaoy/Documents/MjTrainData/check_data",
        help="验证集目录",
    )
    p.add_argument(
        "--ckpt_dir",
        default="/Users/kebiaoy/Documents/MjTrainData/checkpoints",
        help="checkpoint 目录（同时是 best.pt 输出目录）",
    )
    p.add_argument("--batch_size", type=int, default=512)
    p.add_argument("--gamma", type=float, default=GAMMA)
    return p.parse_args()


if __name__ == "__main__":
    main(_parse_args())
