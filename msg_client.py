"""
msg_client.py — 消息客户端（带 GUI）

用法：
  python msg_client.py                        # 打开 GUI，手动填名字/地址连接
  python msg_client.py alice                  # 预填名字
  python msg_client.py alice ws://host:port   # 预填名字和地址

功能：
  - 显示在线客户端列表
  - 选中某个客户端发送点对点消息
  - 广播消息
  - 显示接收到的所有消息
"""

import asyncio
import json
import pickle
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, messagebox
from urllib.parse import urlparse

import websockets

# 复用 scyxjy_test 里的 ReplayBuilder / Pkt / _BufW / MAX_COUNT
sys.path.insert(0, str(Path(__file__).parent))
from scyxjy_test import ReplayBuilder, _BufW, MAX_COUNT
from scyxjy import generalChairTrainData, _card_to_idx
from scyxjy_gen_trainData import (
    _mask_from_channels,
    ACTION_DISCARD_BASE, ACTION_BAO_HU, ACTION_PENG, ACTION_GANG,
    ACTION_JIAGANG, ACTION_HU, ACTION_QINGHU, ACTION_PASS, ACTION_SPACE,
)

REPLAY_DIR = Path(__file__).parent / "replays"

# SanEngine 懒加载（首次用到时才加载 best.pt，避免启动卡顿）
_san_engine = None


def _get_san_engine():
    global _san_engine
    if _san_engine is None:
        from san_engine import SanEngine
        _san_engine = SanEngine()  # 默认加载 /Users/kebiaoy/Documents/MjTrainData/checkpoints/best.pt
    return _san_engine


def _idx_to_card(idx: int) -> int:
    """动作索引 0-18 → 牌字节（与 _card_to_idx 互逆）。"""
    if 0 <= idx <= 8:
        return 0x11 + idx          # 万 1-9
    if 9 <= idx <= 17:
        return 0x21 + (idx - 9)    # 条 1-9
    if idx == 18:
        return 0x35                # 红中
    return 0


def _action_name(action: int) -> str:
    names = {
        ACTION_BAO_HU: "报胡", ACTION_PENG: "碰", ACTION_GANG: "杠",
        ACTION_JIAGANG: "加杠", ACTION_HU: "胡", ACTION_QINGHU: "请胡", ACTION_PASS: "过",
    }
    if action < ACTION_BAO_HU:
        return f"弃牌({_idx_to_card(action):#x})"
    return names.get(action, f"?{action}")


def _infer_action_from_instructions(instructions: list) -> tuple[int, int, str] | None:
    """
    用 ReplayBuilder 构建 replay → generalChairTrainData 生成样本 → 取最新样本
    → SanEngine 推理出动作。

    返回 (action, card_byte, desc) 或 None（无法生成样本）。
    """
    replay, _ = build_replay_from_instructions(instructions)
    # 找 my_chair（sub=100 的 fields 里有）
    my_chair = 0
    for inst in instructions:
        if inst.get("sub") == 100:
            my_chair = (inst.get("fields") or {}).get("my_chair", 0)
            break
    # 生成训练样本（每个决策点一个）
    samples = generalChairTrainData(replay, my_chair)
    if not samples:
        return None
    sample = samples[-1]                      # 最新决策点
    obs = sample.channels                     # (220, 19) float32
    mask = _mask_from_channels(sample.channels)  # (26,) bool
    if not mask.any():
        return None
    engine = _get_san_engine()
    action, q_values = engine.react(obs, mask)
    # 弃牌动作 → 带牌字节；其他动作 card=0
    card = _idx_to_card(action) if action < ACTION_BAO_HU else 0
    desc = _action_name(action)
    return action, card, desc


def _infer_bao_hu_discard(instructions: list, my_chair: int) -> tuple[int, int, str] | None:
    """
    AI 报胡后，伪造一个 BAO_HU_NOTIFY（current_chair=INVALID, last_chair=my_chair, bao_flag=1）
    加入指令列表，重新构建 replay → generalChairTrainData 生成弃牌样本（is_discard=True,
    bao_hu_flags[my_chair]=True → Ch202 收窄为听牌牌）→ SanEngine 推理弃哪张牌。

    返回 (action, card_byte, desc) 或 None。
    """
    # 1. 复制指令列表，追加伪造的 BAO_HU_NOTIFY
    forged = list(instructions)
    forged.append({
        "sub": 115,
        "fields": {
            "current_chair": 0xFFFF,   # 报胡阶段全部结束 → 触发 case_b/d/e
            "last_chair":    my_chair,  # 我报胡了 → bao_hu_flags[my_chair]=True
            "bao_flag":      1,
            "card":          0,
        },
    })
    # 2. 重新构建 replay + 生成样本
    replay, _ = build_replay_from_instructions(forged)
    samples = generalChairTrainData(replay, my_chair)
    if not samples:
        return None
    sample = samples[-1]
    obs = sample.channels
    mask = _mask_from_channels(sample.channels)
    if not mask.any():
        return None
    engine = _get_san_engine()
    action, _ = engine.react(obs, mask)
    # 报胡后弃牌阶段只应出弃牌动作（0-18）；若 AI 输出其他动作，放弃
    if action >= ACTION_BAO_HU:
        return None
    card = _idx_to_card(action)
    return action, card, _action_name(action)


def _build_chihu_payload(fields: dict) -> bytes:
    """sub=107 (CHIHU_RESULT) payload，参考 scyxjy.py:_parse_hzdgk_chihu_result 逆操作。"""
    b = _BufW()
    b.u16(fields.get("operate_chair", 0xFFFF))
    b.u16(fields.get("provide_chair", 0xFFFF))
    b.u16(fields.get("hu_kind", 0))
    b.u8(fields.get("card", 0))
    b.u8(fields.get("multi_pao", 0))
    b.u8(fields.get("qing_hu", 0))
    b.u8(fields.get("card_count", 0))
    cards = fields.get("card_data") or []
    for i in range(MAX_COUNT):
        b.u8(cards[i] if i < len(cards) else 0xFF)
    return b.build()


def build_replay_from_instructions(instructions: list, n: int = 2):
    """把指令列表组装成 VideoReplay。返回 (VideoReplay, 包数)。"""
    builder = ReplayBuilder(n=n)
    for inst in instructions:
        sub = inst.get("sub")
        f = inst.get("fields") or {}
        if sub == 100:
            my_chair = f.get("my_chair", 0)
            banker_chair = f.get("banker_chair", 0)
            # 我的真实手牌（过滤无效牌）
            my_hand = [c for c in (f.get("hand_cards") or []) if c not in (0x00, 0xFF)]
            my_count = len(my_hand)
            hands = {my_chair: my_hand}
            # 对家手牌：庄家比非庄家多 1 张，用占位牌填充（值不重要，只影响 hand_counts 计数）
            for chair in range(n):
                if chair == my_chair:
                    continue
                if my_chair == banker_chair:
                    opp_count = my_count - 1       # 我是庄家，对家少 1 张
                else:
                    opp_count = my_count + 1       # 对家是庄家，多 1 张
                hands[chair] = [0x01] * max(0, opp_count)
            builder.game_starts(
                banker=banker_chair,
                hands=hands,
                bao_chair=f.get("bao_chair", 0xFFFF),
                magic_card=f.get("magic_card", 0x35),
            )
        elif sub == 101:
            builder.discard(chair=f.get("out_chair", 0), card=f.get("card", 0))
        elif sub == 102:
            builder.send(chair=f.get("current_chair", 0), card=f.get("card", 0),
                         action_mask=f.get("action_mask", 0))
        elif sub == 104:
            builder.operate_notify(resume_chair=f.get("resume_chair", 0xFFFF),
                                    action_mask=f.get("action_mask", 0),
                                    action_card=f.get("action_card", 0))
        elif sub == 105:
            builder.operate_result(operate_chair=f.get("operate_chair", 0xFFFF),
                                   provide_chair=f.get("provide_chair", 0xFFFF),
                                   code=f.get("code", 0),
                                   cards=f.get("cards") or [0, 0, 0])
        elif sub == 107:
            builder.add(107, _build_chihu_payload(f), chair=f.get("operate_chair", 0xFFFF))
        elif sub == 115:
            builder.bao_hu(current_chair=f.get("current_chair", 0xFFFF),
                           last_chair=f.get("last_chair", 0xFFFF),
                           bao_flag=f.get("bao_flag", 0),
                           card=f.get("card", 0))
        # sub=108 (gameEnd) 不入 replay 包序列，仅作为结束信号
    replay = builder.build()
    return replay, len(builder._pkts)



class MsgClientApp:
    def __init__(self, root: tk.Tk, default_name: str = "", default_uri: str = "ws://127.0.0.1:8765"):
        self.root = root
        root.title("消息客户端")
        root.geometry("760x520")
        root.minsize(640, 420)

        self.default_name = default_name
        self.default_uri = default_uri

        # 跨线程通信
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.ui_queue: queue.Queue = queue.Queue()  # websocket -> UI
        self.online_clients: list[str] = []
        self.selected_client: str | None = None  # 手动跟踪选中状态，不依赖 listbox 焦点
        self.connected = False

        self._build_ui()
        self.root.after(100, self._poll_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ──────────────────────────────────────────────────────────
    # UI 构建
    # ──────────────────────────────────────────────────────────
    def _build_ui(self):
        # 顶部连接栏
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="名字:").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self.name_var = tk.StringVar(value=self.default_name)
        ttk.Entry(top, textvariable=self.name_var, width=14).grid(row=0, column=1, sticky=tk.W, padx=(0, 12))

        ttk.Label(top, text="服务器:").grid(row=0, column=2, sticky=tk.W, padx=(0, 4))
        self.uri_var = tk.StringVar(value=self.default_uri)
        ttk.Entry(top, textvariable=self.uri_var, width=28).grid(row=0, column=3, sticky=tk.W, padx=(0, 12))

        self.connect_btn = ttk.Button(top, text="连接", command=self._on_connect)
        self.connect_btn.grid(row=0, column=4, sticky=tk.W)

        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(top, textvariable=self.status_var, foreground="#888").grid(row=0, column=5, sticky=tk.W, padx=(12, 0))

        # 主体：左在线列表，右消息区
        body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # 左：在线客户端
        left = ttk.LabelFrame(body, text="在线客户端", padding=4)
        body.add(left, weight=1)
        # activestyle=none 关掉焦点环；选中高亮完全由 _apply_selection_highlight 用 itemconfig 控制，
        # 这样焦点被输入框抢走后选中状态仍然可见、仍然可用
        self.listbox = tk.Listbox(
            left, height=20, activestyle="none",
            selectbackground="#d0e7ff", selectforeground="#000000",
        )
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)
        self.listbox.bind("Double-Button-1", lambda e: self.input_entry.focus_set())

        # 右：消息显示 + 输入
        right = ttk.Frame(body, padding=4)
        body.add(right, weight=3)

        ttk.Label(right, text="消息").pack(anchor=tk.W)
        self.msg_text = scrolledtext.ScrolledText(right, height=18, state=tk.DISABLED, wrap=tk.WORD)
        self.msg_text.pack(fill=tk.BOTH, expand=True)
        # 消息着色 tag
        self.msg_text.tag_config("system", foreground="#888")
        self.msg_text.tag_config("error", foreground="#c00")
        self.msg_text.tag_config("broadcast", foreground="#06c")
        self.msg_text.tag_config("sent", foreground="#080")

        # 输入栏
        input_bar = ttk.Frame(right, padding=(0, 6, 0, 0))
        input_bar.pack(fill=tk.X)
        ttk.Label(input_bar, text="输入:").pack(side=tk.LEFT, padx=(0, 4))
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(input_bar, textvariable=self.input_var)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.input_entry.bind("<Return>", lambda e: self._on_send())
        ttk.Button(input_bar, text="发送(选中)", command=self._on_send).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(input_bar, text="广播", command=self._on_broadcast).pack(side=tk.LEFT)

    # ──────────────────────────────────────────────────────────
    # 连接管理
    # ──────────────────────────────────────────────────────────
    def _on_connect(self):
        if self.connected:
            self._append_msg("[system] 已经连接，先断开再重连", "system")
            return
        name = self.name_var.get().strip()
        uri = self.uri_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入名字")
            return
        if not uri:
            messagebox.showwarning("提示", "请输入服务器地址")
            return
        if not urlparse(uri).scheme:
            uri = f"ws://{uri}"
            self.uri_var.set(uri)

        self.connect_btn.config(state=tk.DISABLED)
        self.status_var.set("连接中...")
        self.online_clients = []
        self._refresh_listbox()

        # 启动 asyncio 线程
        self.loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._run_async, args=(self.loop, uri, name), daemon=True)
        t.start()

    def _run_async(self, loop: asyncio.AbstractEventLoop, uri: str, name: str):
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ws_main(uri, name))
        except Exception as e:
            self.ui_queue.put({"type": "_error", "reason": str(e)})
        finally:
            self.ui_queue.put({"type": "_closed"})

    async def _ws_main(self, uri: str, name: str):
        async with websockets.connect(uri) as ws:
            self.ws = ws
            await ws.send(json.dumps({"type": "register", "name": name}))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self.ui_queue.put({"type": "_invalid", "raw": raw})
                    continue
                self.ui_queue.put(msg)

    async def _send(self, payload: dict):
        if self.ws is not None:
            await self.ws.send(json.dumps(payload, ensure_ascii=False))

    # ──────────────────────────────────────────────────────────
    # UI 队列轮询（主线程）
    # ──────────────────────────────────────────────────────────
    def _poll_ui_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_ui_queue)

    def _handle_msg(self, msg: dict):
        t = msg.get("type")
        if t == "registered":
            self.connected = True
            self.connect_btn.config(text="已连接", state=tk.DISABLED)
            self.status_var.set(f"已注册: {msg.get('name')}")
            self._append_msg(f"[system] 已注册为 {msg.get('name')}", "system")
        elif t == "online":
            self.online_clients = list(msg.get("names", []))
            self._refresh_listbox()
            self._append_msg(f"[system] 在线: {', '.join(self.online_clients) or '无'}", "system")
        elif t == "presence":
            name = msg.get("name")
            event = msg.get("event")
            if event == "join":
                if name not in self.online_clients:
                    self.online_clients.append(name)
                self._append_msg(f"[system] {name} 上线", "system")
            elif event == "leave":
                if name in self.online_clients:
                    self.online_clients.remove(name)
                self._append_msg(f"[system] {name} 下线", "system")
            self._refresh_listbox()
        elif t == "msg":
            content = msg.get("content")
            # 检测游戏指令列表消息（来自 sparrowhzdgk GameClientEngine）
            replay_built = False
            if isinstance(content, str):
                try:
                    inner = json.loads(content)
                    if isinstance(inner, dict) and inner.get("type") == "instructions":
                        instructions = inner.get("data") or []
                        replay, pkt_count = build_replay_from_instructions(instructions)
                        has_end = any(i.get("sub") == 108 for i in instructions)
                        self._append_msg(
                            f"{content}", "system")

                        # 含 gameEnd 的整套：保存到文件
                        if has_end:
                            REPLAY_DIR.mkdir(parents=True, exist_ok=True)
                            fname = REPLAY_DIR / f"replay_{int(time.time())}.pkl"
                            with open(fname, "wb") as fp:
                                pickle.dump(replay, fp)
                            self._append_msg(
                                f"[replay] 来自 {msg.get('from')}，含 gameEnd → 已保存 {fname.name}", "system")
                        # 非结束局：推理动作并回发给游戏客户端
                        if not has_end:
                            try:
                                result = _infer_action_from_instructions(instructions)
                            except Exception as ex:
                                result = None
                                self._append_msg(f"[infer] 推理失败: {ex}", "error")
                            if result is not None:
                                action, card, desc = result
                                sender = msg.get("from")
                                # 报胡/请胡特殊处理：伪造 BAO_HU_NOTIFY 重新推理弃牌，
                                # 两者弃牌都只能弃听牌（Ch202 收窄为 Ch203）
                                # 动作+弃牌合并到一条消息（card 字段=弃的牌）
                                if action in (ACTION_BAO_HU, ACTION_QINGHU):
                                    label = "报胡" if action == ACTION_BAO_HU else "请胡"
                                    my_chair = 0
                                    for inst in instructions:
                                        if inst.get("sub") == 100:
                                            my_chair = (inst.get("fields") or {}).get("my_chair", 0)
                                            break
                                    try:
                                        discard_result = _infer_bao_hu_discard(instructions, my_chair)
                                    except Exception as ex:
                                        discard_result = None
                                        self._append_msg(f"[infer] {label}弃牌推理失败: {ex}", "error")
                                    if discard_result is not None:
                                        _, card2, desc2 = discard_result
                                        merged = {"type": "action", "action": action,
                                                  "card": card2, "desc": f"{label}+弃牌({desc2})"}
                                        if self.connected and self.loop is not None:
                                            asyncio.run_coroutine_threadsafe(
                                                self._send({"type": "send", "to": sender,
                                                             "content": json.dumps(merged, ensure_ascii=False)}),
                                                self.loop,
                                            )
                                        self._append_msg(
                                            f"[infer] → {sender} {label}+弃牌({desc2} card={card2:#x})", "system")
                                    else:
                                        # 弃牌推理失败，单发（card=0）
                                        action_msg = {"type": "action", "action": action, "card": 0, "desc": desc}
                                        if self.connected and self.loop is not None:
                                            asyncio.run_coroutine_threadsafe(
                                                self._send({"type": "send", "to": sender,
                                                             "content": json.dumps(action_msg, ensure_ascii=False)}),
                                                self.loop,
                                            )
                                        self._append_msg(f"[infer] → {sender} 动作={action}({desc})", "system")
                                else:
                                    # 普通单动作
                                    action_msg = {"type": "action", "action": action, "card": card, "desc": desc}
                                    if self.connected and self.loop is not None:
                                        asyncio.run_coroutine_threadsafe(
                                            self._send({"type": "send", "to": sender,
                                                         "content": json.dumps(action_msg, ensure_ascii=False)}),
                                            self.loop,
                                        )
                                    self._append_msg(
                                        f"[infer] → {sender} 动作={action}({desc}) card={card:#x}", "system")
                        replay_built = True
                except (json.JSONDecodeError, ValueError):
                    pass
            if not replay_built:
                self._append_msg(f"<{msg.get('from')}> {content}")
        elif t == "broadcast":
            self._append_msg(f"[广播 {msg.get('from')}] {msg.get('content')}", "broadcast")
        elif t == "error":
            self._append_msg(f"[错误] {msg.get('reason')}", "error")
        elif t == "_invalid":
            self._append_msg(f"[无效数据] {msg.get('raw')}", "error")
        elif t == "_error":
            self._append_msg(f"[连接异常] {msg.get('reason')}", "error")
        elif t == "_closed":
            self.connected = False
            self.ws = None
            self.connect_btn.config(text="连接", state=tk.NORMAL)
            self.status_var.set("未连接")
            self.online_clients = []
            self._refresh_listbox()

    # ──────────────────────────────────────────────────────────
    # 发送
    # ──────────────────────────────────────────────────────────
    def _on_send(self):
        if not self.connected or self.loop is None:
            messagebox.showinfo("提示", "未连接")
            return
        if not self.selected_client:
            messagebox.showinfo("提示", "请先在左侧选中一个在线客户端")
            return
        to = self.selected_client
        content = self.input_var.get()
        if not content:
            return
        asyncio.run_coroutine_threadsafe(
            self._send({"type": "send", "to": to, "content": content}),
            self.loop,
        )
        self._append_msg(f"[我 → {to}] {content}", "sent")
        self.input_var.set("")

    def _on_broadcast(self):
        if not self.connected or self.loop is None:
            messagebox.showinfo("提示", "未连接")
            return
        content = self.input_var.get()
        if not content:
            return
        asyncio.run_coroutine_threadsafe(
            self._send({"type": "broadcast", "content": content}),
            self.loop,
        )
        self._append_msg(f"[我 → 全体] {content}", "sent")
        self.input_var.set("")

    # ──────────────────────────────────────────────────────────
    # 工具
    # ──────────────────────────────────────────────────────────
    def _on_listbox_select(self, _event):
        """点击列表项时，把选中客户端记到 self.selected_client，并刷新高亮。"""
        sel = self.listbox.curselection()
        if sel and sel[0] < len(self.online_clients):
            self.selected_client = self.online_clients[sel[0]]
        self._apply_selection_highlight()

    def _apply_selection_highlight(self):
        """用 itemconfig 给选中的那一行上色，焦点被抢走也能看到。"""
        for i in range(self.listbox.size()):
            self.listbox.itemconfig(i, background="", foreground="")
        if self.selected_client and self.selected_client in self.online_clients:
            idx = self.online_clients.index(self.selected_client)
            self.listbox.itemconfig(idx, background="#cfe8ff", foreground="#000000")
            # 同步原生选中态，保证视觉一致（焦点回来时不会跳）
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(idx)

    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        for n in self.online_clients:
            self.listbox.insert(tk.END, n)
        # 选中的客户端若已下线，清掉选中态
        if self.selected_client and self.selected_client not in self.online_clients:
            self.selected_client = None
        self._apply_selection_highlight()

    def _append_msg(self, text: str, tag: str = ""):
        self.msg_text.config(state=tk.NORMAL)
        self.msg_text.insert(tk.END, text + "\n", tag or ())
        self.msg_text.see(tk.END)
        self.msg_text.config(state=tk.DISABLED)

    def _on_close(self):
        # 关窗时把 asyncio 线程停掉
        if self.loop is not None and self.loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(self._close_ws(), self.loop).result(timeout=2)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()

    async def _close_ws(self):
        if self.ws is not None:
            await self.ws.close()


def parse_args(argv: list[str]) -> tuple[str, str]:
    name = argv[1] if len(argv) > 1 else "ControlApp"
    uri = argv[2] if len(argv) > 2 else "ws://192.168.0.200:8765"
    if uri and not urlparse(uri).scheme:
        uri = f"ws://{uri}"
    return name, uri


if __name__ == "__main__":
    name, uri = parse_args(sys.argv)
    root = tk.Tk()
    MsgClientApp(root, default_name=name, default_uri=uri)
    root.mainloop()
