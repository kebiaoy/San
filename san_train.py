"""
san_train.py — 三川约战 AI 训练脚本

损失计算（参照 wiki 框架设计 https://github.com/kebiaoy/San/wiki/框架设计）：

  # 归一化积分
  reward = 本局赢的积分 / (REWARD_NORM * cell_score)

  # Monte-Carlo 折扣目标
  q_target = gamma^steps_to_done * reward

  # DQN 损失（均方误差）
  dqn_loss = 0.5 * MSE(q(s, a), q_target)

  # CQL 损失（保守 Q 学习，防止高估）
  cql_loss = logsumexp(q_out).mean() - q(s, a).mean()

  # 辅助损失（积分预测，Huber 更鲁棒）
  next_score_pred = aux_net(phi)
  next_score_loss = Huber(next_score_pred, true_score_normalized)

  # 总损失
  loss = dqn_loss + cql_loss * CQL_WEIGHT + next_score_loss * AUX_WEIGHT

用法：
  python san_train.py                  # 使用默认参数
  python san_train.py --epochs 50 --batch_size 512
"""

import argparse
import math
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch import Tensor
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import Dataset, DataLoader

from san_model import (
    SanModel,
    CHAN_TOTAL, CARD_TYPES, ACTION_SPACE, LATENT_SIZE,
)

# ──────────────────────────────────────────────────────────
# 超参默认值
# ──────────────────────────────────────────────────────────
DATA_DIR     = Path("/Users/kebiaoy/Documents/MjTrainData/train_data")
CKPT_DIR     = Path("/Users/kebiaoy/workspace/San/checkpoints")

GAMMA        = 0.99    # Monte-Carlo 折扣因子
CQL_WEIGHT   = 1.0     # CQL 损失权重（初始训练保守值，MPS 上过大会爆炸）
AUX_WEIGHT   = 0.2     # 辅助积分预测损失权重

BATCH_SIZE   = 512
LR           = 3e-4
WEIGHT_DECAY = 1e-4
EPOCHS       = 100
SAVE_EVERY   = 10      # 每 N epoch 保存一次 checkpoint

CONV_CHANNELS = 128    # Brain 卷积通道数（初始训练用小模型）
NUM_BLOCKS    = 20     # ResBlock 数量


# ──────────────────────────────────────────────────────────
# 数据集
# ──────────────────────────────────────────────────────────

class SanDataset(Dataset):
    """
    从 train_data/ 加载所有 .npz 训练文件，
    合并为一个大数据集。

    每条样本：
      obs           : (220, 19) float32
      action        : int
      mask          : (26,) bool
      steps_to_done : int
      reward        : float
      true_score    : float
    """

    def __init__(self, data_dir: Path, gamma: float = GAMMA):
        self.gamma = gamma
        npz_files  = sorted(data_dir.glob("*.npz"))
        if not npz_files:
            raise FileNotFoundError(f"在 {data_dir} 下未找到任何 .npz 文件。"
                                    "请先运行 scyxjy_gen_trainData.py 生成训练数据。")

        obs_list    = []
        actions_list = []
        masks_list  = []
        qtgt_list   = []
        tscore_list = []

        for f in npz_files:
            try:
                d = np.load(f)
                obs_list.append(d["obs"])
                actions_list.append(d["actions"].astype(np.int64))
                masks_list.append(d["masks"])
                # q_target = gamma^steps_to_done * reward
                steps   = d["steps_to_done"].astype(np.float32)
                rewards = d["rewards"].astype(np.float32)
                qtgt_list.append((gamma ** steps) * rewards)
                tscore_list.append(d["true_scores"].astype(np.float32))
            except Exception as e:
                print(f"  [跳过损坏文件] {f.name}: {e}")

        if not obs_list:
            raise RuntimeError("没有可用的训练数据！")

        self.obs      = np.concatenate(obs_list,     axis=0)
        self.actions  = np.concatenate(actions_list, axis=0)
        self.masks    = np.concatenate(masks_list,   axis=0)
        self.q_targets = np.concatenate(qtgt_list,   axis=0)
        self.t_scores  = np.concatenate(tscore_list, axis=0)

        print(f"数据集加载完成：{len(self.obs)} 条样本，来自 {len(obs_list)} 个文件")

    def __len__(self) -> int:
        return len(self.obs)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.obs[idx]),                                   # (220, 19) float32
            torch.tensor(int(self.actions[idx])),                              # scalar int64
            torch.from_numpy(self.masks[idx]),                                 # (26,) bool
            torch.tensor(float(self.q_targets[idx]), dtype=torch.float32),     # scalar float32
            torch.tensor(float(self.t_scores[idx]),  dtype=torch.float32),     # scalar float32
        )


# ──────────────────────────────────────────────────────────
# 损失计算
# ──────────────────────────────────────────────────────────

def compute_loss(
    brain,
    dqn,
    aux_net,
    obs:      Tensor,
    actions:  Tensor,
    masks:    Tensor,
    q_target: Tensor,
    t_score:  Tensor,
    cql_weight: float = CQL_WEIGHT,
    aux_weight: float = AUX_WEIGHT,
) -> tuple[Tensor, dict]:
    """
    计算总损失并返回各分项的监控字典。

    参数：
      obs      : (B, 220, 19)
      actions  : (B,) int64
      masks    : (B, 26) bool
      q_target : (B,) float32  = gamma^steps_to_done * reward
      t_score  : (B,) float32  = 归一化最终积分

    返回：
      loss       : 标量 Tensor
      info_dict  : 各分项损失的监控字典
    """
    B = obs.shape[0]

    # ── 1. 前向计算 ────────────────────────────────────────
    phi        = brain(obs)                          # (B, 1024)
    q_out      = dqn(phi, masks)                     # (B, 26)，非法动作=-inf
    score_pred = aux_net(phi).squeeze(-1)            # (B,)

    # ── 2. DQN 损失（MSE） ─────────────────────────────────
    # q(s, a)：取实际执行的动作对应的 Q 值
    # 用 gather 替代高级索引，在 MPS 上更稳定
    q_sa      = q_out.gather(1, actions.long().unsqueeze(1)).squeeze(1)  # (B,)
    dqn_loss  = 0.5 * F.mse_loss(q_sa, q_target)

    # ── 3. CQL 损失（保守 Q 学习） ─────────────────────────
    # logsumexp 仅在合法动作上计算（将非法动作置为 -1e9 后 exp≈0，不影响结果）
    cql_loss = q_out.logsumexp(-1).mean() - q_sa.mean()

    # ── 4. 辅助积分预测损失（Huber Loss） ──────────────────
    aux_loss = F.huber_loss(score_pred, t_score)

    # ── 5. 加权求和 ────────────────────────────────────────
    loss = dqn_loss + cql_loss * cql_weight + aux_loss * aux_weight

    info = {
        "loss":     loss.item(),
        "dqn":      dqn_loss.item(),
        "cql":      cql_loss.item(),
        "aux":      aux_loss.item(),
        "q_mean":   q_sa.mean().item(),
        "q_target": q_target.mean().item(),
    }
    return loss, info


# ──────────────────────────────────────────────────────────
# 训练主循环
# ──────────────────────────────────────────────────────────

def train(args) -> None:
    device = torch.device(
        "cuda"  if torch.cuda.is_available()  else
        "mps"   if torch.backends.mps.is_available() else
        "cpu"
    )
    print(f"使用设备：{device}")

    # ── 数据加载 ───────────────────────────────────────────
    dataset = SanDataset(Path(args.data_dir), gamma=args.gamma)
    # num_workers > 0 仅在 CUDA 上安全；MPS/CPU 用单进程加载
    num_workers = min(4, os.cpu_count() or 1) if device.type == "cuda" else 0
    loader  = DataLoader(
        dataset,
        batch_size  = args.batch_size,
        shuffle     = True,
        num_workers = num_workers,
        pin_memory  = device.type == "cuda",
        drop_last   = True,
    )

    # ── 模型初始化 ─────────────────────────────────────────
    model   = SanModel(conv_channels=args.conv_channels, num_blocks=args.num_blocks).to(device)
    brain   = model.brain
    dqn     = model.dqn
    aux_net = model.aux_net

    # 从 checkpoint 恢复
    start_epoch = 1
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    ckpts = sorted(CKPT_DIR.glob("san_model_epoch*.pt"))
    if ckpts and args.resume:
        latest = ckpts[-1]
        state  = torch.load(latest, map_location=device)
        model.load_state_dict(state["model"])
        start_epoch = state.get("epoch", 0) + 1
        print(f"从 {latest.name} 恢复，续训 epoch {start_epoch}")

    # ── 优化器 & 调度器 ────────────────────────────────────
    optimizer = AdamW(
        model.parameters(),
        lr           = args.lr,
        weight_decay = args.weight_decay,
    )
    scheduler = CosineAnnealingLR(optimizer, T_max=args.epochs, eta_min=1e-6)
    if ckpts and args.resume:
        # 重新加载 optimizer/scheduler 状态
        state = torch.load(ckpts[-1], map_location=device)
        if "optimizer" in state:
            optimizer.load_state_dict(state["optimizer"])
        if "scheduler" in state:
            scheduler.load_state_dict(state["scheduler"])

    # 混合精度（仅 CUDA）
    use_amp = device.type == "cuda"
    scaler  = torch.amp.GradScaler("cuda", enabled=True) if use_amp else None

    print(f"\n开始训练  epochs={args.epochs}  batch={args.batch_size}  lr={args.lr}")
    print("=" * 60)

    for epoch in range(start_epoch, start_epoch + args.epochs):
        model.train()
        epoch_info: dict = {k: 0.0 for k in ("loss", "dqn", "cql", "aux")}
        n_batches = 0
        t0 = time.time()

        for obs, actions, masks, q_target, t_score in loader:
            # non_blocking=True 仅对 CUDA+pin_memory 有效，MPS 上直接同步传输
            nb = device.type == "cuda"
            obs      = obs.to(device, non_blocking=nb)
            actions  = actions.to(device, non_blocking=nb)
            masks    = masks.to(device, non_blocking=nb)
            q_target = q_target.to(device, non_blocking=nb)
            t_score  = t_score.to(device, non_blocking=nb)

            optimizer.zero_grad()

            if use_amp:
                with torch.amp.autocast("cuda"):
                    loss, info = compute_loss(
                        brain, dqn, aux_net,
                        obs, actions, masks, q_target, t_score,
                        cql_weight = args.cql_weight,
                        aux_weight = args.aux_weight,
                    )
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.5)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss, info = compute_loss(
                    brain, dqn, aux_net,
                    obs, actions, masks, q_target, t_score,
                    cql_weight = args.cql_weight,
                    aux_weight = args.aux_weight,
                )
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=0.5)
                optimizer.step()

            for k in epoch_info:
                epoch_info[k] += info.get(k, 0.0)
            n_batches += 1
            # 检测异常并快速停止（避免 nan 传染后续批次）
            if math.isnan(info.get("loss", 0.0)) or math.isinf(info.get("loss", 0.0)):
                print(f"  [⚠ nan/inf] epoch={epoch} batch={n_batches}: {info}")
                break

        scheduler.step()

        # ── 打印 epoch 统计 ─────────────────────────────────
        elapsed = time.time() - t0
        avg = {k: v / max(n_batches, 1) for k, v in epoch_info.items()}
        print(
            f"Epoch {epoch:3d}/{start_epoch + args.epochs - 1}  "
            f"loss={avg['loss']:.4f}  dqn={avg['dqn']:.4f}  "
            f"cql={avg['cql']:.4f}  aux={avg['aux']:.4f}  "
            f"lr={scheduler.get_last_lr()[0]:.2e}  "
            f"t={elapsed:.1f}s"
        )

        # ── 保存 checkpoint ─────────────────────────────────
        if epoch % args.save_every == 0 or epoch == start_epoch + args.epochs - 1:
            ckpt_path = CKPT_DIR / f"san_model_epoch{epoch:04d}.pt"
            torch.save(
                {
                    "epoch":     epoch,
                    "model":     model.state_dict(),
                    "optimizer": optimizer.state_dict(),
                    "scheduler": scheduler.state_dict(),
                    "args":      vars(args),
                },
                ckpt_path,
            )
            print(f"  → checkpoint 已保存：{ckpt_path}")

    print("=" * 60)
    print("训练完成！")


# ──────────────────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(description="三川约战 AI 训练脚本")
    p.add_argument("--data_dir",      default=str(DATA_DIR),    help="训练数据目录")
    p.add_argument("--epochs",        type=int,   default=EPOCHS)
    p.add_argument("--batch_size",    type=int,   default=BATCH_SIZE)
    p.add_argument("--lr",            type=float, default=LR)
    p.add_argument("--weight_decay",  type=float, default=WEIGHT_DECAY)
    p.add_argument("--gamma",         type=float, default=GAMMA)
    p.add_argument("--cql_weight",    type=float, default=CQL_WEIGHT)
    p.add_argument("--aux_weight",    type=float, default=AUX_WEIGHT)
    p.add_argument("--conv_channels", type=int,   default=CONV_CHANNELS)
    p.add_argument("--num_blocks",    type=int,   default=NUM_BLOCKS)
    p.add_argument("--save_every",    type=int,   default=SAVE_EVERY)
    p.add_argument("--resume",        action="store_true", help="从最新 checkpoint 续训")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    train(args)
