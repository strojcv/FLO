"""Кнопки органайзера без нейросети.
command:new — кнопка (quick_commands type: alias → «/new flows <куда>»): переставить указатель
  (`flows.py go <куда>`) и убрать аргументы, чтобы /new прошёл как обычный — с чистым контекстом.
  Обычный /new не трогаем.
session:reset — после /new от кнопки прислать в Telegram экран под указателем
  (`flows.py view --if-asked`): каталог или карточку точки; обычный /new — тишина. Шлём сами через Bot API (TELEGRAM_BOT_TOKEN из окружения Hermes
  или ~/.hermes/.env), в личный чат (chat_id = user_id)."""
import asyncio
import json
import os
import urllib.request
from pathlib import Path

HH = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
FLOWS = HH / "scripts" / "flows.py"


async def _flows(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "python3", str(FLOWS), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    except asyncio.TimeoutError:
        proc.kill()
        return ""
    return out.decode("utf-8", "replace").strip()


def _token() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not tok and (HH / ".env").exists():
        for line in (HH / ".env").read_text("utf-8").splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    return tok


def _send(token: str, chat_id: str, text: str):
    data = json.dumps({"chat_id": chat_id, "text": text[:4000]}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


async def handle(event_type, context):
    if event_type == "session:reset":
        if context.get("platform") != "telegram" or not context.get("user_id"):
            return None
        text, token = await _flows("view", "--if-asked"), _token()
        if text and token:
            await asyncio.to_thread(_send, token, str(context["user_id"]), text)
        return None
    args = str(context.get("args") or "").split()
    if not args or args[0] != "flows":
        return None
    await _flows("go", *args[1:])
    return {"decision": "rewrite", "command_name": "new", "raw_args": ""}
