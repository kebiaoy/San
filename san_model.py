"""
san_model.py — 三川约战 AI 模型定义

参照 Mortal Brain v3/v4 架构（Pre-Activation ResNet + Dueling DQN），
适配红中断勾卡的通道输入：(B, 220, 19)。

网络结构：
  Brain  : (B, 220, 19) → phi (B, 1024)
  DQN    : phi → Q(s, a) (B, 26)   [Dueling DQN]
  AuxNet : phi → score_pred (B, 1)  [辅助分值预测]
"""

import torch
from torch import nn, Tensor
from torch.nn import functional as F
from typing import Tuple
from functools import partial

# ──────────────────────────────────────────────────────────
# 游戏常量（与 scyxjy.py 保持一致）
# ──────────────────────────────────────────────────────────
CHAN_TOTAL   = 220    # 输入通道数
CARD_TYPES   = 19    # 牌的维度（9万 + 9条 + 红中）
ACTION_SPACE = 26    # 动作空间总数
LATENT_SIZE  = 1024  # Brain 输出的隐向量维度

# 动作索引定义
ACTION_DISCARD_BASE = 0    # 0-18: 弃牌（牌型 0=1万 … 8=9万, 9=1条 … 17=9条, 18=红中）
ACTION_BAO_HU       = 19   # 报胡（起手阶段宣告准备胡牌）
ACTION_PENG         = 20   # 碰（对家打出，手中有两张相同）
ACTION_GANG         = 21   # 杠（直杠 / 暗杠）
ACTION_JIAGANG      = 22   # 加杠（将已有碰副露升级为四张）
ACTION_HU           = 23   # 胡（吃胡 / 点炮 / 自摸）
ACTION_QINGHU       = 24   # 请胡（宣告准备胡牌）
ACTION_PASS         = 25   # 过（放弃当前所有操作选项）


# ──────────────────────────────────────────────────────────
# 子模块
# ──────────────────────────────────────────────────────────

class ChannelAttention(nn.Module):
    """
    通道注意力模块（参照 Mortal ChannelAttention）。
    同时使用平均池化和最大池化的 MLP 输出之和，经 Sigmoid 得到各通道权重。
    """

    def __init__(self, channels: int, ratio: int = 16):
        super().__init__()
        self.shared_mlp = nn.Sequential(
            nn.Linear(channels, channels // ratio, bias=True),
            nn.Mish(),
            nn.Linear(channels // ratio, channels, bias=True),
        )
        # 偏置初始化为 0
        for mod in self.modules():
            if isinstance(mod, nn.Linear):
                nn.init.constant_(mod.bias, 0)

    def forward(self, x: Tensor) -> Tensor:
        # x: (B, C, L)
        avg_out = self.shared_mlp(x.mean(-1))   # (B, C)
        max_out = self.shared_mlp(x.amax(-1))   # (B, C)
        weight  = (avg_out + max_out).sigmoid()  # (B, C)
        return weight.unsqueeze(-1) * x          # (B, C, L)


class ResBlock(nn.Module):
    """
    Pre-Activation 残差块（参照 Mortal ResBlock version 3/4）。
    结构：BN → Mish → Conv1d → BN → Mish → Conv1d → ChannelAttention → 残差加
    """

    def __init__(self, channels: int):
        super().__init__()
        norm = partial(nn.BatchNorm1d, channels, momentum=0.01, eps=1e-3)
        self.res_unit = nn.Sequential(
            norm(),
            nn.Mish(),
            nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
            norm(),
            nn.Mish(),
            nn.Conv1d(channels, channels, kernel_size=3, padding=1, bias=False),
        )
        self.ca = ChannelAttention(channels)

    def forward(self, x: Tensor) -> Tensor:
        out = self.res_unit(x)
        out = self.ca(out)
        return out + x   # 残差连接


class Brain(nn.Module):
    """
    观测编码器：(B, 220, 19) → phi (B, 1024)

    结构（参照 Mortal Brain v3/v4，Pre-Activation ResNet）：
      Conv1d(220 → C) →
      [ResBlock × num_blocks] →
      BN → Mish →
      Conv1d(C → 32) → Mish →
      Flatten →
      Linear(32 × 19 → 1024) →
      Mish

    默认超参：conv_channels=128, num_blocks=20（训练初期可用小模型）
    对应 Mortal 大模型：conv_channels=192, num_blocks=40
    """

    def __init__(self, conv_channels: int = 128, num_blocks: int = 20):
        super().__init__()
        norm = partial(nn.BatchNorm1d, conv_channels, momentum=0.01, eps=1e-3)
        blocks = [ResBlock(conv_channels) for _ in range(num_blocks)]

        self.encoder = nn.Sequential(
            nn.Conv1d(CHAN_TOTAL, conv_channels, kernel_size=3, padding=1, bias=False),
            *blocks,
            norm(),
            nn.Mish(),
            nn.Conv1d(conv_channels, 32, kernel_size=3, padding=1),
            nn.Mish(),
            nn.Flatten(),
            nn.Linear(32 * CARD_TYPES, LATENT_SIZE),
        )
        self.actv = nn.Mish()

    def forward(self, obs: Tensor) -> Tensor:
        """
        obs: (B, CHAN_TOTAL, CARD_TYPES) = (B, 220, 19) float32
        返回 phi: (B, 1024)
        """
        return self.actv(self.encoder(obs))


class DQN(nn.Module):
    """
    Dueling DQN 头：phi → Q(s, a)

    Q(s,a) = V(s) + A(s,a) − mean_{a'∈valid}[A(s,a')]
    非法动作 Q 值填充为 −inf。

    参照 Mortal DQN version 4：单 Linear(1024 → 1 + ACTION_SPACE)，
    分拆为 V（1维）+ A（ACTION_SPACE 维）。
    """

    def __init__(self):
        super().__init__()
        self.net = nn.Linear(LATENT_SIZE, 1 + ACTION_SPACE)
        nn.init.constant_(self.net.bias, 0)

    def forward(self, phi: Tensor, mask: Tensor) -> Tensor:
        """
        phi : (B, 1024)
        mask: (B, ACTION_SPACE) bool，True = 合法动作
        返回 q: (B, ACTION_SPACE)，非法动作为 −inf
        """
        v, a = self.net(phi).split((1, ACTION_SPACE), dim=-1)
        # 仅对合法动作取均值（防止非法动作影响 Q 估计）
        a_sum  = a.masked_fill(~mask, 0.).sum(-1, keepdim=True)
        a_mean = a_sum / mask.sum(-1, keepdim=True).clamp(min=1)
        # 用大负数代替 -inf，避免 MPS 后向传播在 logsumexp 梯度中产生 nan
        return (v + a - a_mean).masked_fill(~mask, -1e9)


class AuxNet(nn.Module):
    """
    辅助网络：预测当前局的归一化积分。

    目标：true_score_normalized = game_score / (REWARD_NORM × cell_score)
    损失：Huber Loss（比 MSE 对异常值更鲁棒）
    """

    def __init__(self):
        super().__init__()
        self.net = nn.Linear(LATENT_SIZE, 1, bias=False)

    def forward(self, phi: Tensor) -> Tensor:
        """返回 score_pred: (B, 1)"""
        return self.net(phi)


# ──────────────────────────────────────────────────────────
# 完整模型（便于保存和加载）
# ──────────────────────────────────────────────────────────

class SanModel(nn.Module):
    """
    三川约战 AI 完整模型（Brain + DQN + AuxNet）。

    用于：
      训练时分别调用 brain / dqn / aux_net 计算损失；
      推理时调用 act() 选择最优动作。
    """

    def __init__(self, conv_channels: int = 128, num_blocks: int = 20):
        super().__init__()
        self.brain   = Brain(conv_channels=conv_channels, num_blocks=num_blocks)
        self.dqn     = DQN()
        self.aux_net = AuxNet()

    def forward(self, obs: Tensor, mask: Tensor) -> Tuple[Tensor, Tensor]:
        """
        obs : (B, 220, 19) float32
        mask: (B, 26)       bool
        返回 (q: (B, 26), score_pred: (B, 1))
        """
        phi        = self.brain(obs)
        q_values   = self.dqn(phi, mask)
        score_pred = self.aux_net(phi)
        return q_values, score_pred

    @torch.no_grad()
    def act(self, obs: Tensor, mask: Tensor) -> Tensor:
        """贪心动作选择（推理用）。返回 action_idx: (B,)"""
        self.eval()
        phi = self.brain(obs)
        q   = self.dqn(phi, mask)
        return q.argmax(-1)


# ──────────────────────────────────────────────────────────
# 简单测试
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    model = SanModel(conv_channels=32, num_blocks=2)
    B = 4
    obs  = torch.randn(B, CHAN_TOTAL, CARD_TYPES)
    mask = torch.ones(B, ACTION_SPACE, dtype=torch.bool)
    mask[:, ACTION_HU] = False  # 假设本步不能胡

    q, score = model(obs, mask)
    print(f"obs    shape: {obs.shape}")
    print(f"q      shape: {q.shape}")
    print(f"score  shape: {score.shape}")
    print(f"action      : {model.act(obs, mask)}")
    print("san_model.py 测试通过 ✓")
