#!/usr/bin/env bash
# Самопроверка пакета: всё в одноразовой папке, реальные файлы не трогаются.
#   bash tests/run_tests.sh
# Нужны: python3 (3.9+), git. ripgrep желателен, но не обязателен.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
H="$ROOT/hermes-scripts"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HOME="$T/home"; mkdir -p "$HOME"
# Настоящий hermes тестам запускать НЕЛЬЗЯ: с чужим HERMES_HOME он переписывает свой лаунчер
# на Python из временной папки (так однажды сломался сервер). Вместо него — заглушка.
mkdir -p "$T/bin"
printf '#!/bin/sh\necho "$*" >> "%s/hermes-calls"\n' "$T" > "$T/bin/hermes"; chmod +x "$T/bin/hermes"
export PATH="$T/bin:$PATH"
export FLOWS_CONFIG="$T/config.json" PYTHONDONTWRITEBYTECODE=1
cat > "$FLOWS_CONFIG" <<EOF
{"brain": "$T/brain", "git_autosync": true, "stt_venv_python": "$T/venv/bin/python"}
EOF
F="python3 $H/flows.py"
K="python3 $H/facts.py"
PASS=0; FAIL=0

check() {  # check "описание" "ожидаемый фрагмент" команда...
  local desc="$1" want="$2"; shift 2
  local out; out="$("$@" 2>&1)"
  if grep -qF -- "$want" <<<"$out"; then PASS=$((PASS+1)); echo "  ✓ $desc"
  else FAIL=$((FAIL+1)); echo "  ✗ $desc"; echo "    ждали: $want"; echo "    вывод: ${out//$'\n'/$'\n'           }"; fi
}
empty() {  # empty "описание" команда... — вывод должен быть пустым
  local desc="$1"; shift
  local out; out="$("$@" 2>&1)"
  if [ -z "$out" ]; then PASS=$((PASS+1)); echo "  ✓ $desc"
  else FAIL=$((FAIL+1)); echo "  ✗ $desc (ждали тишину)"; echo "    вывод: $out"; fi
}
lacks() {  # lacks "описание" "фрагмент, которого быть не должно" команда...
  local desc="$1" bad="$2"; shift 2
  local out; out="$("$@" 2>&1)"
  if grep -qiF -- "$bad" <<<"$out"; then FAIL=$((FAIL+1)); echo "  ✗ $desc"; echo "    лишнее: $bad"; echo "    вывод: $out"
  else PASS=$((PASS+1)); echo "  ✓ $desc"; fi
}
at() { FLOWS_NOW="$1" "${@:2}"; }

echo "▸ хранилище"
check "init" "Хранилище" $F init --git
git -C "$T/brain" config user.email t@t; git -C "$T/brain" config user.name t

echo "▸ потоки и точки"
export FLOWS_NOW=2026-10-20T10:00
check "новый поток" "создан (reklama)" $F new-flow "Реклама" --priority 1 --aliases "таргет" --tags реклама
check "…сразу с открытой точкой" "status: open" cat "$T/brain/flows/reklama/2026-10-20.md"
check "второй поток" "создан" $F new-flow "Продающие звонки" --priority 2
check "приоритет только 1–3" "invalid choice" $F new-flow "Лишний" --priority 4
check "заголовок точки" "Кабинет настроен" $F title рекламе "Кабинет настроен"
check "контекст" "Контекст +1" $F add реклама контекст "Создал кабинет, пиксель стоит"
check "задача — в открытую точку" "20.10 «Кабинет настроен» · Задачи +1" $F add реклама задачи "Проверить пиксель"
check "--next (старое) — тоже в открытую" "20.10 «Кабинет настроен» · Задачи +1" $F add реклама задачи "Сделать статичные креативы" --next
check "комментарий — в открытую точку" "20.10 «Кабинет настроен» · Комментарии +1" $F add реклама комментарий "Пиксель — до запуска"
check "идея в «.» — комментарий с 💡 в поток под указателем" "20.10 «Кабинет настроен» · Комментарии 💡 +1" $F add . идеи "Креатив с отзывом #продажи"
check "ещё комментарий" "Комментарии +1" $F add реклама комментарии "Ставка 5\$ — мало"
check "undo --to идеи — комментарий стал идеей" "в раздел «Комментарии» (💡 идея)" $F undo --to идеи
check "…в файле с лампочкой" "💡 Ставка 5\$ — мало" cat "$T/brain/flows/reklama/2026-10-20.md"
check "вывод — в выводы потока, не в точку" "Реклама · Глобальные выводы +1" $F add реклама вывод "Пиксель ставить до запуска"
check "…в _выводы.md" "Пиксель ставить до запуска" cat "$T/brain/flows/reklama/_выводы.md"
lacks "…а в точке его нет" "Пиксель ставить" cat "$T/brain/flows/reklama/2026-10-20.md"
check "point выводы — подсказка про поток" "Выводы — у потока" $F point реклама 2026-10-20 --section выводы
check "карточка: ⚡ — первый символ у открытой точки" "⚡ Реклама · 🔴
Точка 1 из 1 · 20.10.2026
«Кабинет настроен»
/comments (1)" $F open реклама
lacks "…P1 буквами больше нет" "· P1" $F view
lacks "…кнопки идей нет" "/ideas" $F view
check "/x1 — выполнено" "✓ Выполнено: Проверить пиксель" $F hide 1
check "…в карточке ссылка /done (N)" "/done (1)" $F view
lacks "…сам список свёрнут" "Проверить пиксель" $F view
check "/done — раскрыт список выполненных" "Выполнено (1):" $F nav card done
check "…с ✓ впереди" "✓ Проверить пиксель" $F nav card done
lacks "…ссылки /done на раскрытой нет" "/done (" $F nav card done
check "…через /new (go + view)" "✓ Проверить пиксель" bash -c "$F go card done; $F view"
lacks "…разовый экран: следующий /new — свёрнуто" "Проверить пиксель" $F view
lacks "…а самой задачи нет" "Проверить пиксель" $F view

echo "▸ новая точка и листание"
export FLOWS_NOW=2026-10-22T18:00
check "«начинаем новую точку»" "✓ Точка 20.10.2026 завершена. Перенёс задач: 1." $F new-point рекламе
check "…указатель на новой" "Точка 2 из 2 · 22.10.2026" $F view
check "…невыполненная переехала" "– Сделать статичные креативы  /x1" $F view
lacks "…выполненной в новой нет" "Проверить пиксель" cat "$T/brain/flows/reklama/2026-10-22.md"
check "…выполненная осталась в старой с ✓" "✓ Проверить пиксель" cat "$T/brain/flows/reklama/2026-10-20.md"
lacks "…невыполненной в старой нет" "Сделать статичные" cat "$T/brain/flows/reklama/2026-10-20.md"
check "…у старой — дата завершения" "closed: 2026-10-22" cat "$T/brain/flows/reklama/2026-10-20.md"
check "кнопка назад" "◀ /prev" $F view
check "/prev → прошлая точка" "📍 Реклама · 🔴
Точка 1 из 2 · 20.10.2026" $F nav prev
lacks "…у завершённой нет ⚡" "⚡" $F view
lacks "…только дата создания" "22.10.2026" $F view
check "…сделанная с галочкой" "✓ Проверить пиксель" $F view
lacks "…без кнопок скрытия" "/x1" $F view
check "…вперёд можно" "/next ▶" $F view
check "/comments — 💬 первым символом шапки" "💬 Реклама · 🔴
Точка 1 из 2 · 20.10.2026
«Кабинет настроен»
Комментарии

– Пиксель — до запуска" $F card комментарии
check "…внизу листание и нижний ряд" "/next ▶

/conclusions   /calendar   /list   /point" $F card комментарии
check "/next из комментариев — комментарии следующей точки" "💬 Реклама · 🔴
Точка 2 из 2 · 22.10.2026
Комментарии

комментариев пока нет" $F nav next
check "/prev — снова комментарии прошлой" "– Пиксель — до запуска" $F nav prev
check "/new после комментариев — точка под указателем" "Точка 1 из 2 · 20.10.2026
«Кабинет настроен»
/comments (1)" $F view
check "старое «card выводы» → комментарии" "💬 Реклама" $F card выводы
check "нижний ряд" "/conclusions   /calendar   /list   /point" $F view
lacks "…без /new" "/new" $F view
empty "обычный /new (из меню) — тишина" $F view --if-asked
check "…после кнопки (go) — экран" "Задачи" bash -c "$F go point; $F view --if-asked"
empty "…пометка разовая: следующий /new снова молчит" $F view --if-asked
empty "…устаревшая пометка (> 2 мин) — тишина" bash -c "FLOWS_NOW=2026-10-20T10:00 $F go point; $F view --if-asked"
check "/point из комментариев — та же точка, карточка" "Задачи" $F nav point
check "забытая пометка комментариев: /point всё равно задачи" "Задачи" bash -c "$F go card комментарии; $F go point; $F view"
check "…и запись снимает пометку: /new — карточка" "Задачи" bash -c "$F go card комментарии; $F add . комментарии 'по ходу'; $F view"
check "/prev — старая точка" "Точка 1 из 2" $F nav prev
check "/point с её карточки — последняя точка потока" "Точка 2 из 2" $F nav point
check "/prev, комментарии, /point — та же старая точка" "Точка 1 из 2" bash -c "$F nav prev >/dev/null; $F nav card комментарии >/dev/null; $F nav point"
check "…ещё раз /point — последняя" "Точка 2 из 2" $F nav point
check "через /new (go + view) — так же" "Точка 2 из 2" bash -c "$F nav prev >/dev/null; $F go point; $F view >/dev/null; $F go point; $F view"
$F nav prev >/dev/null                 # дальше проверки ждут указатель на 1-й точке
check "/list снимает указатель → /new показывает список" "ПОТОКИ" bash -c "$F list >/dev/null; $F view"
check "…и снова список (указателя нет)" "ПОТОКИ" $F view
check "/point после /list — последняя точка" "Точка 1 из 2" $F nav point
check "…указатель снова на ней → /new — она же" "Точка 1 из 2" $F view
check "/list3 через go — один раз список 3-го приоритета" "🟢 ПОТОКИ 3-ГО ПРИОРИТЕТА" bash -c "$F go list3; $F view"
lacks "…следующий /new — обычный список" "3-ГО" $F view
check "кнопка /1 через go — точка" "⚡" bash -c "$F list >/dev/null; $F go open 1; $F view"
check "запись, пока смотрю историю, — в открытую" "22.10 «(без заголовка)» · Задачи +1" $F add . задачи "Заказать ролик"
check "/next → текущая" "Точка 2 из 2 · 22.10.2026" $F nav next
empty "go не печатает" $F go prev
check "view после go" "Точка 1 из 2" $F view
check "go open" "Точка 2 из 2" bash -c "$F go open реклама; $F view"
check "show: прошлая точка" "Прошлая точка 20.10 «Кабинет настроен»" $F show таргет
check "show: задачи на сейчас" "Сделать статичные креативы" $F show таргет
check "задача в другой поток" "Задачи +1" $F add звонки задачи "Написать скрипт"
check "глобальный вывод" "Глобальные выводы +1" $F add-global реклама выводы "С лицом лучше" --from 2026-10-20
check "глобальные выводы — отдельный файл" "С лицом лучше" cat "$T/brain/flows/reklama/_выводы.md"
check "общих идей потока нет" "Идея — комментарий с 💡" $F add-global реклама идеи "Отзывы в рекламе"
check "выжимка точки" "кратко — Кабинет и пиксель" $F summary реклама "Кабинет и пиксель" --point 2026-10-20
check "оглавление потока: выжимка" "Кабинет настроен]] — Кабинет и пиксель" cat "$T/brain/flows/reklama/_поток.md"
check "«покажи выводы» — весь список потока" "🧠 Выводы потока «Реклама» (2)" $F collect реклама выводы
check "…с выводом из записи" "– Пиксель ставить до запуска" $F collect реклама выводы
check "collect комментарии — по точкам" "Пиксель — до запуска" $F collect реклама комментарии
check "поиск по тегу" "#продажи" $F find --tag продажи
check "status: на чём остановились" "Остановились: 20.10 «Кабинет настроен»" $F status
check "status: что предстоит" "– Сделать статичные креативы" $F status
check "неизвестный поток" "не найден" $F show блабла
check "оглавление" "Кабинет настроен" cat "$T/brain/ПОТОКИ.md"

echo "▸ напоминания"
check "событие потока за день и за 30 мин" "(⏰ 26.10 19:00, 27.10 18:30)" $F event "Запуск рекламы" --when "2026-10-27 19:00" --flow реклама --remind "1d,30m"
check "календарь: поток и когда напомнит" "1. вт 27.10 19:00 — Запуск рекламы · Реклама  /x1
   ⏰ 26.10 19:00, 27.10 18:30" $F calendar
lacks "времени захода в поток больше нет" "plan" $F --help
lacks "…и в карточке" "Следующий заход" $F open реклама
empty "до времени — тишина" at 2026-10-26T18:00 $F remind
check "за день: название — первая строка" "Запуск рекламы — завтра в 19:00" bash -c "FLOWS_NOW=2026-10-26T19:00 $F remind | head -1"
check "в день события — поток, без «Начать с»" "Запуск рекламы — сегодня в 19:00
⏰ Реклама
↩️" at 2026-10-27T18:30 $F remind
empty "без повторов" at 2026-10-27T18:31 $F remind
check "отложить" "напомню снова в 18:47" at 2026-10-27T18:32 $F move --snooze 15m
check "отложенное пришло" "Запуск рекламы — сегодня в 19:00" at 2026-10-27T18:47 $F remind
check "перенос со сдвигом напоминаний" "напомню 28.10 20:00, 29.10 19:30" at 2026-10-27T18:50 $F move реклама --when "2026-10-29 20:00"
lacks "прошедшее событие закрылось само" "Запуск рекламы" bash -c "FLOWS_NOW=2026-10-30T00:30 $F remind >/dev/null; FLOWS_NOW=2026-10-30T00:31 $F calendar"
export FLOWS_NOW=2026-10-29T19:00
check "show: текущая точка" "Текущая точка с 22.10.2026" $F show реклама
check "путь потока" "2. 22.10" $F show реклама
check "заголовок точки" "Видео запущено" $F title . "Видео запущено"
check "продолжаем: точка уже открыта" "работаем в точке с 22.10.2026" $F continue реклама
check "завершить" "завершена" $F close-point реклама
check "задача — в новую точку" "29.10 «(без заголовка)» · Задачи +1" $F add реклама задачи "Смонтировать видео"
check "продолжаем" "работаем в точке с 29.10.2026" at 2026-10-30T09:00 $F continue рекламе
check "…сводка: задачи на сейчас" "Смонтировать видео" at 2026-10-30T09:05 $F show реклама
check "…указатель переключён" "Реклама" at 2026-10-30T09:06 $F current
check "поток 3-го приоритета" "создан" at 2026-10-30T09:07 $F new-flow "Гитара" --priority 3
check "/list: P1 сверху, с номером" "🔴 Приоритет 1
/1 Реклама" at 2026-10-30T09:07 $F list
check "/list: P2 ниже" "/2 Продающие звонки" at 2026-10-30T09:07 $F list
lacks "/list: без задач" "Смонтировать" at 2026-10-30T09:07 $F list
lacks "/list: без 3-го приоритета" "Гитара" at 2026-10-30T09:07 $F list
check "/list: ссылка на 3-й приоритет" "🟢 Потоки третьего приоритета (1): /list3" at 2026-10-30T09:07 $F list
check "номер из каталога" "Продающие звонки" at 2026-10-30T09:07 $F show 2
check "/2 → карточка потока" "⚡ Продающие звонки" at 2026-10-30T09:07 $F open 2
check "…приоритет кружком справа: P2 — 🟡" "⚡ Продающие звонки · 🟡" at 2026-10-30T09:07 $F open 2
check "/list: 🟡 перед заголовком приоритета" "🟡 Приоритет 2" at 2026-10-30T09:07 $F list
check "P3 в потоке — 🟢 справа" "⚡ Гитара · 🟢" at 2026-10-30T09:07 $F open гитара
check "…с задачами" "– Написать скрипт" at 2026-10-30T09:07 $F open 2
check "…указатель на нём" "Продающие звонки" at 2026-10-30T09:07 $F current
check "/1 → Реклама" "⚡ Реклама" at 2026-10-30T09:07 $F open 1
check "второй поток P1 (точка открыта 28.10)" "создан" at 2026-10-28T09:00 $F new-flow "Сайт" --priority 1
check "/list: где новую точку не открывали дольше — сверху" "/1 Сайт — 28.10
/2 Реклама" at 2026-10-30T09:07 $F list
check "запись в Сайт" "+1" at 2026-10-30T09:07 $F add сайт задачи "тест порядка"
check "/list: запись порядок не меняет" "/1 Сайт
/2 Реклама" at 2026-10-30T09:07 $F list
check "/1 — тот же поток, что в последнем каталоге" "⚡ Сайт" at 2026-10-30T09:07 $F open 1
check "новая точка в Сайте" "завершена" at 2026-10-30T09:07 $F new-point сайт
check "/list: открыл новую точку — в конец своего приоритета, дата — текущей точки" "/1 Реклама
/2 Сайт — 30.10" at 2026-10-30T09:07 $F list
check "убрать тестовый поток" "удалён" at 2026-10-30T09:07 $F delete-flow сайт --force
check "/list3: только 3-й приоритет" "/1 Гитара" at 2026-10-30T09:07 $F list --low
lacks "/list3: без 1-го" "Реклама" at 2026-10-30T09:07 $F list --low
check "номер из /list3" "Гитара" at 2026-10-30T09:07 $F show 1
check "номера нет" "нажми /list" at 2026-10-30T09:07 $F show 7
check "/list: каталог" "ПОТОКИ" at 2026-10-30T09:07 $F list
check "/list сбрасывает указатель" "Текущий поток не задан" at 2026-10-30T09:08 $F current
check "…после /list запись в «.» просит назвать поток" "Текущий поток не задан" at 2026-10-30T09:09 $F add . идеи "тест"
check "…и снова продолжаем" "работаем в точке с 29.10.2026" at 2026-10-30T09:10 $F continue реклама

echo "▸ календарь"
export FLOWS_NOW=2026-10-30T10:00
check "событие с напоминанием" "В календаре: «Пойти к стоматологу»" $F event "Пойти к стоматологу" --when "2026-10-31 15:00" --remind 1h --note "Thames 1234"
check "событие без напоминания" "без напоминания" $F event "Позвонить в банк" --when "2026-11-03 10:00"
check "старое имя todo работает" "В календаре" $F todo "Купить корм" --when "2026-11-04" --remind 0
check "событие без даты" "«Купить зимнюю резину» — без даты" $F event "Купить зимнюю резину"
lacks "…про напоминание не спрашиваем" "напоминания" $F event "Съездить на дачу"
check "напоминание без даты нельзя" "нужна --when" $F event "Что-то" --remind 1h
check "/calendar: с датой по времени, ⏰ — когда напомнит" "🗓 Календарь

1. сб 31.10 15:00 — Пойти к стоматологу  /x1
   ⏰ 31.10 14:00
2. вт 03.11 10:00 — Позвонить в банк  /x2
3. ср 04.11 — Купить корм  /x3
   ⏰ 04.11 09:00

📌 Без даты
4. Купить зимнюю резину  /x4
5. Съездить на дачу  /x5

/conclusions   /calendar   /list   /point" $F nav calendar
check "кнопка через /new: go + view" "🗓 Календарь" bash -c "$F go calendar; $F view"
lacks "…разовый экран: следующий /new — не календарь" "🗓" $F view
check "/x4 убирает событие" "✓ Убрал: Купить зимнюю резину" bash -c "$F nav calendar >/dev/null; $F hide 4"
lacks "…его нет в календаре" "резину" $F calendar
check "…закрыто, не удалено" "status: done" bash -c "cat $T/brain/календарь/без-даты-*rezin*.md"
check "вернуть убранное" "снова в календаре" $F move резину --restore
check "…снова в списке" "Купить зимнюю резину" $F calendar
check "дату задать потом" "перенесено" $F move резину --when "2026-11-07"
check "agenda = календарь" "Пойти к стоматологу" $F agenda
check "текст: сначала дело" "Пойти к стоматологу — сегодня в 15:00" at 2026-10-31T14:00 $F remind
check "детали в напоминании" "Thames 1234" bash -c "cat $T/brain/календарь/2026-10-31-*.md"
lacks "прошедшее закрылось само" "стоматолог" bash -c "FLOWS_NOW=2026-10-31T19:00 $F remind >/dev/null; FLOWS_NOW=2026-10-31T19:00 $F agenda"
check "отмена по названию" "убрано из календаря" at 2026-10-31T19:05 $F move банк --cancel
check "в 09:00 для дня без времени" "Купить корм — сегодня" at 2026-11-04T09:00 $F remind
check "КАЛЕНДАРЬ.md" "Пойти к стоматологу" cat "$T/brain/календарь/КАЛЕНДАРЬ.md"

echo "▸ голосовые (имитация Whisper из venv)"
mkdir -p "$T/venv/bin" "$T/mods"
printf '#!/bin/sh\nPYTHONPATH=%s exec python3 "$@"\n' "$T/mods" > "$T/venv/bin/python"; chmod +x "$T/venv/bin/python"
cat > "$T/mods/whisper.py" <<'PY'
def load_audio(p): return [0] * (16000 * 75)
class _M:
    def transcribe(self, a, language=None, fp16=True, verbose=True):
        return {"segments": [{"start": 0.0, "end": 5.0, "text": " Сделал видеокреативы."},
                             {"start": 8.0, "end": 75.0, "text": " Вывод: вертикальное лучше."}]}
def load_model(n): return _M()
PY
printf 'OggS' > "$T/rec.ogg"
check "длинная голосовая сохраняется" "[voice:2026-11-01-100000" bash -c "FLOWS_NOW=2026-11-01T10:00 python3 $H/flows_stt.py $T/rec.ogg $T/out.txt && cat $T/out.txt"
lacks "без своего значка микрофона" "🎤" cat "$T/out.txt"
check "модель в конце расшифровки" "— Whisper small · " tail -1 "$T/out.txt"
check "inbox видит голосовую" "Сделал видеокреативы" $F inbox
check "привязка к потоку" "привязана: Реклама" at 2026-11-01T10:01 $F attach last реклама

echo "▸ голосовые через GigaAM (имитация: свой venv, onnx int8, нарезка по 24 с)"
mkdir -p "$T/giga/bin" "$T/gmods"
printf '#!/bin/sh\nPYTHONPATH=%s exec python3 "$@"\n' "$T/gmods" > "$T/giga/bin/python"; chmod +x "$T/giga/bin/python"
printf '#!/bin/sh\nhead -c 2080000 /dev/zero\n' > "$T/giga/bin/ffmpeg"; chmod +x "$T/giga/bin/ffmpeg"
cat > "$T/gmods/onnx_asr.py" <<'PY'
import wave
class _M:
    def recognize(self, p):
        with wave.open(p) as w: n = w.getnframes()
        assert n <= 25 * 16000, "кусок длиннее 25 с"
        return f"Кусок {n // 16000} с."
def load_model(name, quantization=None):
    assert quantization == "int8"
    return _M()
PY
python3 - "$T/config.json" "$T/giga/bin/python" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); c["stt_engines"] = ["gigaam:gigaam-v3-e2e-ctc", "openai-whisper:small"]
c["stt_engine_python"] = {"gigaam": sys.argv[2]}; json.dump(c, open(sys.argv[1], "w"))
PY
check "gigaam режет 65 с на куски" "Кусок" bash -c "PATH=$T/giga/bin:\$PATH FLOWS_NOW=2026-11-01T11:00 python3 $H/flows_stt.py $T/rec.ogg $T/out2.txt && cat $T/out2.txt"
check "модель gigaam в конце" "— GigaAM gigaam-v3-e2e-ctc · " tail -1 "$T/out2.txt"
check "расшифровано gigaam" "stt: gigaam:gigaam-v3-e2e-ctc" bash -c "cat $T/brain/_inbox/voice/2026-11-01-110000*.md"

echo "▸ удаление потока"
export FLOWS_NOW=2026-11-01T11:30
check "поток на удаление" "создан (udalyaemyy)" $F new-flow "Удаляемый" --priority 3
check "событие по теме потока" "В календаре" $F event "Созвон с подрядчиком" --when "2026-11-04 12:00" --flow удаляемый
check "без --force не удаляет" "Повтори с --force" $F delete-flow удаляемый
check "поток ещё на месте" "Удаляемый" $F status --all
check "удаление с --force" "удалён безвозвратно" $F delete-flow удаляемый --force
lacks "потока больше нет" "Удаляемый" $F status --all
check "указатель сброшен" "Текущий поток не задан" $F current
check "событие по теме потока осталось" "Созвон с подрядчиком" $F agenda --days 30

echo "▸ короткие заголовки задач"
export FLOWS_NOW=2026-11-01T11:40
check "поток для заголовков" "создан" $F new-flow "Запуск курса" --priority 2
check "задача с заголовком" "Задачи +1" $F add запуск задачи "Сделать рассылку по тем, кто создал счёт и не оплатил. Касается всех." --short "Рассылка неоплатившим"
check "задача без заголовка" "Задачи +1" $F add запуск задачи "Сверить финальное количество участников в канале со списком купивших, это делает Оля, потом сообщить мне"
check "в списке — заголовок" "– Рассылка неоплатившим" $F status
lacks "полного текста в списке нет" "Касается всех" $F status
check "без заголовка — обрезано" "– Сверить финальное количество участников в канале со списком…" $F status
check "в show — полный текст" "Рассылка неоплатившим.** Сделать рассылку" $F show запуск
check "заголовок задним числом" "«Сверка участников с Олей»" $F brief запуск "сверить финальное" "Сверка участников с Олей"
check "теперь в списке он" "– Сверка участников с Олей" $F status
check "заголовок можно поменять" "«Сверка с Олей»" $F brief запуск "сверить финальное" "Сверка с Олей"
lacks "старого заголовка нет" "Сверка участников" $F show запуск
check "кусок не найден" "нет записи" $F brief запуск "чего-то нет" "Х"
check "карточка: кнопки скрытия" "– Рассылка неоплатившим  /x1" $F open запуск
check "вторая задача — /x2" "– Сверка с Олей  /x2" $F open запуск
check "/x1 скрывает" "✓ Выполнено: Рассылка неоплатившим" $F hide 1
lacks "…в карточке её нет" "Рассылка неоплатившим" $F open запуск
check "…счётчик выполненных" "/done (1)" $F open запуск
check "/done: открытые задачи с /xK на месте" "– Сверка с Олей  /x1" $F nav card done
check "…/x на раскрытой — остаётся раскрытой" "✓ Сверка с Олей" $F hide 1
check "…/point с раскрытой — та же точка, свёрнуто" "/done (2)" $F nav point
check "…вернул задачу" "Сверка с Олей" $F unhide запуск "Сверка с Олей"
check "…номера пересчитаны" "– Сверка с Олей  /x1" $F open запуск
lacks "…и в списке потоков нет" "Рассылка неоплатившим" $F status
check "…но в файле осталась" "✓ **Рассылка неоплатившим.**" bash -c "cat $T/brain/flows/zapusk-kursa/2026-11-01.md"
check "пункта нет" "нет пункта 9" $F hide 9
check "вернуть" "✓ Вернул: Рассылка неоплатившим" $F unhide запуск "рассылк"
check "…снова видна" "Рассылка неоплатившим" $F open запуск

echo "▸ идеи в комментариях, /conclusions"
check "идея в поток — комментарий с 💡" "Комментарии 💡 +1" $F add запуск идеи "Сделать вебинар для неоплативших" --short "Вебинар"
check "…в файле — заголовок с лампочкой" "**💡 Вебинар.** Сделать вебинар" bash -c "cat $T/brain/flows/zapusk-kursa/*.md"
check "общей идеи нет" "Идея — комментарий с 💡" $F add-global запуск идеи "Партнёрка с блогерами"
check "глобальный вывод" "+1" $F add-global запуск выводы "Рассылки работают лучше звонков"
check "Запуск курса — последний (точка открыта позже всех)" "/3 Запуск курса" $F list
lacks "в карточке нет /ideas" "/ideas" $F open запуск
lacks "…и идей под задачами" "Вебинар" $F open запуск
check "/comments — идея с лампочкой" "Комментарии

– 💡 Вебинар" $F card комментарии
check "старая кнопка /ideas → комментарии" "💬 Запуск курса" $F card идеи
check "collect идеи — только с 💡" "**💡 Вебинар.**" $F collect запуск идеи
check "/conclusions — выводы потока под указателем" "🧠 Выводы · Запуск курса (1)

– Рассылки работают лучше звонков (01.11)

/conclusions   /calendar   /list   /point" $F nav conclusions
check "…через /new (go + view)" "🧠 Выводы · Запуск курса" bash -c "$F go conclusions; $F view"
lacks "…разовый экран: следующий /new — карточка" "🧠" $F view
check "/conclusions из каталога — поток последней точки" "🧠 Выводы · Запуск курса" bash -c "$F list >/dev/null; $F nav conclusions"
check "/point после выводов — карточка точки" "⚡ Запуск курса" $F nav point
check "выводов нет — подсказка" "выводов пока нет" bash -c "$F nav open звонки >/dev/null; $F nav conclusions"
$F nav open запуск >/dev/null
check "выводы потока — по просьбе" "– Рассылки работают лучше звонков" $F collect запуск выводы

echo "▸ кнопки через /new: хук flows-nav"
mkdir -p "$T/hh/scripts"; ln -s "$H/flows.py" "$T/hh/scripts/flows.py"
printf 'TELEGRAM_BOT_TOKEN=test-token\n' > "$T/hh/.env"
HOOK() { HERMES_HOME="$T/hh" python3 - "$ROOT/hermes-hooks/flows-nav/handler.py" "$@" <<'PY'
import asyncio, importlib.util, sys
spec = importlib.util.spec_from_file_location("h", sys.argv[1]); m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m._send = lambda tok, chat, text: print(f"SEND to={chat} tok={tok}\n{text}")   # без сети
ev = sys.argv[2]
ctx = {"args": sys.argv[3]} if ev == "command:new" else {"platform": sys.argv[3], "user_id": "42"}
print(asyncio.run(m.handle(ev, ctx)))
PY
}
check "обычный /new хук не трогает" "None" HOOK command:new ""
check "/list → убрать аргументы у /new" "'decision': 'rewrite', 'command_name': 'new', 'raw_args': ''" HOOK command:new "flows list"
check "…после /new в Telegram — каталог" "SEND to=42 tok=test-token
ПОТОКИ" HOOK session:reset telegram
check "кнопка /2 через /new" "rewrite" HOOK command:new "flows open 2"
check "…после /new — карточка точки" "⚡" HOOK session:reset telegram
lacks "не Telegram — ничего не шлём" "SEND" HOOK session:reset cli
lacks "обычный /new из меню — в Telegram ничего" "SEND" HOOK session:reset telegram

echo "▸ setup-commands.sh: оба режима"
printf 'model: x\nquick_commands:\n  "mine":\n    type: exec\n    command: "echo hi"\n' > "$T/hh/config.yaml"
check "без свежего Hermes — режим exec" "без /new" env HERMES_HOME="$T/hh" bash "$ROOT/setup-commands.sh"
check "…кнопка листания" 'command: "python3 '"$T"'/hh/scripts/flows.py nav prev"' cat "$T/hh/config.yaml"
check "…чужая команда цела" '"mine":' cat "$T/hh/config.yaml"
mkdir -p "$T/fakeha/hermes_cli"
printf 'CMDS = [\n    CommandDef("new", "x", "S",\n               aliases=("reset",)),\n    CommandDef("stats", "y", "I",\n               aliases=("conclusions",)),\n]\n' > "$T/fakeha/hermes_cli/commands.py"
check "имя кнопки = встроенная команда Hermes (и синоним) — предупреждение" "⚠ Кнопка /conclusions совпадает со встроенной командой Hermes" env HERMES_HOME="$T/hh" HERMES_AGENT_DIR="$T/fakeha" bash "$ROOT/setup-commands.sh"
echo '{"nav_mode": "new"}' > "$T/nav.json"
check "nav_mode new — через /new" "через /new" env HERMES_HOME="$T/hh" FLOWS_CONFIG="$T/nav.json" bash "$ROOT/setup-commands.sh"
check "…/prev → alias" 'target: "/new flows prev"' cat "$T/hh/config.yaml"
check "hermes не вызывается" "0" bash -c "cat $T/hermes-calls 2>/dev/null | wc -l | tr -d ' '"
check "…/x1 остаётся exec" 'command: "python3 '"$T"'/hh/scripts/flows.py hide 1"' cat "$T/hh/config.yaml"
printf '  "tochka":\n    type: alias\n    target: "/new flows point"\n  "conclusions":\n    type: alias\n    target: "/new flows card выводы"\n  "insights":\n    type: alias\n    target: "/new flows insights"\n' >> "$T/hh/config.yaml"
check "старая /tochka убирается" "Кнопки:" env HERMES_HOME="$T/hh" FLOWS_CONFIG="$T/nav.json" bash "$ROOT/setup-commands.sh"
lacks "…её нет" '"tochka"' cat "$T/hh/config.yaml"
check "…/point есть" 'target: "/new flows point"' cat "$T/hh/config.yaml"
check "…/comments есть" 'target: "/new flows card комментарии"' cat "$T/hh/config.yaml"
check "…/calendar есть" 'target: "/new flows calendar"' cat "$T/hh/config.yaml"
check "…/conclusions — выводы потока" 'target: "/new flows conclusions"' cat "$T/hh/config.yaml"
check "…/done — выполненные задачи" 'target: "/new flows card done"' cat "$T/hh/config.yaml"
lacks "…/insights (встроенная Hermes) убрана" '"insights"' cat "$T/hh/config.yaml"
check "повтор — без изменений" "уже на месте" env HERMES_HOME="$T/hh" FLOWS_CONFIG="$T/nav.json" bash "$ROOT/setup-commands.sh"
mkdir -p "$T/hh/hooks/flows-nav" "$T/hh/hermes-agent/gateway"
touch "$T/hh/hooks/flows-nav/handler.py"
echo '{"nav_mode": "auto"}' > "$T/auto.json"
check "auto: старый Hermes — exec" "без /new" env HERMES_HOME="$T/hh" FLOWS_CONFIG="$T/auto.json" bash "$ROOT/setup-commands.sh"
echo 'x = "rewrite"' > "$T/hh/hermes-agent/gateway/run_inbound.py"
echo 'emit("session:reset")' > "$T/hh/hermes-agent/gateway/slash_commands_session.py"
check "auto: свежий Hermes (по исходникам) — new" "через /new" env HERMES_HOME="$T/hh" FLOWS_CONFIG="$T/auto.json" bash "$ROOT/setup-commands.sh"

echo "▸ миграция со «следующей точки»"
export FLOWS_NOW=2026-11-02T10:00
NEXT() { printf -- '---\ntype: point\nflow: %s\nstatus: next\ncreated: 2026-11-01\ntitle: ""\n---\n\n# Следующая точка\n\n## Задачи\n\n- 2026-11-01: %s\n' "$1" "$2" > "$T/brain/flows/$1/_следующая.md"; }
check "поток A" "создан" $F new-flow "Старый А" --slug old-a
check "поток B" "создан" $F new-flow "Старый Б" --slug old-b
sed -i.bak 's/^status: open/status: closed/' "$T/brain/flows/old-a/2026-11-02.md"; rm -f "$T/brain/flows/old-a/"*.bak
NEXT old-a "Задача из следующей А"; NEXT old-b "Задача из следующей Б"
check "migrate" "следующая влита в открытую" $F migrate
check "…A: следующая стала открытой" "Задача из следующей А" $F open old-a
check "…B: влита в открытую" "Задача из следующей Б" $F open old-b
check "…файлов «следующая» нет" "0" bash -c "ls $T/brain/flows/*/_следующая.md 2>/dev/null | wc -l | tr -d ' '"
check "повтор — не нужна" "Миграция не нужна" $F migrate
sed -i.bak 's/^## Комментарии$/## Выводы\n\n- 2026-11-02: Старый вывод точки/' "$T/brain/flows/old-b/2026-11-02.md"; rm -f "$T/brain/flows/old-b/"*.bak
check "migrate: «Выводы» точки → «Комментарии»" "→ «Комментарии»: 1" $F migrate
check "…запись теперь комментарий" "– Старый вывод точки" $F card комментарии
lacks "…старого заголовка нет" "## Выводы" cat "$T/brain/flows/old-b/2026-11-02.md"
check "…повтор — не нужна" "Миграция не нужна" $F migrate
printf '\n## Идеи\n\n- 2026-11-02: **Старая идея.** длинный текст\n- 2026-11-02: Идея без заголовка\n' >> "$T/brain/flows/old-b/2026-11-02.md"
check "migrate: «Идеи» → комментарии с 💡" "идей перенесено в комментарии: 2" $F migrate
check "…идеи среди комментариев" "– 💡 Старая идея
– 💡 Идея без заголовка" $F card комментарии
lacks "…раздела «Идеи» нет" "## Идеи" cat "$T/brain/flows/old-b/2026-11-02.md"
check "…повтор — не нужна" "Миграция не нужна" $F migrate

echo "▸ копилка"
export FLOWS_NOW=2026-11-01T12:00
check "сохранить" "Сохранил в копилку" $K save "Стоматолог Гомес, 11 5555 1234" --title "Стоматолог — доктор Гомес" --tags "стоматолог,медицина,Москва" --field "Телефон=+54 9 11 5555-1234"
check "второй врач" "Сохранил" at 2026-11-01T12:01 $K save "ЛОР Мартинес" --title "ЛОР — доктор Мартинес" --tags "лор,медицина,буэнос-айрес"
check "похожий тег замечен" "похожие уже есть: #стоматолог" at 2026-11-01T12:02 $K save "Клиника" --title "Клиника зубов" --tags "стоматология,медицина"
check "все врачи по категории" "ЛОР — доктор Мартинес" $K find --tag медицина --tag буэнос-айрес
check "любой из тегов" "Стоматолог — доктор Гомес" $K find --tag лор --tag стоматолог --any
check "по тексту" "Стоматолог — доктор Гомес" $K find "гомес"
check "список тегов" "медицина 3" $K tags
check "слить теги" "#стоматология → #стоматолог" $K rename-tag стоматология стоматолог
check "картотека" "#стоматолог" cat "$T/brain/копилка/КОПИЛКА.md"

echo "▸ навык и лишнее"
check "утренней сводки нет" "invalid choice" $F digest
check "без указателя — подсказка спросить поток" "Спроси хозяина" bash -c "$F list >/dev/null; $F add . задачи 'x'"
for r in voice calendar more; do
  check "справка навыка: $r.md упомянута и есть" "references/$r.md" grep -o "references/$r.md" "$ROOT/skill/flows/SKILL.md"
  [ -f "$ROOT/skill/flows/references/$r.md" ] || { echo "  ✗ нет references/$r.md"; FAIL=$((FAIL+1)); }
done
check "кнопка /calendar в навыке и SOUL" "nav calendar" cat "$ROOT/skill/flows/SKILL.md" "$ROOT/SOUL.md"

echo "▸ git"
check "изменения закоммичены" "Копилка" git -C "$T/brain" log --oneline -3

echo
echo "Итог: ✓ $PASS  ✗ $FAIL"
[ "$FAIL" -eq 0 ]
