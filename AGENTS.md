# AGENTS.md

Инструкции для AI-агентов. Для людей — `README.md` и `SHEET.md`.

## Что это

Telegram-бот учёта железа + Google Sheets. Закуп, продажа, касса агента.
Только стандартная библиотека Python. Таблица — источник правды по кассе и лотам, когда вебхук отвечает полем `hand`.

Карман агента ≠ касса железа. Не писать в чужой workbook.

## Корень

Рабочая папка проекта, не `C:\Windows\system32`.

| Путь | Зачем |
| --- | --- |
| `bot.py` | Лонг-полл бот |
| `sheets_webhook.gs` | Apps Script, в git без секретов |
| `sheets_webhook.private.gs` | Локальная копия с id/секретом, gitignore |
| `.env` | Секреты, gitignore |
| `.env.example` | Шаблон |
| `data/` | jsonl / сессии / логи, gitignore |
| `runtime/python.exe` | Опциональный embeddable Python, gitignore |
| `watch.bat` `launch.ps1` `restart.ps1` `install_task.ps1` | Windows-запуск |

Нет `requirements.txt`, нет тестов. Не ставить pip в embeddable runtime без нужды.

## Секреты

- `BOT_TOKEN`, `WEBHOOK_SECRET`, id таблицы, chat id — только `.env` и `sheets_webhook.private.gs` / Script Properties.
- **Никогда** не передавать токен через `powershell -Command`. Defender помечает это как `LummaStealerClick.S!MTB`.
- Не отключать антивирус.
- Не логировать токен. Не класть его в commit.
- `chat_allowed()`: пустой allowlist = никому нельзя. Не менять на «пустой список = пускать всех».
- Apps Script: execute as me, доступ Anyone, проверка `body.secret`.

Ключи `.env` — см. `.env.example`.

Перед `git add` / `git push` проверить, что нет токена, `WEBHOOK_SECRET`, id таблицы, `deals.jsonl`.

## Запуск

Живой процесс держать **вне** Job Object агента, иначе IDE убивает python в конце хода.

```powershell
powershell -NoProfile -File .\launch.ps1
schtasks /Query /TN UchetSkupBot
```

Убивать только процессы с `bot.py` / папкой проекта в CommandLine. Не `Stop-Process python` глобально.

`LastTaskResult 0xFFFFFFFF` у задачи = процесс умер.

`getMe` при фейле не делает `SystemExit` — `loop()` спит и возвращается.

## Таблица «Продажи»

Данные с **строки 9**. Шапка — строка 8.

**Не вставлять колонки в середину.** Новые поля только в конец (T/U).
**Не вызывать `getLastRow()`.** `nextRow_(sheet, col, start)`: Продажи по E с 9, Касса по B с 9.

Колонки — `SHEET.md`. Даты — нативный `Date` + `dd.mm.yyyy`.
`deleteLot_` чистит ячейки, не `deleteRow`.

Касса: `Q5` старт, `N5` на руках ≈ Q5 + Касса!C − Касса!D.
Не считать кассу как SUM F/G/H + продажи.

## Вебхук

`doPost` actions: `ping`, `balance`, `setup`, `unsold`, `lots`, `lot`, `update`, `delete`, иначе `write`.

Неизвестный `action` на старом деплое падает в `write`. Если в теле `type=sell`, повторно отметит продажу. Не слать новые имена экшенов, пока живой деплой их не содержит. Проверка: `setup`/`balance` без `hand` = старый деплой.

Новый деплой часто даёт новый `/exec` URL — обновить `.env`. Лучше новая **версия того же** web app.

POST `/exec` → 302 GET на googleusercontent. Не отключать redirect.

Отмена продажи: `update` + `fields.clear_sell`. Не `delete` (сторнирует и исходный закуп).
`reverseKassa_` слишком широкий для unsell. Бот для продажи сторнирует кассу сам.

После правки `.gs` диск ≠ таблица, пока человек не задеплоит.

## UI и домен

- `parse_mode` только HTML
- `kb(rows)` — массив **рядов**, не плоский список
- `callback_data` ≤ 64 байт
- Мастер редактирует то же сообщение (`ui()`). Меню — новое + `reply_kb()`
- Шапка: `<b>Закуп|Продажа|Деньги</b>` + точки. Не дублировать имя шага в заголовке
- Категории и источники продажи — без эмодзи на кнопках выбора
- Повторный `editMessageText` с тем же текстом = 400. В «Касса» добавлять `HH:MM`
- Роли — дословно, список в `ROLES` / `SHEET.md`
- Меню денег: «Деньги получил», «Деньги потратил», «Данил кинул в кассу»
- `who=me` закуп = расход кассы, продажа = приход. `who=danil` = касса не трогается
- Доставка/расходник из кассы — отдельные jsonl `Доставка` / `Расходник`

Отмена последнего: продажа → `clear_sell`; закуп → `delete`; только деньги → обратное движение. Пропускать уже отменённые (`undoes`), «Отмена», доставку/расходник как «последнее».

## Касса бот vs таблица

1. Предпочитать `hand` из вебхука
2. Иначе `START_CASH` + jsonl по `cash_dir`
3. Не заявлять синхронность, пока `setup` не вернул `hand`

## Не делать

- Не переписывать на aiogram / requests
- Не тащить токен в argv
- Не слать несуществующий `action` вместе с `type=sell`/`buy`
- Не `insertColumn` между A–S, не `getLastRow()`, не `deleteRow` на Продажах
- Не коммитить `.env`, `data/*.jsonl`, `data/*.log`, `runtime/`
- Не выгружать Google Sheet в репозиторий
- Не выдумывать роли и эмодзи «для красоты»

## Проверка

```text
python -m py_compile bot.py
```

Рестарт через `launch.ps1`. В Telegram с allowlist-чата прогнать мастер вперёд/назад и отмену. Если трогали `.gs` — человек деплоит, затем `ping` и `setup` с полем `hand`.
