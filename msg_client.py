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
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from urllib.parse import urlparse

import websockets


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
        self.listbox = tk.Listbox(left, height=20, activestyle="dotbox")
        self.listbox.pack(fill=tk.BOTH, expand=True)
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
            self._append_msg(f"<{msg.get('from')}> {msg.get('content')}")
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
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先在左侧选中一个在线客户端")
            return
        to = self.online_clients[sel[0]]
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
    def _refresh_listbox(self):
        self.listbox.delete(0, tk.END)
        for n in self.online_clients:
            self.listbox.insert(tk.END, n)

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
