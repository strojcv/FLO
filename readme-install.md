# Установка и обновление — шпаргалка

## Быстрая установка

Нужно: сервер с Hermes, бот в Telegram отвечает, в чате с ботом дана команда `/sethome`;
на сервере `python3` (3.9+), `git`, `ffmpeg`. Всё — под пользователем, от которого работает Hermes:

```bash
git clone https://github.com/strojcv/FLO.git ~/flows-organizer
bash ~/flows-organizer/install.sh          # тесты, установка; на «Сделать? [Д/н]» — да
bash ~/flows-organizer/setup-gigaam.sh     # расшифровка голосовых (GigaAM, ~225 МБ)
hermes gateway restart
```

В Telegram — `/new`, затем `/list`. Обновление: `cd ~/flows-organizer && git pull && bash install.sh`,
затем `/restart`. Ниже — то же подробно и вариант без git (FTP).

---

Ниже `<user>` — пользователь, от которого работает Hermes (его папка — `/home/<user>`).
Если заливаешь по FTP под `root`, файлы будут принадлежать `root`, и `<user>` не сможет их менять — тогда нужен шаг 2.
Через `git clone` или `rsync` под самим `<user>` шаги 1–2 не нужны.

## 1. Залить папку

Папку целиком — в `/home/<user>/flows-organizer` (по FTP — с заменой, двоичный режим), или
`git clone https://github.com/strojcv/FLO.git ~/flows-organizer` под `<user>`.

## 2. Отдать папку пользователю `<user>` (под root)

Зайти по SSH под `root` и выполнить:

```bash
chown -R <user>:<user> /home/<user>/flows-organizer
ls -la /home/<user>/flows-organizer | head -3      # должно быть: <user> <user>
```

Под `root` пути пишем полностью: `~` у root — это `/root`, а не `/home/<user>`.

## 3. Запустить установщик (под <user>!)

```bash
su - <user>                                        # из root переключиться на <user>
bash ~/flows-organizer/install.sh
```

Под `root` установщик не запускать: он поставит всё в `/root/...`, и Hermes этого не увидит.

Установщик сначала гоняет тесты; если они не прошли, ничего не ставит. Данные в `~/brain`
и настройки не трогает. Если в Hermes не включены голосовые через органайзер, `/new` без «точно?»
или напоминания — покажет и спросит «Сделать? [Д/н]» (копия `config.yaml` — `.bak-flows-<дата>`).
Строки с ⚠ в конце — что осталось сделать.

## 4. Перезапустить Hermes

В Telegram: `/restart`, затем `/new`.

---

## 4а. Кнопки /list, /1…/30, /prev, /next, /comments, /ideas, /x…

Отдельно запускать не нужно: `install.sh` сам кладёт хук `~/.hermes/hooks/flows-nav` и заводит
кнопки в `config.yaml` (строка «✓ Кнопки…»); после этого нужен `/restart`. В строке видно режим: «через /new, контекст чистится» —
основной; «без /new» — Hermes старый, кнопки работают, но контекст не чистят.

`/new` по умолчанию спрашивает «точно?» — тогда кнопки срабатывают со второго нажатия. Установщик
предлагает это выключить (подтверждение пропадёт и у других «опасных» команд Hermes). Вручную (`bash ~/flows-organizer/setup-commands.sh`) — только если
установщик написал ⚠ про кнопки или поменял `list_cmd_prefix`.

## 5. Модель для голосовых GigaAM (один раз)

Русская модель Сбера GigaAM v3 (~225 МБ, быстрая, с пунктуацией). Ставится после шага 3, под `<user>`:

```bash
bash ~/flows-organizer/setup-gigaam.sh
```

Скрипт сам: ставит её в отдельную папку `~/.venvs/gigaam`, скачивает модель и делает её первой
для голосовых (Whisper остаётся запасным). Перезапуск Hermes не нужен.
Проверка: отправить голосовое на 1–2 минуты; в расшифровке (`~/brain/_inbox/voice/`) строка
`stt: gigaam:…` — значит, сработал GigaAM.

- `нужен Python 3.10+` или `Не создался venv` → под root: `apt install python3-venv`, повторить.
- `Нет ffmpeg` → под root: `apt install ffmpeg`, повторить.
- Вернуть Whisper: убрать строку `"gigaam:…"` из `stt_engines` в `~/.config/flows/config.json`.

При обновлениях этот шаг повторять не нужно.

---

## Новый сервер или перенос

Всё нужное лежит в этой папке. Ваши данные (`~/brain`) и настройки (`~/.config/flows`) — нет, их
переносят отдельно.

**Заранее:** Hermes установлен, бот в Telegram отвечает, в чате с ботом дана команда `/sethome`
(без неё напоминаниям некуда слать). На сервере есть `python3` (3.9+), `git`, `ffmpeg`.
Всё ниже — под пользователем, от которого работает Hermes.

1. Залить папку `flows-organizer` в домашнюю папку (шаги 1–2 выше или `rsync`).
2. **Только при переносе** — со старого сервера скопировать данные и настройки органайзера:
   ```bash
   rsync -a старый:brain/ ~/brain/
   rsync -a старый:.config/flows/ ~/.config/flows/
   ```
   Если на новом сервере другое имя пользователя — поправить пути в `~/.config/flows/config.json`
   (`brain`, `stt_venv_python`, `stt_engine_python`).
3. `bash ~/flows-organizer/install.sh` — ставит всё своё, заводит кнопки; на вопрос «Сделать?»
   ответить «да» (голосовые, `/new` без «точно?», напоминания).
4. Строки с ⚠, если остались, — сделать; повторный `install.sh` покажет, что осталось.
5. Голосовые: `bash ~/flows-organizer/setup-gigaam.sh` (шаг 5). Whisper из OpenClaw
   (`~/.openclaw/tools/venv`) может не быть — тогда убрать строки `openai-whisper…`,
   `faster-whisper…` из `stt_engines` в `config.json` или поставить свой venv и указать его в `stt_venv_python`.
6. `hermes gateway restart`, в Telegram — `/new`. Проверка: `/list`, номер потока, `/comments`,
   «напомни через 2 минуты …».


## Обновление (после доработок)

Через git: `cd ~/flows-organizer && git pull && bash install.sh`, затем шаг 4. По FTP — шаги 1 → 2 → 3 → 4. Установщик напишет «Обновление <старая> → <новая>».
Строки с ⚠ в конце вывода установщика — сделать.


## Что затирается при обновлении

Установщик перезаписывает **только своё**: `~/.hermes/SOUL.md` (старый — в копию `.bak-flows-…`),
скрипты `flows*.py`, `facts.py` и навыки `flows`, `facts`. Правки в них, сделанные самим Hermes,
при следующем обновлении пропадут — такие доработки делать через пакет.
Хук `hooks/flows-nav` — наш, перезаписывается. Файлы `~/.hermes/locales/en.yaml`, `ru.yaml` (пустой стандартный
ответ на `/new`) — тоже наши, если там не было чужих. В `config.yaml`
обновляет только свои кнопки в `quick_commands` (копия — `.bak-commands-…`). Данные в `~/brain`
один раз переводит на «одну открытую точку» (`flows.py migrate`, с коммитом в git хранилища).
Не трогает: `config.json`, остальной `config.yaml`, cron, память, другие навыки — тут
Hermes может менять что угодно.

## Если что-то не так

- `Permission denied` при установке → не сделан шаг 2.
- `hermes` пишет `exec: /tmp/…/python3: not found` → сломан его лаунчер.
  Починка под <user> (подставь путь из ошибки до `/tools`):
  ```bash
  cd ~/.hermes/hermes-agent/.hermes/bin && for f in hermes hermes-acp; do cp -p $f $f.bak; sed -i 's#/tmp/tmp.XXXX/hh#/home/<user>/.hermes#g' $f; done
  hermes --version && systemctl --user restart hermes-gateway
  ```
- Если когда-нибудь заливаешь по FTP что-то прямо в `/home/<user>/.hermes`, сделай шаг 2 и для
  этих файлов: `chown -R <user>:<user> /home/<user>/.hermes`.
