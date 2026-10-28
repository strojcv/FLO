#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
facts.py — «копилка»: факты вне потоков (телефоны, адреса, контакты, любые данные).

  ~/brain/копилка/
    КОПИЛКА.md                картотека: все теги со ссылками + все записи по датам (собирается сама)
    2026-10-23-1218.md        одна запись = один файл, имя — дата и время сохранения
    _files/                   вложения (фото визитки, PDF)

Запись:
  ---
  type: fact
  saved: 2026-10-23T12:18
  title: Стоматолог в Палермо — доктор Гомес
  tags: [стоматолог, медицина, буэнос-айрес]
  ---
  # Стоматолог в Палермо — доктор Гомес
  - Телефон: +54 9 11 5555-1234
  - Адрес: Thames 1234, Palermo
  ## Дословно
  <как сказал хозяин>

Хранилище, git-синхронизация и общие помощники — из flows.py (тот же ~/brain).
Справка: python3 facts.py --help
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flows  # noqa: E402
from flows import FlowsError, Note, die, norm, now, short, dm, parse_dt  # noqa: E402

FACTS = flows.BRAIN / "копилка"
FILES = FACTS / "_files"
CATALOG = FACTS / "КОПИЛКА.md"
FACT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}(?:-\d+)?\.md$")


# ─────────────────────────────── модель ───────────────────────────────────

def norm_tag(t: str) -> str:
    t = norm(t).lstrip("#").strip()
    t = re.sub(r"\s+", "-", t)
    return re.sub(r"[^\w\-]", "", t).strip("-")


def parse_tags(s: str | None) -> list[str]:
    if not s:
        return []
    return list(dict.fromkeys(x for x in (norm_tag(t) for t in re.split(r"[,;]", s)) if x))


class Fact:
    def __init__(self, path: Path):
        self.path = path
        self.note = Note(path)

    @property
    def id(self) -> str:
        return self.path.stem

    @property
    def title(self) -> str:
        return str(self.note.get("title") or self.note.h1() or self.id)

    @property
    def tags(self) -> list[str]:
        t = self.note.get("tags", [])
        return [norm_tag(x) for x in (t if isinstance(t, list) else [t]) if norm_tag(x)]

    @property
    def saved(self):
        return parse_dt(self.note.get("saved") or self.id[:10])[0]

    def body_text(self) -> str:
        return re.sub(r"^#\s+.*\n?", "", self.note.body.strip(), count=1).strip()

    def link(self) -> str:
        return f"[[копилка/{self.id}|{self.title}]]"


def all_facts() -> list[Fact]:
    if not FACTS.exists():
        return []
    return [Fact(p) for p in sorted(FACTS.iterdir(), reverse=True) if p.is_file() and FACT_RE.match(p.name)]


def find_fact(fid: str) -> Fact:
    fid = fid.strip().removesuffix(".md")
    if fid in ("last", "последняя", "последний"):
        facts = all_facts()
        if not facts:
            die("Копилка пуста.")
        return facts[0]
    p = FACTS / f"{fid}.md"
    if p.exists():
        return Fact(p)
    hits = [f for f in all_facts() if f.id.startswith(fid)]
    if len(hits) == 1:
        return hits[0]
    die(f"Нет записи {fid}." if not hits else f"{fid}: подходит несколько — " + ", ".join(f.id for f in hits[:5]))


def tag_counts() -> dict[str, int]:
    c: dict[str, int] = {}
    for f in all_facts():
        for t in f.tags:
            c[t] = c.get(t, 0) + 1
    return c


def similar_tags(tag: str, existing: list[str]) -> list[str]:
    out = []
    for e in existing:
        if e == tag:
            continue
        if (e in tag or tag in e) and min(len(e), len(tag)) >= 3:
            out.append(e)
        elif flows._common_prefix(e, tag) >= max(5, min(len(e), len(tag)) - 2):
            out.append(e)
    return out


# ─────────────────────────────── картотека ────────────────────────────────

def build_catalog() -> str:
    facts = all_facts()
    by_tag: dict[str, list[Fact]] = {}
    for f in facts:
        for t in f.tags:
            by_tag.setdefault(t, []).append(f)
    out = ["# Копилка", "", "_Собирается автоматически — правки здесь затрутся. "
           "Править нужно сами записи._", "", f"Записей: {len(facts)}, тегов: {len(by_tag)}", "",
           "## Теги", ""]
    for t in sorted(by_tag, key=lambda x: (-len(by_tag[x]), x)):
        out.append(f"- **#{t}** ({len(by_tag[t])}): " + " · ".join(f.link() for f in by_tag[t]))
    out += ["", "## Все записи", ""]
    for f in facts:
        d = f.saved
        out.append(f"- {dm(d.date()) if d else f.id[:10]} — {f.link()} · " + " ".join(f"#{t}" for t in f.tags))
    return "\n".join(out).rstrip() + "\n"


def write_catalog():
    FACTS.mkdir(parents=True, exist_ok=True)
    content = build_catalog()
    if not CATALOG.exists() or CATALOG.read_text("utf-8") != content:
        CATALOG.write_text(content, "utf-8")


class Mutation(flows.Mutation):
    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            write_catalog()
        return super().__exit__(exc_type, exc, tb)


# ─────────────────────────────── вывод ────────────────────────────────────

def print_full(f: Fact):
    d = f.saved
    print(f"📌 {f.title}  [{f.id}]")
    print("Теги: " + (", ".join(f.tags) or "—") + (f" · сохранено {dm(d.date())}" if d else ""))
    body = f.body_text()
    if body:
        print(flows.plain(body))


def print_brief(f: Fact):
    first = ""
    for line in f.body_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            first = flows.plain(re.sub(r"^[-*]\s+", "", line))
            break
    print(f"• {f.title} [{f.id}] · {', '.join(f.tags)}" + (f"\n  {short(first, 120)}" if first else ""))


def print_results(facts: list[Fact], full: bool, limit: int):
    if not facts:
        print("Ничего не нашёл.")
        return
    show = facts[:limit]
    if full or len(show) <= 3:
        for i, f in enumerate(show):
            if i:
                print()
            print_full(f)
    else:
        print(f"Найдено: {len(facts)}")
        for f in show:
            print_brief(f)
        print("Подробно: show <id>")
    if len(facts) > limit:
        print(f"… и ещё {len(facts) - limit} (уточни запрос или --limit)")


# ─────────────────────────────── команды ──────────────────────────────────

def new_fact_path() -> Path:
    t = now()
    base = t.strftime("%Y-%m-%d-%H%M")
    p, n = FACTS / f"{base}.md", 2
    while p.exists():
        p = FACTS / f"{base}-{n}.md"
        n += 1
    return p


def cmd_save(a):
    text = flows.read_text_input(a.text)
    tags = parse_tags(a.tags)
    if not tags:
        die("Нужны теги: --tags \"стоматолог,медицина,буэнос-айрес\"")
    title = (a.title or "").strip() or short(text.splitlines()[0], 70)
    existing = list(tag_counts())
    with Mutation(f"Копилка: {title}"):
        path = new_fact_path()
        note = Note(path)
        note.set("type", "fact")
        note.set("saved", now().strftime("%Y-%m-%dT%H:%M"))
        note.set("title", title)
        note.set("tags", tags)
        lines = [f"# {title}", ""]
        for fld in a.field or []:
            k, sep, v = fld.partition("=")
            lines.append(f"- {k.strip()}: {v.strip()}" if sep else f"- {fld.strip()}")
        for src in a.attach or []:
            sp = Path(src).expanduser()
            if not sp.is_file():
                die(f"Нет файла {src}")
            FILES.mkdir(parents=True, exist_ok=True)
            dest = FILES / f"{path.stem}-{re.sub(r'[^\w.\-]', '_', sp.name)}"
            shutil.copy2(sp, dest)
            lines.append(f"- Файл: ![[копилка/_files/{dest.name}]]")
        if len(lines) > 2:
            lines.append("")
        lines += ["## Дословно", text]
        note.body = "\n" + "\n".join(lines) + "\n"
        note.save()
    fact = Fact(path)
    print(f"✓ Сохранил в копилку: «{title}» [{fact.id}] · теги: {', '.join(tags)}")
    new = [t for t in tags if t not in existing]
    for t in new:
        sim = similar_tags(t, existing)
        print(f"  новый тег #{t}" + (f" — похожие уже есть: {', '.join('#' + s for s in sim)}" if sim else ""))


def cmd_find(a):
    facts = all_facts()
    tags = parse_tags(",".join(a.tag or []))
    if tags:
        if a.any:
            facts = [f for f in facts if set(tags) & set(f.tags)]
        else:
            facts = [f for f in facts if set(tags) <= set(f.tags)]
    if a.query:
        ids: set[str] = set()
        if shutil.which("rg"):
            r = subprocess.run(["rg", "-l", "-i", "-F", "-g", "*.md", "-g", "!КОПИЛКА.md", "--", a.query, str(FACTS)],
                               capture_output=True, text=True)
            ids = {Path(x).stem for x in r.stdout.split()}
        else:
            q = norm(a.query)
            ids = {f.id for f in all_facts() if q in norm(f.path.read_text("utf-8"))}
        # «ё/е» и регистр: добираем совпадения по нормализованному тексту
        q = norm(a.query)
        ids |= {f.id for f in facts if q in norm(f.title) or any(q in t for t in f.tags)}
        facts = [f for f in facts if f.id in ids]
    if not tags and not a.query:
        die("Что искать? find \"текст\" или find --tag тег")
    print_results(facts, a.full, a.limit)


def cmd_show(a):
    for i, fid in enumerate(a.ids):
        if i:
            print()
        print_full(find_fact(fid))


def cmd_recent(a):
    print_results(all_facts()[: a.n], False, a.n)


def cmd_tags(a):
    c = tag_counts()
    if not c:
        print("Тегов пока нет.")
        return
    items = sorted(c.items(), key=lambda x: (-x[1], x[0]))
    print(f"Теги копилки ({len(items)}): " + ", ".join(f"{t} {n}" for t, n in items))


def cmd_retag(a):
    f = find_fact(a.id)
    add, rem = parse_tags(a.add), set(parse_tags(a.remove))
    tags = [t for t in f.tags if t not in rem] + [t for t in add if t not in f.tags]
    if not tags:
        die("У записи должен остаться хотя бы один тег.")
    with Mutation(f"Копилка: теги «{f.title}»"):
        f.note.set("tags", tags)
        f.note.save()
    print(f"✓ «{f.title}»: теги {', '.join(tags)}")


def cmd_rename_tag(a):
    old, new = norm_tag(a.old), norm_tag(a.new)
    hits = [f for f in all_facts() if old in f.tags]
    if not hits:
        die(f"Тега #{old} нет.")
    with Mutation(f"Копилка: #{old} → #{new}"):
        for f in hits:
            f.note.set("tags", list(dict.fromkeys(new if t == old else t for t in f.tags)))
            f.note.save()
    print(f"✓ #{old} → #{new} в {len(hits)} записях")


def cmd_note(a):
    f = find_fact(a.id)
    text = flows.read_text_input(a.text)
    with Mutation(f"Копилка: дополнение «{f.title}»"):
        f.note.append_entry("Дополнения", flows.make_entry(text))
        f.note.save()
    print(f"✓ «{f.title}»: дополнение записано")


def cmd_title(a):
    f = find_fact(a.id)
    with Mutation(f"Копилка: название «{a.title}»"):
        f.note.set("title", a.title.strip())
        f.note.set_h1(a.title.strip())
        f.note.save()
    print(f"✓ [{f.id}] теперь «{a.title.strip()}»")


def cmd_delete(a):
    f = find_fact(a.id)
    with Mutation(f"Копилка: удалена «{f.title}»"):
        for m in re.findall(r"!\[\[копилка/_files/([^\]|]+)", f.note.body):
            (FILES / m).unlink(missing_ok=True)
        f.path.unlink()
    print(f"✓ Удалил «{f.title}» [{f.id}] (копия остаётся в истории git)")


def cmd_index(a):
    with Mutation("Копилка: картотека"):
        pass
    print(f"✓ {CATALOG.relative_to(flows.BRAIN)} обновлён")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="facts.py", description="Копилка фактов: телефоны, адреса, контакты, данные.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        return p

    p = add("save", cmd_save, "сохранить запись")
    p.add_argument("text", help="дословно, что сказал хозяин, или - (stdin)")
    p.add_argument("--title", help="короткое название (видно в списках)")
    p.add_argument("--tags", required=True, help="через запятую: что, категория, место")
    p.add_argument("--field", action="append", help="'Телефон=+54 9 11 …' (можно несколько)")
    p.add_argument("--attach", action="append", help="путь к файлу (фото визитки, PDF)")

    p = add("find", cmd_find, "поиск: по тексту и/или тегам")
    p.add_argument("query", nargs="?")
    p.add_argument("--tag", action="append", help="можно несколько; по умолчанию нужны все")
    p.add_argument("--any", action="store_true", help="хватит любого из тегов")
    p.add_argument("--full", action="store_true", help="показать записи целиком")
    p.add_argument("--limit", type=int, default=20)

    p = add("show", cmd_show, "запись(и) целиком")
    p.add_argument("ids", nargs="+")
    p = add("recent", cmd_recent, "последние записи")
    p.add_argument("n", nargs="?", type=int, default=10)
    add("tags", cmd_tags, "все теги с количеством (компактно)")

    p = add("retag", cmd_retag, "поменять теги записи")
    p.add_argument("id")
    p.add_argument("--add")
    p.add_argument("--remove")
    p = add("rename-tag", cmd_rename_tag, "переименовать/слить тег во всех записях")
    p.add_argument("old")
    p.add_argument("new")
    p = add("note", cmd_note, "дописать обновление в запись (новый телефон и т.п.)")
    p.add_argument("id")
    p.add_argument("text")
    p = add("title", cmd_title, "переименовать запись")
    p.add_argument("id")
    p.add_argument("title")
    p = add("delete", cmd_delete, "удалить запись (только после подтверждения хозяина)")
    p.add_argument("id")
    add("index", cmd_index, "пересобрать КОПИЛКА.md")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
        return 0
    except FlowsError as exc:
        print(f"ОШИБКА: {exc}")
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
