#!/usr/bin/env bash
# Ставит GigaAM v3 E2E-CTC (Сбер, русская речь, с пунктуацией) в ONNX int8 через onnx-asr
# (~225 МБ, без PyTorch) в отдельный venv и делает его первым движком расшифровки.
# Whisper остаётся запасным. Запускать под пользователем Hermes (не под root), после install.sh:
#   bash ~/flows-organizer/setup-gigaam.sh
# Можно запускать повторно. Откат: убрать "gigaam:…" из stt_engines в ~/.config/flows/config.json.
set -euo pipefail

VENV="$HOME/.venvs/gigaam"
MODEL="${1:-gigaam-v3-e2e-ctc}"
CFG="$HOME/.config/flows/config.json"

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
  || { echo "✗ onnx-asr нужен Python 3.10+, а тут $(python3 --version)"; exit 1; }
command -v ffmpeg >/dev/null || { echo "✗ Нет ffmpeg (под root: apt install ffmpeg)"; exit 1; }
[ -f "$CFG" ] || { echo "✗ Нет $CFG — сначала install.sh"; exit 1; }

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV" || { echo "✗ Не создался venv (под root: apt install python3-venv)"; exit 1; }
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q "onnx-asr[cpu,hub]"

echo "▸ Скачиваю модель $MODEL (int8) …"
"$VENV/bin/python" -c "import onnx_asr; onnx_asr.load_model('$MODEL', quantization='int8'); print('▸ Модель на месте')"

python3 - "$CFG" "$VENV/bin/python" "$MODEL" <<'PY'
import json, sys
path, py, model = sys.argv[1:]
c = json.load(open(path))
eng = [e for e in c.get("stt_engines") or ["openai-whisper:small", "faster-whisper:base"]
       if not str(e).startswith("gigaam:")]
c["stt_engines"] = [f"gigaam:{model}"] + eng
c.setdefault("stt_engine_python", {})["gigaam"] = py
json.dump(c, open(path, "w"), ensure_ascii=False, indent=2)
print("▸ Движки расшифровки:", ", ".join(c["stt_engines"]))
PY
du -sh "$VENV" 2>/dev/null | sed 's/^/▸ venv: /'
echo "✅ GigaAM подключён. Перезапуск Hermes не нужен: flows_stt.py читает config.json при каждом голосовом."
