"""
file_server.py — HTTP 文件服务器，供游戏客户端拉取 lua 脚本等资源

用法：
  python3 file_server.py                              # 默认 0.0.0.0:8080，根目录=脚本所在目录
  python3 file_server.py --port 9000                  # 自定义端口
  python3 file_server.py --root /path/to/files        # 自定义根目录
  python3 file_server.py --root /path --port 9000

特性：
  - 多线程并发（ThreadingHTTPServer）
  - .lua / .json / .txt 等扩展 MIME 类型
  - If-Modified-Since 缓存协商（客户端可省流量）
  - Range 断点续传（大文件支持）
  - 目录列表（GET / 列出根目录文件）
  - /__manifest__ 端点：返回所有文件清单（路径+大小+mtime+md5），便于客户端做增量更新
  - /InjectLua 端点：返回可注入的 lua 文件信息 JSON，格式 {"name":"ClientScene.lua","path":"http://host:port/ClientScene.lua"}

部署提示：
  游戏客户端通常按 luaScript 的目录结构拉取，例如：
    http://host:port/plaza/src/views/ClientScene.lua
  所以根目录下应保持同样的目录结构，把 ClientScene.lua 放到：
    <root>/plaza/src/views/ClientScene.lua
  或直接平铺：把根目录设为 San/，访问 http://host:port/ClientScene.lua
"""

import argparse
import hashlib
import json
import os
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

EXTRA_MIME = {
    ".lua":      "text/plain; charset=utf-8",
    ".luac":     "application/octet-stream",
    ".json":     "application/json; charset=utf-8",
    ".txt":      "text/plain; charset=utf-8",
    ".csv":      "text/csv; charset=utf-8",
    ".manifest": "text/plain; charset=utf-8",
    ".mp3":      "audio/mpeg",
    ".png":      "image/png",
    ".jpg":      "image/jpeg",
    ".jpeg":     "image/jpeg",
    ".pvr":      "application/octet-stream",
    ".ccbi":     "application/octet-stream",
    ".csd":      "text/plain; charset=utf-8",
    ".exportjson": "application/json; charset=utf-8",
}

ROOT_DIR = os.getcwd()


class FileHandler(SimpleHTTPRequestHandler):
    def guess_type(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in EXTRA_MIME:
            return EXTRA_MIME[ext]
        return super().guess_type(path)

    def end_headers(self):
        # 允许跨域，方便浏览器调试
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        # 用 urlparse 剥掉 query 字符串，兼容 /InjectLua? /InjectLua?x=1 等写法
        path_only = urlparse(self.path).path
        # /__manifest__ 端点：返回文件清单
        if path_only == "/__manifest__":
            self._send_manifest()
            return
        # /InjectLua 端点：返回可注入的 lua 文件信息（name + 下载 URL）
        if path_only == "/InjectLua" or path_only == "/InjectLua/":
            self._send_inject_lua()
            return
        super().do_GET()

    def _send_inject_lua(self):
        # 用请求的 Host 头拼下载地址，客户端访问哪个 IP/端口就返回对应链接
        addr = self.server.server_address
        host = f"{addr[0]}:{addr[1]}"


        name = "ClientScene.lua"
        payload = {
            "files":[{
                "name": name,
                "path": f"http://{host}/{name}",
            }]
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_manifest(self):
        files = []
        root = Path(ROOT_DIR)
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            rel = p.relative_to(root).as_posix()
            try:
                data = p.read_bytes()
            except OSError:
                continue
            files.append({
                "path":  rel,
                "size":  len(data),
                "mtime": int(p.stat().st_mtime),
                "md5":   hashlib.md5(data).hexdigest(),
            })
        body = json.dumps({"files": files, "generated": int(time.time())}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # 简化日志：时间 方法 路径 状态
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description="HTTP 文件服务器")
    parser.add_argument("--host", default="192.168.0.200", help="监听地址")
    parser.add_argument("--port", type=int, default=9000, help="监听端口")
    parser.add_argument("--root", default=None, help="根目录（默认脚本所在目录）")
    args = parser.parse_args()

    global ROOT_DIR
    ROOT_DIR = args.root or os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(ROOT_DIR):
        print(f"根目录不存在: {ROOT_DIR}")
        sys.exit(1)
    os.chdir(ROOT_DIR)

    server = ThreadingHTTPServer((args.host, args.port), FileHandler)
    print(f"文件服务器已启动")
    print(f"  地址: http://{args.host}:{args.port}/")
    print(f"  根目录: {ROOT_DIR}")
    print(f"  清单: http://{args.host}:{args.port}/__manifest__")
    print(f"  注入: http://{args.host}:{args.port}/InjectLua")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
        server.shutdown()


if __name__ == "__main__":
    main()
