import asyncio
import json
import sys
from urllib.parse import urlparse

import websockets


async def run(uri: str, name: str) -> None:
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "register", "name": name}))

        async def receiver():
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"[invalid] {raw}")
                    continue
                t = msg.get("type")
                if t == "registered":
                    print(f"[system] registered as {msg.get('name')}")
                elif t == "online":
                    print(f"[system] online: {msg.get('names')}")
                elif t == "presence":
                    print(f"[system] {msg.get('name')} {msg.get('event')}")
                elif t == "msg":
                    print(f"<{msg.get('from')}> {msg.get('content')}")
                elif t == "broadcast":
                    print(f"[broadcast {msg.get('from')}] {msg.get('content')}")
                elif t == "error":
                    print(f"[error] {msg.get('reason')}")
                else:
                    print(f"[unknown] {msg}")

        async def sender():
            loop = asyncio.get_running_loop()
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.rstrip("\n")
                if not line:
                    continue
                if line.startswith("@"):
                    # @bob hello -> send to bob
                    parts = line[1:].split(maxsplit=1)
                    if len(parts) == 2:
                        await ws.send(json.dumps({
                            "type": "send",
                            "to": parts[0],
                            "content": parts[1],
                        }))
                    else:
                        print("[usage] @<name> <message>")
                elif line.startswith("!"):
                    # !hello -> broadcast
                    await ws.send(json.dumps({"type": "broadcast", "content": line[1:]}))
                else:
                    print("[usage] @<name> <msg>  or  !<broadcast>")

        await asyncio.gather(receiver(), sender())


def parse_args(argv: list[str]) -> tuple[str, str]:
    if len(argv) < 2:
        print("usage: python msg_client.py <name> [ws://host:port]")
        sys.exit(1)
    name = argv[1]
    uri = argv[2] if len(argv) > 2 else "ws://127.0.0.1:8765"
    if not urlparse(uri).scheme:
        uri = f"ws://{uri}"
    return uri, name


if __name__ == "__main__":
    uri, name = parse_args(sys.argv)
    try:
        asyncio.run(run(uri, name))
    except KeyboardInterrupt:
        pass
