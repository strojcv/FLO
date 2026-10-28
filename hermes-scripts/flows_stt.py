#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flows_stt.py — расшифровка голосовых для Hermes с сохранением исходника.

Hermes вызывает его как «STT command provider» (см. README.md):
    python3 $HOME/.hermes/scripts/flows_stt.py {input_path} {output_path}

Что делает:
  1. Расшифровывает аудио. По умолчанию — ТВОИМ уже установленным Whisper из venv
     (stt_backend = "venv"): движки из stt_engines по очереди (openai-whisper small,
     faster-whisper base, gigaam gigaam-v3-e2e-ctc — у него свой venv в stt_engine_python).
     Ничего не ставит и не скачивает. Можно переключить на Groq / OpenAI.
  2. Если запись длиннее stt_min_save_seconds (по умолчанию 60 с):
     - копирует аудио в  ~/brain/_audio/ГГГГ/ММ/<id>.<ext>
     - сохраняет расшифровку с таймкодами в  ~/brain/_inbox/voice/<id>.md
     - первой строкой ответа Hermes ставит метку  [voice:<id> · 18:04 · исходник сохранён]
  3. Отдаёт Hermes обычный текст (без таймкодов) — его агент и видит.

Короткие голосовые (команды вроде «новая точка», «что по рекламе») не сохраняются.
Ключи API — в ~/.config/flows/secrets.env (FLOWS_GROQ_API_KEY / FLOWS_OPENAI_API_KEY),
потому что Hermes вычищает свои секреты из окружения команд.
Сам скрипт — только стандартная библиотека; Whisper работает в отдельном процессе из venv.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flows  # noqa: E402  (общий конфиг, пути, Note)

SECRETS = flows.CONFIG_PATH.parent / "secrets.env"
WORKER = Path(__file__).resolve().with_name("flows_whisper_worker.py")
UPLOAD_EXT = {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "wav", "webm"}
MIME = {"ogg": "audio/ogg", "m4a": "audio/mp4", "mp3": "audio/mpeg", "wav": "audio/wav",
        "webm": "audio/webm", "flac": "audio/flac", "mp4": "audio/mp4"}


def secret(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if SECRETS.exists():
        for line in SECRETS.read_text("utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    return None


def upload_name(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower().lstrip(".")
    if ext == "oga" or ext == "opus":  # формат голосовых Telegram — по сути ogg
        ext = "ogg"
    if ext not in UPLOAD_EXT:
        ext = "ogg"
    return f"audio.{ext}", MIME.get(ext, "application/octet-stream")


def multipart(fields: dict, file_path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    fname, mime = upload_name(file_path)
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode() + file_path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def api_transcribe(url: str, key: str, fields: dict, path: Path) -> dict:
    body, ctype = multipart(fields, path)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": ctype, "User-Agent": "flows-stt/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} → HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def venv_transcribe(path: Path, lang: str | None):
    """Локальный Whisper из уже существующего venv, по очереди движки из stt_engines."""
    cfg = flows.CFG
    errors = []
    for spec in cfg.get("stt_engines") or ["openai-whisper:small", "faster-whisper:base"]:
        engine, _, model = str(spec).partition(":")
        # у движка может быть свой venv (GigaAM — отдельно от Whisper): stt_engine_python
        py = os.path.expanduser(str((cfg.get("stt_engine_python") or {}).get(engine)
                                    or cfg.get("stt_venv_python") or ""))
        if not py or not os.path.exists(py):
            errors.append(f"{spec}: не найден Python {py!r} — поправь stt_venv_python / stt_engine_python в {flows.CONFIG_PATH}")
            continue
        r = subprocess.run([py, str(WORKER), str(path), engine, model or "small", lang or ""],
                           capture_output=True, text=True)
        if r.returncode == 0:
            for line in reversed(r.stdout.strip().splitlines()):
                if line.startswith("{"):
                    data = json.loads(line)
                    segs = [(float(a), float(b), str(t)) for a, b, t in data.get("segments", []) if str(t).strip()]
                    if segs:
                        return segs, float(data.get("duration") or segs[-1][1]), spec
                    break
            errors.append(f"{spec}: пустой результат")
        else:
            lines = (r.stderr or r.stdout or "без вывода").strip().splitlines()
            errors.append(f"{spec}: {lines[-1][:300]}")
    raise RuntimeError(" | ".join(errors))


def transcribe(path: Path) -> tuple[list[tuple[float, float, str]], float, str]:
    """→ (сегменты [(начало, конец, текст)], длительность в секундах, чем расшифровано)."""
    cfg = flows.CFG
    backend = cfg.get("stt_backend", "venv")
    lang = cfg.get("stt_language") or None

    if backend == "venv":
        return venv_transcribe(path, lang)
    if backend == "groq":
        key = secret("FLOWS_GROQ_API_KEY")
        if not key:
            raise RuntimeError(f"Нет FLOWS_GROQ_API_KEY в {SECRETS}")
        fields = {"model": cfg.get("stt_groq_model", "whisper-large-v3"),
                  "response_format": "verbose_json", "temperature": "0"}
        if lang:
            fields["language"] = lang
        res = api_transcribe("https://api.groq.com/openai/v1/audio/transcriptions", key, fields, path)
        label = f"groq:{fields['model']}"
    elif backend == "openai":
        key = secret("FLOWS_OPENAI_API_KEY")
        if not key:
            raise RuntimeError(f"Нет FLOWS_OPENAI_API_KEY в {SECRETS}")
        model = cfg.get("stt_openai_model", "whisper-1")
        # таймкоды (verbose_json) отдаёт только whisper-1; gpt-4o-*-transcribe — просто текст
        fmt = "verbose_json" if model == "whisper-1" else "json"
        fields = {"model": model, "response_format": fmt}
        if lang:
            fields["language"] = lang
        res = api_transcribe("https://api.openai.com/v1/audio/transcriptions", key, fields, path)
        label = f"openai:{model}"
    else:
        raise RuntimeError(f"Неизвестный stt_backend: {backend}")

    segs = [(float(s.get("start", 0)), float(s.get("end", 0)), str(s.get("text", "")).strip())
            for s in res.get("segments") or []]
    if not segs and res.get("text"):
        segs = [(0.0, 0.0, res["text"].strip())]
    duration = float(res.get("duration") or (segs[-1][1] if segs else 0))
    return segs, duration, label


def mmss(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def plain_text(segs) -> str:
    """Текст для агента: абзац рвётся на паузе дольше 1.5 с."""
    paras, cur, prev_end = [], [], None
    for start, end, text in segs:
        if prev_end is not None and start - prev_end > 1.5 and cur:
            paras.append(" ".join(cur))
            cur = []
        cur.append(text)
        prev_end = end
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(p for p in paras if p.strip())


def save_source(audio: Path, segs, duration: float, engine: str = "", took: float = 0) -> str:
    t = flows.now()
    vid = t.strftime("%Y-%m-%d-%H%M%S")
    ext = audio.suffix.lower() or ".ogg"
    adir = flows.AUDIO_DIR / t.strftime("%Y") / t.strftime("%m")
    adir.mkdir(parents=True, exist_ok=True)
    apath = adir / f"{vid}{ext}"
    shutil.copy2(audio, apath)

    note = flows.Note(flows.INBOX_VOICE / f"{vid}.md")
    note.set("type", "source")
    note.set("id", vid)
    note.set("date", t.strftime("%Y-%m-%dT%H:%M"))
    note.set("duration", mmss(duration))
    note.set("audio", str(apath.relative_to(flows.BRAIN)))
    note.set("stt", engine)
    note.set("stt_seconds", int(took))
    note.set("flow", None)
    note.set("point", None)
    has_times = any(e > 0 for _, e, _ in segs)
    lines = [f"[{mmss(s)}] {txt}" if has_times else txt for s, _, txt in segs]
    note.body = f"\n# Голосовая {t:%d.%m %H:%M} ({mmss(duration)})\n\n" + "\n".join(lines) + "\n"
    note.save()
    return vid


ENGINE_NAMES = {"gigaam": "GigaAM", "openai-whisper": "Whisper", "faster-whisper": "faster-whisper"}


def engine_label(spec: str) -> str:
    """'gigaam:gigaam-v3-e2e-ctc' → 'GigaAM gigaam-v3-e2e-ctc'; облачные метки — как есть."""
    engine, _, model = spec.partition(":")
    name = ENGINE_NAMES.get(engine)
    return f"{name} {model}".strip() if name else spec


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: flows_stt.py <audio> [output.txt]", file=sys.stderr)
        return 2
    audio = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    started = time.monotonic()
    try:
        segs, duration, engine = transcribe(audio)
    except Exception as exc:
        print(f"flows_stt: расшифровка не удалась: {exc}", file=sys.stderr)
        return 1
    text = plain_text(segs)
    if not text.strip():
        print("flows_stt: пустая расшифровка", file=sys.stderr)
        return 1
    if duration >= float(flows.CFG.get("stt_min_save_seconds", 60)):
        try:
            vid = save_source(audio, segs, duration, engine, time.monotonic() - started)
            text = f"[voice:{vid} · {mmss(duration)} · исходник сохранён]\n\n{text}"
        except Exception as exc:  # сохранить не вышло — расшифровку всё равно отдаём
            print(f"flows_stt: не сохранил исходник: {exc}", file=sys.stderr)
            text = f"[ИСХОДНИК НЕ СОХРАНЁН: {exc}]\n\n{text}"
    # Hermes сам показывает расшифровку как 🎙️ "…" — свой значок не добавляем, иначе их два.
    # Внизу — чем расшифровано и за сколько.
    text = f"{text}\n\n— {engine_label(engine)} · {time.monotonic() - started:.0f} с"
    if out:
        out.write_text(text + "\n", "utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
