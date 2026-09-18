# Учёт железа — Telegram-бот + Google Sheets

Бот для агента: закуп, продажа из непродавшегося, касса, расходники, отмена последнего действия. Пишет в Google Таблицу через Apps Script. Зависимостей pip нет, только стандартная библиотека Python 3.12+.

В репозитории **нет** живой таблицы, сделок, токена и кассы. Это шаблон: подключаешь своего бота и свою таблицу.

## Что получится

- Telegram: кнопки Закуп / Продажа / Лот / Деньги / Касса / Отменить последнее
- Инвентаризация вынесена в `backup/inventory/` (живой бот её не зовёт)
- Продажа: категория или поиск по висящим лотам, не каждый раз новое сообщение при листании
- Касса: получил / потратил / принципал кинул в кассу
- Отмена продажи снимает отметку «продано», товар снова висит, деньги сторнируются. Закуп при отмене удаляется
- Инвентаризация: один человек запускает, второй видит «идёт». Листаешь непроданные лоты, отмечаешь «на месте» / «отсутствует». Не отмеченное при завершении считается отсутствующим. Итог пишется на отдельный лист «Инвентаризация», история — по месяцам, отсутствующее за прошлый месяц можно поправить

## Быстрый старт

### 1. Python

Нужен **3.10+** (лучше 3.12). pip-пакеты не нужны.

- Windows: [python.org](https://www.python.org/downloads/), галка **Add python.exe to PATH**. Если есть локальный `runtime\python.exe` — батники возьмут его.
- Linux: `sudo apt install python3` (Debian/Ubuntu) или `sudo dnf install python3`.

### 2. Бот в Telegram

1. [@BotFather](https://t.me/BotFather) → `/newbot`
2. Скопируй токен
3. Напиши боту `/start` со своего аккаунта
4. Узнай свой `chat_id`: напиши [@userinfobot](https://t.me/userinfobot) или временно запусти бота без allowlist (не оставляй так)

### 3. Таблица

Собери пустую таблицу по [SHEET.md](SHEET.md). Id — кусок URL между `/d/` и `/edit`:

`https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`

### 4. Вебхук Apps Script

1. В таблице: Расширения → Apps Script
2. Вставь содержимое `sheets_webhook.gs`
3. Проект → Настройки проекта → Свойства скрипта:
   - `SHEET_ID` = id таблицы
   - `WEBHOOK_SECRET` = тот же секрет, что в `.env`
4. Развернуть → Новое развёртывание → Тип: **Веб-приложение**
   - Выполнять как: я
   - Кто имеет доступ: все
5. Скопируй URL `.../exec` в `.env` как `SHEETS_WEBHOOK`

После правки скрипта делай **новую версию того же** web app, чтобы URL не сменился.

### 5. Конфиг бота

Windows: `copy .env.example .env`  
Linux: `cp .env.example .env`

Заполни `.env`. `ALLOWED_CHAT_ID` — числовые id, через запятую. Пустой список = никому нельзя.

### 6. Запуск

**Windows**, в консоли (окно не закрывать):

```text
start.bat
```

Автозапуск: `powershell -File install_task.ps1` (задача `UchetSkupBot`). Рестарт: `powershell -File launch.ps1`.

**Linux / VPS** — входящих портов не надо, бот сам ходит в Telegram и Google по HTTPS.

```text
chmod +x *.sh
./start.sh
```

Чтобы жил после выхода из ssh:

```text
sudo ./install-service.sh
journalctl -u uchetskup-bot -f
```

без root: `./install-service.sh --user`  
рестарт: `sudo systemctl restart uchetskup-bot`  
снять: `sudo ./install-service.sh --remove`

На машине без systemd можно так: `./launch.sh` (сторож в фоне, лог `data/watch.log`).

В Telegram: `/start`.

## Структура

| Файл | Зачем |
| --- | --- |
| `bot.py` | Лонг-полл бот |
| `sheets_webhook.gs` | Сервер таблицы |
| `.env.example` | Шаблон секретов |
| `SHEET.md` | Как разметить колонки |
| `AGENTS.md` | Правила для AI-агентов |
| `start.sh` `watch.sh` `launch.sh` | Запуск на Linux |
| `install-service.sh` | systemd на сервере |
| `watch.bat` / `launch.ps1` | Сторож на Windows |
