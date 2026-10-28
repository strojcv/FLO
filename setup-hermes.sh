#!/usr/bin/env bash
# Включает в Hermes три настройки, без которых органайзер работает не весь:
#   – голосовые идут в расшифровку органайзера (flows_stt.py: GigaAM, Whisper) — блок stt;
#   – /new без «точно?» — кнопки органайзера идут через /new и иначе срабатывают со второго нажатия;
#   – фоновая задача напоминаний раз в минуту (без нейросети) + название в push и ответ на напоминание.
# Чего не хватает — показывает и спрашивает «Сделать? [Д/н]»; на «да» копирует config.yaml в
# config.yaml.bak-flows-<дата> и меняет штатными командами hermes (комментарии в config.yaml целы).
# Без терминала (ssh без -t, cron) не спрашивает, а печатает команды; FLO_YES=1 — делать без вопроса.
# Запускается сам из install.sh (и вручную под пользователем Hermes). Можно повторно.
# Выход: 0 — всё на месте, 1 — что-то осталось сделать.
set -uo pipefail

HH="${HERMES_HOME:-$HOME/.hermes}"
CY="$HH/config.yaml"
JOBS="$HH/cron/jobs.json"
STT_CMD="python3 $HH/scripts/flows_stt.py {input_path} {output_path}"

missing() {  # что из трёх не сделано — по строке на пункт
  grep -q "flows_stt.py" "$CY" 2>/dev/null || echo stt
  grep -qE "^\s*destructive_slash_confirm:\s*false" "$CY" 2>/dev/null || echo confirm
  grep -q "flows_remind.py" "$JOBS" 2>/dev/null || echo cron
}
explain() {
  case "$1" in
    stt)     echo "  – голосовые → расшифровка органайзера (GigaAM, запасной Whisper)";;
    confirm) echo "  – кнопки с одного нажатия: /new без «точно?» (пропадёт и у других «опасных» команд)";;
    cron)    echo "  – напоминания: фоновая задача раз в минуту, без нейросети, токены не тратит";;
  esac
}
commands() {
  case "$1" in
    stt)     echo "hermes config set stt.providers.flows.type command"
             echo "hermes config set stt.providers.flows.command \"$STT_CMD\""
             echo "hermes config set stt.providers.flows.format txt"
             echo "hermes config set stt.providers.flows.timeout 3600"
             echo "hermes config set stt.provider flows";;
    confirm) echo "hermes config set approvals.destructive_slash_confirm false";;
    cron)    echo "hermes cron create \"every 1m\" --no-agent --script flows_remind.py --deliver telegram --name flows-reminders"
             echo "hermes config set cron.wrap_response false"
             echo "hermes config set cron.mirror_delivery true";;
  esac
}

TODO=$(missing)
if [ -z "$TODO" ]; then echo "✓ Настройки Hermes на месте (голосовые, /new, напоминания)"; exit 0; fi

echo
echo "Hermes нужно настроить:"
for k in $TODO; do explain "$k"; done

# Настоящий hermes — только со своим HERMES_HOME (с чужим он однажды переписал свой лаунчер и сломался)
CAN=1
[ "$HH" = "$HOME/.hermes" ] || CAN=0
command -v hermes >/dev/null || CAN=0
[ -f "$CY" ] || CAN=0

ANS=""
if [ "$CAN" = 1 ]; then
  if [ "${FLO_YES:-}" = 1 ]; then ANS=y
  elif [ -t 0 ]; then read -r -p "Сделать? [Д/н] " ANS; ANS="${ANS:-y}"
  fi
fi
case "$ANS" in   # не [yYдД]*: без UTF-8-локали «н» и «д» совпадают по первому байту
  y*|Y*|д*|Д*) ;;
  *)
    echo "Не сделано. Команды (под пользователем Hermes), потом hermes gateway restart:"
    for k in $TODO; do commands "$k" | sed 's/^/  /'; done
    exit 1;;
esac

BAK="$CY.bak-flows-$(date +%Y%m%d-%H%M)"
cp "$CY" "$BAK" && echo "▸ Копия настроек: $BAK"
for k in $TODO; do
  while IFS= read -r c; do
    out="$(eval "$c" 2>&1)" || { echo "✗ $c"; echo "$out" | tail -3 | sed 's/^/    /'; }
  done < <(commands "$k")
done
LEFT=$(missing)
if [ -z "$LEFT" ]; then echo "✓ Настройки Hermes включены — нужен hermes gateway restart"; exit 0; fi
echo "⚠ Не включилось:"; for k in $LEFT; do explain "$k"; done
echo "  Откат: cp $BAK $CY"
exit 1
