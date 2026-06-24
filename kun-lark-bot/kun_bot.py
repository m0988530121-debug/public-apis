#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kun — a Lark (Feishu) chatbot bridged to an open-source AI, running on your
home server.

It uses Lark's long-connection (WebSocket) mode, so the machine it runs on
does NOT need a public IP, port forwarding, or any tunnel: Kun dials out to
Lark and Lark pushes incoming messages down the connection.

When you message it in Lark, Kun forwards your text to your open-source AI
(via an OpenAI-compatible /chat/completions endpoint — Ollama, vLLM, LocalAI,
LM Studio, text-generation-webui, etc.) and replies with the AI's answer.

A few built-in commands work without the AI, so you can test connectivity
before the model is wired up:
    ping            -> "pong" with host + time          (Lark connectivity test)
    status          -> host status + whether the AI endpoint is reachable
    reset           -> clear the conversation history for this chat
    help            -> list commands
Anything else you type is sent to the AI as a conversation.

Configuration via environment variables (see .env.example):
    LARK_APP_ID, LARK_APP_SECRET          (required, your Lark app)
    KUN_API_BASE                          (open-source AI base URL)
    KUN_MODEL                             (model name to use)
    KUN_API_KEY                           (optional; many local servers ignore it)
    KUN_SYSTEM_PROMPT                     (optional; persona / instructions)
"""

import json
import os
import platform
import socket
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
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


# --- Lark credentials -------------------------------------------------------
APP_ID = os.environ.get("LARK_APP_ID", "").strip()
APP_SECRET = os.environ.get("LARK_APP_SECRET", "").strip()

# --- Open-source AI ("Kun") endpoint ----------------------------------------
# Defaults target a local Ollama install. Point these at wherever your AI runs.
KUN_API_BASE = os.environ.get("KUN_API_BASE", "http://localhost:11434/v1").rstrip("/")
# Leave KUN_MODEL empty to auto-detect whichever model is installed (preferring
# a Qwen / "q"-prefixed one). Set it explicitly only if you want to pin a model.
KUN_MODEL = os.environ.get("KUN_MODEL", "").strip()
KUN_API_KEY = os.environ.get("KUN_API_KEY", "").strip()
KUN_SYSTEM_PROMPT = os.environ.get(
    "KUN_SYSTEM_PROMPT", "You are Kun, a helpful assistant. Reply concisely."
).strip()
KUN_TIMEOUT = float(os.environ.get("KUN_TIMEOUT", "120"))

# Keep a short rolling history per chat so conversations have context.
HISTORY_TURNS = int(os.environ.get("KUN_HISTORY_TURNS", "8"))
_history = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))

# Resolved model name is cached after the first lookup.
_resolved_model = None

# A REST client (used to reply to messages). The WS client only receives.
_api_client = None


def get_api_client():
    global _api_client
    if _api_client is None:
        _api_client = (
            lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()
        )
    return _api_client


def list_available_models() -> list:
    """Query the OpenAI-compatible /models endpoint for installed model ids."""
    headers = {}
    if KUN_API_KEY:
        headers["Authorization"] = f"Bearer {KUN_API_KEY}"
    request = urllib.request.Request(f"{KUN_API_BASE}/models", headers=headers)
    with urllib.request.urlopen(request, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m.get("id", "") for m in data.get("data", []) if m.get("id")]


def resolve_model() -> str:
    """Return the model to use.

    Honors KUN_MODEL if set; otherwise auto-detects the installed model so you
    never have to touch config when the model is updated or renamed. Prefers a
    Qwen / "q"-prefixed model when several are available.
    """
    global _resolved_model
    if KUN_MODEL:
        return KUN_MODEL
    if _resolved_model:
        return _resolved_model
    try:
        models = list_available_models()
    except Exception as exc:  # noqa: BLE001
        lark.logger.warning(f"could not auto-detect model: {exc}")
        return ""
    if not models:
        return ""
    preferred = next((m for m in models if m.lower().startswith("q")), None)
    _resolved_model = preferred or models[0]
    lark.logger.info(f"auto-detected model: {_resolved_model} (from {models})")
    return _resolved_model


# --------------------------------------------------------------------------- #
# Open-source AI call (OpenAI-compatible /chat/completions)
# --------------------------------------------------------------------------- #
def call_kun(chat_id: str, prompt: str) -> str:
    """Send the prompt (plus recent history) to the AI and return its reply."""
    model = resolve_model()
    if not model:
        return (
            f"⚠️ 找不到可用的模型。請確認 AI 服務（{KUN_API_BASE}）已啟動且至少裝了"
            "一個模型（Ollama 可用 `ollama list` 查看、`ollama pull qwen2.5` 下載），"
            "或在 .env 設定 KUN_MODEL。"
        )

    messages = []
    if KUN_SYSTEM_PROMPT:
        messages.append({"role": "system", "content": KUN_SYSTEM_PROMPT})
    messages.extend(_history[chat_id])
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps(
        {"model": model, "messages": messages, "stream": False}
    ).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if KUN_API_KEY:
        headers["Authorization"] = f"Bearer {KUN_API_KEY}"

    request = urllib.request.Request(
        f"{KUN_API_BASE}/chat/completions", data=payload, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=KUN_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        return f"⚠️ AI 回應錯誤 (HTTP {exc.code})：{detail}"
    except urllib.error.URLError as exc:
        return (
            f"⚠️ 連不到 Kun AI（{KUN_API_BASE}）：{exc.reason}\n"
            "請確認 AI 服務已啟動，且 KUN_API_BASE 設定正確。"
        )
    except Exception as exc:  # noqa: BLE001 - keep the bot alive
        return f"⚠️ 呼叫 AI 時發生未預期錯誤：{exc}"

    try:
        answer = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        return f"⚠️ AI 回傳格式無法解析：{json.dumps(data)[:300]}"

    # Remember this turn so follow-up messages keep context.
    _history[chat_id].append({"role": "user", "content": prompt})
    _history[chat_id].append({"role": "assistant", "content": answer})
    return answer or "（AI 回了空白內容）"


def ai_endpoint_reachable() -> bool:
    """Best-effort check that the AI base URL is at least listening."""
    try:
        urllib.request.urlopen(KUN_API_BASE, timeout=5)
        return True
    except urllib.error.HTTPError:
        return True  # responded (even if 404) -> server is up
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Built-in commands (work without the AI, for connectivity testing)
# --------------------------------------------------------------------------- #
def cmd_ping(_chat_id: str, _arg: str) -> str:
    host = socket.gethostname()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"pong ✅\nhost: {host}\ntime: {now}\nKun is alive and connected."


def cmd_status(_chat_id: str, _arg: str) -> str:
    reachable = "✅ 可連線" if ai_endpoint_reachable() else "❌ 連不到"
    model = resolve_model() or "(偵測不到)"
    model_src = "手動指定" if KUN_MODEL else "自動偵測"
    return (
        "Kun status ✅\n"
        f"host: {socket.gethostname()}\n"
        f"os: {platform.system()} {platform.release()}\n"
        f"python: {platform.python_version()}\n"
        f"AI endpoint: {KUN_API_BASE} ({reachable})\n"
        f"model: {model} ({model_src})\n"
        f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def cmd_reset(chat_id: str, _arg: str) -> str:
    _history.pop(chat_id, None)
    return "已清除這個對話的記憶。🧹"


def cmd_help(_chat_id: str, _arg: str) -> str:
    lines = ["Kun 指令清單："]
    lines += [f"  {name} — {desc}" for name, (_, desc) in COMMANDS.items()]
    lines.append("  其他任何文字 — 直接與 Kun(AI) 對話")
    return "\n".join(lines)


# name -> (handler, description)
COMMANDS = {
    "ping": (cmd_ping, "測試 Lark 連線，回覆 pong + 主機與時間"),
    "status": (cmd_status, "回報主機狀態與 AI 端點是否可連線"),
    "reset": (cmd_reset, "清除這個對話的上下文記憶"),
    "help": (cmd_help, "列出所有可用指令"),
}


def route(chat_id: str, text: str) -> str:
    """Built-in command if it matches one; otherwise talk to the AI."""
    text = (text or "").strip()
    if not text:
        return "（空訊息）輸入 help 看可用指令，或直接打字跟我聊天。"

    cmd = text.split(maxsplit=1)[0].lower()
    handler = COMMANDS.get(cmd, (None, None))[0]
    if handler is not None:
        arg = text[len(cmd):].strip()
        return handler(chat_id, arg)

    return call_kun(chat_id, text)


# --------------------------------------------------------------------------- #
# Lark plumbing
# --------------------------------------------------------------------------- #
def extract_text(message) -> str:
    if getattr(message, "message_type", None) != "text":
        return ""
    try:
        content = json.loads(message.content or "{}")
    except (TypeError, ValueError):
        return ""
    raw = content.get("text", "")
    # Strip @mentions so group chats work cleanly.
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
    chat_id = getattr(message, "chat_id", "") or "default"
    text = extract_text(message)
    lark.logger.info(f"received: {text!r} (chat={chat_id} msg={message.message_id})")

    reply = route(chat_id, text)
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
        APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO
    )

    print("Kun 啟動中 — 正在以長連線模式連到 Lark…")
    model = resolve_model() or "(啟動時偵測不到，會在第一次對話時再試)"
    print(f"AI 端點：{KUN_API_BASE}  模型：{model}")
    print("連上後，在 Lark 對 Kun 傳 'ping' 測試連線，或直接打字跟它對話。")
    while True:
        try:
            ws_client.start()  # blocks; auto-reconnects internally
        except KeyboardInterrupt:
            print("\nKun 已停止。")
            return 0
        except Exception as exc:  # noqa: BLE001 - keep the home server bot alive
            lark.logger.error(f"connection error: {exc}; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
