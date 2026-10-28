#!/usr/bin/env bash
# Установка И обновление потокового органайзера для Hermes (один и тот же скрипт).
# Запускать на сервере ОТ ТОГО ПОЛЬЗОВАТЕЛЯ, под которым работает Hermes:
#   bash ~/flows-organizer/install.sh            # хранилище в ~/brain
#   bash ~/flows-organizer/install.sh /path/vault
#
# Что делает (можно запускать сколько угодно раз):
#   - кладёт скрипты в ~/.hermes/scripts и навыки flows, facts в ~/.hermes/skills/productivity (перезаписывая свои);
#   - заменяет ~/.hermes/SOUL.md, если он отличается (старый — в SOUL.md.bak-flows-<дата>);
#   - создаёт ~/.config/flows/config.json и ~/brain, только если их ещё нет;
#   - кладёт хук ~/.hermes/hooks/flows-nav (кнопки через /new и экран после /new) и
#     ~/.hermes/locales/{en,ru}.yaml — пустой стандартный ответ на /new (вместо него — экран);
#   - переводит данные на «одну открытую точку в потоке» (flows.py migrate, можно повторять);
#   - заводит кнопки в config.yaml (setup-commands.sh: только свои quick_commands, остальное не трогает);
#   - прогоняет тесты пакета.
# Не трогает: config.json, остальной config.yaml, другие навыки, задачи cron, Whisper.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
HH="${HERMES_HOME:-$HOME/.hermes}"
BRAIN="${1:-$HOME/brain}"
CFG_DIR="$HOME/.config/flows"
VENV_PY="$HOME/.openclaw/tools/venv/bin/python"
NEW_VER="$(cat "$SRC/VERSION")"
OLD_VER="$(cat "$CFG_DIR/installed-version" 2>/dev/null || echo "")"
STAMP="$(date +%Y%m%d-%H%M)"
TZ_NAME="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)"

echo "▸ Hermes: $HH"
echo "▸ Хранилище: $BRAIN"
if [ -n "$OLD_VER" ]; then echo "▸ Обновление: $OLD_VER → $NEW_VER"; else echo "▸ Первая установка: $NEW_VER"; fi

# 0. Не затираем чужое: навык или скрипты с такими именами уже есть?
for SK in flows facts; do
  if [ -e "$HH/skills/productivity/$SK" ] && [ ! -f "$HH/skills/productivity/$SK/.flows-organizer" ]; then
    echo "✗ Уже есть навык $HH/skills/productivity/$SK — не наш. Останавливаюсь, ничего не изменено."
    exit 1
  fi
  find "$HH/skills" -name SKILL.md -path "*/$SK/*" 2>/dev/null | grep -v "productivity/$SK/" && {
    echo "✗ Навык с именем $SK уже есть в другой папке (см. выше). Останавливаюсь."; exit 1; } || true
done

# 1. Тесты пакета — до установки: сломанное не ставим.
#    Отчёт — во временный файл: в папку пакета писать не можем (её могли залить по FTP от другого пользователя)
LOG="$(mktemp "${TMPDIR:-/tmp}/flows-install-tests.XXXXXX")"
if ! bash "$SRC/tests/run_tests.sh" > "$LOG" 2>&1; then
  echo "✗ Тесты пакета не прошли — ничего не установлено. Подробности: $LOG"
  grep "✗" "$LOG" | head -10
  tail -1 "$LOG"
  exit 1
fi
echo "▸ Тесты: $(tail -1 "$LOG")"
rm -f "$LOG"

mkdir -p "$HH/scripts" "$HH/skills/productivity/flows" "$HH/skills/productivity/facts" "$CFG_DIR"

# 2. Скрипты — в $HERMES_HOME/scripts (cron без агента запускает только оттуда)
cp "$SRC"/hermes-scripts/flows.py "$SRC"/hermes-scripts/flows_stt.py \
   "$SRC"/hermes-scripts/flows_whisper_worker.py "$SRC"/hermes-scripts/facts.py \
   "$SRC"/hermes-scripts/flows_remind.py "$HH/scripts/"
rm -f "$HH/scripts/flows_digest.py"   # утренней сводки больше нет (2026.10.27.2)
chmod +x "$HH"/scripts/flows*.py "$HH"/scripts/facts.py

# 3. Навыки flows и facts (отдельные папки, рядом с твоими)
for SK in flows facts; do
  cp -r "$SRC"/skill/$SK/. "$HH/skills/productivity/$SK/"
  touch "$HH/skills/productivity/$SK/.flows-organizer"
done

# 3а. Хук для кнопок через /new (наша папка целиком); плагин flows-view из 2026.10.26 больше не нужен
mkdir -p "$HH/hooks/flows-nav"
cp -r "$SRC/hermes-hooks/flows-nav/." "$HH/hooks/flows-nav/"
rm -rf "$HH/plugins/flows-view"

# 3б. Пустой стандартный ответ на /new — вместо него экран органайзера (чужие файлы перевода не трогаем)
mkdir -p "$HH/locales"
for L in en ru; do
  F="$HH/locales/$L.yaml"
  if [ ! -f "$F" ] || grep -q "^# flows-organizer" "$F"; then
    cp "$SRC/hermes-locales/quiet-new.yaml" "$F"
  else
    echo "⚠ $F — чужой файл перевода, не трогаю. Чтобы /new не слал стандартный ответ, добавь в него"
    echo "  ключи из $SRC/hermes-locales/quiet-new.yaml"
  fi
done

# 4. SOUL.md — наш целиком; старый сохраняем
if [ -f "$HH/SOUL.md" ] && cmp -s "$SRC/SOUL.md" "$HH/SOUL.md"; then
  echo "▸ SOUL.md без изменений"
else
  [ -f "$HH/SOUL.md" ] && cp "$HH/SOUL.md" "$HH/SOUL.md.bak-flows-$STAMP" && echo "▸ Старый SOUL.md → SOUL.md.bak-flows-$STAMP"
  cp "$SRC/SOUL.md" "$HH/SOUL.md"
  echo "▸ SOUL.md обновлён"
fi

# 5. Конфиг органайзера (существующий не перезаписываем)
if [ ! -f "$CFG_DIR/config.json" ]; then
  cat > "$CFG_DIR/config.json" <<EOF
{
  "brain": "$BRAIN",
  "timezone": "$TZ_NAME",
  "git_autosync": false,
  "stale_days": {"1": 3, "2": 7, "3": 14},
  "stt_backend": "venv",
  "stt_venv_python": "$VENV_PY",
  "stt_engines": ["openai-whisper:small", "faster-whisper:base"],
  "stt_language": "ru",
  "stt_min_save_seconds": 60
}
EOF
  echo "▸ Создан $CFG_DIR/config.json"
fi
if [ -x "$VENV_PY" ]; then
  echo "▸ Whisper venv найден: $VENV_PY"
else
  echo "⚠ Не нашёл $VENV_PY — поправь stt_venv_python в $CFG_DIR/config.json"
fi

# 6. Структура хранилища + git (существующие данные не трогает)
python3 "$HH/scripts/flows.py" init --git
python3 "$HH/scripts/flows.py" migrate
python3 "$HH/scripts/facts.py" index >/dev/null
if [ -z "$(git -C "$BRAIN" config user.name || true)" ]; then
  git -C "$BRAIN" config user.name "Hermes"
  git -C "$BRAIN" config user.email "hermes@localhost"
fi
echo "$NEW_VER" > "$CFG_DIR/installed-version"

# 7. config.yaml: кнопки заводим сами, остальное проверяем и подсказываем
TODO=0
CY="$HH/config.yaml"
if ! grep -q "flows_stt.py" "$CY" 2>/dev/null; then
  TODO=1
  cat <<EOF

⚠ В $CY не подключена расшифровка. Резервная копия: cp $CY $CY.bak-flows
  Затем замени блок stt на:

stt:
  provider: flows
  providers:
    flows:
      type: command
      command: "python3 $HH/scripts/flows_stt.py {input_path} {output_path}"
      format: txt
      timeout: 3600
EOF
fi
if [ -f "$CY" ]; then
  HERMES_HOME="$HH" bash "$SRC/setup-commands.sh" || { TODO=1; echo "⚠ Кнопки не завелись — запусти: bash $SRC/setup-commands.sh"; }
fi
if ! grep -qE "^\s*destructive_slash_confirm:\s*false" "$CY" 2>/dev/null; then
  TODO=1
  cat <<EOF

⚠ /new спрашивает подтверждение — кнопки органайзера (они идут через /new) сработают со второго
  нажатия. Чтобы с одного: hermes config set approvals.destructive_slash_confirm false
EOF
fi
if ! grep -q "flows_remind.py" "$HH/cron/jobs.json" 2>/dev/null; then
  TODO=1
  cat <<EOF

⚠ Нет фоновых задач органайзера (без нейросети, токены не тратят):
   hermes cron create "every 1m" --no-agent --script flows_remind.py --deliver telegram --name flows-reminders
   hermes config set cron.wrap_response false     # чтобы в push было название точки, а не «Cronjob Response»
   hermes config set cron.mirror_delivery true    # чтобы на напоминание можно было ответить «отложи на 15 минут»
EOF
fi

cat <<EOF

✅ FLO $NEW_VER на месте. Данные в $BRAIN сохранены, config.json не тронут.
EOF
if [ "$TODO" = 1 ]; then echo "Сначала сделай пункты с ⚠ выше (подробно — README.md)."; fi
cat <<EOF
Чтобы Hermes подхватил новые навыки и SOUL.md: hermes gateway restart
(или /restart в Telegram), затем /new.
Проверка: python3 $HH/scripts/flows.py status
EOF
