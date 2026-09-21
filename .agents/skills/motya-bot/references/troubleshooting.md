# motya-bot: диагностика и грабли

## Политика ретраев sheets_call (история дублей)

`write`/`delete` — неидемпотентные: каждый повтор может создать вторую строку в таблице.

- **Было (до 18.09):** attempts=2, дедлайн 8с. Таймаут → слепой ретрай → Apps Script успевал выполнить обе → дубли в Кассе (продажа +3000 ×2, «Лич расходы» −3800 ×2 и т.д.).
- **Было (18.09, bdaea38):** write вообще без ретрая → 404-ошибка (запрос не дошёл) терял запись навсегда (+1800 «Данил кинул» не легла; юзер добавил руками).
- **Стало (19.09, 389ed2a):** write/delete = 2 попытки, дедлайн ≥18с; ретрай ТОЛЬКО при «безопасных» ошибках — HTTP 4xx/5xx (ответ получен, doPost не запускался) и обрыве соединения. Таймаут чтения, «не json», «кэш отдал чужой ответ» (poisoned) — НЕ ретраятся.
- Замечено 19.09: таймауты Apps Script в реальности обычно НЕ выполняются сервер-сайд (тест +50 дропнулся дважды и не записался ни разу) — но всё равно не ретраить.
- Чтения (balance/unsold/lots/lot/setup) идемпотентны — ретраятся свободно. Юзерские дедлайны 15с (Apps Script ходит 2–9с; дедлайны 5–8с валили половину запросов вечером — бот показывал кривой «на руках» из локального фолбэка). keepwarm — 5с.
- Локальный баланс (`source:"local"`, считается из deals.jsonl) кэшируется на 8с и перепроверяется: deals.jsonl на VPS только с 10.09 → local_balance врёт относительно листа.

## Проверка «какая версия .gs живая»

POST `{"secret": WEBHOOK_SECRET, "action": "kassa_all"}` на SHEETS_WEBHOOK (curl -L, следовать редиректам; из под Windows curl может глючить — надёжнее из-под бота на VPS: `cd /opt/motya-bot && python3 -c "import bot; print(bot.sheets_call({'action':'kassa_all'}))"`).

- `{"ok":true,"rows":[…]}` — новый код (19.09+).
- `{"ok":true,"sheet_row":0}` — старый код; бот детектит это как poisoned (sheet_row ∈ SHEETS_FOREIGN).

## Лист Касса

- Колонки: A дата, B what, C приход(inn), D расход(out), E комментарий. Данные с строки 9.
- Строка баланса: `start` 14400 (или ячейка Q5 «Продажи») + inn − out.
- `kassa_all` → все строки `{row, date, what, inn, out, comment}`. `kassa_del` + `{"row": N}` — удалить строку; **только снизу вверх** — deleteRow сдвигает номера ниже.
- Роль Матвея (с 21.09): K=11 роль, L=12 процент — **L формульная** (`IF(K=…;INDEX('Правила'!…;MATCH(K;…)))`), бот и вебхук её НЕ пишут и НЕ чистят (setValue/clearContent убивают формулу → «проценты не выставляются»). Канон текстов ролей = лист **«Правила»** A5:A10; в `ROLES` (bot.py) тексты должны совпадать байт-в-байт (в «Фулл процент, в редких случаях » есть хвостовой пробел!). Выпадашка K = `Правила!A5:A10` (была `Продажи!U2:U6` со старыми текстами — красные уголки на валидных строках). Диагностика: экшен `sales_roles`; разовая починка — идемпотентный `roles_fix` (канонизирует K + восстанавливает формулы L + перенаправляет валидацию).
- Отмена продажи лота: `update` + `fields.clear_sell`, НЕ `delete` (он сторнирует и исходный закуп — reverseKassa_ слишком широкий).

## Журналы

- `journalctl -u uchetskup-bot --since "…" ` и `/opt/motya-bot/data/bot.log` (одинаковый формат лога).
- Паттерны: `commit sheets ok row=N` (сделка записана), `sheets fail Xs <action> tryN:` (ошибка попытки), `sheets err <action> after` (итоговая неудача), `poisoned` (CDN отдал чужой ответ), `commit sheets ok row=0` — норм для cash-записей (sheet_row 0 = касса-строка без лота).
- Реальные баги = traceback'и; в data/stderr.log пусто обычно.

## Известные ложные тревоги

- Одинаковые `id` в deals.jsonl — НЕ дубли: две записи в одну секунду делят id (точность до секунды), например основная сделка + доставка.
- `sheets err … HTTP Error 404` сразу после деплоя — мимолётные 404 Google; при новой политике ретраится безопасно.
- `query is too old` в Telegram — шаг занял >15с (Apps Script лагает), не баг.
- 409 от Telegram — где-то запущен второй инстанс бота. На VPS один systemd-unit; проверить `ps aux | grep [b]ot.py`.

## Грабли окружения (Windows / Git Bash)

- SSH к VPS/домашнему серверу: ключ и known_hosts только через `/tmp/...` (кириллический HOME): `-i /tmp/id_ed25519 -o UserKnownHostsFile=/tmp/kh_vps` (VPS) / `/tmp/kh_server` (домашний 192.168.50.25, юзер tolstogo).
- heredoc: неэкранированный heredoc разворачивает `$VAR` (ломает python-код) — использовать `<<'PY'`. `UID` в bash readonly — не называть переменную UID.
- scp на VPS → обязательно `sed "s/\r$//"` перед запуском (CRLF из Windows).
- `clasp list` может вернуть пусто — scriptId доставать из истории Chrome: скопировать `AppData/Local/Google/Chrome/User Data/<профиль>/History` (sqlite) и `SELECT url FROM urls WHERE url LIKE '%script.google.com/home/projects%'` → id в URL.
- `clasp login`: URL брать из stdout фонового `clasp login`, открывать через `powershell -EncodedCommand` + `Start-Process '<url>'` (cmd `start` ломается об `&` в URL); юзер жмёт «Разрешить». Без «Google Apps Script API» (тумблер script.google.com/home/usersettings) clasp version/redeploy дают «User has not enabled the Apps Script API».
- git-репо motya-bot: sheets_webhook.gs помечен skip-worktree → `git add` отказывает; снять: `git update-index --no-skip-worktree sheets_webhook.gs`.
- Локальный python для компиль-чека: `runtime/python.exe` из репо (системный — заглушка). У встраиваемого runtime не работает import из cwd — тесты гонять на VPS.
- Набирание юзера в неверной раскладке: «cltkfkk» = «сделал». Переводить QWERTY→ЙЦУКЕН при бессмысленных сообщениях.

## Проекты «Касса» — два!

Правильный: scriptId `1ZM1y…` (привязан к «Матвей-учет железо1.2», развёртывание AKfycbxveb… = URL в .env). Второй `1Y03n…` — привязан к копии 1.1, в него НЕ деплоить (мёртвый, archived).
