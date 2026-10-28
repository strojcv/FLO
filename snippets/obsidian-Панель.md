# Панель потоков

Нужен плагин **Dataview**. Скопируй этот файл в корень хранилища.
(Без плагина есть `ПОТОКИ.md` — его собирает сервер.)

## Открытые потоки

```dataview
TABLE WITHOUT ID link(file.path, title) AS "Поток", priority AS "P", status AS "Статус", tags AS "Теги"
FROM "flows"
WHERE type = "flow" AND status != "closed"
SORT priority ASC
```

## Календарь

```dataview
TABLE WITHOUT ID link(file.path, title) AS "Событие", flow AS "Поток", when AS "Когда", remind AS "Напомнить"
FROM "календарь"
WHERE type = "event" AND status = "active"
SORT when ASC
```

## Последние точки

```dataview
TABLE WITHOUT ID link(file.path, title) AS "Точка", flow AS "Поток", date AS "Дата"
FROM "flows"
WHERE type = "point" AND status != "next"
SORT date DESC
LIMIT 15
```

## Неразобранные голосовые

```dataview
LIST duration
FROM "_inbox/voice"
SORT file.name DESC
```
