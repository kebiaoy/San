"""
scyxjy_gen_trainData.py — 训练数据生成脚本

直接复用 scyxjy.py 中的 generalChairTrainData 生成观测通道，
仅在其基础上追加动作标签、动作掩码和奖励信息。

流程：
  1. 对每个 .video 文件调用 parseVideoReplay
  2. 对每个玩家调用 generalChairTrainData 获得 TrainSample 列表
     （TrainSample 已包含完整 channels + pkt_idx + hand_snap）
  3. 对每个 TrainSample 从 pkt_idx 向后扫包，确定实际动作
  4. 从 channels 的 cans 通道（Ch202-210）提取动作掩码
  5. 从 GAME_END 包提取归一化奖励
  6. 将结果保存为 .npz

动作空间（26 个，与 san_model.py 一致）：
  0-18: 弃牌（牌型索引 0=1万…8=9万，9=1条…17=9条，18=红中）
  19  : 报胡
  20  : 碰
  21  : 杠（直杠 / 暗杠）
  22  : 加杠
  23  : 胡（吃胡 / 点炮 / 自摸）
  24  : 请胡
  25  : 过
"""

import sys
import os
import numpy as np
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
from scyxjy import (
    parseVideoReplay, VideoReplay,
    MDM_GF_GAME, _Buf,
    _card_to_idx,
    CARD_TYPES, _INVALID_CHAIR,
    _WIK_PENG, _WIK_GANG, _WIK_JIA_GANG,
    MAX_COUNT,
    generalChairTrainData, TrainSample,
)

# ──────────────────────────────────────────────────────────
# 路径 & 超参
# ──────────────────────────────────────────────────────────
SRC_DIR     = Path("/Users/kebiaoy/Documents/MjTrainData")
OUT_DIR     = SRC_DIR / "train_data"
GAMMA       = 0.99
REWARD_NORM = 64.0   # reward = game_score / (REWARD_NORM * cell_score)

# 动作索引（与 san_model.py 保持一致）
ACTION_DISCARD_BASE = 0
ACTION_BAO_HU       = 19
ACTION_PENG         = 20
ACTION_GANG         = 21
ACTION_JIAGANG      = 22
ACTION_HU           = 23
ACTION_QINGHU       = 24
ACTION_PASS         = 25
ACTION_SPACE        = 26


# ──────────────────────────────────────────────────────────
# 1. 从 channels 提取动作掩码
# ──────────────────────────────────────────────────────────

def _mask_from_channels(channels: np.ndarray) -> np.ndarray:
    """
    从 TrainSample.channels 的 cans 通道直接读取动作掩码。

    通道映射（来自 scyxjy.py snap()）：
      Ch202 : 合法弃牌（19维）→ 弃牌动作 0-18
      Ch205 : 可以碰（全广播）
      Ch206 : 可以杠（全广播，含直杠/加杠）
      Ch207 : 可以胡（全广播）
      Ch208 : 可以请胡（全广播）
      Ch209 : 可以报胡（全广播）
      Ch210 : 可以过（全广播）
    """
    mask = np.zeros(ACTION_SPACE, dtype=bool)
    # 弃牌：Ch202 中哪些牌位为非零
    for i in range(CARD_TYPES):
        if channels[202, i] > 0:
            mask[ACTION_DISCARD_BASE + i] = True
    # 特殊操作：取第 0 维（全广播通道任意位置均相同）
    if channels[205, 0] > 0:
        mask[ACTION_PENG]    = True
    if channels[206, 0] > 0:
        mask[ACTION_GANG]    = True
        mask[ACTION_JIAGANG] = True   # Ch206 合并了杠/加杠，两者都标为合法
    if channels[207, 0] > 0:
        mask[ACTION_HU]      = True
    if channels[208, 0] > 0:
        mask[ACTION_QINGHU]  = True
    if channels[209, 0] > 0:
        mask[ACTION_BAO_HU]  = True
    if channels[210, 0] > 0:
        mask[ACTION_PASS]    = True
    return mask


# ──────────────────────────────────────────────────────────
# 2. 从 pkt_idx 向后扫描确定动作标签
# ──────────────────────────────────────────────────────────

def _determine_action(
    packets:   list,
    pkt_idx:   int,
    event:     str,
    chair_id:  int,
) -> int | None:
    """
    从触发决策的包（pkt_idx）之后的数据包，确定 chair_id 实际执行的动作。
    返回动作索引（0-25），或 None（无法确定，样本将被丢弃）。

    决策类型由 event 字符串判断：
      'case_c' → 报胡决策
      其他含 'discard'/'摸牌'/'case_a'/'case_d' → 弃牌决策
      'case_g'/'操作' → 碰/杠/胡/过
    """
    pkts = packets

    # ── 报胡决策 ──────────────────────────────────────────
    if 'case_c' in event:
        for j in range(pkt_idx + 1, len(pkts)):
            pkt = pkts[j]
            if pkt.main_cmd != MDM_GF_GAME or not pkt.payload:
                continue
            if pkt.sub_cmd == 115:   # BAO_HU_NOTIFY
                buf      = _Buf(pkt.payload)
                _current = buf.read_word()
                last_bao = buf.read_word()
                bao_flag = buf.read_byte()
                if last_bao == chair_id:
                    return ACTION_BAO_HU if bao_flag else ACTION_PASS
            if pkt.sub_cmd == 108:   # GAME_END
                break
        return None

    # ── 弃牌决策 ──────────────────────────────────────────
    is_discard_event = any(
        x in event
        for x in ('case_a', 'case_b', 'case_d', 'case_e', 'case_f', '摸牌', '弃牌')
    )
    if is_discard_event:
        for j in range(pkt_idx + 1, len(pkts)):
            pkt = pkts[j]
            if pkt.main_cmd != MDM_GF_GAME or not pkt.payload:
                continue
            sub = pkt.sub_cmd

            if sub == 101:   # OUT_CARD
                buf       = _Buf(pkt.payload)
                buf.read_byte()          # trustee
                out_chair = buf.read_word()
                card_byte = buf.read_byte()
                if out_chair == chair_id:
                    ci = _card_to_idx(card_byte)
                    if 0 <= ci < CARD_TYPES:
                        return ACTION_DISCARD_BASE + ci

            elif sub == 105:   # OPERATE_RESULT（杠/加杠来自摸牌后）
                buf           = _Buf(pkt.payload)
                operate_chair = buf.read_word()
                _provide      = buf.read_word()
                operate_code  = buf.read_byte()
                if operate_chair == chair_id:
                    if operate_code & _WIK_JIA_GANG:
                        return ACTION_JIAGANG
                    if operate_code & _WIK_GANG:
                        return ACTION_GANG

            elif sub == 107:   # CHIHU_RESULT（自摸胡来自摸牌后）
                buf      = _Buf(pkt.payload)
                op_chair = buf.read_word()
                if op_chair == chair_id:
                    return ACTION_HU

            elif sub == 108:   # GAME_END
                break
        return None

    # ── 操作决策（碰/杠/胡/过）────────────────────────────
    if 'case_g' in event or '操作' in event:
        for j in range(pkt_idx + 1, len(pkts)):
            pkt = pkts[j]
            if pkt.main_cmd != MDM_GF_GAME or not pkt.payload:
                continue
            sub = pkt.sub_cmd

            if sub == 105:   # OPERATE_RESULT
                buf           = _Buf(pkt.payload)
                operate_chair = buf.read_word()
                _provide      = buf.read_word()
                operate_code  = buf.read_byte()
                if operate_chair == chair_id:
                    if operate_code & _WIK_PENG:
                        return ACTION_PENG
                    if operate_code & _WIK_JIA_GANG:
                        return ACTION_JIAGANG
                    if operate_code & _WIK_GANG:
                        return ACTION_GANG

            elif sub == 107:   # CHIHU_RESULT
                buf      = _Buf(pkt.payload)
                op_chair = buf.read_word()
                if op_chair == chair_id:
                    return ACTION_HU

            elif sub == 102:   # SEND_CARD（机会过去了 → 过）
                return ACTION_PASS

            elif sub == 101:   # OUT_CARD（也意味着机会过去了）
                return ACTION_PASS

            elif sub == 108:   # GAME_END
                break
        return None

    return None


# ──────────────────────────────────────────────────────────
# 3. 提取游戏最终奖励
# ──────────────────────────────────────────────────────────

def _extract_reward(replay: VideoReplay, chair_id: int) -> float:
    """
    从 GAME_END(108) 和 RULE_SETTING(113) 包提取归一化奖励。
    reward = game_score[chair_id] / (REWARD_NORM * cell_score)
    """
    cell_score = 1.0
    game_score = 0.0

    for pkt in replay.packets:
        if pkt.main_cmd != MDM_GF_GAME or not pkt.payload:
            continue
        sub = pkt.sub_cmd

        if sub == 113:   # RULE_SETTING
            try:
                cs = float(_Buf(pkt.payload).read_int64())
                if cs > 0:
                    cell_score = cs
            except Exception:
                pass

        elif sub == 108:   # GAME_END
            try:
                buf = _Buf(pkt.payload)
                cs  = float(buf.read_int64())   # cell_score
                if cs > 0:
                    cell_score = cs
                [buf.read_word()  for _ in range(4)]   # provide_chairs
                buf.read_word()                         # escape_chair
                buf.read_byte()                         # escape_fan
                [buf.read_byte()  for _ in range(4)]   # geng_count
                [buf.read_byte()  for _ in range(4)]   # chihu_order
                [buf.read_dword() for _ in range(4)]   # chihu_kind
                [[buf.read_dword(), buf.read_dword()] for _ in range(4)]  # chihu_right
                scores = [buf.read_int64() for _ in range(4)]
                if 0 <= chair_id < len(scores):
                    game_score = float(scores[chair_id])
            except Exception:
                pass
            break

    norm = REWARD_NORM * cell_score
    return game_score / norm if norm != 0 else 0.0


# ──────────────────────────────────────────────────────────
# 4. 处理单个 .video 文件
# ──────────────────────────────────────────────────────────

def process_video_file(src_path: Path, out_dir: Path) -> int:
    """
    解析一个 .video 文件，生成训练数据并保存为 .npz。
    返回生成的样本数（非红中断勾卡或已存在则返回 0）。
    """
    out_path = out_dir / (src_path.stem + ".npz")
    if out_path.exists():
        return 0

    try:
        replay = parseVideoReplay(src_path)
    except Exception as e:
        print(f"  [解析失败] {src_path.name}: {e}")
        return 0

    # 仅处理红中断勾卡（KindID=150）
    if replay.kind_id != 150:
        return 0

    all_obs:   list[np.ndarray] = []
    all_act:   list[int]        = []
    all_mask:  list[np.ndarray] = []
    all_step:  list[int]        = []
    all_rew:   list[float]      = []

    for chair_id in range(replay.user_count):
        try:
            samples = generalChairTrainData(replay, chair_id)
        except Exception as e:
            print(f"  [通道生成失败] {src_path.name} chair{chair_id}: {e}")
            continue

        if not samples:
            continue

        reward = _extract_reward(replay, chair_id)

        for step_idx, sample in enumerate(samples):
            action = _determine_action(
                replay.packets, sample.pkt_idx, sample.event, chair_id
            )
            if action is None:
                continue  # 无法确定动作，丢弃此样本

            mask = _mask_from_channels(sample.channels)
            if not mask[action]:
                # 动作不在掩码内（异常数据），修正掩码以包含实际动作
                mask[action] = True

            all_obs.append(sample.channels)
            all_act.append(action)
            all_mask.append(mask)
            all_step.append(step_idx)
            all_rew.append(reward)

    if not all_obs:
        return 0

    obs_arr   = np.stack(all_obs,  axis=0).astype(np.float32)   # (N, 220, 19)
    act_arr   = np.array(all_act,  dtype=np.int16)               # (N,)
    mask_arr  = np.stack(all_mask, axis=0)                       # (N, 26)
    rew_arr   = np.array(all_rew,  dtype=np.float32)             # (N,)
    step_arr  = np.array(all_step, dtype=np.int32)               # (N,)
    # steps_to_done：同一玩家序列内的倒序步数；不同玩家各自独立计算
    max_step  = int(step_arr.max()) if len(step_arr) > 0 else 0
    std_arr   = (max_step - step_arr).astype(np.int32)

    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        obs           = obs_arr,
        actions       = act_arr,
        masks         = mask_arr,
        steps_to_done = std_arr,
        rewards       = rew_arr,
        true_scores   = rew_arr,
    )
    return len(all_obs)


# ──────────────────────────────────────────────────────────
# 5. 单任务包装（用于多进程）
# ──────────────────────────────────────────────────────────

def _worker(fp: Path) -> int:
    """多进程工作函数：处理单个文件并返回生成的样本数。"""
    try:
        return process_video_file(fp, OUT_DIR)
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────
# 6. 主入口
# ──────────────────────────────────────────────────────────

def main() -> None:
    video_files = sorted(SRC_DIR.rglob("*.video"))
    video_files = [p for p in video_files if "train_data" not in p.parts]

    # 过滤掉已有 npz 的文件（已处理过），减少无效并发任务
    pending = [fp for fp in video_files if not (OUT_DIR / (fp.stem + ".npz")).exists()]

    total      = len(video_files)
    done_count = total - len(pending)

    print(f"共找到 {total} 个 .video 文件")
    print(f"已处理（跳过）：{done_count}，待处理：{len(pending)}")
    print(f"输出目录：{OUT_DIR}")

    if not pending:
        print("所有文件均已处理完毕！")
        return

    workers = min(os.cpu_count() or 4, 8)
    print(f"并行进程数：{workers}")
    print("-" * 60)

    total_samples = 0
    skipped       = 0
    finished      = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_worker, fp): fp for fp in pending}
        for fut in as_completed(futures):
            finished += 1
            n = fut.result()
            if n == 0:
                skipped += 1
            else:
                total_samples += n
            if finished % 200 == 0 or finished == len(pending):
                pct = (done_count + finished) / total * 100
                print(
                    f"  进度：{done_count + finished}/{total} ({pct:.1f}%)"
                    f"  已生成 {total_samples} 条样本  跳过 {skipped}"
                )

    print("-" * 60)
    print(f"完成！共生成 {total_samples} 条训练样本，跳过 {skipped} 个文件。")
    print(f"数据保存在：{OUT_DIR}")


if __name__ == "__main__":
    main()
