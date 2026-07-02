"""
san_engine.py — San 推理引擎，加载训练好的 best.pt，根据 obs+mask 输出动作

参照 /Users/kebiaoy/workspace/Mortal/mortal/engine.py 的 MortalEngine

用法：
  engine = SanEngine("/path/to/best.pt")
  action = engine.react(obs, mask)   # obs:(220,19) float32, mask:(26,) bool → action 0-25
"""

import numpy as np
import torch

from san_model import SanModel


class SanEngine:
    def __init__(
        self,
        ckpt_path: str = "/Users/kebiaoy/Documents/MjTrainData/checkpoints/best.pt",
        device: str | None = None,
    ):
        if device:
            self.device = torch.device(device)
        else:
            self.device = torch.device(
                "cuda" if torch.cuda.is_available() else
                "mps" if torch.backends.mps.is_available() else
                "cpu"
            )

        # 加载 checkpoint（含 model + args）
        state = torch.load(ckpt_path, map_location=self.device, weights_only=False)
        args = state.get("args", {}) or {}
        conv_channels = args.get("conv_channels", 192)
        num_blocks = args.get("num_blocks", 40)

        # 构建模型并加载权重
        model = SanModel(conv_channels=conv_channels, num_blocks=num_blocks).to(self.device)
        model.load_state_dict(state["model"])
        model.eval()

        self.brain = model.brain
        self.dqn = model.dqn
        print(f"[SanEngine] loaded {ckpt_path}")
        print(f"[SanEngine] conv_channels={conv_channels}, num_blocks={num_blocks}, device={self.device}")

    @torch.inference_mode()
    def react(self, obs: np.ndarray, mask: np.ndarray) -> tuple[int, list[float]]:
        """
        根据 obs 和合法动作掩码，贪心选择 Q 值最大的动作。

        参数：
          obs:  (220, 19) float32 — 通道特征
          mask: (26,) bool — 合法动作掩码

        返回：
          action: int (0-25)
          q_values: list[float] — 26 维 Q 值（非法动作为 -inf）
        """
        obs_t = torch.from_numpy(np.ascontiguousarray(obs)).float().unsqueeze(0).to(self.device)
        mask_t = torch.from_numpy(mask).bool().unsqueeze(0).to(self.device)

        phi = self.brain(obs_t)       # (1, 1024)
        q = self.dqn(phi, mask_t)     # (1, 26)

        action = int(q.argmax(-1).item())
        q_values = q.squeeze(0).tolist()
        return action, q_values
