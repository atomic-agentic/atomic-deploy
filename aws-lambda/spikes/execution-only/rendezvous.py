# SPDX-License-Identifier: Apache-2.0
#
# A stand-in for what atomic serve would host for execution-only placement on
# Lambda: the registry endpoint Codex's exec-server registers with in its
# `direct` remote mode, and a rendezvous that pipes the exec-server's outbound
# WebSocket to the Codex harness's. Frames pass unchanged (one JSON-RPC message
# per WebSocket message, both sides); the rendezvous only times each request.
#
#   POST /cloud/environment/{id}/direct/register   from exec-server (signed with SigV4; not verified here)
#   GET  /executor/{id}                             exec-server's WebSocket, dialled out from Lambda
#   GET  /harness/{id}                              Codex's WebSocket (its environments.toml url)
#   GET  /status, POST /done                        for the spike's driver
import asyncio
import json
import os
import time

from aiohttp import WSMsgType, web

# What exec-server is told to dial. It must be loopback or wss://, so it points
# at the Lambda handler's loopback relay, which forwards to this service.
EXECUTOR_URL = os.environ.get("EXECUTOR_URL", "ws://127.0.0.1:8090/executor/{id}")

state = {"registered": [], "executor": None, "harness_connected": False, "done": False,
         "requests": {}, "calls": [], "started": time.time()}
executor_ready = asyncio.Event()


async def register(request):
    env_id = request.match_info["id"]
    body = await request.json()
    signed = "AWS4-HMAC-SHA256" in request.headers.get("Authorization", "")
    state["registered"].append({"environment_id": env_id, "transport": body.get("transport"),
                                "sigv4_signed": signed, "at_s": round(time.time() - state["started"], 2)})
    return web.json_response({"environment_id": env_id, "transport": "direct_jsonrpc_v1",
                              "registration_id": "reg-%d" % len(state["registered"]),
                              "url": EXECUTOR_URL.format(id=env_id)})


def note_outbound(raw):
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    if "method" in msg and "id" in msg:
        state["requests"][msg["id"]] = (msg["method"], time.time())


def note_inbound(raw):
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    if "id" in msg and msg["id"] in state["requests"] and "method" not in msg:
        method, sent = state["requests"].pop(msg["id"])
        state["calls"].append({"method": method, "ms": round((time.time() - sent) * 1000, 1),
                               "error": "error" in msg})


async def executor(request):
    ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024, heartbeat=20)
    await ws.prepare(request)
    state["executor"] = {"ws": ws, "queue": asyncio.Queue(), "connected_at_s": round(time.time() - state["started"], 2)}
    executor_ready.set()
    async for msg in ws:
        if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
            await state["executor"]["queue"].put(msg)
    await state["executor"]["queue"].put(None)
    return ws


async def harness(request):
    ws = web.WebSocketResponse(max_msg_size=64 * 1024 * 1024, heartbeat=20)
    await ws.prepare(request)
    try:
        await asyncio.wait_for(executor_ready.wait(), timeout=120)
    except asyncio.TimeoutError:
        await ws.close(message=b"no executor")
        return ws
    state["harness_connected"] = True
    ex = state["executor"]

    async def to_executor():
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                note_outbound(msg.data)
                await ex["ws"].send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await ex["ws"].send_bytes(msg.data)

    async def to_harness():
        while True:
            msg = await ex["queue"].get()
            if msg is None:
                break
            if msg.type == WSMsgType.TEXT:
                note_inbound(msg.data)
                await ws.send_str(msg.data)
            else:
                await ws.send_bytes(msg.data)

    pumps = [asyncio.ensure_future(to_executor()), asyncio.ensure_future(to_harness())]
    await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
    for p in pumps:
        p.cancel()
    await ws.close()
    return ws


async def status(request):
    ex = state["executor"]
    by_method = {}
    for c in state["calls"]:
        by_method.setdefault(c["method"], []).append(c["ms"])
    return web.json_response({
        "registered": state["registered"],
        "executor_connected_at_s": ex["connected_at_s"] if ex else None,
        "harness_connected": state["harness_connected"],
        "done": state["done"],
        "calls": len(state["calls"]),
        "by_method": {m: {"count": len(v), "median_ms": sorted(v)[len(v) // 2], "max_ms": max(v)} for m, v in by_method.items()},
    })


async def done(request):
    state["done"] = True
    return web.json_response({"done": True})


app = web.Application()
app.add_routes([web.post("/cloud/environment/{id}/direct/register", register),
                web.get("/executor/{id}", executor), web.get("/harness/{id}", harness),
                web.get("/status", status), web.post("/done", done)])

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8765")), print=None)
