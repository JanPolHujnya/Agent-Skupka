---
name: motya-bot
description: Операционный мануал по Telegram-боту учёта @uchetskup_bot (проект motya-bot — закуп/продажа/касса + Google Sheets «Матвей-учет железо1.2»). Использовать при ЛЮБОЙ правке, деплое или диагностике этого бота: «мотя/мотя-бот/учёт-бот/касса/дубли записей/синхронизация таблицы/таблица не отвечает/задеплой/обнови бота/залей на гит», правки bot.py или sheets_webhook.gs, проблемы записи в Google Sheets, вопросы про VPS 78.17.15.190.
---

# motya-bot: деплой и диагностика

## Карта системы

- **Код:** `C:\Users\Пользователь\Desktop\motya-bot\` (git → github.com/JanPolHujnya/Agent-Skupka, main). `bot.py` — бот на stdlib-python; `sheets_webhook.gs` — серверный код таблицы (в проекте Google файл называется «Код»).
- **Рантайм:** финский VPS `root@78.17.15.190`, systemd unit `uchetskup-bot`, каталог `/opt/motya-bot` (НЕ git-репозиторий). SSH всегда с явными путями (кириллический HOME ломает дефолты):
  `ssh -i /tmp/id_ed25519 -o UserKnownHostsFile=/tmp/kh_vps -o IdentitiesOnly=yes root@78.17.15.190`
- **Таблица:** «Матвей-учет железо1.2», листы Продажи / Касса / Инвентаризация. Бот ↔ таблица через вебхук Apps Script: URL в `.env` (`SHEETS_WEBHOOK`), секрет `WEBHOOK_SECRET`.
- **Apps Script:** проект «Касса», scriptId `1ZM1yKFKlHhBecb_V-y_vI91YyJyRH1BjMwGE6pocW-6lh4wz4_8r3Mll`. РАБОЧЕЕ развёртывание (его URL в .env): `AKfycbxvebXrN4KnJFe84ZbjmroRryjpIrGaUIaEubGA2LCxsXsLZGciuyND2akx5y-Oy6_r`. Аккаунт: danya.gubin.2005@inbox.ru (clasp уже залогинен, `~/.clasprc.json`).
- **Данные бота на VPS:** `/opt/motya-bot/data/` — `deals.jsonl` (локальный журнал сделок, только с 10.09), `bot.log`, `state.json`. Секреты — `.env` на VPS и локальная копия.

## Правила

1. Ничего не ломать. Диагностика read-only до полного понимания. Юзер болезненно реагирует на потерю данных таблицы.
2. Локально бота не запускать — 409 conflict с VPS-инстансом. Всегда ровно один инстанс.
3. Перед правкой bot.py — бэкап на VPS (`bot.py.bak-<дата>`), перед правками строк Кассы — дамп `kassa_all` в файл.
4. После каждого фикса юзер ждёт коммит в git («залей на гит»).

## Деплой bot.py на VPS

1. Правки в локальном `bot.py`. Компиль-чек: `runtime/python.exe -m py_compile bot.py` (системный python — заглушка).
2. Если менял `_sheets_call_inner`/ретраи — прогнать мок-тесты `_sheets_http` (5 сценариев: write+404 → ретрай; write+таймаут → НЕТ ретрая; write+не-json → НЕТ ретрая; чтение+таймаут → ретрай; write+404 оба раза → 2 попытки).
3. git add/commit/push.
4. На VPS одним заходом: бэкап текущего → scp во временный файл → `sed "s/\r$//"` (CRLF!) → `python3 -m py_compile` → переложить → `systemctl restart uchetskup-bot` → `journalctl -u uchetskup-bot -n 6` (ждать строку `bot @uchetskup_bot started`).
5. Откат = бэкап-файл обратно + restart.

## Деплой sheets_webhook.gs через clasp

Условия: включён «Google Apps Script API» (script.google.com/home/usersettings, юзер включил 19.09) и выполнен `clasp login` (креды в `~/.clasprc.json`; при логине юзер один раз жмёт «Разрешить» в браузере; ссылку открывать `powershell Start-Process`, не cmd — амперсанды рвут команду).

Рабочий каталог: `~/.zcode/workspace/default/clasp-cassa` (`.clasp.json` уже указывает на правильный scriptId).

1. `clasp pull` → придёт `Код.js`; убедиться, что это живой код (маркеры: `kassa_all`, `COL_ROLE = 11`).
2. Перезаписать `Код.js` содержимым локального `sheets_webhook.gs`.
3. `clasp version "<описание>"` → из вывода взять номер версии.
4. `clasp redeploy AKfycbxvebXrN4KnJFe84ZbjmroRryjpIrGaUIaEubGA2LCxsXsLZGciuyND2akx5y-Oy6_r -V <номер> -d "<описание>"` — обновляет СУЩЕСТВУЮЩЕЕ развёртывание, URL в .env не меняется.
5. Проверка живости: POST на URL из .env c `{"secret":"…","action":"kassa_all"}` → новый код отвечает `{"ok":true,"rows":[…]}`, старый — `{"ok":true,"sheet_row":0}` (бот пометит «кэш отдал чужой ответ»).

**НИКОГДА** не создавать новое развёртывание (ни в UI «Развернуть → Новое развертывание», ни `clasp deploy`): новый URL + доступ «Только я» = бот его не увидит. В проекте уже 8 мёртвых развёртываний.

## Диагностика

При любой жалобе на дубли/рассинхрон/«таблица не отвечает» — читать `references/troubleshooting.md`, там же структура листа Касса и политика ретраев.
