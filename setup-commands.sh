#!/usr/bin/env bash
# Заводит в ~/.hermes/config.yaml кнопки органайзера (quick_commands, без нейросети):
#   /list  — каталог потоков 1-го и 2-го приоритета;  /list3 — 3-го приоритета;
#   /1 … /30 — карточка открытой точки потока с этим номером из последнего каталога;
#   /point — последняя открытая точка;  /prev, /next — листать точки потока;
#   /comments — комментарии точки под указателем (идеи — среди них, с 💡);
#   /full, /zip — задачи в карточке полным текстом / короткими заголовками (вид запоминается);
#   /conclusions — выводы потока;  /calendar — календарь;
#   /x1 … /x30 — отметить выполненной (скрыть) задачу/идею с этим номером в последней карточке
#                (на экране календаря — закрыть событие).
# Режим кнопок перехода (всё, кроме /x…):
#   new  — кнопка = «/new flows <куда>»: хук flows-nav переставляет указатель, /new чистит контекст,
#          тот же хук на session:reset присылает экран. Умеет ли Hermes — по его исходникам.
#   exec — кнопка сразу запускает flows.py nav <куда>; контекст не чистится. Запасной режим.
# Выбор: "nav_mode" в ~/.config/flows/config.json (auto | new | exec), по умолчанию auto.
# Запускается сам из install.sh (и вручную под пользователем Hermes). Можно повторно: свои команды заменяет,
# чужие quick_commands не трогает; если всё уже на месте — config.yaml не переписывает.
# Если в Telegram /1 не нажимается — в config.json поставь "list_cmd_prefix": "n" и запусти снова.
set -euo pipefail

HH="${HERMES_HOME:-$HOME/.hermes}"
CY="$HH/config.yaml"
CFG="${FLOWS_CONFIG:-$HOME/.config/flows/config.json}"
[ -f "$CY" ] || { echo "✗ Нет $CY"; exit 1; }

HA="${HERMES_AGENT_DIR:-$HH/hermes-agent}"
WANT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("nav_mode","auto"))' "$CFG" 2>/dev/null || echo auto)
MODE=exec
if [ "$WANT" = new ]; then
  MODE=new
elif [ "$WANT" = auto ] && [ -f "$HH/hooks/flows-nav/handler.py" ]; then
  # Умеет ли Hermes: читаем его исходники (ничего не запускаем — однажды запуск hermes из скрипта сломал его лаунчер)
  # нужно: хук command:new может подменить команду ("rewrite") и /new шлёт событие session:reset
  if grep -rqs --include='*.py' '"rewrite"' "$HA/gateway" && grep -rqs --include='*.py' 'session:reset' "$HA/gateway"
  then MODE=new; fi
fi

python3 - "$CY" "$HH/scripts/flows.py" "$MODE" "$HA" <<'PY'
import json, os, pathlib, re, shutil, sys, time
cy, flows, mode = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
builtin_src = pathlib.Path(sys.argv[4]) / "hermes_cli" / "commands.py"   # встроенные команды Hermes
cfg = pathlib.Path(os.environ.get("FLOWS_CONFIG", pathlib.Path.home() / ".config/flows/config.json"))
prefix = str(json.loads(cfg.read_text()).get("list_cmd_prefix", "")) if cfg.exists() else ""

nav = {"list": "list", "list3": "list3", "point": "point", "prev": "prev", "next": "next",
       "comments": "card комментарии", "done": "card done", "full": "full", "zip": "zip", "conclusions": "conclusions", "calendar": "calendar"}
nav.update({f"{prefix}{i}": f"open {i}" for i in range(1, 31)})
ours = {}
for name, where in nav.items():
    ours[name] = (("alias", f"/new flows {where}") if mode == "new"
                  else ("exec", f"python3 {flows} nav {where}"))
ours.update({f"x{i}": ("exec", f"python3 {flows} hide {i}") for i in range(1, 31)})

# Встроенная команда Hermes срабатывает раньше quick_commands — кнопка с тем же именем не дойдёт до нас
builtin = set()
if builtin_src.exists():
    for chunk in builtin_src.read_text("utf-8").split("CommandDef(")[1:]:
        m = re.match(r'\s*"([\w-]+)"', chunk)
        if m:
            builtin.add(m.group(1))
            al = re.search(r"aliases=\(([^)]*)\)", chunk.split("CommandDef")[0])
            builtin.update(re.findall(r'"([\w-]+)"', al.group(1)) if al else [])
for name in sorted(n for n in ours if n in builtin):
    print(f"⚠ Кнопка /{name} совпадает со встроенной командой Hermes — Hermes перехватит её. Переименуй в setup-commands.sh и flows.py.")

LEGACY = {"tochka", "nazad", "vpered", "vyvody", "idei", "ideas", "insights"}   # наши старые кнопки — убрать
L = cy.read_text().split("\n")
keep = []                                   # чужие команды из старого блока — сохраняем
if "quick_commands:" in L:
    s = L.index("quick_commands:"); e = s + 1
    while e < len(L) and (not L[e].strip() or L[e].startswith(" ")): e += 1
    block, cur = L[s + 1:e], None
    for line in block:
        m = re.match(r'^  ["\']?([^"\':\s]+)["\']?:\s*$', line)
        if m:
            cur = m.group(1)
        if cur and cur not in ours and cur not in LEGACY and not re.fullmatch(r"[nx]?\d+", cur):
            keep.append(line)
    del L[s:e]
new = ["quick_commands:"] + [l for l in keep if l.strip()]
for name, (kind, cmd) in ours.items():
    new += [f'  "{name}":', f"    type: {kind}", f'    {"target" if kind == "alias" else "command"}: "{cmd}"']
while L and not L[-1].strip(): L.pop()
text = "\n".join(L + [""] + new) + "\n"
how = "через /new, контекст чистится" if mode == "new" else "без /new, контекст не чистится"
if text == cy.read_text():
    print(f"✓ Кнопки уже на месте ({how})"); sys.exit(0)
shutil.copy(cy, f"{cy}.bak-commands-{time.strftime('%Y%m%d-%H%M')}")
cy.write_text(text)
print(f"✓ Кнопки: /list, /list3, /point, /{prefix}1 … /{prefix}30, /prev, /next, /comments, /done, /full, /zip, /conclusions, /calendar, /x1 … /x30"
      f" ({how}) — нужен /restart")
PY
