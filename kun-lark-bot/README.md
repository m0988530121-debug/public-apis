# Kun — Lark 聊天機器人（跑在家裡的伺服器上）

Kun 是一個用 [Lark / 飛書官方 SDK](https://github.com/larksuite/oapi-sdk-python)
寫的聊天機器人，把你的 **開源 AI** 接到 Lark：你在 Lark 講的話會轉給 AI，
再把 AI 的回答回覆給你。它用 **長連線（WebSocket）模式** 連到 Lark，所以：

- ✅ 家裡的機器**不需要公開 IP**
- ✅ **不需要 port forwarding / 內網穿透 / 反向代理**
- ✅ **不需要對外開防火牆 port**

Kun 主動撥號連到 Lark，Lark 再把你傳的訊息推下來。

**支援哪些 AI？** 任何提供 **OpenAI 相容 `/chat/completions` 介面** 的開源
AI 都能接，例如 **Ollama、LM Studio、vLLM、LocalAI、text-generation-webui**。
你只要在 `.env` 設定 AI 的網址（`KUN_API_BASE`）與模型名（`KUN_MODEL`）即可。

---

## 一、在 Lark 開發者後台設定（一次性）

你說已經有 App ID / App Secret，以下確認幾項開關有打開：

1. 進入你的 App → **「機器人」(Bot)** 功能，**啟用機器人**。
2. → **「事件與回調」/ Event Subscription**：
   - 訂閱方式選 **「長連線 / Long Connection」**（不要選 Webhook，這樣就不用公開網址）。
   - 加入事件：**`im.message.receive_v1`**（接收訊息）。
3. → **「權限管理」/ Permissions**，加入並開啟：
   - `im:message`（讀取與發送單聊/群組訊息）
   - `im:message:send_as_bot`（以機器人身分發送訊息）
4. **發布版本**：改完權限/事件後要建立並發布一個版本，設定才會生效。
5. 把 Kun 機器人**加進**你要對話的單聊或群組。

> 註：海外版 Lark 後台是 https://open.larksuite.com/ ，
> 中國版飛書是 https://open.feishu.cn/ 。兩者 SDK 相同。

---

## 二、在家裡的機器上安裝與執行

需要 Python 3.8 以上。

```bash
cd kun-lark-bot

# 1) 安裝依賴
pip install -r requirements.txt

# 2) 填入設定
cp .env.example .env
#   用編輯器打開 .env：
#     - 填入 LARK_APP_ID 與 LARK_APP_SECRET
#     - 設定 KUN_API_BASE（你的開源 AI 網址）與 KUN_MODEL（模型名）

# 3) 啟動 Kun
set -a; source .env; set +a
python kun_bot.py
```

看到 `Kun 啟動中 — 正在以長連線模式連到 Lark…` 並且沒有報錯，就代表已連上。

---

## 三、遠端測試是否接通

在 Lark（手機或電腦都可以）對 Kun 傳訊息：

| 你傳 | Kun 回覆 |
|------|----------|
| `ping` | `pong ✅` + 家裡機器主機名稱與時間（**先確認 Lark 連線**）|
| `status` | 主機狀態 **＋ AI 端點是否可連線** |
| `reset` | 清除這個對話的上下文記憶 |
| `help` | 指令清單 |
| **其他任何文字** | **轉給開源 AI，回覆 AI 的回答**（這就是你要的「能對話回應」）|

驗證順序建議：
1. 先傳 `ping` → 收到 `pong ✅` 代表 **Lark → 家裡機器** 連線 OK。
2. 再傳 `status` → 看 `AI endpoint ... ✅ 可連線` 代表 AI 服務也接上了。
3. 直接打一句話（例如「你好，自我介紹一下」）→ 收到 AI 的回答，代表
   **整條「Lark 對話 → Kun(AI) 回應」已成功運作**。

---

## 四、讓 Kun 在家裡常駐（選用）

關掉終端機 Kun 就會停。要讓它開機自動執行，可用 systemd（Linux）：

```ini
# /etc/systemd/system/kun.service
[Unit]
Description=Kun Lark bot
After=network-online.target

[Service]
WorkingDirectory=/path/to/public-apis/kun-lark-bot
EnvironmentFile=/path/to/public-apis/kun-lark-bot/.env
ExecStart=/usr/bin/python3 kun_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kun
sudo systemctl status kun     # 看狀態
journalctl -u kun -f          # 看即時日誌
```

---

## 五、調整 AI 行為 / 擴充指令

- **換模型 / 換 AI**：改 `.env` 的 `KUN_MODEL` 與 `KUN_API_BASE` 即可，不用改程式。
- **調整人設**：改 `.env` 的 `KUN_SYSTEM_PROMPT`。
- **記憶長度**：改 `KUN_HISTORY_TURNS`（每個對話保留的來回輪數）。

要新增「指令型」功能，打開 `kun_bot.py`，在 `COMMANDS` 字典裡加。處理函式的
簽名是 `def handler(chat_id, arg) -> str`。例如回報磁碟空間：

```python
def cmd_disk(_chat_id, _arg):
    import shutil
    total, _used, free = shutil.disk_usage("/")
    return f"磁碟剩餘 {free // (2**30)} GB / 共 {total // (2**30)} GB"

COMMANDS["disk"] = (cmd_disk, "回報磁碟剩餘空間")
```

⚠️ **安全提醒**：不要在沒有嚴格白名單的情況下加入「任意 shell 指令執行」，
因為任何能傳訊息給 Kun 的人都能觸發它。
