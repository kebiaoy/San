"""Self-contained test for msg_server / msg_client protocol.

Starts the server in-process, then runs several client scenarios against it.
"""
import asyncio
import json
import sys

import websockets

import msg_server


async def recv_type(ws, expected_type, timeout=2.0):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    msg = json.loads(raw)
    assert msg["type"] == expected_type, f"expected {expected_type}, got {msg}"
    return msg


async def register(uri, name):
    ws = await websockets.connect(uri)
    await ws.send(json.dumps({"type": "register", "name": name}))
    await recv_type(ws, "registered")
    return ws


async def drain(ws, timeout=0.3):
    """Read any pending messages, return list. Tolerates connection close."""
    out = []
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            break
        except websockets.ConnectionClosed:
            break
        out.append(json.loads(raw))
    return out


async def main():
    srv = await websockets.serve(msg_server.connection_handler, "127.0.0.1", 18765)
    uri = "ws://127.0.0.1:18765"
    results = []

    def check(name, cond):
        results.append((name, cond))
        print(("PASS" if cond else "FAIL"), name)

    try:
        # 1. register alice & bob
        alice = await register(uri, "alice")
        bob = await register(uri, "bob")
        # alice should see bob join via presence
        msgs = await drain(alice)
        joined = any(m.get("type") == "presence" and m.get("name") == "bob" for m in msgs)
        check("presence notifies join", joined)

        # 2. alice -> bob: hello
        await alice.send(json.dumps({"type": "send", "to": "bob", "content": "hello bob"}))
        msgs = await drain(bob)
        got = next((m for m in msgs if m.get("type") == "msg"), None)
        check("p2p delivery", got and got["from"] == "alice" and got["content"] == "hello bob")

        # 3. send to non-existent
        await alice.send(json.dumps({"type": "send", "to": "nobody", "content": "hi"}))
        msgs = await drain(alice)
        err = next((m for m in msgs if m.get("type") == "error"), None)
        check("error on missing user", err and "not found" in err["reason"])

        # 4. broadcast
        carol = await register(uri, "carol")
        await drain(alice)  # carol join notice
        await drain(bob)
        await carol.send(json.dumps({"type": "broadcast", "content": "team hi"}))
        a_msgs = await drain(alice)
        b_msgs = await drain(bob)
        c_msgs = await drain(carol)
        a_got = any(m.get("type") == "broadcast" and m["content"] == "team hi" for m in a_msgs)
        b_got = any(m.get("type") == "broadcast" and m["content"] == "team hi" for m in b_msgs)
        c_self = any(m.get("type") == "broadcast" for m in c_msgs)
        check("broadcast reaches alice", a_got)
        check("broadcast reaches bob", b_got)
        check("broadcast skips sender", not c_self)

        # 5. name takeover: new alice kicks old alice
        alice2 = await register(uri, "alice")
        old_msgs = await drain(alice)
        kicked = any(m.get("type") == "error" and "taken" in m.get("reason", "") for m in old_msgs)
        check("old connection kicked on name clash", kicked)
        await alice.close()

        # 6. online list on connect
        dave = await register(uri, "dave")
        msgs = await drain(dave)
        online = next((m for m in msgs if m.get("type") == "online"), None)
        check("online list sent on connect", online and "bob" in online["names"] and "carol" in online["names"])

        # 7. leave presence
        await bob.close()
        msgs = await drain(carol)
        left = any(m.get("type") == "presence" and m.get("name") == "bob" and m.get("event") == "leave" for m in msgs)
        check("presence notifies leave", left)

        for ws in (alice2, bob, carol, dave):
            try:
                await ws.close()
            except Exception:
                pass
    finally:
        srv.close()
        await srv.wait_closed()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results)-len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
