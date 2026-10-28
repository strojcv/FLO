#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запускается ПИТОНОМ ИЗ ТВОЕГО VENV с Whisper (не системным), его вызывает flows_stt.py:
    <venv>/bin/python flows_whisper_worker.py <аудио> <движок> <модель> <язык>

движок: openai-whisper | faster-whisper | gigaam
Печатает последней строкой JSON: {"segments": [[начало, конец, текст], ...], "duration": сек}
Ничего не устанавливает и не скачивает: берёт уже закэшированные модели (~/.cache/whisper,
~/.cache/huggingface).

gigaam — GigaAM v3 (Сбер, только русский) в ONNX int8 через onnx-asr: ~225 МБ, без PyTorch.
За раз модель берёт не больше 20–30 с, поэтому запись режется по самым тихим местам на куски
до 24 с — без отдельной VAD-модели.
"""
import array
import json
import os
import subprocess
import sys
import tempfile
import wave

SR = 16000
FRAME = SR // 10                 # 100 мс — шаг поиска паузы
CHUNK_MAX = 24 * SR              # предел GigaAM — 25 с, берём с запасом
CHUNK_MIN = 12 * SR              # раньше 12 с не режем


def load_pcm(path: str) -> array.array:
    """ffmpeg → 16 кГц моно, 16 бит."""
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
                          "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    return pcm


def split_points(pcm: array.array) -> list[tuple[int, int]]:
    """Куски до CHUNK_MAX, граница — в самом тихом 100-мс окне между CHUNK_MIN и CHUNK_MAX."""
    out, pos, n = [], 0, len(pcm)
    while n - pos > CHUNK_MAX:
        best, cut = None, pos + CHUNK_MAX
        for f in range(pos + CHUNK_MIN, pos + CHUNK_MAX - FRAME + 1, FRAME):
            e = sum(map(abs, pcm[f:f + FRAME]))
            if best is None or e < best:
                best, cut = e, f + FRAME // 2
        out.append((pos, cut))
        pos = cut
    if n > pos:
        out.append((pos, n))
    return out


def gigaam_transcribe(audio_path: str, model_name: str):
    import onnx_asr
    model = onnx_asr.load_model(model_name, quantization="int8")
    pcm = load_pcm(audio_path)
    segs = []
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "chunk.wav")
        for a, b in split_points(pcm):
            with wave.open(wav, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
                w.writeframes(pcm[a:b].tobytes())
            text = str(model.recognize(wav) or "").strip()
            if text:
                segs.append([a / SR, b / SR, text])
    return segs, len(pcm) / SR


def main() -> int:
    audio_path, engine, model_name = sys.argv[1], sys.argv[2], sys.argv[3]
    lang = (sys.argv[4] if len(sys.argv) > 4 else "") or None

    if engine == "openai-whisper":
        import whisper
        audio = whisper.load_audio(audio_path)        # ffmpeg → 16 кГц моно
        duration = len(audio) / 16000
        model = whisper.load_model(model_name)
        res = model.transcribe(audio, language=lang, fp16=False, verbose=None)
        segs = [[float(s["start"]), float(s["end"]), s["text"].strip()] for s in res.get("segments", [])]
    elif engine == "faster-whisper":
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        it, info = model.transcribe(audio_path, language=lang, vad_filter=True)
        segs = [[float(s.start), float(s.end), s.text.strip()] for s in it]
        duration = float(info.duration)
    elif engine == "gigaam":
        segs, duration = gigaam_transcribe(audio_path, model_name)
    else:
        print(f"неизвестный движок: {engine}", file=sys.stderr)
        return 2

    print(json.dumps({"segments": segs, "duration": duration}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
