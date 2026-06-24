#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kun — a Lark (Feishu) chatbot that runs on your home server.

It uses Lark's long-connection (WebSocket) mode, so the machine it runs on
does NOT need a public IP, port forwarding, or any tunnel: Kun dials out to
Lark and Lark pushes incoming messages down the connection.

Send Kun a message in Lark to verify the connection works end to end:
    ping            -> Kun replies "pong" with host + time   (connectivity test)
    status          -> Kun replies with basic host status
    echo <text>     -> Kun echoes <text> back
    help            -> Kun lists the commands it understands

Credentials are read from environment variables (see .env.example):
    LARK_APP_ID, LARK_APP_SECRET
"""

import json
import os
import platform
import socket
import sys
import time
from datetime import datetime

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        P2ImMessageReceiveV1,
        ReplyMessageRequest,
        ReplyMessageRequestBody,
    )
except ImportError:
    sys.stderr.write(
        "Missing dependency 'lark-oapi'. Install it first:\n"
        "    pip install -r requirements.txt\n"
    )
    raise


APP_ID = os.environ.get("LARK_APP_ID", "").strip()
APP_SECRET = os.environ.get("LARK_APP_SECRET", "").strip()

# A REST client (used to send / reply messages). The WS client only receives.
_api_client = None


def get_api_client():
    global _api_client
    if _api_client is None:
        _api_client = (
            lark.Client.builder()
            .app_id(APP_ID)
            .app_secret(APP_SECRET)
            .build()
        )
    return _api_client


# --------------------------------------------------------------------------- #
# Command handlers
#
# Add your own "home" actions here. Each handler receives the raw user text
# (already stripped of the leading command word) and returns a string reply.
# Keep handlers safe — do NOT add arbitrary shell execution unless you fully
# trust everyone who can message this bot.
# --------------------------------------------------------------------------- #
def cmd_ping(_arg: str) -> str:
    host = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"pong ✅\nhost: {host}\ntime: {now}\nKun is alive and connected."


def cmd_status(_arg: str) -> str:
    host = socket.gethostname()
    return (
        "Kun status ✅\n"
        f"host: {host}\n"
        f"os: {platform.system()} {platform.release()}\n"
        f"python: {platform.python_version()}\n"
        f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def cmd_echo(arg: str) -> str:
    return arg if arg else "(nothing to echo)"


def cmd_help(_arg: str) -> str:
    lines = ["Kun 指令清單："]
    lines += [f"  {name} — {desc}" for name, (_, desc) in COMMANDS.items()]
    return "\n".join(lines)


# name -> (handler, description)
COMMANDS = {
    "ping": (cmd_ping, "測試連線，回覆 pong + 主機與時間"),
    "status": (cmd_status, "回報這台家裡機器的基本狀態"),
    "echo": (cmd_echo, "把你輸入的文字原樣回覆"),
    "help": (cmd_help, "列出所有可用指令"),
}


def handle_text(text: str) -> str:
    """Route a plain-text message to a command handler."""
    text = (text or "").strip()
    if not text:
        return "（空訊息）輸入 help 看可用指令。"

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    handler, _desc = COMMANDS.get(cmd, (None, None))
    if handler is None:
        return f"不認識的指令：{cmd}\n輸入 help 看可用指令。"
    return handler(arg)


def extract_text(message) -> str:
    """Pull the plain text out of a Lark message event payload."""
    if getattr(message, "message_type", None) != "text":
        return ""
    try:
        content = json.loads(message.content or "{}")
    except (TypeError, ValueError):
        return ""
    # Lark text content looks like {"text": "hello"}; strip any @mentions.
    raw = content.get("text", "")
    return " ".join(p for p in raw.split() if not p.startswith("@")).strip()


def reply_to_message(message_id: str, text: str) -> None:
    body = (
        ReplyMessageRequestBody.builder()
        .content(json.dumps({"text": text}))
        .msg_type("text")
        .build()
    )
    request = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(body)
        .build()
    )
    resp = get_api_client().im.v1.message.reply(request)
    if not resp.success():
        lark.logger.error(
            f"reply failed: code={resp.code} msg={resp.msg} "
            f"log_id={resp.get_log_id()}"
        )


def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    message = data.event.message
    text = extract_text(message)
    lark.logger.info(f"received: {text!r} (msg_id={message.message_id})")

    reply = handle_text(text)
    reply_to_message(message.message_id, reply)


def build_event_handler():
    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )


def main() -> int:
    if not APP_ID or not APP_SECRET:
        sys.stderr.write(
            "LARK_APP_ID / LARK_APP_SECRET are not set.\n"
            "Copy .env.example to .env, fill in your credentials, then:\n"
            "    set -a; source .env; set +a; python kun_bot.py\n"
        )
        return 1

    handler = build_event_handler()
    ws_client = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )

    print("Kun 啟動中 — 正在以長連線模式連到 Lark…")
    print("連上後，在 Lark 對 Kun 傳 'ping' 即可遠端測試是否接通。")
    while True:
        try:
            ws_client.start()  # blocks; auto-reconnects internally
        except KeyboardInterrupt:
            print("\nKun 已停止。")
            return 0
        except Exception as exc:  # keep the home server bot resilient
            lark.logger.error(f"connection error: {exc}; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
