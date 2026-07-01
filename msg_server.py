import asyncio
import json
import logging
import signal
from websockets.exceptions import ConnectionClosed
from websockets.server import serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("msg_server")

clients: dict[str, "Client"] = {}


class Client:
    def __init__(self, ws, name: str):
        self.ws = ws
        self.name = name

    async def send_json(self, obj: dict) -> None:
        await self.ws.send(json.dumps(obj, ensure_ascii=False))


async def broadcast_presence(event: str, name: str, exclude: "Client | None" = None) -> None:
    """Tell everyone (except optionally `exclude`) about join/leave."""
    payload = json.dumps({"type": "presence", "event": event, "name": name})
    for client in list(clients.values()):
        if client is exclude:
            continue
        try:
            await client.ws.send(payload)
        except ConnectionClosed:
            pass


async def handle_register(client: Client, name: str) -> dict | None:
    if not name or not isinstance(name, str):
        return {"type": "error", "reason": "invalid name"}
    if name in clients:
        old = clients.pop(name)
        try:
            await old.ws.send(json.dumps({"type": "error", "reason": "name taken, kicked"}))
            await old.ws.close()
        except ConnectionClosed:
            pass
        log.info("kicked existing connection for name=%s", name)
    clients[name] = client
    log.info("registered name=%s, online=%d", name, len(clients))
    return None


async def handle_send(sender: Client, msg: dict) -> dict | None:
    to = msg.get("to")
    content = msg.get("content")
    if not to or not isinstance(to, str):
        return {"type": "error", "reason": "missing 'to'"}
    target = clients.get(to)
    if target is None:
        return {"type": "error", "reason": f"user not found: {to}"}
    await target.send_json({"type": "msg", "from": sender.name, "content": content})
    return None


async def handle_broadcast(sender: Client, msg: dict) -> dict | None:
    content = msg.get("content")
    payload = json.dumps({"type": "broadcast", "from": sender.name, "content": content})
    for name, client in list(clients.items()):
        if name == sender.name:
            continue
        try:
            await client.ws.send(payload)
        except ConnectionClosed:
            pass
    return None


async def dispatch(raw: str, client: Client) -> dict | None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "error", "reason": "invalid json"}

    t = msg.get("type")
    if t == "send":
        return await handle_send(client, msg)
    if t == "broadcast":
        return await handle_broadcast(client, msg)
    return {"type": "error", "reason": f"unknown type: {t}"}


async def connection_handler(ws) -> None:
    name: str | None = None
    client: Client | None = None
    try:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
        except asyncio.TimeoutError:
            await ws.send(json.dumps({"type": "error", "reason": "register timeout"}))
            await ws.close()
            return

        try:
            hello = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send(json.dumps({"type": "error", "reason": "invalid json"}))
            await ws.close()
            return

        if hello.get("type") != "register" or not hello.get("name"):
            await ws.send(json.dumps({"type": "error", "reason": "must register first"}))
            await ws.close()
            return

        name = hello["name"]
        client = Client(ws, name)
        err = await handle_register(client, name)
        if err:
            await ws.send(json.dumps(err))
            await ws.close()
            return

        await ws.send(json.dumps({"type": "registered", "name": name}))
        await ws.send(json.dumps({
            "type": "online",
            "names": [n for n in clients if n != name],
        }))
        await broadcast_presence("join", name, exclude=client)

        async for raw in ws:
            err = await dispatch(raw, client)
            if err:
                await ws.send(json.dumps(err))

    except ConnectionClosed:
        pass
    finally:
        if client and name and clients.get(name) is client:
            clients.pop(name, None)
            await broadcast_presence("leave", name)
            log.info("disconnected name=%s, online=%d", name, len(clients))


async def main(host: str = "127.0.0.1", port: int = 8765) -> None:
    stop = asyncio.Future()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set_result, None)
        except NotImplementedError:
            pass

    log.info("server listening on ws://%s:%d", host, port)
    async with serve(connection_handler, host, port, ping_interval=20, ping_timeout=20):
        await stop


if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.200"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        pass
