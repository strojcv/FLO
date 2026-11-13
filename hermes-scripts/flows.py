#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flows.py — «потоковый органайзер» для Hermes Agent.

Хранилище — обычная папка с markdown-файлами, которую можно открыть в Obsidian:

  ~/brain/
    ПОТОКИ.md                    оглавление всех потоков (генерируется, руками не править)
    _теги.md                     словарь тегов
    _inbox/voice/<id>.md         расшифровки голосовых, ещё не привязанные к потоку
    _inbox/заметки.md            мысли без потока
    _audio/ГГГГ/ММ/<id>.*        исходное аудио (в git не попадает)
    _templates/                  шаблоны для ручного создания точек в Obsidian
    flows/<slug>/_поток.md       шапка потока, комментарии (новые сверху), оглавление точек (собирается само)
    flows/<slug>/ГГГГ-ММ-ДД.md   точки потока: задачи и контекст, новые сверху (вторая за день: …-2.md)
    календарь/<дата>-<имя>.md    события и напоминания (с потоком или без)
    календарь/КАЛЕНДАРЬ.md       всё по времени (генерируется)
    flows/<slug>/sources/<id>.md привязанные к потоку расшифровки

Главный принцип: единственный источник правды — файлы точек и потоков.
Все сводки (ПОТОКИ.md, оглавления потоков, status, напоминания) каждый раз вычисляются
из них заново, поэтому ничего не «разъезжается».

Зависимости: только стандартная библиотека Python 3.9+.
Справка: python3 flows.py --help  /  python3 flows.py <команда> --help
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # очень старый Python
    ZoneInfo = None

# ─────────────────────────────── настройки ────────────────────────────────

CONFIG_PATH = Path(os.environ.get("FLOWS_CONFIG", "~/.config/flows/config.json")).expanduser()
STATE_PATH = CONFIG_PATH.parent / "state.json"

DEFAULT_CONFIG = {
    "brain": "~/brain",                                # где лежит хранилище
    "timezone": "UTC",      # в каком часовом поясе думать о датах
    "git_autosync": False,                             # pull перед изменением, commit+push после
    "current_flow_hours": 0,                           # сколько часов помнить указатель на поток (0 — пока не переключат)
    "remind_grace_hours": 12,                          # опоздавшее напоминание ещё шлём в этом окне
    "remind_pull_minutes": 5,                          # git pull в проверке напоминаний не чаще, чем раз в N минут
    "remind_reply_hint": False,                        # подсказка «отложи/перенеси» в конце напоминания
    "event_autodone_hours": 3,                         # событие календаря закрывается само через N ч после времени
    # настройки расшифровки (используются flows_stt.py)
    "stt_backend": "venv",                             # venv (твой локальный Whisper) | groq | openai
    "stt_venv_python": "~/.openclaw/tools/venv/bin/python",
    "stt_engines": ["openai-whisper:small", "faster-whisper:base"],  # по очереди, при сбое — следующий
    "stt_language": "ru",
    "stt_groq_model": "whisper-large-v3",
    "stt_openai_model": "whisper-1",
    "stt_min_save_seconds": 60,                        # короче — не сохраняем как исходник (это команды)
    "stt_timeout_seconds": 1800,                       # предел расшифровки на все движки вместе
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
        except Exception as exc:  # битый конфиг не должен ронять всё
            print(f"⚠ Не читается {CONFIG_PATH}: {exc}. Беру значения по умолчанию.", file=sys.stderr)
    if os.environ.get("FLOWS_BRAIN"):
        cfg["brain"] = os.environ["FLOWS_BRAIN"]
    return cfg


CFG = load_config()
BRAIN = Path(CFG["brain"]).expanduser()
FLOWS_DIR = BRAIN / "flows"
INBOX_DIR = BRAIN / "_inbox"
INBOX_VOICE = INBOX_DIR / "voice"
INBOX_NOTES = INBOX_DIR / "заметки.md"
AUDIO_DIR = BRAIN / "_audio"
INDEX_FILE = BRAIN / "ПОТОКИ.md"
TAGS_FILE = BRAIN / "_теги.md"
CAL_DIR = BRAIN / "календарь"
CAL_INDEX = CAL_DIR / "КАЛЕНДАРЬ.md"
TEMPLATES_DIR = BRAIN / "_templates"
FLOW_FILE = "_поток.md"

# Разделы записей: у точки — задачи и контекст (POINT_KEYS), комментарии — одни на поток
# (раздел в _поток.md, новые сверху; с 2026.11.12.3)
HEADINGS = {
    "tasks": "Задачи",
    "context": "Контекст",
    "comments": "Комментарии",
}
POINT_KEYS = ("tasks", "context")
# У потока всегда одна открытая точка — в неё пишется всё. Когда точку завершают («начинаем новую
# точку»), невыполненные (не скрытые) задачи переезжают в новую; сделанные (✓) и контекст
# остаются в завершённой — это история, её листают кнопками /prev и /next. Комментарии — у потока,
# точки их не трогают. Идей и выводов как понятий нет: всё — комментарии вперемешку.
FORWARD_KEYS = ("tasks",)
SECTIONS_HINT = "Раздел: контекст | задачи | комментарии"
TOC_HEADING = "Точки"
SECTION_ALIASES = {
    "контекст": "context", "сделано": "context", "context": "context", "ctx": "context",
    "задачи": "tasks", "задача": "tasks", "tasks": "tasks", "todo": "tasks",
    "шаг": "tasks", "шаги": "tasks", "следующий": "tasks", "дальше": "tasks", "next": "tasks",
    "комментарии": "comments", "комментарий": "comments", "коммент": "comments", "comments": "comments",
    "comment": "comments",
    # идей и выводов нет — слова хозяина по привычке значат «комментарий»
    "выводы": "comments", "вывод": "comments", "conclusions": "comments",
    "идеи": "comments", "идея": "comments", "ideas": "comments", "idea": "comments",
}
STATUS_ALIASES = {
    "open": "open", "открыт": "open", "открыть": "open", "active": "open",
    "paused": "paused", "pause": "paused", "пауза": "paused", "на паузе": "paused",
    "closed": "closed", "close": "closed", "закрыт": "closed", "закрыть": "closed",
}
STATUS_RU = {"open": "открыт", "paused": "на паузе", "closed": "закрыт"}
# Приоритетов два: 1 — основные (видны в /list, никак не подписаны), 2 — фоновые (/background).
PRIO_MAIN, PRIO_BG = 1, 2
PRIO_LABEL = {PRIO_MAIN: "основной", PRIO_BG: "фоновый"}
STALE_DAYS = {PRIO_MAIN: 7, PRIO_BG: 14}   # ⚠ «давно не трогали»: столько дней без записей
# Новое — сверху: задачи и контекст точки, комментарии потока.
BG_CMD = "background"


def prio_value(v) -> int:
    """priority из файла или от агента → 1 (основной) или 2 (фоновый)."""
    s = norm(str(v))
    if s in ("фон", "фоновый", "фоновые", "background", "bg"):
        return PRIO_BG
    if s in ("основной", "основные", "main"):
        return PRIO_MAIN
    n = int(s)
    if n not in (PRIO_MAIN, PRIO_BG):
        raise ValueError(s)
    return n
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

POINT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.md$")
ENTRY_DATE_RE = re.compile(r"^[-*]\s+(\d{4}-\d{2}-\d{2}):\s*")
WIKILINK_LABEL_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ─────────────────────────────── мелкие помощники ─────────────────────────

class FlowsError(Exception):
    """Ошибка, которую надо просто показать (без трассировки)."""


def die(msg: str):
    raise FlowsError(msg)


def warn(msg: str):
    print(f"⚠ {msg}", file=sys.stderr)


def _tz():
    name = CFG.get("timezone")
    if ZoneInfo and name:
        try:
            return ZoneInfo(name)
        except Exception:
            warn(f"Неизвестный часовой пояс {name!r}, беру время сервера.")
    return None


def now() -> datetime:
    """Текущее локальное время без tzinfo. FLOWS_NOW подменяет его в тестах."""
    fake = os.environ.get("FLOWS_NOW")
    if fake:
        return datetime.fromisoformat(fake)
    return datetime.now(_tz()).replace(tzinfo=None)


def today() -> date:
    return now().date()


def norm(s: str) -> str:
    return str(s).casefold().replace("ё", "е").strip()


def dm(d: date) -> str:
    """23.10 (или 23.10.2025, если год не текущий)."""
    return d.strftime("%d.%m") if d.year == today().year else d.strftime("%d.%m.%Y")


def dmy(d: date) -> str:
    """23.10.2026 — дата точки в карточке."""
    return d.strftime("%d.%m.%Y")


def dmt(dt: datetime | None, has_time: bool = True) -> str:
    if dt is None:
        return ""
    return dm(dt.date()) + (dt.strftime(" %H:%M") if has_time else "")


def short(s: str, n: int = 120) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


BRIEF_RE = re.compile(r"^\*\*(.+?)\*\*\.?\s*")
BRIEF_LEN = 70          # запись без заголовка в списках обрезается до этой длины
TITLE_LEN = 140         # заголовок в свёрнутом виде (тезис в 1–2 строки) длиннее — обрезается
FULL_INDENT = "   "     # раскрытая запись в несколько строк: строки текста — с этим отступом


HIDDEN_MARK = "✓ "      # скрытая запись: текст остаётся в файле, в Telegram не показывается


def is_hidden(entry: str) -> bool:
    return entry_parts(entry)[1].lstrip().startswith(HIDDEN_MARK.strip())


def brief(text: str, n: int = BRIEF_LEN) -> str:
    """Для списков: заголовок записи (**…** в начале), а если его нет — начало текста."""
    text = text.strip()
    if text.startswith(HIDDEN_MARK.strip()):
        text = text[len(HIDDEN_MARK.strip()):].strip()
    m = BRIEF_RE.match(text)
    if m:
        return m.group(1).strip().rstrip(".")
    s = " ".join(text.split())
    if len(s) <= n:
        return s
    return s[: n - 1].rsplit(" ", 1)[0].rstrip(",.;:—- ") + "…"   # по границе слова


def with_brief(text: str, title: str | None) -> str:
    """Текст записи с заголовком: «**Рассылка неоплатившим.** Полный текст…»."""
    text = BRIEF_RE.sub("", text.strip(), count=1)
    title = " ".join((title or "").split()).rstrip(".")
    return f"**{title}.** {text}" if title else text


def section_key(name: str) -> str:
    """Раздел по слову хозяина → ключ («идея», «вывод» — это комментарий)."""
    key = SECTION_ALIASES.get(norm(name or ""))
    if not key:
        die(SECTIONS_HINT)
    return key


def plain(s: str) -> str:
    """Убирает вики-ссылки Obsidian для показа в Telegram: [[путь|подпись]] → подпись."""
    s = WIKILINK_LABEL_RE.sub(r"\2", s)
    return WIKILINK_RE.sub(lambda m: m.group(1).split("/")[-1], s)


def days_ago(d: date) -> int:
    return (today() - d).days


def ago(d: date) -> str:
    n = days_ago(d)
    if n <= 0:
        return "сегодня"
    if n == 1:
        return "вчера"
    return f"{n} дн. назад"


def read_text_input(text: str) -> str:
    """'-' означает: прочитать текст из stdin (удобно для многострочного)."""
    if text == "-":
        text = sys.stdin.read()
    text = text.strip()
    if not text:
        die("Пустой текст.")
    return text


TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
     "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


def slugify(title: str) -> str:
    out = "".join(TRANSLIT.get(ch, ch) for ch in title.casefold())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:40].strip("-") or "flow"


# ─────────────────────────────── даты и время ─────────────────────────────

def parse_dt(value) -> tuple[datetime | None, bool]:
    """Разбирает дату/время. Возвращает (datetime, есть_ли_время)."""
    if value is None:
        return None, False
    if isinstance(value, datetime):
        return value, True
    s = str(value).strip().replace("Т", "T")
    if not s:
        return None, False
    for fmt, has_time in (
        ("%Y-%m-%dT%H:%M", True), ("%Y-%m-%d %H:%M", True),
        ("%Y-%m-%dT%H:%M:%S", True), ("%Y-%m-%d %H:%M:%S", True),
        ("%Y-%m-%d", False),
        ("%d.%m.%Y %H:%M", True), ("%d.%m.%Y", False),
    ):
        try:
            return datetime.strptime(s, fmt), has_time
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:  # «27.10» или «27.10 19:00» без года
        try:
            dt = datetime(today().year, int(m.group(2)), int(m.group(1)),
                          int(m.group(3) or 0), int(m.group(4) or 0))
            if dt.date() < today() - timedelta(days=60):  # «05.01» в декабре — это следующий год
                dt = dt.replace(year=today().year + 1)
            return dt, bool(m.group(3))
        except ValueError:
            pass
    return None, False


def iso_dt(dt: datetime, has_time: bool) -> str:
    """Формат, который понимает Obsidian Properties: 2026-10-27T19:00 или 2026-10-27."""
    return dt.strftime("%Y-%m-%dT%H:%M") if has_time else dt.strftime("%Y-%m-%d")


def parse_delta(s: str) -> timedelta | None:
    """'30m', '2h', '1d', '1h30m', '90' (минуты) → timedelta."""
    s = s.strip().lower().replace(" ", "")
    if re.fullmatch(r"\d+", s):
        return timedelta(minutes=int(s))
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m(?:in)?)?", s)
    if not m or not any(m.groups()):
        m2 = re.fullmatch(r"(?:(\d+)д)?(?:(\d+)ч)?(?:(\d+)м(?:ин)?)?", s)
        if not m2 or not any(m2.groups()):
            return None
        m = m2
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


def parse_reminds(spec: str, w: datetime, has_time: bool) -> list[datetime]:
    """'30m' | '1d,1h' | '2026-10-27 18:30' | 'none' → моменты напоминаний для точки на w."""
    out: list[datetime] = []
    for part in [x.strip() for x in str(spec).split(",") if x.strip()]:
        if norm(part) in ("none", "нет", "без", "off"):
            return []
        delta = parse_delta(part)
        if delta is not None:
            if not has_time:
                if delta % timedelta(days=1):
                    die("Для напоминания «за N минут/часов» у точки нужно время: --when \"2026-10-27 19:00\"")
                out.append((w - delta).replace(hour=9, minute=0))  # «за день» к точке без времени → 09:00
            else:
                out.append(w - delta)
            continue
        r, r_time = parse_dt(part)
        if not r:
            die(f"Не понял время напоминания «{part}». Примеры: 30m, 2h, 1d, 1d,1h или 2026-10-27 18:30")
        out.append(r if r_time else r.replace(hour=9, minute=0))
    return sorted(set(out))


def set_reminds(note: "Note", reminds: list[datetime]):
    vals = [iso_dt(r, True) for r in sorted(set(reminds))]
    if not vals:
        note.delete("remind")
    else:
        note.set("remind", vals[0] if len(vals) == 1 else vals)


# ─────────────────────────────── frontmatter ──────────────────────────────
# Свой маленький разборщик YAML-шапки: без PyYAML и с сохранением полей,
# которых скрипт не знает (их мог добавить Obsidian или ты руками).

_NEEDS_QUOTE = re.compile(
    r"""^[\s\-?:,\[\]{}#&*!|>'"%@`]|[:#]\s|:$|\s$|^$|^(true|false|null|yes|no|~)$""", re.I)


def yq(s) -> str:
    s = str(s)
    if _NEEDS_QUOTE.search(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def unq(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if text.startswith("---\n") or text.startswith("---\r\n"):
        text = text.replace("\r\n", "\n")
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[4:end]
            rest = text[end + 4:]
            rest = rest[1:] if rest.startswith("\n") else rest
            return fm, rest
    return None, text


_KEY_RE = re.compile(r"^([^\s:#][^:]*?):(?:\s|$)")


def parse_fm(fm: str) -> list[list]:
    items: list[list] = []
    for line in fm.split("\n"):
        m = _KEY_RE.match(line)
        if m:
            items.append([m.group(1).strip(), [line]])
        elif items:
            items[-1][1].append(line)
        elif line.strip():
            items.append([None, [line]])
    return items


def fm_value(lines: list[str]):
    first = lines[0].split(":", 1)[1].strip() if ":" in lines[0] else ""
    rest = [l for l in lines[1:] if l.strip()]
    if first.startswith("[") and first.endswith("]"):
        inner = first[1:-1]
        parts = re.findall(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|[^,]+', inner)
        return [unq(p) for p in parts if p.strip()]
    if not first and rest and all(l.strip().startswith("-") for l in rest):
        return [unq(l.strip()[1:]) for l in rest if l.strip()[1:].strip()]
    if not first and not rest:
        return None
    return unq(first)


def render_kv(key: str, value) -> str:
    if value is None or value == "":
        return f"{key}:"
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (list, tuple)):
        return f"{key}: [" + ", ".join(yq(v) for v in value) + "]"
    if isinstance(value, int):
        return f"{key}: {value}"
    return f"{key}: {yq(value)}"


class Note:
    """Markdown-файл с YAML-шапкой."""

    def __init__(self, path: Path):
        self.path = Path(path)
        text = self.path.read_text("utf-8") if self.path.exists() else ""
        fm, body = split_frontmatter(text)
        self.items = parse_fm(fm) if fm is not None else []
        self.body = body

    def get(self, key: str, default=None):
        for k, lines in self.items:
            if k == key:
                v = fm_value(lines)
                return default if v in (None, "", []) else v
        return default

    def set(self, key: str, value):
        line = render_kv(key, value)
        for item in self.items:
            if item[0] == key:
                item[1] = [line]
                return
        self.items.append([key, [line]])

    def delete(self, key: str):
        self.items = [it for it in self.items if it[0] != key]

    def text(self) -> str:
        fm = "\n".join(l for _, lines in self.items for l in lines)
        body = self.body if self.body.startswith("\n") else "\n" + self.body
        return f"---\n{fm}\n---{body}" if fm else body.lstrip("\n")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        out = self.text()
        if not out.endswith("\n"):
            out += "\n"
        tmp.write_text(out, "utf-8")
        os.replace(tmp, self.path)

    # ── разделы тела ──

    @staticmethod
    def heading_match(text: str, heading: str) -> bool:
        """«Задача» ≈ «Задачи», но «Глобальные идеи» ≠ «Глобальные выводы»."""
        a, b = norm(text).split(), norm(heading).split()
        if len(a) != len(b):
            return False
        return all(x == y or _common_prefix(x, y) >= min(len(y), 5) for x, y in zip(a, b))

    def _find_section(self, lines: list[str], heading: str) -> tuple[int | None, int | None]:
        start = None
        for i, l in enumerate(lines):
            m = re.match(r"^##\s+(.*)$", l)
            if m and self.heading_match(m.group(1), heading):
                start = i
                break
        if start is None:
            return None, None
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^#{1,2}\s", lines[j]):
                end = j
                break
        return start, end

    def entries(self, heading: str) -> list[str]:
        lines = self.body.split("\n")
        s, e = self._find_section(lines, heading)
        if s is None:
            return []
        out: list[str] = []
        for l in lines[s + 1:e]:
            if re.match(r"^[-*]\s+", l):
                out.append(l)
            elif l.strip() and out and (l.startswith(" ") or l.startswith("\t")):
                out[-1] += "\n" + l
            elif l.strip():
                out.append(l)
        return out

    def append_entry(self, heading: str, entry: str):
        lines = self.body.rstrip("\n").split("\n")
        s, e = self._find_section(lines, heading)
        if s is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", f"## {heading}", entry]
        else:
            ins = e
            while ins - 1 > s and not lines[ins - 1].strip():
                ins -= 1
            lines[ins:ins] = entry.split("\n")
            after = ins + len(entry.split("\n"))
            if after < len(lines) and lines[after].startswith("#"):
                lines.insert(after, "")
        self.body = "\n".join(lines) + "\n"

    def prepend_entry(self, heading: str, entry: str, before: str | None = None):
        """Запись в начало раздела (новые сверху). Раздела нет — создаётся перед разделом before
        (нет и его — в конце файла)."""
        lines = self.body.rstrip("\n").split("\n")
        s, _ = self._find_section(lines, heading)
        new = entry.split("\n")
        if s is None:
            b = self._find_section(lines, before)[0] if before else None
            if b is None:
                while lines and not lines[-1].strip():
                    lines.pop()
                lines += ["", f"## {heading}", *new]
            else:
                lines[b:b] = [f"## {heading}", *new, ""]
        else:
            lines[s + 1:s + 1] = new
            after = s + 1 + len(new)
            if after < len(lines) and lines[after].startswith("#"):
                lines.insert(after, "")
        self.body = "\n".join(lines) + "\n"

    def set_entries(self, heading: str, entries: list[str]):
        """Заменить записи раздела (тот же раздел, новый порядок)."""
        lines = self.body.split("\n")
        s, e = self._find_section(lines, heading)
        if s is None:
            return
        tail = [""] if e < len(lines) else []
        lines[s + 1:e] = [l for x in entries for l in x.split("\n")] + tail
        self.body = "\n".join(lines)

    def drop_section(self, heading: str) -> list[str]:
        """Убрать раздел целиком (заголовок и записи); вернуть его записи."""
        lines = self.body.split("\n")
        s, e = self._find_section(lines, heading)
        if s is None:
            return []
        es = self.entries(heading)
        del lines[s:e]
        self.body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
        return es

    def remove_entry(self, entry: str) -> bool:
        if entry in self.body:
            # удаляем первое вхождение вместе с переводом строки
            idx = self.body.find(entry)
            end = idx + len(entry)
            if end < len(self.body) and self.body[end] == "\n":
                end += 1
            self.body = self.body[:idx] + self.body[end:]
            return True
        return False

    def set_h1(self, title: str):
        lines = self.body.split("\n")
        for i, l in enumerate(lines):
            if re.match(r"^#\s+", l):
                lines[i] = f"# {title}"
                self.body = "\n".join(lines)
                return
        self.body = f"\n# {title}\n" + self.body

    def h1(self) -> str:
        m = re.search(r"^#\s+(.+)$", self.body, re.M)
        return m.group(1).strip() if m else ""


def make_entry(text: str, src: str | None = None, day: date | None = None) -> str:
    lines = [l.rstrip() for l in text.strip().split("\n") if l.strip()]
    first = f"- {(day or today()).isoformat()}: {lines[0]}"
    rest = ["  " + l.strip() for l in lines[1:]]
    if src:
        if rest:
            rest[-1] += f" · {src}"
        else:
            first += f" · {src}"
    return "\n".join([first] + rest)


def entry_parts(entry: str) -> tuple[date | None, str]:
    m = ENTRY_DATE_RE.match(entry)
    if m:
        try:
            return date.fromisoformat(m.group(1)), entry[m.end():]
        except ValueError:
            pass
    return None, re.sub(r"^[-*]\s+", "", entry)


# ─────────────────────────────── модель: поток и точка ────────────────────

class Point:
    """Точка — один заход в работу: ГГГГ-ММ-ДД.md (открыта или закрыта). У потока одна открытая."""

    def __init__(self, flow: "Flow", path: Path):
        self.flow = flow
        self.path = path
        self.note = Note(path)
        m = POINT_RE.match(path.name)
        self.day = date.fromisoformat(m.group(1))
        self.suffix = int(m.group(2) or 1)

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def is_open(self) -> bool:
        return norm(self.note.get("status", "closed")) == "open"

    @property
    def title(self) -> str:
        return str(self.note.get("title") or "").strip()

    @property
    def label(self) -> str:
        return self.title or "(без заголовка)"

    @property
    def where(self) -> str:
        """Как назвать точку в ответе: «23.10 «Заголовок»»."""
        return f"{dm(self.day)} «{self.label}»"

    @property
    def summary(self) -> str:
        """Выжимка точки одной фразой: поле summary, иначе первая запись «Контекста»."""
        s = str(self.note.get("summary") or "").strip()
        if s:
            return s
        for e in reversed(self.entries("context")):   # новые сверху — берём самую раннюю
            _, t = entry_parts(e)
            t = plain(t).strip()
            if t and not t.startswith("🎙"):
                return short(t.split("\n")[0], 100)
        return ""

    @property
    def closed_on(self) -> date | None:
        dt, _ = parse_dt(self.note.get("closed"))
        return dt.date() if dt else None

    @property
    def dates_txt(self) -> str:
        """Только дата создания точки."""
        return dmy(self.day)

    def link(self, label: str | None = None) -> str:
        return f"[[flows/{self.flow.slug}/{self.stem}|{label or self.label}]]"

    def entries(self, key: str) -> list[str]:
        return self.note.entries(HEADINGS[key])

    def visible(self, key: str) -> list[str]:
        """Записи без скрытых (✓) — только их показываем."""
        return [e for e in self.entries(key) if not is_hidden(e)]

    def hidden_count(self, key: str) -> int:
        return sum(1 for e in self.entries(key) if is_hidden(e))

    def texts(self, key: str, n: int = 120) -> list[str]:
        """Полный текст видимых записей (для «на чём остановились», show, point)."""
        return [short(plain(entry_parts(e)[1]), n) for e in self.visible(key)]

    def briefs(self, key: str) -> list[str]:
        """Короткие заголовки видимых записей — для списков (status, /list, календарь, напоминания)."""
        return [brief(plain(entry_parts(e)[1])) for e in self.visible(key)]

    def last_date(self) -> date:
        dates = [self.day]
        closed, _ = parse_dt(self.note.get("closed"))
        if closed:
            dates.append(closed.date())
        for m in re.finditer(r"^[-*]\s+(\d{4}-\d{2}-\d{2}):", self.note.body, re.M):
            try:
                dates.append(date.fromisoformat(m.group(1)))
            except ValueError:
                pass
        past = [d for d in dates if d <= today()]
        return max(past) if past else self.day

    def counts(self) -> str:
        parts = []
        t = len(self.entries("tasks"))
        v = len(set(re.findall(r"/sources/([\w-]+)", self.note.body)))
        if t:
            parts.append(f"зад {t}")
        if v:
            parts.append(f"🎙{v}")
        return f" [{', '.join(parts)}]" if parts else ""


class Flow:
    def __init__(self, d: Path):
        self.dir = d
        self.slug = d.name
        self.note = Note(d / FLOW_FILE)
        self._points: list[Point] | None = None

    @property
    def title(self) -> str:
        return str(self.note.get("title") or self.slug)

    @property
    def aliases(self) -> list[str]:
        a = self.note.get("aliases", [])
        return a if isinstance(a, list) else [a]

    @property
    def tags(self) -> list[str]:
        t = self.note.get("tags", [])
        return t if isinstance(t, list) else [t]

    @property
    def status(self) -> str:
        return STATUS_ALIASES.get(norm(self.note.get("status", "open")), "open")

    @property
    def priority(self) -> int:
        try:
            return prio_value(self.note.get("priority", 1))
        except ValueError:
            return 1

    @property
    def created(self) -> date | None:
        dt, _ = parse_dt(self.note.get("created"))
        return dt.date() if dt else None

    def points(self) -> list[Point]:
        """Точки от ранней к поздней."""
        if self._points is None:
            pts = [Point(self, p) for p in self.dir.iterdir() if p.is_file() and POINT_RE.match(p.name)]
            self._points = sorted(pts, key=lambda p: (p.day, p.suffix))
        return self._points

    def ensure_open(self) -> Point:
        """Открытая точка потока; нет — создаётся сегодняшняя (вызывать внутри Mutation)."""
        return self.open_point() or create_point(self, today())

    def open_point(self) -> Point | None:
        op = [p for p in self.points() if p.is_open]
        return op[-1] if op else None

    def current(self) -> Point | None:
        """Точка, к которой относятся «что сделано» и комментарии: открытая, иначе последняя."""
        pts = self.points()
        return self.open_point() or (pts[-1] if pts else None)

    def ahead(self) -> Point | None:
        """Точка с задачами «на сейчас» — открытая."""
        return self.open_point()

    def last_touch(self) -> date:
        cands = [p.last_date() for p in self.points()[-2:]] + ([self.created] if self.created else [])
        return max(cands) if cands else today()

    def point_started(self) -> datetime:
        """Когда открыли текущую точку: opened: точки, у старых — closed: предыдущей, иначе её дата."""
        pts = self.points()
        op = self.open_point() or (pts[-1] if pts else None)
        if not op:
            return datetime.combine(self.created or today(), time.min)
        opened, _ = parse_dt(op.note.get("opened"))
        if opened:
            return opened
        k = pts.index(op)
        closed, _ = parse_dt(pts[k - 1].note.get("closed")) if k > 0 else (None, False)
        return closed or datetime.combine(op.day, time.min)

    def mtime(self) -> float:
        """Когда последний раз меняли файлы потока — различает потоки, тронутые в один день."""
        return max((p.stat().st_mtime for p in self.dir.glob("*.md")), default=0.0)

    def is_stale(self) -> bool:
        return self.status == "open" and days_ago(self.last_touch()) > STALE_DAYS[self.priority]

    def names(self) -> list[str]:
        return [self.slug, self.title] + [str(a) for a in self.aliases]

    @property
    def comments_path(self) -> Path:
        return self.dir / FLOW_FILE

    def comments(self) -> list[str]:
        """Комментарии потока (раздел в _поток.md), новые сверху."""
        return Note(self.comments_path).entries(HEADINGS["comments"])

    def add_comment(self, entry: str):
        """Новый комментарий — в начало раздела (вызывать внутри Mutation)."""
        note = Note(self.comments_path)
        note.prepend_entry(HEADINGS["comments"], entry, before=TOC_HEADING)
        note.save()
        self.note = note

    def resolve_point(self, spec: str | None) -> Point:
        pts = self.points()
        if spec in (None, "", "last", "текущая", "последняя", "current"):
            cur = self.current()
            if not cur:
                die(f"В потоке «{self.title}» ещё нет начатых точек. Начать: new-point {self.slug}")
            return cur
        if not pts:
            die(f"В потоке «{self.title}» ещё нет начатых точек.")
        spec = str(spec).strip()
        if re.fullmatch(r"#?\d{1,3}", spec):  # номер из списка show (1 — самая ранняя)
            n = int(spec.lstrip("#"))
            if 1 <= n <= len(pts):
                return pts[n - 1]
            die(f"Нет точки №{n}: в потоке их {len(pts)}.")
        for p in pts:
            if p.stem == spec:
                return p
        dt, _ = parse_dt(spec)
        if dt:
            same = [p for p in pts if p.day == dt.date()]
            if same:
                return same[-1]
        die(f"Не нашёл точку «{spec}» в потоке «{self.title}».")


def all_flows() -> list[Flow]:
    if not FLOWS_DIR.exists():
        return []
    return [Flow(d) for d in sorted(FLOWS_DIR.iterdir()) if d.is_dir() and (d / FLOW_FILE).exists()]


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def resolve_flow(q: str | None) -> Flow:
    flows = all_flows()
    if not flows:
        die("Потоков пока нет. Создай: new-flow \"Название\"")
    if q in (None, "", "-", "текущий", "current", "."):
        cur = get_current()
        if not cur:
            die("Текущий поток не задан (указателя нет). Спроси хозяина, к какому потоку это; номера — status.")
        q = cur
    if str(q).strip().isdigit():          # номер из последнего каталога /list или /background
        last = load_state().get("last_list") or []
        i = int(str(q).strip())
        if 1 <= i <= len(last):
            hit = [f for f in flows if f.slug == last[i - 1]]
            if hit:
                return hit[0]
        die(f"Номера {q} нет в последнем списке потоков — нажми /list.\n\n" + FOOTER)
    nq = norm(q)
    exact = [f for f in flows if nq in {norm(n) for n in f.names()}]
    if len(exact) == 1:
        return exact[0]
    fuzzy = []
    for f in flows:
        for name in f.names():
            n = norm(name)
            words = n.replace("-", " ").split()
            if (n.startswith(nq) or nq.startswith(n) or nq in n
                    or _common_prefix(n, nq) >= max(4, len(n) - 2)
                    or any(_common_prefix(w, nq) >= max(4, len(w) - 2) for w in words)):
                fuzzy.append(f)
                break
    if len(fuzzy) == 1:
        return fuzzy[0]
    if fuzzy:
        die(f"«{q}» подходит к нескольким потокам: " + ", ".join(f"{f.title} ({f.slug})" for f in fuzzy)
            + ". Уточни.")
    die(f"Поток «{q}» не найден. Есть: " + ", ".join(f"{f.title} ({f.slug})" for f in flows))


# ─────────────────────────────── состояние ────────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text("utf-8"))
    except Exception:
        return {}


def save_state(st: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, STATE_PATH)


# Указатель — одна запись state["pointer"] = {"flow", "point", "at"}: точка потока (point "" —
# открытая). Стоит только на точке или нигде. Ставят его кнопки точки, запись в поток, continue,
# show, use. /list снимает его (запомнив в last_point — её вернёт /point). /new: указатель есть —
# его точка, нет — список. screen — разовая пометка, какой экран показать следующим view (комментарии потока,
# фоновые потоки, календарь); её ставит кнопка через go и снимает view.

def get_pointer(st: dict | None = None) -> dict | None:
    return (load_state() if st is None else st).get("pointer")


def set_pointer(flow: str, point: str = "", screen: str = ""):
    st = load_state()
    st["pointer"] = {"flow": flow, "point": point, "at": now().isoformat(timespec="minutes")}
    if screen:
        st["screen"] = screen
    else:
        st.pop("screen", None)            # забытая пометка экрана не должна всплыть на /point или /new
    save_state(st)


def set_current(slug: str):
    """Запись в поток / вопрос про поток: указатель на этот поток (на нём уже — точку не меняем)."""
    ptr = get_pointer()
    set_pointer(slug, ptr.get("point", "") if ptr and ptr.get("flow") == slug else "")


def clear_current(screen: str = ""):
    """/list: снять указатель (где стоял — запомнить для /point)."""
    st = load_state()
    ptr = get_pointer(st)
    if ptr and ptr.get("flow"):
        st["last_point"] = {"flow": ptr["flow"], "point": ptr.get("point", "")}
    st.pop("pointer", None)
    if screen:
        st["screen"] = screen
    save_state(st)


def get_current() -> str | None:
    cur = get_pointer()
    if not cur or not cur.get("flow"):
        return None
    try:
        at = datetime.fromisoformat(cur["at"])
    except Exception:
        return None
    hours = float(CFG.get("current_flow_hours", 0))
    if hours and now() - at > timedelta(hours=hours):
        return None
    if not (FLOWS_DIR / cur["flow"] / FLOW_FILE).exists():
        return None
    return cur["flow"]


def remember_last_add(path: Path, heading: str, entry: str):
    st = load_state()
    st["last_add"] = {"path": str(path), "heading": heading, "entry": entry}
    save_state(st)


# ─────────────────────────────── git ──────────────────────────────────────

class GitSync:
    def __init__(self, prefer: str | None = None):
        self.enabled = bool(CFG.get("git_autosync")) and (BRAIN / ".git").exists()
        self._remote = None
        # при rebase «theirs» — это наши локальные коммиты, «ours» — пришедшие с GitHub
        self.strategy = {"server": ["-X", "theirs"], "pc": ["-X", "ours"]}.get(prefer or "", [])

    def git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(BRAIN), *args],
                              capture_output=True, text=True, timeout=90)

    def has_remote(self) -> bool:
        if self._remote is None:
            self._remote = bool(self.git("remote").stdout.strip())
        return self._remote

    def pull(self):
        if not (self.enabled and self.has_remote()):
            return
        r = self.git("pull", "--rebase", "--autostash", "-q", *self.strategy)
        if r.returncode != 0:
            self.git("rebase", "--abort")
            conflict = "conflict" in (r.stderr + r.stdout).lower() or "could not apply" in r.stderr
            if conflict:
                warn("КОНФЛИКТ синхронизации: одну и ту же строку правили и на сервере, и в Obsidian. "
                     "Работаю с локальной копией. Решить: sync --prefer server (оставить серверную версию) "
                     "или sync --prefer pc (оставить версию из Obsidian).")
            else:
                warn("git pull не прошёл (работаю с локальной копией): " + short(r.stderr, 200))

    def push(self, msg: str):
        if not self.enabled:
            return
        self.git("add", "-A")
        if self.git("diff", "--cached", "--quiet").returncode == 0:
            return
        r = self.git("commit", "-q", "-m", msg)
        if r.returncode != 0:
            warn("git commit не прошёл: " + short(r.stderr or r.stdout, 200))
            return
        if self.has_remote():
            r = self.git("push", "-q")
            if r.returncode != 0:
                self.pull()
                r = self.git("push", "-q")
                if r.returncode != 0:
                    warn("git push не прошёл, изменения сохранены на сервере локально; "
                         "уйдут при следующей удачной синхронизации.")


class Mutation:
    """with Mutation('сообщение'): pull до изменений, оглавление + commit/push после."""

    def __init__(self, msg: str):
        self.msg = msg
        self.sync = GitSync()

    def __enter__(self):
        self.sync.pull()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            write_index()
            self.sync.push(self.msg)
        return False


# ─────────────────────────────── шаблоны ──────────────────────────────────

def point_body(title: str) -> str:
    sections = "\n\n".join(f"## {HEADINGS[k]}" for k in POINT_KEYS)
    return f"\n# {title}\n\n{sections}\n"


def flow_body(slug: str, title: str) -> str:
    return f"\n# {title}\n\n## {TOC_HEADING}\n"


def new_point_path(flow: Flow, day: date) -> Path:
    p = flow.dir / f"{day.isoformat()}.md"
    n = 2
    while p.exists():
        p = flow.dir / f"{day.isoformat()}-{n}.md"
        n += 1
    return p


def create_point(flow: Flow, day: date, title: str = "") -> Point:
    path = new_point_path(flow, day)
    note = Note(path)
    note.set("type", "point")
    note.set("flow", flow.slug)
    note.set("date", day.isoformat())
    note.set("opened", iso_dt(now(), True))     # когда открыли — по нему порядок в каталоге
    note.set("status", "open")
    note.set("title", title)
    note.set("tags", [])
    note.body = point_body(title or day.strftime("%d.%m.%Y"))
    note.save()
    flow._points = None
    return Point(flow, path)


def put_entry(note: "Note", key: str, entry: str):
    """Запись в раздел точки — в начало (новые сверху): задачи, контекст."""
    note.prepend_entry(HEADINGS[key], entry, before=HEADINGS["context"] if key == "tasks" else None)


def route_point(f: Flow, spec: str | None = None) -> Point:
    """Куда писать запись: в открытую точку (нет — создаётся); явно названная прошлая (дата, номер) — в неё."""
    return f.resolve_point(spec) if spec else f.ensure_open()


# ─────────────────────────────── оглавление ПОТОКИ.md ─────────────────────

def build_index() -> str:
    flows = all_flows()
    out = ["# Потоки", "",
           "_Файл собирается автоматически из точек — правки здесь затрутся._", ""]
    groups = [("open", "Открытые"), ("paused", "На паузе"), ("closed", "Закрытые")]
    for status, caption in groups:
        fl = [f for f in flows if f.status == status]
        if not fl:
            continue
        out.append(f"## {caption} ({len(fl)})")
        out.append("")
        if status == "closed":
            for f in sorted(fl, key=lambda f: f.last_touch(), reverse=True):
                out.append(f"- [[flows/{f.slug}/_поток|{f.title}]] · до {dm(f.last_touch())}")
            out.append("")
            continue
        for f in sorted(fl, key=lambda f: (f.priority, -f.last_touch().toordinal())):
            bg = " · фоновый" if f.priority == PRIO_BG else ""
            out.append(f"### [[flows/{f.slug}/_поток|{f.title}]]{bg} · посл. {dm(f.last_touch())}")
            for p in reversed(f.points()[-2:]):
                out.append(f"- {dm(p.day)} — {p.link()}" + (" (открыта)" if p.is_open else ""))
            nt = _next_tasks(f)
            if nt:
                out.append("- ➡️ Дальше:")
                out += bullets(nt, "    ", 160, 5)
            out.append("")
    if not flows:
        out.append("Потоков пока нет.")
    soon = agenda_items(days=7)
    if soon:
        out += ["## Календарь, 7 дней", ""]
        for ev in soon:
            out.append(f"- {ev.when_txt()} — {ev.link()}")
        out += ["", "Всё: [[календарь/КАЛЕНДАРЬ|Календарь]]"]
    return "\n".join(out).rstrip() + "\n"


def build_flow_toc(f: Flow) -> list[str]:
    out = [f"## {TOC_HEADING}", "", "_Собирается автоматически из точек — правки здесь затрутся._", ""]
    for p in reversed(f.points()):
        s = p.summary
        out.append(f"- {dm(p.day)} — {p.link()}" + (f" — {s}" if s and s != p.title else "")
                   + (" (открыта)" if p.is_open else ""))
    if len(out) == 4:
        out.append("Точек пока нет.")
    return out


def write_flow_toc(f: Flow):
    note = Note(f.note.path)
    lines = note.body.rstrip("\n").split("\n")
    s, e = note._find_section(lines, TOC_HEADING)
    toc = build_flow_toc(f)
    if s is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += [""] + toc
    else:
        lines[s:e] = toc + ([""] if e < len(lines) else [])
    body = "\n".join(lines) + "\n"
    if body != note.body:
        note.body = body
        note.save()


def write_index():
    if not BRAIN.exists():
        return
    for f in all_flows():
        write_flow_toc(f)
    write_calendar_index()
    content = build_index()
    old = INDEX_FILE.read_text("utf-8") if INDEX_FILE.exists() else ""
    if old != content:
        INDEX_FILE.write_text(content, "utf-8")


# ─────────────────────────────── команды: просмотр ────────────────────────

def inbox_counts() -> tuple[int, int]:
    v = len(list(INBOX_VOICE.glob("*.md"))) if INBOX_VOICE.exists() else 0
    n = len(_inbox_notes())
    return v, n


def _next_tasks(f: Flow) -> list[str]:
    """Задачи «на сейчас» — из открытой точки."""
    p = f.ahead()
    return p.briefs("tasks") if p else []


def bullets(items: list[str], indent: str = "", n: int = 150, limit: int = 0) -> list[str]:
    """Список — каждая запись с новой строки (не через «;»), длинные обрезаются."""
    shown = items[:limit] if limit else items
    out = [f"{indent}– {short(x, n)}" for x in shown]
    if limit and len(items) > limit:
        out.append(f"{indent}… ещё {len(items) - limit}")
    return out


def _print_entries(caption: str, items: list[str], n: int = 200, limit: int = 10):
    if not items:
        return
    print(f"{caption}:")
    for t in items[-limit:]:
        print(f"- {short(t, n)}")


def _prev_here(f: Flow) -> tuple[Point | None, Point | None]:
    """Прошлая (последняя завершённая) точка и текущая открытая."""
    pts = f.points()
    op = f.open_point()
    done = [p for p in pts if not p.is_open and (op is None or pts.index(p) < pts.index(op))]
    return (done[-1] if done else None), op


def _here_head(f: Flow, here: Point) -> str:
    return f"Текущая точка с {dmy(here.day)}" + (f" «{here.title}»" if here.title else "")


def cmd_status(a):
    """«Покажи потоки»: по каждому актуальному — на чём остановились и что предстоит."""
    flows = all_flows()
    opened = sorted([f for f in flows if f.status == "open"],
                    key=lambda f: (f.priority, -f.last_touch().toordinal()))
    paused = [f for f in flows if f.status == "paused"]
    closed = [f for f in flows if f.status == "closed"]
    cur = get_current()
    print(f"АКТУАЛЬНЫЕ ПОТОКИ ({len(opened)})" if opened else "Актуальных потоков нет.")
    for i, f in enumerate(opened, 1):
        stale = " ⚠ давно не трогали" if f.is_stale() else ""
        mark = " ◀ указатель" if f.slug == cur else ""
        bg = " · фоновый" if f.priority == PRIO_BG else ""
        print(f"\n{i}. {f.title}{bg}{mark} — посл. {dm(f.last_touch())}, {ago(f.last_touch())}{stale}")
        prev, here = _prev_here(f)
        if prev:
            print(f"   Остановились: {dm(prev.day)} «{prev.label}»")
            t = prev.briefs("context")
            if t:
                print("     сделано:")
                print("\n".join(bullets(t[:3], "       ", 120)))   # новые сверху
        cm = [brief(plain(entry_parts(e)[1])) for e in f.comments()[:3]]
        if cm:
            print("   Свежие комментарии:")
            print("\n".join(bullets(cm, "       ", 120)))
        if here:
            print(f"   {_here_head(f, here)}")
            for caption, key in (("задачи", "tasks"),):
                t = here.briefs(key)
                if t:
                    print(f"     {caption}:")
                    print("\n".join(bullets(t, "       ", 120, 7)))
        if not prev and not here and not cm:
            print("   точек пока нет")
    if paused:
        print(f"\nНА ПАУЗЕ ({len(paused)}): " + ", ".join(f.title + (" (фоновый)" if f.priority == PRIO_BG else "") for f in paused))
    if closed:
        if a.all:
            print(f"\nЗАКРЫТЫЕ ({len(closed)}): " + ", ".join(f"{f.title} ({f.slug})" for f in closed))
        else:
            print(f"\nЗакрытых: {len(closed)} (показать: status --all)")
    soon = [ev for ev in agenda_items(days=3) if not ev.flow]
    if soon:
        print("\n🗓 Календарь:")
        print("\n".join(bullets([f"{ev.when_txt()} {ev.title}" for ev in soon], "", 60, 5)))
    v, n = inbox_counts()
    if v or n:
        print(f"\n📥 Неразобранное: голосовых {v}, заметок {n} (inbox)")


LIST_PREFIX = str(CFG.get("list_cmd_prefix", ""))   # "" → /1, /2…; если Telegram не жмёт — "n" → /n1
LIST_LINKS = 30                                       # столько команд /1…/30 заводит setup-commands.sh


FOOTER = "/calendar   /list"   # нижний ряд кнопок каждого экрана (/point работает, но в ряд не выводится)
HIDE_PREFIX = "x"        # кнопки скрытия задач в карточке: /x1, /x2 …
EXPAND_PREFIX = "f"      # раскрыть/свернуть одну запись (задачу, комментарий): /f1, /f2 …
BACK_CMD = "back"        # из комментариев — назад в карточку точки
HIGHLIGHT_MARK = "👉"    # в каталоге — поток, на котором стоит (стоял до /list) указатель
# Панель кнопок записи — отдельной строкой под ней: «/f1  ▫️  /x1» (квадратик — перебивка между
# кнопками вместо пустых пробелов). Нечего раскрывать — на месте /fK прочерк («——  ▫️  /x1»), чтобы
# /xK всегда стоял на своём месте и его не нажать вместо раскрытия.
BTN_INDENT = " " * 3
BTN_SEP = "  ▫️  "
BTN_EMPTY = "——"
# кнопки карточки точки: комментарии (под датой), выполненные (внизу); /point — последняя точка
CARD_CMDS = {"comments": "comments", "done": "done"}
CARD_KEYS = {"комментарии": "comments", "комментарий": "comments", "comments": "comments",
             "done": "done", "выполнено": "done", "выполненные": "done"}
NAV_CMDS = {"prev": "prev", "next": "next", "point": "point"}
CURRENT_MARK = "⚡"      # открытая точка — первый символ шапки карточки (у завершённых POINT_MARK)


def bg_tail(f: "Flow") -> str:
    """Пометка справа от названия внутри потока: у фонового — «· фоновый», у основного — ничего."""
    return " · фоновый" if f.priority == PRIO_BG else ""
POINT_MARK = "📍"
COMMENTS_MARK = "💬"     # первый символ шапки на экране комментариев


def cmd_open(a):
    """Кнопка /N из каталога: карточка открытой точки потока. Ставит указатель на неё."""
    f = resolve_flow(a.flow)
    set_pointer(f.slug)
    render_view()


def _screen_id(f: Flow, p: Point | None, kind: str) -> list[str]:
    """Чей экран: поток, точка, карточка или комментарии (раскрытое /fK живёт, пока экран тот же).
    Комментарии — экран потока, точка не важна."""
    if kind == "comments":
        return [f.slug, "", "comments"]
    return [f.slug, p.stem if p else "", "card"]


def _expanded(f: Flow, p: Point | None, kind: str) -> set[str]:
    ex = load_state().get("expanded") or {}
    return set(ex.get("keys") or []) if ex.get("screen") == _screen_id(f, p, kind) else set()


def _save_card(f: Flow, kind: str, items: list[tuple[Path, str]], p: Point | None = None,
               shown: list[tuple[Path, str]] | None = None):
    """Запомнить экран: items — пункты для /xK, shown — все строки по порядку для /fK."""
    st = load_state()
    st["last_card"] = {"flow": f.slug, "kind": kind, "items": [[str(x), e] for x, e in items],
                       "shown": [[str(x), e] for x, e in shown or []],
                       "point": "" if not p or p.is_open else p.stem}
    if (st.get("expanded") or {}).get("screen") != _screen_id(f, p, kind):
        st.pop("expanded", None)              # другой экран — раскрытое сбрасывается
    save_state(st)


def _entry_line(e: str, tail: str = "", full: bool = False) -> str:
    """Строка пункта: «– заголовок» (до TITLE_LEN знаков); full — весь текст без обрезки и с
    разбивкой хозяина: в одну строку — «– Заголовок — текст», в несколько (пункты, абзацы) —
    заголовок, ниже каждая строка текста с отступом."""
    mark = "✓" if is_hidden(e) else "–"
    if not full:
        return f"{mark} {short(brief(plain(entry_parts(e)[1])), TITLE_LEN)}{tail}"
    lines = [" ".join(l.split()) for l in plain(entry_parts(e)[1]).split("\n")]
    lines = [l for l in lines if l]
    first = lines[0] if lines else ""
    if first.startswith(HIDDEN_MARK.strip()):
        first = first[len(HIDDEN_MARK.strip()):].lstrip()
    title, m = "", BRIEF_RE.match(first)
    if m:
        title, first = m.group(1).strip().rstrip("."), first[m.end():].strip()
    body = [l for l in [first] + lines[1:] if l]
    if title and len(body) == 1 and norm(body[0]).rstrip(".") == norm(title):
        body = []                                   # заголовок и есть весь текст — не повторять
    if not title:
        title, body = (body[0], body[1:]) if body else ("", [])
    if len(body) <= 1:
        return f"{mark} {title}" + (f" — {body[0]}" if body else "") + tail
    return f"{mark} {title}" + "".join(f"\n{FULL_INDENT}{l}" for l in body) + tail


def _expandable(e: str) -> bool:
    """Есть что раскрыть: полный текст длиннее короткой строки."""
    return _entry_line(e, full=True).rstrip(".…") != _entry_line(e).rstrip(".…")


def _shown_line(e: str, path: Path, shown: list, open_: set[str], x_n: int = 0) -> str:
    """Запись (свёрнута или раскрыта) и под ней панель кнопок: /fK (если есть что раскрыть, иначе ——),
    квадратик, /xK (x_n — номер пункта; 0 — без него). Запись — в shown."""
    shown.append((path, e))
    k = len(shown)
    line = _entry_line(e, full=e in open_)
    can_f = k <= LIST_LINKS and _expandable(e)
    can_x = 0 < x_n <= LIST_LINKS
    if not (can_f or can_x):
        return line
    left = f"/{EXPAND_PREFIX}{k}" if can_f else BTN_EMPTY
    right = f"{BTN_SEP}/{HIDE_PREFIX}{x_n}" if can_x else ""
    return f"{line}\n{BTN_INDENT}{left}{right}"


def _print_items(caption: str, path: Path, es: list[str], items: list, shown: list, open_: set[str],
                 hide: bool = True):
    """Пункты короткими заголовками (раскрытые /fK — полным текстом); hide — с кнопками /xK
    (скрытые не показываются), иначе все, скрытые (сделанные) — с ✓."""
    if hide:
        es = [e for e in es if not is_hidden(e)]
    if not es:
        return
    print(f"\n{caption}:")
    for e in es:
        if hide:
            items.append((path, e))
        print(_shown_line(e, path, shown, open_, len(items) if hide else 0))


def _point_head(f: Flow, p: Point | None, mark: str = "") -> str:
    """Шапка карточки и её экранов: маркер, поток (фоновый — с пометкой); «Точка i из n · дата»; заголовок."""
    mark = mark or (CURRENT_MARK if p and p.is_open else POINT_MARK)
    head = f"{mark} {f.title}{bg_tail(f)}" + (" · пауза" if f.status == "paused" else "")
    if not p:
        return head
    pts = f.points()
    return (f"{head}\nТочка {pts.index(p) + 1} из {len(pts)} · {p.dates_txt}"
            + (f"\n«{p.title}»" if p.title else ""))


def _print_nav(f: Flow, p: Point):
    pts = f.points()
    k = pts.index(p)
    nav = ([f"◀ /{NAV_CMDS['prev']}"] if k > 0 else []) + ([f"/{NAV_CMDS['next']} ▶"] if k < len(pts) - 1 else [])
    if nav:
        print("\n" + "   ".join(nav))


def print_point_card(f: Flow, p: Point | None, done_open: bool = False):
    """Карточка точки: дата, задачи (в открытой — с /xK, в завершённой — сделанные с ✓),
    под датой — комментарии, внизу листание по точкам. В открытой под задачами /done (N);
    done_open (кнопка /done) — вместо ссылки раскрытый список выполненных с ✓. /fK — раскрыть одну."""
    print(_point_head(f, p))
    items: list[tuple[Path, str]] = []
    shown: list[tuple[Path, str]] = []
    kind = "done" if done_open and p and p.is_open else "point"
    if not p:
        print("точек пока нет")
        _save_card(f, "point", items, p)
        print("\n" + FOOTER)
        return
    print(f"/{CARD_CMDS['comments']} ({len(f.comments())})")
    open_ = _expanded(f, p, kind)
    if p.is_open:
        _print_items("Задачи", p.path, p.entries("tasks"), items, shown, open_)
        if not items:
            print("\nзадач пока нет")
        done = [e for e in p.entries("tasks") if is_hidden(e)]
        if done and done_open:
            print(f"\nВыполнено ({len(done)}):")
            for e in done:
                print(_shown_line(e, p.path, shown, open_))
        elif done:
            print(f"\n/{CARD_CMDS['done']} ({len(done)})")
    else:
        _print_items("Задачи", p.path, p.entries("tasks"), items, shown, open_, hide=False)
        if not p.entries("tasks"):
            print("\nзадач не было")
    _save_card(f, kind, items, p, shown)
    _print_nav(f, p)
    print("\n" + FOOTER)


def print_comments(f: Flow, p: Point | None = None):
    """/comments: комментарии потока, новые сверху. Под каждым — /fK (раскрыть) и /xK (удалить);
    /back — назад в карточку точки p (под указателем). Листания по точкам здесь нет."""
    print(_point_head(f, None, COMMENTS_MARK) + f"\nКомментарии  /{BACK_CMD}")
    es = f.comments()
    items: list[tuple[Path, str]] = []
    shown: list[tuple[Path, str]] = []
    if es:
        open_ = _expanded(f, p, "comments")
        out = []
        for e in es:
            items.append((f.comments_path, e))
            out.append(_shown_line(e, f.comments_path, shown, open_, len(items)))
        print("\n" + "\n".join(out))
    else:
        print("\nкомментариев пока нет")
    _save_card(f, "comments", items, p, shown)
    print("\n" + FOOTER)


def _pointer_point(f: Flow, ptr: dict) -> Point | None:
    pts = f.points()
    for p in pts:
        if p.stem == ptr.get("point"):
            return p
    return f.open_point() or (pts[-1] if pts else None)


def render_view(screen: str = "", if_asked: bool = False):
    """Экран: указатель есть — его точка (или её комментарии, если так просили), нет — список.
    if_asked (хук после /new): печатать, только если /new запустила кнопка (свежая пометка show_at от go)."""
    st = load_state()
    asked = st.pop("show_at", "")
    if if_asked:
        try:
            fresh = now() - datetime.fromisoformat(asked) <= timedelta(minutes=2)
        except (TypeError, ValueError):
            fresh = False
        if not fresh:                         # обычный /new из меню — тишина
            return save_state(st)
    screen = screen or st.pop("screen", "")
    save_state(st)
    if screen == "calendar":
        return print_calendar()
    cur = get_current()
    if not cur or screen in ("list", BG_CMD):
        return print_catalog(screen == BG_CMD)
    f = resolve_flow(cur)
    p = _pointer_point(f, get_pointer() or {})
    if screen == "comments":
        print_comments(f, p)
    else:
        print_point_card(f, p, done_open=screen == "done")


def go(target: list[str]):
    """Переставить указатель (без вывода): list | background | open N | prev | next | point | back
    | card комментарии | calendar (календарь указатель не трогает — разовый экран)."""
    what = norm(target[0]) if target else "list"
    rest = target[1:]
    if what in ("list", BG_CMD, "фон", "фоновые"):
        return clear_current("list" if what == "list" else BG_CMD)
    if what in ("calendar", "календарь"):
        st = load_state()
        st["screen"] = "calendar"
        return save_state(st)
    if what == BACK_CMD and get_current():   # из комментариев — карточка той же точки
        return set_pointer(get_current(), (get_pointer() or {}).get("point", ""))
    if what == "open":
        f = resolve_flow(rest[0] if rest else None)
        return set_pointer(f.slug)
    if what in ("point", BACK_CMD):              # back без указателя — как /point
        cur = get_current()
        if cur:                               # указатель уже на точке — её карточку и покажем,
            st = load_state()                 # а если она уже на экране — последнюю точку потока
            lc, ptr = st.get("last_card") or {}, get_pointer(st) or {}
            again = (not st.get("screen") and lc.get("kind") == "point" and lc.get("flow") == cur
                     and lc.get("point", "") == ptr.get("point", ""))
            return set_pointer(cur, "" if again else ptr.get("point", ""))
        lp = load_state().get("last_point") or {}
        if not lp.get("flow"):
            die("Ещё не открывал ни одной точки — нажми /list и номер потока.\n\n" + FOOTER)
        return set_pointer(resolve_flow(lp["flow"]).slug, lp.get("point", ""))
    cur = get_current()
    if not cur:
        die("Сначала открой поток: /list и номер.\n\n" + FOOTER)
    f = resolve_flow(cur)
    ptr = get_pointer() or {}
    p = _pointer_point(f, ptr)
    if what in ("card", "карточка"):
        key = CARD_KEYS.get(norm(rest[0]) if rest else "")
        if not key:
            die("Карточка: комментарии | выполнено")
        return set_pointer(f.slug, ptr.get("point", ""), screen=key)
    if what in ("prev", "next"):
        pts = f.points()
        if not p:
            return set_pointer(f.slug)
        k = pts.index(p) + (-1 if what == "prev" else 1)
        q = pts[max(0, min(k, len(pts) - 1))]
        return set_pointer(f.slug, "" if q.is_open else q.stem)
    die(f"Не понимаю «{what}». Можно: list, background, open N, prev, next, point, back, card комментарии, calendar")


def cmd_go(a):
    st = load_state()                         # пометка для хука: этот /new — от кнопки, экран нужен
    st["show_at"] = now().isoformat(timespec="seconds")
    save_state(st)
    go(a.target)


def cmd_view(a):
    render_view(if_asked=a.if_asked)


def cmd_nav(a):
    """go + view одной командой (кнопки без /new)."""
    go(a.target)
    render_view()


def cmd_card(a):
    """Кнопка /comments: комментарии точки, на которой стоит указатель."""
    if a.flow:
        set_pointer(resolve_flow(a.flow).slug)
    go(["card", a.kind])
    render_view()


def _set_hidden(path: Path, entry: str, hide: bool) -> str:
    """Поставить/снять пометку ✓ у записи в файле точки; вернуть новую запись."""
    note = Note(path)
    if entry not in note.body:
        die("Карточка устарела — нажми номер потока ещё раз.\n\n" + FOOTER)
    m = ENTRY_DATE_RE.match(entry)
    head, rest = (entry[:m.end()], entry[m.end():]) if m else (entry[:2], entry[2:])
    rest = rest.lstrip()
    if rest.startswith(HIDDEN_MARK.strip()):
        rest = rest[len(HIDDEN_MARK.strip()):].lstrip()
    new = head + (HIDDEN_MARK if hide else "") + rest
    note.body = note.body.replace(entry, new, 1)
    note.save()
    return new


def cmd_hide(a):
    """Кнопка /xK в карточке: скрыть K-й пункт (остаётся в файле с ✓) и показать ту же карточку снова."""
    card = load_state().get("last_card") or {}
    if card.get("kind") == "calendar":
        return _hide_event(card.get("items") or [], a.n)
    items = card.get("items") or []
    if not 1 <= a.n <= len(items):
        die(f"В последней карточке нет пункта {a.n} — нажми номер потока ещё раз.\n\n" + FOOTER)
    f = resolve_flow(card["flow"])
    path, entry = items[a.n - 1]
    if card.get("kind") == "comments":           # на экране комментариев /xK удаляет комментарий
        with Mutation(f"{f.title}: удалён комментарий"):
            note = Note(Path(path))
            if not note.remove_entry(entry):
                die("Экран устарел — нажми /comments ещё раз.\n\n" + FOOTER)
            note.save()
        print(f"🗑 Удалил комментарий: {brief(plain(entry_parts(entry)[1]), 60)}\n")
        return render_view("comments")
    with Mutation(f"{f.title}: скрыта запись"):
        _set_hidden(Path(path), entry, True)
    print(f"✓ Выполнено: {brief(plain(entry_parts(entry)[1]), 60)}\n")
    kind = card.get("kind", "point")
    render_view(kind if kind in CARD_CMDS else "point")


def cmd_expand(a):
    """Кнопка /fK: раскрыть K-ю запись последнего экрана (задачу в карточке, комментарий) полным
    текстом; повторное нажатие — свернуть. Перерисовывает тот же экран, указатель не трогает."""
    st = load_state()
    card = st.get("last_card") or {}
    kind = card.get("kind")
    if kind not in ("point", "done", "comments"):
        die("Раскрывать здесь нечего — открой точку: /list и номер потока.\n\n" + FOOTER)
    shown = card.get("shown") or []
    if not 1 <= a.n <= len(shown):
        die(f"На последнем экране нет записи {a.n} — открой его ещё раз.\n\n" + FOOTER)
    f = resolve_flow(card["flow"])
    p = _pointer_point(f, {"point": card.get("point", "")})
    sid = _screen_id(f, p, kind)
    ex = st.get("expanded") or {}
    keys = set(ex.get("keys") or []) if ex.get("screen") == sid else set()
    keys ^= {shown[a.n - 1][1]}               # раскрыта — свернуть, свёрнута — раскрыть
    st["expanded"] = {"screen": sid, "keys": sorted(keys)}
    save_state(st)
    if kind == "comments":
        print_comments(f, p)
    else:
        print_point_card(f, p, done_open=kind == "done")


def _hide_event(stems: list[str], n: int):
    """/xK на экране календаря: закрыть K-е событие (status: done, как прошедшее)."""
    if not 1 <= n <= len(stems):
        die(f"В календаре нет пункта {n} — нажми /calendar ещё раз.\n\n" + FOOTER)
    hit = [e for e in all_events() if e.stem == stems[n - 1]]
    if hit:
        with Mutation(f"Календарь: убрано «{hit[0].title}»"):
            hit[0].note.set("status", "done")
            hit[0].note.save()
        print(f"✓ Убрал: {hit[0].title}\n")
    print_calendar()


def cmd_unhide(a):
    """Вернуть скрытую задачу по куску текста (по умолчанию — в открытой точке)."""
    f = resolve_flow(a.flow)
    p = f.resolve_point(a.point) if a.point else f.ahead()
    if not p:
        die(f"У потока «{f.title}» нет открытой точки — укажи --point.")
    q = norm(a.find)
    hits = [e for e in p.entries("tasks") if is_hidden(e) and q in norm(e)]
    if len(hits) != 1:
        die(f"Скрытых записей с «{a.find}»: {len(hits)} — уточни." if hits
            else f"Среди скрытых в точке {p.where} нет «{a.find}».")
    with Mutation(f"{f.title}: возвращена запись"):
        _set_hidden(p.path, hits[0], False)
    print(f"✓ Вернул: {brief(plain(entry_parts(hits[0])[1]), 60)}")


def cmd_list(a):
    """Кнопки /list и /background: указатель — на каталог, показать каталог потоков без задач."""
    low = getattr(a, "low", False)
    clear_current()
    print_catalog(low)


def print_catalog(low: bool = False):
    """/list — основные потоки одним списком, /background (low) — фоновые. Без подзаголовков и кружков.
    Порядок — по моменту открытия текущей точки: открыл новую — поток вниз; записи порядок не меняют.
    Номера запоминаются (state.last_list)."""
    live = [f for f in all_flows() if f.status != "closed"]
    shown = sorted([f for f in live if (f.priority == PRIO_BG) == low],
                   key=lambda f: (f.status != "open", f.point_started(), f.title))
    st = load_state()                         # выделяем поток указателя (после /list — где он стоял)
    here = get_current() or (st.get("last_point") or {}).get("flow")
    print("ФОНОВЫЕ ПОТОКИ" if low else "ПОТОКИ")
    print("\nпусто" if not shown else "")
    for i, f in enumerate(shown, 1):
        marks = (" · пауза" if f.status == "paused" else "") + (" ⚠" if f.is_stale() else "")
        num = f"/{LIST_PREFIX}{i}" if i <= LIST_LINKS else f"{i}."
        mark = f"{HIGHLIGHT_MARK} " if f.slug == here else ""
        print(f"{mark}{num} {f.title} — {dm(f.point_started().date())}{marks}")
    st = load_state()
    st["last_list"] = [f.slug for f in shown]
    save_state(st)
    bg_n = len([f for f in live if f.priority == PRIO_BG])
    if not low and bg_n:
        print(f"\nФоновые потоки ({bg_n}): /{BG_CMD}")
    elif low:
        print("\nОсновные потоки: /list")
    print("\nНажми номер — открою поток.\n\n" + FOOTER)


def _print_state(f: Flow):
    """Прошлая точка (задачи, сделано, комментарии) + задачи на сейчас."""
    prev, here = _prev_here(f)
    if prev:
        print(f"\nПрошлая точка {dm(prev.day)} «{prev.label}»:")
        _print_entries("Задачи были", prev.texts("tasks", 300))
        _print_entries("Сделано", prev.texts("context", 300))
    cm = [short(plain(entry_parts(e)[1]), 300) for e in f.comments()]
    if cm:
        print("")
        _print_entries("Комментарии потока (новые сверху)", cm, limit=5)
    if here:
        print(f"\n{_here_head(f, here)}:")
        _print_entries("Задачи", here.texts("tasks", 300))
        if not here.entries("tasks"):
            print("задач пока нет")


def cmd_show(a):
    """«На чём остановился»: путь, прошлая точка, задачи на сейчас. Ставит указатель на поток."""
    f = resolve_flow(a.flow)
    set_current(f.slug)
    created = f" · с {dm(f.created)}" if f.created else ""
    print(f"📍 {f.title} ({f.slug}) · {PRIO_LABEL[f.priority]} · {STATUS_RU[f.status]}{created}")
    if f.tags:
        print("Теги: " + ", ".join(f.tags))
    if f.aliases:
        print("Другие названия: " + ", ".join(map(str, f.aliases)))
    pts = f.points()
    if pts:
        print("\nПуть:")
        shown = pts if a.full or len(pts) <= 12 else pts[-12:]
        if len(shown) < len(pts):
            print(f"… ещё {len(pts) - len(shown)} ранних точек (show {f.slug} --full)")
        for p in shown:
            n = pts.index(p) + 1
            sm = str(p.note.get("summary") or "").strip()
            tail = "  ◀ открыта" if p.is_open else ""
            print(f"{n}. {dm(p.day)} — {p.label}" + (f" · {short(sm, 100)}" if sm else "") + f"{p.counts()}{tail}")
    else:
        print("\nНачатых точек пока нет.")
    _print_state(f)


def cmd_point(a):
    f = resolve_flow(a.flow)
    p = f.resolve_point(a.point)
    pts = f.points()
    print(f"📍 {f.title} · точка {pts.index(p) + 1}/{len(pts)} · {dm(p.day)} — {p.label}"
          + (" · открыта" if p.is_open else " · закрыта"))
    if p.note.get("summary"):
        print(f"Кратко: {p.note.get('summary')}")
    tags = p.note.get("tags", [])
    if tags:
        print("Теги: " + ", ".join(tags if isinstance(tags, list) else [tags]))
    keys = [section_key(a.section)] if a.section else list(POINT_KEYS)
    for key in keys:
        es = f.comments() if key == "comments" else p.entries(key)   # комментарии — потока
        print(f"\n{HEADINGS[key]}:" + ("" if es else " —"))
        for e in es:
            d, t = entry_parts(e)
            txt = t if a.raw else plain(t)
            print(f"- {('[' + dm(d) + '] ') if d else ''}{txt}")
    print(f"\nФайл: {p.path.relative_to(BRAIN)}")


def cmd_collect(a):
    f = resolve_flow(a.flow)
    key = section_key(a.section)
    if key == "comments":                    # комментарии одни на поток
        es = f.comments()
        print(f"{f.title}: комментарии потока (новые сверху)")
        for e in es:
            d, t = entry_parts(e)
            print(f"- {('[' + dm(d) + '] ') if d else ''}{plain(t)}")
        if not es:
            print("\nЗаписей нет.")
        return
    print(f"{f.title}: {HEADINGS[key].lower()} по всем точкам")
    total = 0
    for p in f.points():
        es = p.entries(key)
        if not es:
            continue
        total += len(es)
        print(f"\n{dm(p.day)} — {p.label}:")
        for e in es:
            d, t = entry_parts(e)
            later = f" (дописано {dm(d)})" if d and d != p.day else ""
            print(f"- {plain(t)}{later}")
    if not total:
        print("\nЗаписей нет.")


def cmd_current(a):
    cur = get_current()
    if not cur:
        print("Текущий поток не задан.")
        return
    f = resolve_flow(cur)
    p = f.current()
    state = "" if not p else (" (открыта)" if p.is_open else " (закрыта)")
    print(f"Текущий поток: {f.title} ({f.slug})"
          + (f", точка {dm(p.day)} — {p.label}{state}" if p else ", начатых точек нет"))


def cmd_use(a):
    f = resolve_flow(a.flow)
    set_current(f.slug)
    p = f.current()
    print(f"📍 Текущий поток: {f.title}" + (f" · точка {dm(p.day)} — {p.label}" if p else " · начатых точек пока нет"))
    nt = _next_tasks(f)
    if nt:
        print("Дальше:")
        print("\n".join(bullets(nt, "", 300)))


# ─────────────────────────────── команды: изменения ───────────────────────

def cmd_new_flow(a):
    title = a.title.strip()
    for f in all_flows():
        if norm(title) in {norm(n) for n in f.names()}:
            die(f"Поток «{f.title}» ({f.slug}) уже есть.")
    slug = a.slug or slugify(title)
    base, n = slug, 2
    while (FLOWS_DIR / slug).exists():
        slug = f"{base}-{n}"
        n += 1
    try:
        prio = prio_value(a.priority)
    except ValueError:
        die("Приоритет: 1 — основной, 2 — фоновый.")
    with Mutation(f"Новый поток: {title}"):
        d = FLOWS_DIR / slug
        d.mkdir(parents=True)
        note = Note(d / FLOW_FILE)
        note.set("type", "flow")
        note.set("title", title)
        note.set("aliases", [x.strip() for x in (a.aliases or "").split(",") if x.strip()])
        note.set("status", "open")
        note.set("priority", prio)
        note.set("created", today().isoformat())
        note.set("tags", [x.strip().lstrip("#") for x in (a.tags or "").split(",") if x.strip()])
        note.body = flow_body(slug, title)
        note.save()
        f = Flow(d)
        create_point(f, today())
        set_pointer(slug)
    print(f"✓ Поток «{title}» создан ({slug}), {PRIO_LABEL[prio]}. Он теперь текущий.")


def begin_point(f: Flow, title: str = "", fresh: bool = False) -> tuple[Point, Point | None]:
    """«Начинаем новую точку»: открытая завершается (closed — дата и время), создаётся новая с
    датой этого момента, в неё переезжают невыполненные задачи (fresh — без переноса).
    Указатель — на новую. Возвращает (новая, завершённая или None). Вызывать внутри Mutation."""
    op = f.open_point()
    p = create_point(f, today(), title)
    if op:
        if not fresh:
            for key in FORWARD_KEYS:
                for e in op.visible(key):
                    op.note.remove_entry(e)
                    p.note.append_entry(HEADINGS[key], e)
        op.note.set("status", "closed")
        op.note.set("closed", iso_dt(now(), True))   # завершённый — в конец каталога
        op.note.save()
        p.note.save()
    f._points = None
    set_pointer(f.slug)
    return Point(f, p.path), op


def cmd_new_point(a):
    """«Начинаем новую точку / позицию»: завершить текущую, открыть новую, перенести невыполненное."""
    f = resolve_flow(a.flow)
    with Mutation(f"{f.title}: новая точка"):
        p, op = begin_point(f, a.title or "", a.fresh)
    if op:
        print(f"✓ Точка {dmy(op.day)} завершена. Перенёс задач: {len(p.entries('tasks'))}.\n")
    render_view()


def cmd_continue(a):
    """«Продолжаем работать в …»: указатель на открытую точку потока (нет — создаётся) + сводка."""
    f = resolve_flow(a.flow)
    op = f.open_point()
    if not op:
        with Mutation(f"{f.title}: открыта точка"):
            op = f.ensure_open()
    set_pointer(f.slug)
    print(f"📍 {f.title} · работаем в точке с {dmy(op.day)}" + (f" «{op.title}»" if op.title else ""))
    f._points = None
    _print_state(f)


def cmd_add(a):
    f = resolve_flow(a.flow)
    key = section_key(a.section)
    text = read_text_input(a.text)
    src = _source_link(f, a.src) if a.src else None
    with Mutation(f"{f.title}: +{HEADINGS[key].lower()}"):
        entry = make_entry(with_brief(text, a.short), src)
        if key == "comments":                # комментарии — потока, новые сверху
            f.add_comment(entry)
            path, where = f.comments_path, ""
            p = f.ensure_open() if a.summary else None
        else:
            p = route_point(f, a.point)
            put_entry(p.note, key, entry)
            path, where = p.path, f" · {p.where}"
        if a.summary:
            p.note.set("summary", a.summary.strip())
        if p:
            p.note.save()
        set_current(f.slug)
        remember_last_add(path, HEADINGS[key], entry)
    print(f"✓ {f.title}{where} · {HEADINGS[key]} +1"
          + (" · кратко обновлено" if a.summary else ""))


def cmd_brief(a):
    """Задать/поменять короткий заголовок записи, найденной по куску текста (задачи, комментарии…)."""
    f = resolve_flow(a.flow)
    p = f.resolve_point(a.point) if a.point else f.ahead()
    if not p:
        die(f"У потока «{f.title}» нет открытой точки — укажи --point.")
    q = norm(a.find)
    hits = [(p.path, HEADINGS[k], e) for k in POINT_KEYS for e in p.entries(k) if q in norm(e)]
    hits += [(f.comments_path, HEADINGS["comments"], e) for e in f.comments() if q in norm(e)]
    if not hits:
        die(f"В точке {p.where} и комментариях потока нет записи с «{a.find}».")
    if len(hits) > 1:
        die(f"«{a.find}» подходит к нескольким записям: "
            + " | ".join(brief(plain(entry_parts(e)[1]), 50) for _, _, e in hits))
    path, heading, old = hits[0]
    m = ENTRY_DATE_RE.match(old)
    head, rest = (old[:m.end()], old[m.end():]) if m else ("- ", re.sub(r"^[-*]\s+", "", old))
    first, nl, tail = rest.partition("\n")
    mark = HIDDEN_MARK if first.lstrip().startswith(HIDDEN_MARK.strip()) else ""
    if mark:
        first = first.lstrip()[len(HIDDEN_MARK.strip()):].lstrip()
    new = head + mark + with_brief(first, a.title) + nl + tail
    with Mutation(f"{f.title}: заголовок записи"):
        note = Note(path)
        note.body = note.body.replace(old, new, 1)
        note.save()
    where = "" if path == f.comments_path else f" · {p.where}"
    print(f"✓ {f.title}{where} · {heading}: «{a.title.strip().rstrip('.')}»")


def cmd_summary(a):
    f = resolve_flow(a.flow)
    with Mutation(f"{f.title}: кратко о точке"):
        p = f.resolve_point(a.point)
        p.note.set("summary", a.text.strip())
        p.note.save()
    print(f"✓ {f.title} · {p.where}: кратко — {a.text.strip()}")


def cmd_title(a):
    f = resolve_flow(a.flow)
    with Mutation(f"{f.title}: заголовок точки"):
        p = f.resolve_point(a.point)
        p.note.set("title", a.title.strip())
        p.note.set_h1(a.title.strip())
        p.note.save()
    print(f"✓ {f.title} · {p.where}")


def cmd_tag_point(a):
    f = resolve_flow(a.flow)
    new = [t.strip().lstrip("#") for t in a.tags.split(",") if t.strip()]
    with Mutation(f"{f.title}: теги точки"):
        p = f.resolve_point(a.point)
        cur = p.note.get("tags", [])
        cur = cur if isinstance(cur, list) else [cur]
        merged = list(dict.fromkeys([*cur, *new]))
        p.note.set("tags", merged)
        p.note.save()
    unknown = [t for t in new if norm(t) not in {norm(x) for x in _tag_dictionary()}]
    print(f"✓ {f.title} · {dm(p.day)}: теги {', '.join(merged)}")
    if unknown:
        print("⚠ Нет в словаре тегов: " + ", ".join(unknown) + " (tag-add, если это новый тег)")


def cmd_set(a):
    f = resolve_flow(a.flow)
    changes = []
    with Mutation(f"{f.title}: настройки потока"):
        if a.priority is not None:
            try:
                prio = prio_value(a.priority)
            except ValueError:
                die("Приоритет: 1 — основной, 2 — фоновый.")
            f.note.set("priority", prio)
            changes.append(PRIO_LABEL[prio])
        if a.status:
            st = STATUS_ALIASES.get(norm(a.status))
            if not st:
                die("Статус: open | paused | closed (открыт | пауза | закрыт)")
            f.note.set("status", st)
            if st == "closed":
                f.note.set("closed", today().isoformat())
            changes.append(STATUS_RU[st])
        if a.title:
            f.note.set("title", a.title)
            f.note.set_h1(a.title)
            changes.append(f"название «{a.title}»")
        if a.add_alias:
            al = list(dict.fromkeys([*map(str, f.aliases), *[x.strip() for x in a.add_alias.split(",") if x.strip()]]))
            f.note.set("aliases", al)
            changes.append("синонимы: " + ", ".join(al))
        if a.tags is not None:
            f.note.set("tags", [x.strip().lstrip("#") for x in a.tags.split(",") if x.strip()])
            changes.append("теги обновлены")
        if not changes:
            die("Нечего менять: --priority / --status / --title / --add-alias / --tags")
        f.note.save()
    print(f"✓ {f.title}: " + ", ".join(changes))


def cmd_delete_flow(a):
    """Удалить поток безвозвратно: папку со всеми точками. Без --force только предупреждает.
    У его событий календаря снимается привязка к потоку; указатель и «последняя запись» сбрасываются, если вели в этот поток."""
    f = resolve_flow(a.flow)
    if not a.force:
        die(f"Это удалит поток «{f.title}» ({f.slug}) со всеми точками безвозвратно. "
            f"Повтори с --force, если уверен.")
    slug, unlinked = f.slug, 0
    with Mutation(f"Удалён поток: {f.title}"):
        shutil.rmtree(f.dir)
        for e in all_events(active_only=False):
            if e.flow != slug:
                continue
            e.note.set("flow", None)
            e.note.save()
            unlinked += 1
        st = load_state()
        if (get_pointer(st) or {}).get("flow") == slug:
            st.pop("pointer", None)
        if (st.get("last_point") or {}).get("flow") == slug:
            st.pop("last_point", None)
        if str((st.get("last_add") or {}).get("path", "")).startswith(str(f.dir)):
            st.pop("last_add", None)
        save_state(st)
    print(f"✓ Поток «{f.title}» ({slug}) удалён безвозвратно.")
    if unlinked:
        print(f"  Календарь: отвязано событий: {unlinked}.")


def cmd_migrate(a):
    """Перевод данных при смене формата (запускает install.sh, можно повторять). Сейчас переводить
    нечего: старые форматы выведены 2026.11.13.2. Новая смена формата — новый шаг здесь."""
    print("✓ Миграция не нужна.")


def cmd_undo(a):
    st = load_state()
    la = st.get("last_add")
    if not la:
        die("Нечего отменять.")
    path = Path(la["path"])
    with Mutation("Отмена последней записи"):
        note = Note(path)
        if not note.remove_entry(la["entry"]):
            die("Последняя запись уже изменена или удалена вручную.")
        if a.to:
            key = section_key(a.to)
            heading = caption = HEADINGS[key]
            note.save()
            f = Flow(path.parent)
            if key == "comments":                # комментарии — потока, новые сверху
                target = f.comments_path
                note = Note(target)
                note.prepend_entry(heading, la["entry"], before=TOC_HEADING)
            else:
                target = path if POINT_RE.match(path.name) else f.ensure_open().path
                note = Note(target)
                put_entry(note, key, la["entry"])
            note.save()
            st["last_add"] = {"path": str(target), "heading": heading, "entry": la["entry"]}
            print(f"✓ Перенёс последнюю запись в раздел «{caption}».")
        else:
            note.save()
            st.pop("last_add", None)
            print("✓ Последняя запись удалена.")
        save_state(st)


# ─────────────────────────────── голосовые и входящие ─────────────────────

def _find_voice(vid: str) -> Path:
    if vid in ("last", "последняя", "последнее"):
        files = sorted(INBOX_VOICE.glob("*.md")) if INBOX_VOICE.exists() else []
        if not files:
            die("Во входящих нет непривязанных голосовых.")
        return files[-1]
    vid = vid.strip().removeprefix("voice:")
    p = INBOX_VOICE / f"{vid}.md"
    if p.exists():
        return p
    for f in all_flows():
        q = f.dir / "sources" / f"{vid}.md"
        if q.exists():
            return q
    die(f"Не нашёл запись {vid}.")


def _source_link(f: Flow, vid: str) -> str:
    vid = vid.strip().removeprefix("voice:")
    return f"[[flows/{f.slug}/sources/{vid}|🎙]]"


def cmd_attach(a):
    f = resolve_flow(a.flow)
    src = _find_voice(a.voice)
    vid = src.stem
    with Mutation(f"{f.title}: голосовая {vid}"):
        p = route_point(f, a.point)
        dest = f.dir / "sources" / f"{vid}.md"
        if src != dest:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        note = Note(dest)
        note.set("flow", f.slug)
        note.set("point", p.stem)
        note.save()
        dur = note.get("duration", "")
        label = f"🎙 Голосовая {dur}".strip()
        entry = make_entry(f"[[flows/{f.slug}/sources/{vid}|{label}]]")
        put_entry(p.note, "context", entry)
        p.note.save()
        set_current(f.slug)
    print(f"✓ Запись {vid} привязана: {f.title} · {p.where}. "
          f"Ссылка на неё для записей: --src {vid}")


def _inbox_notes() -> list[str]:
    if not INBOX_NOTES.exists():
        return []
    return [l for l in INBOX_NOTES.read_text("utf-8").split("\n") if re.match(r"^[-*]\s+", l)]


def cmd_note(a):
    text = read_text_input(a.text)
    with Mutation("Заметка во входящие"):
        INBOX_NOTES.parent.mkdir(parents=True, exist_ok=True)
        old = INBOX_NOTES.read_text("utf-8") if INBOX_NOTES.exists() else "# Входящие заметки\n\n"
        entry = make_entry(text)
        INBOX_NOTES.write_text(old.rstrip("\n") + "\n" + entry + "\n", "utf-8")
    print("✓ Заметка во входящих (без потока).")


def cmd_inbox(a):
    voices = sorted(INBOX_VOICE.glob("*.md")) if INBOX_VOICE.exists() else []
    notes = _inbox_notes()
    if not voices and not notes:
        print("📥 Входящие пусты.")
        return
    if voices:
        print(f"🎙 Непривязанные голосовые ({len(voices)}):")
        for v in voices:
            n = Note(v)
            text = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", "", n.body)
            text = re.sub(r"^#.*$", "", text, flags=re.M)
            print(f"- {v.stem} · {n.get('duration', '')} · {short(text, 140)}")
    if notes:
        print(f"\n📝 Заметки без потока ({len(notes)}):")
        for i, l in enumerate(notes, 1):
            d, t = entry_parts(l)
            print(f"{i}. {('[' + dm(d) + '] ') if d else ''}{short(plain(t), 200)}")


def cmd_inbox_move(a):
    notes = _inbox_notes()
    if not 1 <= a.n <= len(notes):
        die(f"Нет заметки №{a.n}.")
    line = notes[a.n - 1]
    f = resolve_flow(a.flow)
    key = section_key(a.section)
    with Mutation(f"{f.title}: заметка из входящих"):
        if key == "comments":                # комментарии — потока, новые сверху
            f.add_comment(line)                # дата сохраняется исходная
            where = ""
        else:
            p = route_point(f, a.point)
            put_entry(p.note, key, line)
            p.note.save()
            where = f" · {p.where}"
        content = INBOX_NOTES.read_text("utf-8").split("\n")
        content.remove(line)
        INBOX_NOTES.write_text("\n".join(content), "utf-8")
    print(f"✓ Заметка перенесена: {f.title}{where} · {HEADINGS[key]}")


# ─────────────────────────────── поиск и теги ─────────────────────────────

def _tag_dictionary() -> list[str]:
    if not TAGS_FILE.exists():
        return []
    return re.findall(r"^[-*]\s+#?([\wА-Яа-яЁё\-]+)", TAGS_FILE.read_text("utf-8"), re.M)


def _point_files(include_sources: bool, flow: Flow | None = None):
    flows = [flow] if flow else all_flows()
    for f in flows:
        yield f, f.dir / FLOW_FILE
        for p in f.points():
            yield f, p.path
        if include_sources and (f.dir / "sources").exists():
            for s in sorted((f.dir / "sources").glob("*.md")):
                yield f, s


def cmd_find(a):
    flow = resolve_flow(a.flow) if a.flow else None
    limit = a.limit
    results: list[str] = []
    if a.tag:
        tag = norm(a.tag.lstrip("#"))
        for f, path in _point_files(False, flow):
            note = Note(path)
            tags = note.get("tags", [])
            tags = tags if isinstance(tags, list) else [tags]
            rel = f"{f.slug}/{path.stem}"
            if tag in {norm(t) for t in tags}:
                title = note.get("title") or note.h1()
                what = "весь поток" if path.name == FLOW_FILE else "вся точка"
                results.append(f"{rel} · {what}: {short(str(title), 80)}")
            for line in note.body.split("\n"):
                if re.search(r"(?<![\w])#" + re.escape(tag) + r"(?![\w-])", norm(line)):
                    txt = plain(re.sub(r"^[-*]\s+", "", line))
                    results.append(f"{rel}: {short(txt, 200)}")
        if a.query:
            results = [r for r in results if norm(a.query) in norm(r)]
    else:
        if not a.query:
            die("Что искать? find \"текст\" или find --tag тег")
        root = flow.dir if flow else FLOWS_DIR
        if shutil.which("rg"):
            cmd = ["rg", "-n", "-i", "--no-heading", "--max-count", "5", "-g", "*.md"]
            if not a.sources:
                cmd += ["-g", "!**/sources/**"]
            cmd += ["--", a.query, str(root)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            for line in r.stdout.splitlines():
                m = re.match(r"^(.*?\.md):(\d+):(.*)$", line)
                if not m:
                    continue
                rel = Path(m.group(1)).relative_to(FLOWS_DIR).with_suffix("")
                txt = plain(re.sub(r"^[-*]\s+", "", m.group(3).strip()))
                if txt.startswith(("title:", "aliases:", "tags:")):
                    continue
                results.append(f"{rel}: {short(txt, 200)}")
        else:
            q = norm(a.query)
            for f, path in _point_files(a.sources, flow):
                for line in path.read_text("utf-8").split("\n"):
                    if q in norm(line) and not line.startswith(("title:", "aliases:", "tags:")):
                        results.append(f"{f.slug}/{path.stem}: {short(plain(line.strip()), 200)}")
    if not results:
        print("Ничего не нашёл." + ("" if a.sources or a.tag else " (в исходниках голосовых: --sources)"))
        return
    for r in results[:limit]:
        print(r)
    if len(results) > limit:
        print(f"… и ещё {len(results) - limit} (сузь запрос или --limit)")


def cmd_tags(a):
    dic = _tag_dictionary()
    counts: dict[str, int] = {}
    for f, path in _point_files(False):
        note = Note(path)
        tags = note.get("tags", [])
        for t in (tags if isinstance(tags, list) else [tags]):
            counts[norm(t)] = counts.get(norm(t), 0) + 1
        for t in re.findall(r"(?<![\w/])#([\wА-Яа-яЁё][\wА-Яа-яЁё\-]*)", note.body):
            counts[norm(t)] = counts.get(norm(t), 0) + 1
    print("Словарь тегов:" if dic else "Словарь тегов пуст (tag-add тег \"описание\").")
    for t in dic:
        print(f"- #{t} ({counts.get(norm(t), 0)})")
    extra = sorted(set(counts) - {norm(t) for t in dic})
    if extra:
        print("\nВстречаются, но нет в словаре: " + ", ".join(f"#{t} ({counts[t]})" for t in extra))


def cmd_tag_add(a):
    tag = a.tag.strip().lstrip("#")
    if norm(tag) in {norm(t) for t in _tag_dictionary()}:
        die(f"#{tag} уже есть в словаре.")
    with Mutation(f"Тег #{tag}"):
        old = TAGS_FILE.read_text("utf-8") if TAGS_FILE.exists() else "# Словарь тегов\n\n"
        line = f"- #{tag}" + (f" — {a.description.strip()}" if a.description else "")
        TAGS_FILE.write_text(old.rstrip("\n") + "\n" + line + "\n", "utf-8")
    print(f"✓ #{tag} добавлен в словарь.")


# ─────────────────────────────── напоминания ────────────────────────────────

def _when_words(w: datetime | None, ht: bool, t: datetime) -> str:
    if not w:
        return ""
    d = w.date()
    txt = ("сегодня" if d == t.date() else "завтра" if d == t.date() + timedelta(days=1)
           else f"{dm(d)} ({WEEKDAYS[d.weekday()]})")
    return txt + (w.strftime(" в %H:%M") if ht else "")


# ─────────────────────────────── календарь ────────────────────────────────
# Одно событие = один файл в календарь/. Событие может быть само по себе
# («к стоматологу») или по теме потока (flow), с датой или без (when нет — внизу календаря).


def _is_past(w: datetime | None, ht: bool, t: datetime) -> bool:
    """Прошло ли событие. Без времени (весь день) «прошло» = день кончился, не полночь."""
    if not w:
        return False
    if ht:
        return w < t
    return w.date() < t.date()


class Event:
    def __init__(self, path: Path):
        self.path = path
        self.note = Note(path)

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return str(self.note.get("title") or self.stem)

    @property
    def when(self) -> tuple[datetime | None, bool]:
        return parse_dt(self.note.get("when"))

    @property
    def flow(self) -> str:
        return str(self.note.get("flow") or "")

    @property
    def active(self) -> bool:
        return norm(self.note.get("status", "active")) == "active"

    @property
    def reminds(self) -> list[datetime]:
        raw = self.note.get("remind")
        if raw is None:
            return []
        out = [parse_dt(x)[0] for x in (raw if isinstance(raw, list) else [raw])]
        return sorted(x for x in out if x)

    def reminds_txt(self) -> str:
        return ", ".join(dmt(r) for r in self.reminds)

    def when_txt(self) -> str:
        w, ht = self.when
        return f"{WEEKDAYS[w.weekday()]} {dmt(w, ht)}" if w else "без даты"

    def sort_key(self) -> datetime:
        w, _ = self.when
        return w or datetime.max

    def link(self) -> str:
        return f"[[календарь/{self.stem}|{self.title}]]"

    def details(self) -> str:
        body = re.sub(r"^#.*$", "", self.note.body, flags=re.M)
        body = re.sub(r"^Поток: .*$", "", body, flags=re.M)
        return short(plain(body.strip()), 200)


def all_events(active_only: bool = True) -> list[Event]:
    if not CAL_DIR.exists():
        return []
    evs = [Event(p) for p in CAL_DIR.glob("*.md") if p.name != CAL_INDEX.name]
    if active_only:
        evs = [e for e in evs if e.active]
    return sorted(evs, key=lambda e: (e.sort_key(), e.stem))


def agenda_items(days: int | None = None) -> list[Event]:
    """Активные события по времени; days — только до этой даты (прошедшие активные — тоже)."""
    evs = all_events()
    if days is not None:
        limit = today() + timedelta(days=days)
        evs = [e for e in evs if e.when[0] and e.when[0].date() <= limit]
    return evs


def _event_path(w: datetime | None, title: str) -> Path:
    base = f"{w:%Y-%m-%d}-{slugify(title)[:30]}" if w else f"без-даты-{slugify(title)[:30]}"
    p, n = CAL_DIR / f"{base}.md", 2
    while p.exists():
        p, n = CAL_DIR / f"{base}-{n}.md", n + 1
    return p


def create_event(title: str, w: datetime | None, has_time: bool, reminds: list[datetime],
                 flow: Flow | None = None, details: str = "") -> Event:
    note = Note(_event_path(w, title))
    note.set("type", "event")
    note.set("title", title)
    if w:
        note.set("when", iso_dt(w, has_time))
    if flow:
        note.set("flow", flow.slug)
    note.set("status", "active")
    set_reminds(note, reminds)
    body = [f"\n# {title}", ""]
    if flow:
        body += [f"Поток: [[flows/{flow.slug}/{FLOW_FILE[:-3]}|{flow.title}]]", ""]
    if details:
        body += [details.strip(), ""]
    note.body = "\n".join(body)
    note.save()
    return Event(note.path)


def shift_reminds(ev: Event, new_w: datetime, new_ht: bool) -> list[datetime]:
    """При переносе «за 30 минут» остаётся «за 30 минут»; если время/без времени поменялось — сброс."""
    old_w, old_ht = ev.when
    if ev.reminds and old_w and old_ht == new_ht:
        return [new_w - (old_w - r) for r in ev.reminds]
    return []


def update_event(ev: Event, title: str, w: datetime, has_time: bool, reminds: list[datetime]) -> Event:
    note = ev.note
    note.set("title", title)
    note.set_h1(title)
    note.set("when", iso_dt(w, has_time))
    set_reminds(note, reminds)
    old = ev.path
    if not old.name.startswith(f"{w:%Y-%m-%d}-"):
        note.path = _event_path(w, title)
    note.save()
    if note.path != old:
        old.unlink()
        st = load_state()
        if (st.get("last_reminder") or {}).get("event") == old.stem:
            st["last_reminder"]["event"] = note.path.stem
        st.get("snoozes", {}).pop(old.stem, None)
        save_state(st)
    return Event(note.path)


def build_calendar_index() -> str:
    out = ["# Календарь", "", "_Файл собирается автоматически — правки здесь затрутся._", ""]
    t = now()
    evs = all_events(active_only=False)
    ahead = [e for e in evs if e.active]
    past = [e for e in evs if not e.active][-20:]
    out += [f"## Впереди ({len(ahead)})", ""]
    for e in ahead:
        w, ht = e.when
        late = " (прошло)" if _is_past(w, ht, t) else ""
        rem = f" · ⏰ {e.reminds_txt()}" if e.reminds else ""
        fl = f" · [[flows/{e.flow}/_поток|{e.flow}]]" if e.flow else ""
        out.append(f"- {e.when_txt()} — {e.link()}{fl}{rem}{late}")
    if not ahead:
        out.append("Ничего не запланировано.")
    if past:
        out += ["", "## Прошедшие", ""]
        for e in reversed(past):
            out.append(f"- {e.when_txt()} — {e.link()}")
    return "\n".join(out).rstrip() + "\n"


def write_calendar_index():
    if not CAL_DIR.exists():
        return
    content = build_calendar_index()
    old = CAL_INDEX.read_text("utf-8") if CAL_INDEX.exists() else ""
    if old != content:
        CAL_INDEX.write_text(content, "utf-8")


def reminder_text(ev: Event, t: datetime) -> str:
    """Первая строка — то, о чём напоминание: её видно в push-уведомлении."""
    w, ht = ev.when
    when = _when_words(w, ht, t)
    lines = [f"{ev.title} — {when}" if when else ev.title]
    f = next((x for x in all_flows() if x.slug == ev.flow), None) if ev.flow else None
    lines.append(f"⏰ {f.title}" if f else "⏰ напоминание")
    det = ev.details()
    if det:
        lines.append(det)
    if CFG.get("remind_reply_hint"):
        lines.append("↩️ отложи на 15 мин · перенеси на … · отмени")
    return "\n".join(lines)


def cmd_remind(a):
    """Для no-agent cron раз в минуту. Пустой вывод = ничего не отправлять."""
    st = load_state()
    t = now()
    # подхватить события, добавленные в Obsidian, — но не дёргать GitHub каждую минуту
    last_pull = st.get("last_pull")
    every = timedelta(minutes=float(CFG.get("remind_pull_minutes", 5)))
    if not last_pull or t - datetime.fromisoformat(last_pull) >= every - timedelta(seconds=30):
        GitSync().pull()
        st = load_state()
        st["last_pull"] = t.isoformat(timespec="seconds")
    sent: dict = st.setdefault("reminded", {})
    snoozes: dict = st.setdefault("snoozes", {})
    grace = timedelta(hours=float(CFG.get("remind_grace_hours", 12)))
    msgs = []
    for ev in all_events():
        due = [r for r in ev.reminds if r <= t
               and f"{ev.stem}@{r.isoformat(timespec='minutes')}" not in sent]
        for r in due:
            sent[f"{ev.stem}@{r.isoformat(timespec='minutes')}"] = t.isoformat(timespec="minutes")
        if ev.stem in snoozes and datetime.fromisoformat(snoozes[ev.stem]) <= t:
            due.append(datetime.fromisoformat(snoozes.pop(ev.stem)))  # «отложи на 15 минут»
        if not due:
            continue
        if all(t - r > grace for r in due):
            continue  # сильно опоздали (сервер лежал) — молча пропускаем
        msgs.append(reminder_text(ev, t))
        st["last_reminder"] = {"event": ev.stem, "at": t.isoformat(timespec="minutes")}
    cutoff = (t - timedelta(days=60)).isoformat()
    st["reminded"] = {k: v for k, v in sent.items() if v >= cutoff}
    save_state(st)
    _autodone_events(t)
    if msgs:
        print("\n\n".join(msgs))


def _autodone_events(t: datetime):
    """Прошедшие события закрываются сами через N часов."""
    keep = timedelta(hours=float(CFG.get("event_autodone_hours", 3)))
    done = []
    for ev in all_events():
        w, ht = ev.when
        end = (w if ht else datetime.combine(w.date(), datetime.max.time())) if w else None
        if end and t - end > keep:
            done.append(ev)
    if done:
        with Mutation("Календарь: прошедшие события закрыты"):
            for ev in done:
                ev.note.set("status", "done")
                ev.note.save()


def cmd_event(a):
    """Событие в календаре: само по себе или по теме потока. Без --remind — без напоминания,
    без --when — без даты (внизу календаря)."""
    w, has_time = parse_dt(a.when) if a.when else (None, False)
    if a.when and not w:
        die(f"Не понял дату «{a.when}». Формат: 2026-10-27 15:00")
    if a.remind and not w:
        die("Напоминание без даты не поставить — нужна --when.")
    reminds = parse_reminds(a.remind, w, has_time) if a.remind else []
    f = resolve_flow(a.flow) if a.flow else None
    title = a.title.strip()
    with Mutation(f"Календарь: {title}"):
        CAL_DIR.mkdir(parents=True, exist_ok=True)
        ev = create_event(title, w, has_time, reminds, flow=f, details=a.note or "")
    print(f"✓ В календаре: «{title}» — {_when_words(w, has_time, now()) or 'без даты'}"
          + (f" · {f.title}" if f else "")
          + (f" (⏰ {ev.reminds_txt()})" if reminds else " · без напоминания" if w else ""))


def print_calendar(days: int | None = None):
    """Кнопка /calendar: все активные события с номерами /xK — с датой по времени, ниже без даты."""
    t = now()
    evs = agenda_items(days=days)
    flows = {f.slug: f.title for f in all_flows()}
    dated = [e for e in evs if e.when[0]]
    undated = [] if days is not None else [e for e in evs if not e.when[0]]
    print("🗓 Календарь")
    shown: list[str] = []

    def row(ev: Event, label: str):
        shown.append(ev.stem)
        n = len(shown)
        fl = f" · {flows[ev.flow]}" if ev.flow in flows else ""
        tail = f"  /{HIDE_PREFIX}{n}" if n <= LIST_LINKS else ""
        print(f"{n}. {label}{fl}{tail}")
        if ev.reminds:
            print(f"   ⏰ {ev.reminds_txt()}")

    if dated:
        print()
    for i, ev in enumerate(dated):
        if i:
            print()                           # события с датой — через пустую строку
        w, ht = ev.when
        today_mark = f"{CURRENT_MARK} " if w.date() == t.date() else ""   # сегодня — ⚡ сразу после номера
        day = f"{WEEKDAYS[w.weekday()]}" + (w.strftime(" %H:%M") if ht else "") + f" {dm(w.date())}"   # сб 15:00 31.10
        row(ev, f"{today_mark}{day}" + (" (прошло)" if _is_past(w, ht, t) else "") + f"\n   {ev.title}")   # день, время, дата; ниже что
    if undated:
        print("\n📌 Без даты")
    for ev in undated:
        row(ev, ev.title)
    if not shown:
        print("\nВ календаре ничего нет.")
    st = load_state()
    st["last_card"] = {"kind": "calendar", "items": shown}
    save_state(st)
    print("\n" + FOOTER)


def cmd_calendar(a):
    """«Покажи календарь», «что у меня ближайшее» — тот же экран, что /calendar."""
    print_calendar(a.days)


def _event_target(q: str | None, active_only: bool = True) -> Event:
    """Какое событие двигать: id, часть названия, поток — или из последнего напоминания."""
    evs = all_events(active_only)
    if q:
        hit = [e for e in evs if e.stem == q] or [e for e in evs if norm(q) in norm(e.title)]
        if not hit:
            try:
                f = resolve_flow(q)
            except FlowsError:
                f = None
            if f:
                hit = [e for e in evs if e.flow == f.slug]
        if len(hit) == 1:
            return hit[0]
        if hit:
            die(f"«{q}» подходит к нескольким событиям: " + "; ".join(f"{e.title} ({e.stem})" for e in hit))
        die(f"Не нашёл в календаре «{q}». Список: calendar")
    lr = load_state().get("last_reminder") or {}
    hit = [e for e in evs if e.stem == lr.get("event")]
    if not hit:
        die("Какое событие? Назови его или поток.")
    return hit[0]


def cmd_move(a):
    if not (a.when or a.remind or a.snooze or a.cancel or a.restore):
        die("Что сделать: --when (перенести), --snooze (отложить), --remind (напоминания), --cancel"
            " или --restore")
    if a.restore:
        if not a.target:
            die("Какое событие вернуть? Назови его.")
        ev = _event_target(a.target, active_only=False)
        with Mutation(f"Календарь: возвращено «{ev.title}»"):
            ev.note.set("status", "active")
            ev.note.save()
        print(f"✓ «{ev.title}» снова в календаре ({ev.when_txt()}).")
        return
    ev = _event_target(a.target)
    t = now()
    title = ev.title
    with Mutation(f"Календарь: {'отмена' if a.cancel else 'перенос'} «{title}»"):
        if a.cancel:
            ev.path.unlink()
            st = load_state()
            st.get("snoozes", {}).pop(ev.stem, None)
            save_state(st)
            print(f"✓ «{title}» ({ev.when_txt()}) убрано из календаря.")
            return
        w, ht = ev.when
        reminds = ev.reminds
        msg = []
        if a.when:
            new_w, new_ht = parse_dt(a.when)
            if not new_w:
                die(f"Не понял дату «{a.when}». Формат: 2026-10-29 19:00")
            if not w:
                reminds = []
            if not a.remind:
                reminds = shift_reminds(ev, new_w, new_ht)
                if ev.reminds and not reminds:
                    msg.append("напоминания сброшены — задай заново")
            msg.insert(0, f"перенесено на {_when_words(new_w, new_ht, t)}")
            w, ht = new_w, new_ht
        if a.remind:
            if not w:
                die("Напоминание без даты не поставить — сначала --when.")
            reminds = parse_reminds(a.remind, w, ht)
        if a.when or a.remind:
            ev = update_event(ev, title, w, ht, reminds)
        if a.snooze:
            delta = parse_delta(a.snooze)
            if delta is None:
                die(f"Не понял «{a.snooze}». Примеры: 15m, 1h, 2h30m")
            r = (t + delta).replace(second=0, microsecond=0)
            st = load_state()  # отложенное хранится отдельно: исходные напоминания в файле не трогаем
            st.setdefault("snoozes", {})[ev.stem] = r.isoformat(timespec="minutes")
            save_state(st)
            msg.append(f"напомню снова в {r:%H:%M}" + ("" if r.date() == t.date() else f" {dm(r.date())}"))
    rem = ""
    if (a.when or a.remind) and not a.snooze:
        rem = f", напомню {ev.reminds_txt()}" if ev.reminds else ", без напоминаний"
    print(f"✓ «{title}»: " + ", ".join(msg or ["напоминания обновлены"]) + rem)


def cmd_index(a):
    with Mutation("Оглавление потоков"):
        pass
    print(f"✓ {INDEX_FILE.relative_to(BRAIN.parent)} обновлён.")


def cmd_sync(a):
    s = GitSync(prefer=a.prefer)
    if not s.enabled:
        die("Синхронизация выключена: нужен git-репозиторий в хранилище и \"git_autosync\": true в конфиге.")
    s.pull()
    write_index()
    s.push("Синхронизация")
    if s.has_remote():
        r = s.git("rev-list", "--left-right", "--count", "HEAD...@{u}")
        ahead, behind = (int(x) for x in (r.stdout.split() or ["0", "0"]))
        if ahead and behind:
            die("Сервер и GitHub разошлись (конфликт). Выбери: sync --prefer server или sync --prefer pc")
        if ahead:
            die(f"{ahead} изменений не ушли на GitHub (нет связи или доступа). Повтори sync позже.")
    print("✓ Синхронизировано.")


# ─────────────────────────────── init ─────────────────────────────────────

GITIGNORE = """# аудио не храним в git (большое); оно лежит только на сервере
_audio/
*.tmp
.obsidian/workspace*.json
.obsidian/cache
.trash/
"""

TPL_POINT = """---
type: point
flow:
date: {{date:YYYY-MM-DD}}
status: open
title:
tags: []
---

# {{title}}

## Задачи

## Контекст
"""

TPL_FLOW = """---
type: flow
title: {{title}}
aliases: []
status: open
priority: 1
created: {{date:YYYY-MM-DD}}
tags: []
---

# {{title}}

## Точки
"""

TAGS_STARTER = """# Словарь тегов

Агент ставит только теги из этого списка. Новый — сначала предлагает.

- #реклама — реклама, кабинеты, креативы
- #продажи — продажи, звонки, скрипты
- #интуитивное-пение — методика и продукт
"""


def cmd_init(a):
    for d in (FLOWS_DIR, INBOX_VOICE, AUDIO_DIR, TEMPLATES_DIR, CAL_DIR):
        d.mkdir(parents=True, exist_ok=True)
    created = []
    for path, content in (
        (BRAIN / ".gitignore", GITIGNORE),
        (TEMPLATES_DIR / "Точка.md", TPL_POINT),
        (TEMPLATES_DIR / "Поток.md", TPL_FLOW),
        (TAGS_FILE, TAGS_STARTER),
        (INBOX_NOTES, "# Входящие заметки\n\n"),
    ):
        if not path.exists():
            path.write_text(content, "utf-8")
            created.append(str(path.relative_to(BRAIN)))
    write_index()
    if a.git and not (BRAIN / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(BRAIN)], check=False)
        created.append(".git")
    print(f"✓ Хранилище: {BRAIN}" + (f" (созданы: {', '.join(created)})" if created else " (уже было)"))


# ─────────────────────────────── CLI ──────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="flows.py", description="Потоковый органайзер: потоки → точки → задачи/контекст; комментарии — у потока.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, aliases=()):
        p = sub.add_parser(name, help=help_, aliases=list(aliases))
        p.set_defaults(fn=fn)
        return p

    p = add("status", cmd_status, "актуальные потоки: на чём остановились и что предстоит")
    p.add_argument("--all", action="store_true", help="показать и закрытые")

    p = add("list", cmd_list, "каталог потоков без задач + сброс указателя (кнопки /list, /background)")
    p.add_argument("--low", "--background", dest="low", action="store_true", help="фоновые потоки (кнопка /background)")

    p = add("open", cmd_open, "карточка открытой точки потока: задачи (кнопки /1, /2… из каталога)")
    p.add_argument("flow", help="номер из последнего каталога или название")

    p = add("go", cmd_go, "переставить указатель без вывода (для кнопок через /new)")
    p.add_argument("target", nargs="*", help="list | background | open N | prev | next | point | back | card комментарии | calendar")

    p = add("view", cmd_view, "экран после /new: точка под указателем, нет указателя — список")
    p.add_argument("--if-asked", action="store_true", help="только после кнопки (go), обычный /new — пусто")

    p = add("nav", cmd_nav, "go + view одной командой")
    p.add_argument("target", nargs="*")

    add("migrate", cmd_migrate, "перевод данных при смене формата (делает install.sh; сейчас — нечего)")

    p = add("card", cmd_card, "комментарии потока под указателем (кнопка /comments)")
    p.add_argument("kind", help="комментарии | done")
    p.add_argument("flow", nargs="?", help="по умолчанию — поток последней карточки")

    p = add("hide", cmd_hide, "скрыть пункт K последней карточки (кнопки /x1, /x2…)")
    p.add_argument("n", type=int)

    p = add("expand", cmd_expand, "раскрыть/свернуть запись K последнего экрана (кнопки /f1, /f2…)")
    p.add_argument("n", type=int)

    p = add("unhide", cmd_unhide, "вернуть выполненную (скрытую) задачу по куску текста")
    p.add_argument("flow")
    p.add_argument("find")
    p.add_argument("--point")

    p = add("show", cmd_show, "на чём остановился: путь, прошлая точка, задачи на сейчас")
    p.add_argument("flow", nargs="?")
    p.add_argument("--full", action="store_true")

    p = add("point", cmd_point, "показать точку (по умолчанию текущую)")
    p.add_argument("flow", nargs="?")
    p.add_argument("point", nargs="?", help="дата 2026-10-23, номер из show или last; по умолчанию — открытая")
    p.add_argument("--section", help="контекст | задачи | комментарии")
    p.add_argument("--raw", action="store_true", help="не убирать ссылки")

    p = add("collect", cmd_collect, "все записи раздела по всем точкам потока")
    p.add_argument("flow")
    p.add_argument("section")

    add("current", cmd_current, "какой поток текущий")
    p = add("use", cmd_use, "сделать поток текущим")
    p.add_argument("flow")

    p = add("new-flow", cmd_new_flow, "создать поток")
    p.add_argument("title")
    p.add_argument("--priority", default="1", help="1 — основной (по умолчанию), 2 — фоновый")
    p.add_argument("--tags", help="через запятую")
    p.add_argument("--aliases", help="другие названия через запятую")
    p.add_argument("--slug")

    p = add("new-point", cmd_new_point, "«начинаем новую точку»: завершить текущую, открыть новую, "
                                        "перенести невыполненные задачи")
    p.add_argument("flow", nargs="?")
    p.add_argument("--title", default="")
    p.add_argument("--fresh", action="store_true", help="не переносить задачи")

    p = add("continue", cmd_continue, "«продолжаем работать в …»: указатель + актуальная точка + сводка")
    p.add_argument("flow")


    p = add("event", cmd_event, "событие в календаре (с потоком или без)", aliases=["todo"])
    p.add_argument("title", help="что: «Пойти к стоматологу»")
    p.add_argument("--when", help="2026-10-27 15:00; без ключа — событие без даты")
    p.add_argument("--remind", help="0 (в само время) | 1h | 1d,1h; без ключа — без напоминания")
    p.add_argument("--flow", help="поток, к теме которого относится событие")
    p.add_argument("--note", help="детали: адрес, телефон")

    p = add("calendar", cmd_calendar, "календарь: события по времени, ниже без даты (кнопка /calendar)",
            aliases=["agenda", "events", "todos"])
    p.add_argument("--days", type=int, help="только события с датой на N дней вперёд")

    p = add("move", cmd_move, "перенести / отложить / отменить событие календаря")
    p.add_argument("target", nargs="?", help="событие (название или id) или поток; по умолчанию — из последнего напоминания")
    p.add_argument("--when", help="новая дата и время")
    p.add_argument("--remind", help="заменить напоминания: 0 | 30m | 1d,1h | none")
    p.add_argument("--snooze", help="отложить напоминание: 15m | 1h")
    p.add_argument("--cancel", action="store_true", help="убрать событие из календаря")
    p.add_argument("--restore", action="store_true", help="вернуть закрытое событие (после /xK)")

    p = add("add", cmd_add, "дописать запись в раздел точки")
    p.add_argument("flow")
    p.add_argument("section", help="контекст | задачи | комментарии («идеи», «выводы» — тоже комментарии)")
    p.add_argument("text", help="текст или - (из stdin)")
    p.add_argument("--point", help="дата или номер прошлой точки; по умолчанию — открытая")
    p.add_argument("--src", help="id голосовой, к которой относится запись")
    p.add_argument("--summary", help="заодно обновить выжимку точки (одна фраза)")
    p.add_argument("--short", help="тезис в 1–2 строки (до ~100 знаков): суть сразу; его видно в списках, весь текст — по /fK")

    p = add("brief", cmd_brief, "дать тезис-заголовок уже записанной задаче, контексту или комментарию")
    p.add_argument("flow")
    p.add_argument("find", help="кусок текста записи")
    p.add_argument("title", help="тезис в 1–2 строки")
    p.add_argument("--point", help="дата или номер прошлой точки; по умолчанию — открытая")

    p = add("title", cmd_title, "заголовок точки")
    p.add_argument("flow")
    p.add_argument("title")
    p.add_argument("--point")

    p = add("summary", cmd_summary, "выжимка точки одной фразой (для оглавления потока)")
    p.add_argument("flow")
    p.add_argument("text")
    p.add_argument("--point")

    p = add("tag-point", cmd_tag_point, "теги точки")
    p.add_argument("flow")
    p.add_argument("tags", help="через запятую")
    p.add_argument("--point")

    p = add("set", cmd_set, "приоритет/статус/название потока")
    p.add_argument("flow")
    p.add_argument("--priority", help="1 — основной, 2 — фоновый")
    p.add_argument("--status")
    p.add_argument("--title")
    p.add_argument("--add-alias")
    p.add_argument("--tags")

    p = add("delete-flow", cmd_delete_flow, "удалить поток безвозвратно (нужен --force)")
    p.add_argument("flow")
    p.add_argument("--force", action="store_true", help="подтверждение безвозвратного удаления")

    p = add("undo", cmd_undo, "удалить последнюю добавленную запись (или --to перенести)")
    p.add_argument("--to", help="перенести в раздел вместо удаления")

    p = add("attach", cmd_attach, "привязать голосовую к потоку")
    p.add_argument("voice", help="id записи или last")
    p.add_argument("flow", nargs="?")
    p.add_argument("--point")

    p = add("note", cmd_note, "мысль без потока → входящие")
    p.add_argument("text")
    add("inbox", cmd_inbox, "неразобранное")
    p = add("inbox-move", cmd_inbox_move, "перенести заметку из входящих в поток")
    p.add_argument("n", type=int)
    p.add_argument("flow")
    p.add_argument("section")
    p.add_argument("--point")

    p = add("find", cmd_find, "поиск по точкам (исходники голосовых — с --sources)")
    p.add_argument("query", nargs="?")
    p.add_argument("--tag")
    p.add_argument("--flow")
    p.add_argument("--sources", action="store_true")
    p.add_argument("--limit", type=int, default=30)

    add("tags", cmd_tags, "словарь тегов и использование")
    p = add("tag-add", cmd_tag_add, "добавить тег в словарь")
    p.add_argument("tag")
    p.add_argument("description", nargs="?")

    add("remind", cmd_remind, "напоминания (для cron, пустой вывод = тишина)")
    add("index", cmd_index, "пересобрать ПОТОКИ.md и оглавления потоков")
    p = add("sync", cmd_sync, "git pull + push")
    p.add_argument("--prefer", choices=["server", "pc"], help="как решать конфликт одной строки")
    p = add("init", cmd_init, "создать структуру хранилища")
    p.add_argument("--git", action="store_true", help="сразу git init")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
        return 0
    except FlowsError as exc:
        print(f"ОШИБКА: {exc}")
        return 2
    except BrokenPipeError:  # вывод обрезали (| head) — это не ошибка
        return 0


if __name__ == "__main__":
    sys.exit(main())
