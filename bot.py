# -*- coding: utf-8 -*-
"""Telegram-бот учёта железа: сделки и касса. Только стандартная библиотека."""
from __future__ import annotations

import json
import http.client
import http.cookiejar
import os
import re
import socket
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.version_info < (3, 10):
    raise SystemExit("Нужен Python 3.10+")

# VPS often has IPv6 DNS but no IPv6 route (sysctl disable_ipv6).
# urllib then waits on unreachable AAAA before falling back to IPv4.
_GA = socket.getaddrinfo


def _ipv4_addrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _GA(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_addrinfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
DEALS_PATH = DATA / "deals.jsonl"
STATE_PATH = DATA / "state.json"
LOG_PATH = DATA / "bot.log"
UNSOLD_PATH = DATA / "unsold_cache.json"
LOTS_PATH = DATA / "lots_cache.json"

API = "https://api.telegram.org/bot{token}/{method}"

CATS = [
    "Процессор",
    "Видеокарта",
    "ОЗУ",
    "Накопитель",
    "Материнка",
    "Блок питания",
    "Корпус",
    "Охлаждение",
    "Переходник",
    "ПК/комплект",
    "Периферия",
    "Другое",
]
PLACES = [
    ("avito", "Avito"),
    ("vk", "VK"),
    ("tg", "Telegram"),
    ("youla", "Юла"),
    ("friend", "Знакомые"),
    ("other", "Другое"),
]
PLACE_MAP = {k: v for k, v in PLACES}
ROLES = [
    ("self", "Сам закрыл, Данил — товар и логистика", 30),
    ("full", "Фулл процент, в редких случаях", 100),
    ("lead", "Кинул контакт, дальше Данил", None),
    ("bring", "Привёл покупателя и довёл до оплаты", None),
    ("both", "И товар, и клиент. Данил — только логистика", None),
]
ROLE_MAP = {k: (full, pct) for k, full, pct in ROLES}
PAGE = 6
HR = "────────"
BUY_TOTAL = 10
SELL_TOTAL = 11

USERS_KEY = "_users"


def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    token = env.get("BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if not token:
        tfile = ROOT / ".token"
        if tfile.exists():
            token = tfile.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("Нет BOT_TOKEN в .env")
    env["BOT_TOKEN"] = token
    env["START_CASH"] = float(env.get("START_CASH") or 14400)
    env["SHEETS_WEBHOOK"] = env.get("SHEETS_WEBHOOK") or ""
    env["WEBHOOK_SECRET"] = env.get("WEBHOOK_SECRET") or ""
    env["SHEET_ID"] = env.get("SHEET_ID") or ""
    env["ALLOWED_CHAT_ID"] = env.get("ALLOWED_CHAT_ID") or ""
    return env


ENV = load_env()
TOKEN = ENV["BOT_TOKEN"]


def parse_allowed():
    raw = ENV.get("ALLOWED_CHAT_ID") or ""
    out = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


ALLOWED_CHAT_IDS = parse_allowed()


def log(msg: str) -> None:
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("utf-8", "replace").decode("ascii", "replace"), flush=True)


def bg(fn, *a, **k):
    def wrap():
        try:
            fn(*a, **k)
        except Exception:
            log(traceback.format_exc())

    threading.Thread(target=wrap, daemon=True, name="bg").start()


def api(method: str, payload: dict | None = None, timeout: int = 12) -> dict:
    url = API.format(token=TOKEN, method=method)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        log("HTTP %s %s %s" % (e.code, method, body[:400]))
        return {"ok": False, "error": body}
    except Exception as e:
        log("API %s fail: %s" % (method, e))
        return {"ok": False, "error": str(e)}


def kb(rows):
    return {"inline_keyboard": rows}


def btn(text, data):
    return {"text": text, "callback_data": data}


def reply_kb():
    return {
        "keyboard": [
            [{"text": "🛒 Закуп"}, {"text": "💸 Продажа"}],
            [{"text": "✏️ Лот"}, {"text": "💵 Деньги"}],
            [{"text": "💰 Касса"}, {"text": "Отменить последнее"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def send(chat_id, text, markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = markup
    return api("sendMessage", payload, timeout=8)


def edit_message(chat_id, message_id, text, markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = markup
    return api("editMessageText", payload, timeout=4)


def answer_cb(cb_id, text=""):
    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text}, timeout=4)


def cancel_row():
    return [btn("✕ Отмена", "m:cancel")]


def nav_row():
    return [btn("‹ Назад", "m:back"), btn("✕ Отмена", "m:cancel")]


def skip_note_kb():
    return kb([[btn("без примечания", "n:skip")], nav_row()])


def dots(n, total):
    n = max(0, min(int(n), int(total)))
    return "●" * n + "○" * (total - n)


def progress_line(kind, step):
    if kind == "buy":
        mapping = {
            "product": (1, "товар"),
            "buy_cat": (2, "категория"),
            "amount": (3, "сумма"),
            "who": (4, "деньги"),
            "extras_d": (5, "доставка"),
            "delivery_amt": (5, "доставка"),
            "extras_c": (6, "расходники"),
            "cons_amt": (6, "расходники"),
            "lot": (7, "чей лот"),
            "role": (8, "роль"),
            "role_other": (8, "роль"),
            "date": (9, "дата"),
            "date_custom": (9, "дата"),
            "note": (10, "примечание"),
            "confirm": (10, "проверка"),
        }
        total = BUY_TOTAL
        title = "Закуп"
    elif kind == "sell":
        mapping = {
            "sell_how": (1, "поиск"),
            "sell_cat": (2, "категория"),
            "search": (2, "поиск"),
            "pick": (2, "товар"),
            "amount": (3, "цена"),
            "who": (4, "деньги"),
            "extras_d": (5, "доставка"),
            "delivery_amt": (5, "доставка"),
            "extras_c": (6, "расходники"),
            "cons_amt": (6, "расходники"),
            "place": (7, "где"),
            "place_other": (7, "где"),
            "buyer": (8, "кому"),
            "role": (9, "роль"),
            "role_other": (9, "роль"),
            "date": (10, "дата"),
            "date_custom": (10, "дата"),
            "note": (11, "примечание"),
            "confirm": (11, "проверка"),
        }
        total = SELL_TOTAL
        title = "Продажа"
    elif kind == "cash":
        mapping = {
            "cash_what": (1, "тип"),
            "amount": (2, "сумма"),
            "date": (3, "дата"),
            "date_custom": (3, "дата"),
            "note": (4, "примечание"),
            "confirm": (4, "проверка"),
        }
        total = 4
        title = "Деньги"
    else:
        titles = {"edit": "Лот", "kind": "Сделка"}
        return "<b>" + esc(titles.get(kind, kind or "Учёт")) + "</b>"
    n, _name = mapping.get(step, (1, ""))
    return "<b>%s</b>\n%s" % (title, dots(n, total))


def screen(kind, step, body=""):
    head = progress_line(kind, step)
    if body:
        return head + "\n\n" + body
    return head


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, obj) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


SESSIONS: dict[str, dict] = load_json(STATE_PATH, {})
_STATE_LOCK = threading.Lock()
_CASH = {"t": 0.0, "data": None}
_UNSOLD = {
    "t": 0.0,
    "items": None,
    "lock": threading.Lock(),
    "busy": False,
    "done": threading.Event(),
}
_LOTS = {
    "t": 0.0,
    "items": None,
    "lock": threading.Lock(),
    "busy": False,
    "done": threading.Event(),
}


def sid(chat_id) -> str:
    return str(chat_id)


def sess(chat_id) -> dict:
    s = SESSIONS.get(sid(chat_id))
    if not s:
        s = {"step": "idle"}
        SESSIONS[sid(chat_id)] = s
    return s


def clear_sess(chat_id):
    with _STATE_LOCK:
        SESSIONS[sid(chat_id)] = {"step": "idle"}
        save_json(STATE_PATH, SESSIONS)


def persist_sess():
    with _STATE_LOCK:
        save_json(STATE_PATH, SESSIONS)


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_amount(text: str):
    t = (
        text.lower()
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace("₽", "")
        .replace("р.", "")
        .replace("руб", "")
        .replace("рублей", "")
        .replace(",", ".")
    )
    try:
        n = float(t)
    except ValueError:
        return None
    if n <= 0 or n > 10_000_000:
        return None
    return int(round(n))


def parse_date(text: str):
    t = text.strip().replace("/", ".").replace("-", ".").replace(" ", "")
    parts = [p for p in t.split(".") if p]
    try:
        if len(parts) == 2:
            d, m = int(parts[0]), int(parts[1])
            y = date.today().year
        elif len(parts) == 3:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
        else:
            return None
        return date(y, m, d).strftime("%d.%m.%Y")
    except ValueError:
        return None


def fmt_money(n) -> str:
    try:
        n = int(round(float(n)))
    except (TypeError, ValueError):
        return "0 ₽"
    s = f"{n:,}".replace(",", " ")
    return s + " ₽"


def today_str() -> str:
    return date.today().strftime("%d.%m.%Y")


def yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).strftime("%d.%m.%Y")


def guess_category(name: str) -> str:
    n = (name or "").lower()
    rules = [
        ("Переходник", r"переходник|riser|райзер|12-2x6"),
        ("ПК/комплект", r"комплект|\bпк\b|сборк"),
        ("Периферия", r"клав|наушн|колон|мышь|перифер"),
        ("Видеокарта", r"rtx|gtx|rx\s|r9\s|hd\d|видео|gpu|1050|3060|470|580|5500|отвал"),
        ("Процессор", r"\bi[3579]\b|xeon|ryzen|процессор|\bcpu\b|12400|10100|2650|2670|3570|3440"),
        ("ОЗУ", r"ddr|озу|память|hynix|hyperx"),
        ("Накопитель", r"ssd|hdd|nvme|накоп|toshiba|barracuda|kingston 240|wd blue"),
        ("Материнка", r"плат|материн|b760|h81|h410|b250|x99|fintech|ds3h"),
        ("Блок питания", r"блок пит|\bpsu\b|ginzu|accord|\d+w"),
        ("Корпус", r"корпус|case|cougar|deepcool|panzer"),
        ("Охлаждение", r"кулер|охлажд|вертуш|вентилятор|argb|башен"),
        ("Монитор", r"монитор|display"),
    ]
    for cat, pat in rules:
        if re.search(pat, n):
            return cat
    return "Другое"


def cat_label(name: str, count=None) -> str:
    if count is None:
        return name
    return "%s · %s" % (name, count)


def load_deals() -> list:
    if not DEALS_PATH.exists():
        return []
    out = []
    for line in DEALS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def local_balance() -> dict:
    start = int(ENV["START_CASH"])
    inn = 0
    out = 0
    recent = []
    for d in load_deals():
        amt = int(d.get("amount") or 0)
        direction = d.get("cash_dir")
        if not amt or direction not in ("in", "out"):
            continue
        if direction == "in":
            inn += amt
        else:
            out += amt
        recent.append(
            {
                "date": d.get("date") or "",
                "what": d.get("kassa_what") or d.get("type") or "",
                "inn": amt if direction == "in" else 0,
                "out": amt if direction == "out" else 0,
                "comment": d.get("product") or d.get("comment") or "",
            }
        )
    recent = list(reversed(recent))[:8]
    hand = start + inn - out
    return {
        "hand": hand,
        "start": start,
        "inn": inn,
        "out": out,
        "computed": hand,
        "recent": recent,
        "source": "local",
    }


def remember_cash(data: dict) -> dict:
    _CASH["t"] = time.time()
    _CASH["data"] = data
    return data


def ingest_cash(payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    if "hand" not in payload:
        return
    try:
        hand = int(round(float(payload["hand"])))
    except (TypeError, ValueError):
        return
    start = payload.get("start")
    try:
        start_n = int(round(float(start))) if start is not None else int(ENV["START_CASH"])
    except (TypeError, ValueError):
        start_n = int(ENV["START_CASH"])
    try:
        inn = int(round(float(payload.get("inn") or 0)))
        out = int(round(float(payload.get("out") or 0)))
    except (TypeError, ValueError):
        inn, out = 0, 0
    computed = payload.get("computed")
    try:
        computed_n = int(round(float(computed))) if computed is not None else start_n + inn - out
    except (TypeError, ValueError):
        computed_n = start_n + inn - out
    remember_cash(
        {
            "hand": hand,
            "start": start_n,
            "inn": inn,
            "out": out,
            "computed": computed_n,
            "recent": payload.get("recent") or [],
            "source": "sheet",
        }
    )


def peek_balance() -> dict:
    cached = _CASH.get("data")
    if cached:
        return cached
    return local_balance()


def fetch_balance(force=False) -> dict:
    now = time.time()
    cached = _CASH.get("data")
    if cached:
        # локальная оценка (таблица не ответила) живёт 8 с и потом
        # перепроверяется — лист мог ожить; кэш таблицы не стареет до записи
        ttl = 8 if cached.get("source") == "local" else 25
        if not force and now - _CASH["t"] < ttl:
            return cached
        if cached.get("source") != "local" and not force:
            return cached
    r = sheets_call({"action": "balance"}, attempts=2, deadline_s=15)
    if r.get("ok") and "hand" in r:
        ingest_cash(r)
        return _CASH["data"]
    return remember_cash(local_balance())


def cash_on_hand() -> int:
    return int(peek_balance().get("hand") or 0)


def append_deal(deal: dict) -> None:
    with DEALS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(deal, ensure_ascii=False) + "\n")


def is_extra_deal(d: dict) -> bool:
    return d.get("type") == "cash" and (d.get("kassa_what") or "") in ("Доставка", "Расходник")


def last_action() -> dict | None:
    acts = undoable_actions(1)
    return acts[0] if acts else None


def undoable_actions(limit: int = 15) -> list:
    deals = load_deals()
    undone = {str(d.get("undoes") or "") for d in deals if d.get("undoes")}
    undone.discard("")
    out = []
    for d in reversed(deals):
        did = str(d.get("id") or "")
        if not did or did in undone or d.get("undoes"):
            continue
        what = str(d.get("kassa_what") or "")
        if "Отмена" in what:
            continue
        if is_extra_deal(d):
            continue
        if d.get("type") not in ("buy", "sell", "cash"):
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def undo_label(d: dict) -> str:
    ico = {"buy": "🛒", "sell": "💸", "cash": "💵"}.get(d.get("type"), "•")
    name = d.get("product") or d.get("note") or d.get("kassa_what") or "?"
    label = "%s %s · %s" % (ico, name, fmt_money(d.get("amount") or 0))
    date = str(d.get("date") or "")
    if date:
        label += " · " + date
    return label[:64]


def extras_for(deal: dict) -> list:
    if deal.get("type") not in ("buy", "sell"):
        return []
    prod = deal.get("product") or ""
    ts = (deal.get("ts") or "")[:16]
    out = []
    for d in load_deals():
        if not is_extra_deal(d):
            continue
        if (d.get("product") or "") != prod:
            continue
        if ts and (d.get("ts") or "")[:16] == ts:
            out.append(d)
    return out


def resolve_row(deal: dict) -> int:
    try:
        row = int(deal.get("sheet_row") or 0)
    except (TypeError, ValueError):
        row = 0
    if row >= 9:
        return row
    name = str(deal.get("product") or "").strip().lower()
    if not name:
        return 0
    items = fetch_lots("all", deal.get("product") or "") or []
    for it in items:
        if str(it.get("product") or "").strip().lower() != name:
            continue
        sold = bool(it.get("sold"))
        if deal.get("type") == "sell" and sold:
            return int(it.get("row") or 0)
        if deal.get("type") == "buy" and not sold:
            return int(it.get("row") or 0)
    return 0


def reverse_cash_row(orig: dict, what: str, direction: str) -> None:
    amt = int(orig.get("amount") or 0)
    if not amt or direction not in ("in", "out"):
        return
    extra = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": "cash",
        "product": orig.get("product") or "",
        "amount": amt,
        "who": "other_out" if direction == "out" else "other_in",
        "date": today_str(),
        "kassa_what": what,
        "cash_dir": direction,
        "comment": (orig.get("product") or "") + " · отмена последнего",
        "undoes": orig.get("id") or "",
    }
    append_deal(extra)
    push_sheets(extra)


# у каждого действия свой обязательный ключ: по нему отличаем честный ответ
# от чужого, который отдает CDN-кэш Apps Script (тот ключует по URL,
# не глядя на метод и тело запроса)
SHEETS_KEY = {
    "unsold": "items",
    "lots": "items",
    "balance": "hand",
    "setup": "hand",
    "write": "sheet_row",
    "lot": "item",
}
SHEETS_FOREIGN = ("service", "hand", "items", "entries", "sheet_row", "item")

# поход в таблицу стоит 2-9 с (скорость Apps Script), поэтому повторные
# чтения в пределах 10 с берем из кэша. любое пишущее действие кэш сбрасывает
SHEETS_READ = {"balance", "setup", "unsold", "lots", "lot"}
SHEETS_CACHE_TTL = 10
_sheets_cache: dict = {}
_SHEETS_MU = threading.Lock()
_SHEETS_USER = threading.Event()


class SheetsRedirect(urllib.request.HTTPRedirectHandler):
    # Apps Script иногда отвечает 302 обратно на /exec (печёт cookie).
    # по умолчанию urllib повторяет как GET — выполняется doGet вместо doPost
    # и приходит {"ok":true,"service":...}. поэтому на script.google.com
    # повторяем исходный POST, на googleusercontent (подписанный ответ) — GET.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (
            req.get_method() == "POST"
            and code in (301, 302, 303, 307, 308)
            and "script.google.com/macros" in newurl
        ):
            return urllib.request.Request(
                newurl,
                data=req.data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SHEETS_OPENER = urllib.request.build_opener(
    SheetsRedirect(),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)
_SHEETS_SSL = ssl.create_default_context()


def _sheets_http(url: str, data: bytes, deadline: float) -> str:
    # один дедлайн на всю цепочку 302, иначе каждый хоп ждёт свой timeout
    method = "POST"
    body = data
    for _ in range(5):
        left = deadline - time.time()
        if left <= 0.2:
            raise TimeoutError("sheets deadline")
        p = urllib.parse.urlparse(url)
        host = p.hostname
        if not host:
            raise OSError("bad sheets url")
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        conn = http.client.HTTPSConnection(
            host, p.port or 443, timeout=left, context=_SHEETS_SSL
        )
        try:
            hdrs = {}
            if method == "POST":
                hdrs["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=body if method == "POST" else None, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
            loc = resp.getheader("Location") or ""
        finally:
            conn.close()
        if status in (301, 302, 303, 307, 308) and loc:
            url = urllib.parse.urljoin(url, loc)
            if "script.google.com/macros" in url:
                method = "POST"
            else:
                method = "GET"
                body = None
            continue
        if status >= 400:
            raise OSError("HTTP Error %s" % status)
        return raw.decode("utf-8", "replace")
    raise TimeoutError("sheets redirects")


def sheets_call(payload: dict, attempts: int = 2, deadline_s: float = 8, priority: str = "user") -> dict:
    url = ENV.get("SHEETS_WEBHOOK") or ""
    if not url:
        return {"ok": False, "error": "local"}
    action = str(payload.get("action") or "")
    key = SHEETS_KEY.get(action)
    if action not in SHEETS_READ:
        _sheets_cache.clear()
    else:
        hit = _sheets_cache.get(action)
        if hit and time.time() - hit[0] < SHEETS_CACHE_TTL:
            return hit[1]
    if priority == "warm":
        if _SHEETS_USER.is_set():
            return {"ok": False, "error": "busy"}
        got = _SHEETS_MU.acquire(timeout=0.05)
        if not got:
            return {"ok": False, "error": "busy"}
        try:
            if _SHEETS_USER.is_set():
                return {"ok": False, "error": "busy"}
            return _sheets_call_inner(url, payload, action, key, attempts, deadline_s)
        finally:
            _SHEETS_MU.release()
    _SHEETS_USER.set()
    try:
        with _SHEETS_MU:
            return _sheets_call_inner(url, payload, action, key, attempts, deadline_s)
    finally:
        _SHEETS_USER.clear()


def _sheets_call_inner(url, payload, action, key, attempts, deadline_s) -> dict:
    secret = ENV.get("WEBHOOK_SECRET") or ""
    err = ""
    t_all = time.time()
    # append/delete вслепую не повторяем при таймауте: строка уже могла
    # записаться — повтор даёт дубль в таблице. Но 404/обрыв соединения
    # значит «запрос точно не дошёл» — такой повтор безопасен и нужен.
    if action in ("write", "delete"):
        ntry = 2
        deadline_s = max(float(deadline_s), 18)
    else:
        ntry = max(1, int(attempts))
    for attempt in range(1, ntry + 1):
        body = dict(payload)
        if secret:
            body["secret"] = secret
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        sep = "&" if "?" in url else "?"
        t0 = time.time()
        try:
            text = _sheets_http(
                url + sep + "cb=" + str(time.time_ns()), raw, time.time() + deadline_s
            )
            log("sheets %.1fs %s: %s" % (time.time() - t0, action, text[:240]))
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
            if not isinstance(data, dict):
                err = "не json: " + text[:60]
                if action in ("write", "delete"):
                    # ответ получен, но непонятен: запись уже могла пройти
                    break
                continue
            if data.get("error") == "forbidden":
                return data
            poisoned = bool(data.get("ok")) and (
                data.get("service")
                or (key and key not in data)
                or (not key and any(m in data for m in SHEETS_FOREIGN))
            )
            if poisoned:
                err = "кэш отдал чужой ответ"
                log("sheets %.1fs %s poisoned, retry" % (time.time() - t0, action))
                if action in ("write", "delete"):
                    break
                continue
            if data.get("ok") and action in SHEETS_READ:
                _sheets_cache[action] = (time.time(), data)
            ingest_cash(data)
            return data
        except Exception as e:
            err = str(e)
            log("sheets fail %.1fs %s try%d: %s" % (time.time() - t0, action, attempt, e))
            low = err.lower()
            if action in ("write", "delete") and (
                "timed out" in low or "timeout" in low
                or "deadline" in low or "redirects" in low
            ):
                # google мог уже выполнить запись — вслепую не повторяем
                break
            # 404/чужой кэш — сразу ещё раз. пауза только если google завис
            if "timed out" in low or "timeout" in low:
                time.sleep(0.3)
            continue
    log("sheets err %s after %.1fs: %s" % (action, time.time() - t_all, err[:120]))
    return {"ok": False, "error": err[:80]}


def _sheets_keepwarm():
    # не блокируем пользователя: короткий дедлайн, низкий приоритет, кэш с диска.
    time.sleep(0.3)
    _unsold_from_disk()
    _lots_from_disk()
    while True:
        try:
            age_u = time.time() - (_UNSOLD.get("t") or 0)
            age_l = time.time() - (_LOTS.get("t") or 0)
            if _UNSOLD.get("items") is None or age_u > 90:
                sheets_call({"action": "setup"}, attempts=1, deadline_s=5, priority="warm")
                _unsold_refresh(block=False, priority="warm")
            if _LOTS.get("items") is None or age_l > 90:
                _lots_refresh(block=False, priority="warm")
        except Exception as e:
            log("sheets warm fail: %s" % e)
        time.sleep(45)


def push_sheets(deal: dict) -> str:
    r = sheets_call(dict(deal, action="write"))
    if r.get("ok"):
        return "ok"
    if r.get("error") == "local":
        return "local"
    return str(r.get("error") or "fail")[:80]


def _unsold_from_disk() -> None:
    if _UNSOLD["items"] is not None:
        return
    try:
        raw = json.loads(UNSOLD_PATH.read_text(encoding="utf-8"))
        items = raw.get("items")
        if isinstance(items, list):
            _UNSOLD["items"] = items
            _UNSOLD["t"] = float(raw.get("t") or 0)
            log("unsold disk %s шт. age=%.0fs" % (len(items), time.time() - _UNSOLD["t"]))
    except Exception:
        pass


def _unsold_to_disk(items: list) -> None:
    try:
        UNSOLD_PATH.write_text(
            json.dumps({"t": time.time(), "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _unsold_set(items: list) -> None:
    _UNSOLD["items"] = items
    _UNSOLD["t"] = time.time()
    _unsold_to_disk(items)


def _unsold_drop_row(row) -> None:
    try:
        row = int(row or 0)
    except (TypeError, ValueError):
        return
    items = _UNSOLD.get("items")
    if items and row:
        nxt = [it for it in items if int(it.get("row") or 0) != row]
        if len(nxt) != len(items):
            _unsold_set(nxt)
    lots = _LOTS.get("items")
    if lots and row:
        for it in lots:
            if int(it.get("row") or 0) == row:
                it["sold"] = True
        _lots_to_disk(lots)


def _unsold_refresh(block: bool = False, priority: str = "user") -> None:
    start = False
    with _UNSOLD["lock"]:
        if not _UNSOLD["busy"]:
            _UNSOLD["busy"] = True
            _UNSOLD["done"] = threading.Event()
            start = True
        ev = _UNSOLD["done"]

    def worker():
        try:
            r = sheets_call(
                {"action": "unsold"},
                attempts=(1 if priority == "warm" else 2),
                deadline_s=(5 if priority == "warm" else 15),
                priority=priority,
            )
            items = r.get("items") if r.get("ok") else None
            if isinstance(items, list):
                _unsold_set(items)
                log("unsold refresh %s шт." % len(items))
        except Exception as e:
            log("unsold refresh fail: %s" % e)
        finally:
            _UNSOLD["busy"] = False
            ev.set()

    if start:
        if block:
            worker()
            return
        threading.Thread(target=worker, name="unsold-refresh", daemon=True).start()
    if block:
        ev.wait(10)


def fetch_unsold() -> list | None:
    _unsold_from_disk()
    items = _UNSOLD.get("items")
    age = time.time() - (_UNSOLD.get("t") or 0)
    if items is not None:
        if age > 45:
            _unsold_refresh(block=False)
        return items
    _unsold_refresh(block=False)
    return None


def _lots_from_disk() -> None:
    if _LOTS["items"] is not None:
        return
    try:
        raw = json.loads(LOTS_PATH.read_text(encoding="utf-8"))
        items = raw.get("items")
        if isinstance(items, list):
            _LOTS["items"] = items
            _LOTS["t"] = float(raw.get("t") or 0)
            log("lots disk %s шт. age=%.0fs" % (len(items), time.time() - _LOTS["t"]))
    except Exception:
        pass


def _lots_to_disk(items: list) -> None:
    try:
        LOTS_PATH.write_text(
            json.dumps({"t": time.time(), "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _lots_set(items: list) -> None:
    _LOTS["items"] = items
    _LOTS["t"] = time.time()
    _lots_to_disk(items)


def _lots_refresh(block: bool = False, priority: str = "user") -> None:
    start = False
    with _LOTS["lock"]:
        if not _LOTS["busy"]:
            _LOTS["busy"] = True
            _LOTS["done"] = threading.Event()
            start = True
        ev = _LOTS["done"]

    def worker():
        try:
            r = sheets_call(
                {"action": "lots", "mode": "all"},
                attempts=(1 if priority == "warm" else 2),
                deadline_s=(5 if priority == "warm" else 15),
                priority=priority,
            )
            items = r.get("items") if r.get("ok") else None
            if isinstance(items, list):
                _lots_set(items)
                log("lots refresh %s шт." % len(items))
        except Exception as e:
            log("lots refresh fail: %s" % e)
        finally:
            _LOTS["busy"] = False
            ev.set()

    if start:
        if block:
            worker()
            return
        threading.Thread(target=worker, name="lots-refresh", daemon=True).start()
    if block:
        ev.wait(10)


def _filter_lots(items, mode="all", query=""):
    q = (query or "").lower().strip()
    out = []
    for it in items or []:
        if mode == "unsold" and it.get("sold"):
            continue
        if q:
            blob = " ".join(
                str(it.get(k) or "") for k in ("product", "category", "note", "buyer", "lot")
            ).lower()
            if q not in blob:
                continue
        out.append(it)
    return out


def fetch_lots(mode="all", query="") -> list | None:
    _lots_from_disk()
    items = _LOTS.get("items")
    age = time.time() - (_LOTS.get("t") or 0)
    if items is None:
        _unsold_from_disk()
        items = _UNSOLD.get("items")
        age = time.time() - (_UNSOLD.get("t") or 0)
    if items is None:
        _lots_refresh(block=False)
        _unsold_refresh(block=False)
        return None
    if age > 45:
        _lots_refresh(block=False)
    return _filter_lots(items, mode, query)


def remember_user(chat_id, u: dict) -> None:
    frm = (u.get("message") or u.get("callback_query") or {}).get("from") or {}
    name = ""
    if frm.get("username"):
        name = "@" + str(frm["username"])
    elif frm.get("first_name"):
        name = str(frm["first_name"])
    if not name:
        return
    users = SESSIONS.get(USERS_KEY)
    if not isinstance(users, dict):
        users = {}
        SESSIONS[USERS_KEY] = users
    if users.get(sid(chat_id)) != name:
        users[sid(chat_id)] = name
        persist_sess()


def user_name(chat_id) -> str:
    users = SESSIONS.get(USERS_KEY)
    if isinstance(users, dict):
        return str(users.get(sid(chat_id)) or "") or "неизвестный"
    return "неизвестный"


def kassa_what(deal: dict) -> str:
    t = deal.get("type")
    who = deal.get("who")
    if t == "buy":
        return "Закуп"
    if t == "sell":
        return "Продажа"
    if t == "cash":
        return {
            "from_danil": "Данил кинул в кассу",
            "to_danil": "Отдал Данилу",
            "self": "Себе",
            "other_in": "Деньги получил",
            "other_out": "Деньги потратил",
            "got": "Деньги получил",
            "spent": "Деньги потратил",
        }.get(who, "Прочее")
    return "Прочее"


def cash_dir_for(deal: dict) -> str | None:
    t = deal.get("type")
    who = deal.get("who")
    if t == "buy":
        return "out" if who == "me" else None
    if t == "sell":
        return "in" if who == "me" else None
    if t == "cash":
        if who in ("from_danil", "other_in", "got"):
            return "in"
        if who in ("to_danil", "self", "other_out", "spent"):
            return "out"
    return None


def line(label, value) -> str:
    return "<b>%s</b>  %s" % (label, value)


def card(deal: dict) -> str:
    t = deal.get("type")
    ico = {"buy": "🛒", "sell": "💸", "cash": "💵"}.get(t, "•")
    title = {"buy": "Закуп", "sell": "Продажа", "cash": "Деньги"}.get(t, "Сделка")
    rows = ["%s <b>%s</b>" % (ico, title), HR]
    if deal.get("category"):
        rows.append(line("Категория", cat_label(str(deal["category"]))))
    if deal.get("product"):
        extra = ""
        if deal.get("cost"):
            extra = "  <i>закуп " + fmt_money(deal["cost"]) + "</i>"
        rows.append(line("Товар", esc(deal["product"]) + extra))
    if deal.get("amount"):
        rows.append(line("Сумма", fmt_money(deal["amount"])))
    who = deal.get("who")
    if deal.get("type") == "buy":
        if who == "me":
            rows.append(line("Деньги", "потратил из кассы"))
        elif who == "danil":
            rows.append(line("Деньги", "Данил заплатил сам"))
    elif deal.get("type") == "sell":
        if who == "me":
            rows.append(line("Деньги", "получил в кассу"))
        elif who == "danil":
            rows.append(line("Деньги", "ушли Данилу напрямую"))
    else:
        who_map = {
            "from_danil": "Данил кинул в кассу",
            "to_danil": "отдал Данилу из кассы",
            "self": "забрал себе из кассы",
            "other_in": "деньги получил",
            "other_out": "деньги потратил",
            "got": "деньги получил",
            "spent": "деньги потратил",
        }
        if who:
            rows.append(line("Деньги", who_map.get(who, who)))
    if deal.get("delivery"):
        pay = "из кассы" if deal.get("delivery_pay") == "cash" else "Данил"
        rows.append(line("Доставка", fmt_money(deal["delivery"]) + " · " + pay))
    elif deal.get("type") in ("buy", "sell"):
        rows.append(line("Доставка", "нет"))
    if deal.get("consumable"):
        pay = "из кассы" if deal.get("consumable_pay") == "cash" else "Данил"
        rows.append(line("Расходники", fmt_money(deal["consumable"]) + " · " + pay))
    elif deal.get("type") in ("buy", "sell"):
        rows.append(line("Расходники", "нет"))
    if deal.get("role"):
        rows.append(line("Роль", esc(deal["role"])))
    if deal.get("lot"):
        rows.append(line("Лот", esc(deal["lot"])))
    if deal.get("place"):
        rows.append(line("Где", esc(deal["place"])))
    if deal.get("buyer"):
        rows.append(line("Кому", esc(deal["buyer"])))
    if deal.get("date"):
        rows.append(line("Дата", deal["date"]))
    if deal.get("note"):
        rows.append(line("Примечание", esc(deal["note"])))
    direction = cash_dir_for(deal)
    rows.append(HR)
    if direction == "in":
        rows.append(line("Касса", "+" + fmt_money(deal["amount"])))
    elif direction == "out":
        rows.append(line("Касса", "−" + fmt_money(deal["amount"])))
    else:
        rows.append(line("Касса", "без движения"))
    return "\n".join(rows)


def menu_text(info=None) -> str:
    info = info or peek_balance()
    hand = fmt_money(info.get("hand") or 0)
    src = "как в таблице «На руках»" if info.get("source") == "sheet" else "по записям бота"
    return (
        "<b>Учёт железа</b>\n"
        + HR
        + "\nНа руках    <code>"
        + hand
        + "</code>\n<i>"
        + src
        + "</i>\n\n"
        + "кнопки внизу — закуп, продажа, лот, деньги, касса"
    )


def balance_text(info=None) -> str:
    info = info or peek_balance()
    hand = fmt_money(info.get("hand") or 0)
    start = fmt_money(info.get("start") or 0)
    inn = fmt_money(info.get("inn") or 0)
    out = fmt_money(info.get("out") or 0)
    now = datetime.now().strftime("%H:%M")
    src = (
        "таблица, ячейка «На руках»"
        if info.get("source") == "sheet"
        else "пока по записям бота"
    )
    rows = [
        "<b>Касса</b>  <i>" + now + "</i>",
        HR,
        "На руках     <code>" + hand + "</code>",
        "Старт        " + start,
        "Пришло       " + inn,
        "Ушло         " + out,
        "<i>" + src + "</i>",
    ]
    computed = info.get("computed")
    hand_n = info.get("hand")
    if (
        computed is not None
        and hand_n is not None
        and abs(int(computed) - int(hand_n)) >= 1
    ):
        rows.append(
            "<i>по листу Касса выходит "
            + fmt_money(computed)
            + " — глянь движения</i>"
        )
    recent = info.get("recent") or []
    if recent:
        rows.append("")
        rows.append("<b>Последние движения</b>")
        for it in recent[:6]:
            amt = ""
            if it.get("inn"):
                amt = "+" + fmt_money(it["inn"])
            elif it.get("out"):
                amt = "−" + fmt_money(it["out"])
            what = esc(it.get("what") or "")
            comment = esc(str(it.get("comment") or "")[:40])
            ds = esc(str(it.get("date") or ""))
            bit = (ds + "  " if ds else "") + what
            if amt:
                bit += "  " + amt
            rows.append(bit)
            if comment:
                rows.append("<i>" + comment + "</i>")
    return "\n".join(rows)


def ui(chat_id, text, markup=None, force_new=False):
    s = sess(chat_id)
    mid = s.get("msg_id")
    if not force_new and mid:
        r = edit_message(chat_id, mid, text, markup)
        if r.get("ok"):
            persist_sess()
            return r
        err = str(r.get("error") or "").lower()
        if "not modified" in err:
            return r
        # старое сообщение не отредактировалось — шлём новое вниз, не молчим
    r = send(chat_id, text, markup)
    if r.get("ok"):
        try:
            s["msg_id"] = r["result"]["message_id"]
            persist_sess()
        except Exception:
            pass
    else:
        log("ui send fail: " + str(r.get("error") or "")[:200])
    return r


def go_menu(chat_id, prefix=""):
    clear_sess(chat_id)
    body = menu_text(peek_balance())
    if prefix:
        body = prefix + "\n\n" + body
    send(chat_id, body, reply_kb())
    if time.time() - (_CASH.get("t") or 0) > 30:
        bg(fetch_balance, True)


def start_deal(chat_id, kind=None):
    s = sess(chat_id)
    mid = s.get("msg_id")
    SESSIONS[sid(chat_id)] = {"step": "kind", "type": kind, "msg_id": mid}
    persist_sess()
    if kind == "buy":
        ask_product(chat_id)
        return
    if kind == "sell":
        ask_sell_how(chat_id)
        return
    ui(
        chat_id,
        "<b>Новая сделка</b>\n\nчто оформляем?",
        kb(
            [
                [btn("🛒 Закуп", "k:buy"), btn("💸 Продажа", "k:sell")],
                cancel_row(),
            ]
        ),
    )


def go_back(chat_id):
    s = sess(chat_id)
    step = s.get("step") or "idle"
    kind = s.get("type")
    if kind == "undo":
        if step == "undo_list":
            start_undo(chat_id)
        else:
            show_undo_list(chat_id, s.get("undo_page") or 0)
        return
    if kind == "edit":
        if step in ("edit_item", "edit_how", "pick", "search", "sell_cat"):
            start_edit(chat_id)
            return
        show_edit_item(chat_id)
        return
    if kind == "buy":
        if step == "product":
            start_deal(chat_id)
        elif step == "buy_cat":
            ask_product(chat_id)
        elif step == "amount":
            ask_buy_category(chat_id)
        elif step == "who":
            ask_amount(chat_id, force_new=False)
        elif step in ("extras_d", "delivery_amt"):
            ask_who_deal(chat_id)
        elif step in ("extras_c", "cons_amt"):
            ask_delivery(chat_id)
        elif step == "lot":
            ask_consumable(chat_id)
        elif step in ("role", "role_other"):
            ask_lot(chat_id)
        elif step in ("date", "date_custom"):
            ask_role(chat_id)
        elif step == "note":
            ask_date(chat_id, force_new=False)
        elif step == "confirm":
            ask_note(chat_id)
        else:
            start_deal(chat_id)
        return
    if kind == "sell":
        if step in ("sell_how", "kind"):
            start_deal(chat_id)
        elif step in ("sell_cat", "search"):
            ask_sell_how(chat_id)
        elif step == "pick":
            if s.get("query"):
                ask_search(chat_id)
            else:
                show_categories(chat_id, for_sell=True)
        elif step == "amount":
            show_item_list(chat_id, s.get("page") or 0)
        elif step == "who":
            ask_amount(chat_id, force_new=False)
        elif step in ("extras_d", "delivery_amt"):
            ask_who_deal(chat_id)
        elif step in ("extras_c", "cons_amt"):
            ask_delivery(chat_id)
        elif step == "place":
            ask_consumable(chat_id)
        elif step in ("buyer", "place_other"):
            ask_place(chat_id)
        elif step in ("role", "role_other"):
            ask_buyer(chat_id)
        elif step in ("date", "date_custom"):
            ask_role(chat_id)
        elif step == "note":
            ask_date(chat_id, force_new=False)
        elif step == "confirm":
            ask_note(chat_id)
        else:
            start_deal(chat_id)
        return
    if kind == "undo":
        go_menu(chat_id)
        return
    if kind == "cash":
        if step == "amount":
            start_cash(chat_id)
        elif step in ("date", "date_custom"):
            ask_amount(chat_id, force_new=False)
        elif step == "note":
            ask_date(chat_id, force_new=False)
        elif step == "confirm":
            ask_note(chat_id)
        else:
            go_menu(chat_id)
        return
    go_menu(chat_id)


def start_cash(chat_id):
    s = sess(chat_id)
    SESSIONS[sid(chat_id)] = {"step": "cash_what", "type": "cash", "msg_id": s.get("msg_id")}
    persist_sess()
    ui(
        chat_id,
        screen("cash", "cash_what", "что с деньгами?"),
        kb(
            [
                [btn("Деньги получил", "w:got")],
                [btn("Деньги потратил", "w:spent")],
                [btn("Данил кинул в кассу", "w:from_danil")],
                cancel_row(),
            ]
        ),
        force_new=True,
    )


def undo_explain(deal: dict) -> str:
    t = deal.get("type")
    if t == "sell":
        return "продажа снимется, товар снова будет висеть. если деньги зашли в кассу — сторнирую."
    if t == "buy":
        return "лот удалится из таблицы, закуп по кассе сторнирую."
    return "движение по кассе сторнирую."


def start_undo(chat_id):
    acts = undoable_actions()
    if not acts:
        send(chat_id, "отменять нечего — последнего действия нет.", reply_kb())
        return
    deal = acts[0]
    s = sess(chat_id)
    SESSIONS[sid(chat_id)] = {
        "step": "undo_confirm",
        "type": "undo",
        "undo_item": deal,
        "undo_list": acts,
        "undo_page": 0,
        "msg_id": s.get("msg_id"),
    }
    persist_sess()
    rows = [[btn("да, отменить это", "un:yes")]]
    if len(acts) > 1:
        rows.append([btn("выбрать из списка", "un:list")])
    rows.append([btn("нет", "un:no")])
    ui(
        chat_id,
        "<b>Отменить последнее?</b>\n\n"
        + card(deal)
        + "\n\n"
        + undo_explain(deal)
        + ("\n\n<i>если это не то — выбери из списка</i>" if len(acts) > 1 else ""),
        kb(rows),
        force_new=True,
    )


UNDO_PAGE = 6


def show_undo_list(chat_id, page=0):
    s = sess(chat_id)
    acts = s.get("undo_list") or undoable_actions()
    if not acts:
        send(chat_id, "отменять нечего.", reply_kb())
        return
    s["undo_list"] = acts
    s["step"] = "undo_list"
    page = max(0, int(page or 0))
    max_page = max(0, (len(acts) - 1) // UNDO_PAGE)
    if page > max_page:
        page = max_page
    s["undo_page"] = page
    persist_sess()
    chunk = acts[page * UNDO_PAGE : (page + 1) * UNDO_PAGE]
    rows = []
    base = page * UNDO_PAGE
    for i, d in enumerate(chunk):
        rows.append([btn(undo_label(d), "un:i:%d" % (base + i))])
    nav = []
    if page > 0:
        nav.append(btn("‹", "un:pg:%d" % (page - 1)))
    nav.append(btn("%d / %d" % (page + 1, max_page + 1), "un:pg:%d" % page))
    if page < max_page:
        nav.append(btn("›", "un:pg:%d" % (page + 1)))
    rows.append(nav)
    rows.append([btn("‹ к последнему", "un:last"), btn("✕ Отмена", "m:cancel")])
    ui(
        chat_id,
        "<b>Что отменить?</b>\n\nпоследние %d действий. нажми нужное." % len(acts),
        kb(rows),
    )


def confirm_undo_pick(chat_id, idx: int):
    s = sess(chat_id)
    acts = s.get("undo_list") or undoable_actions()
    if idx < 0 or idx >= len(acts):
        show_undo_list(chat_id, s.get("undo_page") or 0)
        return
    deal = acts[idx]
    s["undo_item"] = deal
    s["undo_list"] = acts
    s["step"] = "undo_confirm"
    persist_sess()
    ui(
        chat_id,
        "<b>Отменить это?</b>\n\n" + card(deal) + "\n\n" + undo_explain(deal),
        kb(
            [
                [btn("да, отменить", "un:yes")],
                [btn("‹ к списку", "un:list")],
                [btn("нет", "un:no")],
            ]
        ),
    )


def perform_undo(chat_id):
    s = sess(chat_id)
    deal = s.get("undo_item") or last_action()
    if not deal:
        go_menu(chat_id, "уже нечего отменять.")
        return
    ui(chat_id, "отменяю…", kb([cancel_row()]))
    bg(_perform_undo_body, chat_id, deal)


def _perform_undo_body(chat_id, deal):
    t = deal.get("type")
    extras = extras_for(deal)
    row = resolve_row(deal)
    notes = []
    if t == "sell":
        if row:
            r = sheets_call(
                {
                    "action": "update",
                    "sheet_row": row,
                    "fields": {"clear_sell": True, "place": "", "buyer": ""},
                }
            )
            if r.get("ok"):
                notes.append("продажа снята, товар снова висит")
            else:
                notes.append("в таблице продажу не снял — глянь строку глазами")
        else:
            notes.append("строку в таблице не нашёл, кассу всё равно сторнирую")
        if deal.get("cash_dir") == "in":
            reverse_cash_row(deal, "Отмена продажи", "out")
            notes.append("касса −" + fmt_money(deal.get("amount") or 0))
        elif deal.get("cash_dir") == "out":
            reverse_cash_row(deal, "Отмена продажи", "in")
            notes.append("касса +" + fmt_money(deal.get("amount") or 0))
        else:
            append_deal(
                {
                    "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "type": "cash",
                    "product": deal.get("product") or "",
                    "amount": 0,
                    "kassa_what": "Отмена продажи",
                    "cash_dir": None,
                    "undoes": deal.get("id") or "",
                    "comment": "продажа снята, касса не тронута",
                }
            )
            notes.append("касса не тронута — продажа была мимо кассы")
        for ex in extras:
            if ex.get("cash_dir") == "out":
                reverse_cash_row(ex, "Отмена " + (ex.get("kassa_what") or "расхода"), "in")
            elif ex.get("cash_dir") == "in":
                reverse_cash_row(ex, "Отмена " + (ex.get("kassa_what") or "прихода"), "out")
    elif t == "buy":
        payload = {
            "action": "delete",
            "sheet_row": row,
            "product": deal.get("product") or "",
            "cost": deal.get("amount") or deal.get("cost") or 0,
            "sale": 0,
        }
        r = sheets_call(payload)
        if r.get("ok"):
            for x in r.get("reversed") or []:
                append_deal(
                    {
                        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "type": "cash",
                        "product": x.get("product") or deal.get("product") or "",
                        "amount": int(x.get("amount") or 0),
                        "who": "other_in" if x.get("dir") == "in" else "other_out",
                        "date": today_str(),
                        "kassa_what": x.get("what") or "Отмена закупа",
                        "cash_dir": x.get("dir"),
                        "comment": "отмена последнего",
                        "undoes": deal.get("id") or "",
                    }
                )
            nrev = len(r.get("reversed") or [])
            notes.append("лот удален")
            if nrev:
                notes.append("касса: сторно %s запис." % nrev)
            else:
                notes.append("по кассе движений с этим товаром не нашёл")
        else:
            if deal.get("cash_dir") == "out":
                reverse_cash_row(deal, "Отмена закупа", "in")
            elif deal.get("cash_dir") == "in":
                reverse_cash_row(deal, "Отмена закупа", "out")
            notes.append("в таблицу не ушло, кассу сторнировал у бота")
        append_deal(
            {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "ts": datetime.now().isoformat(timespec="seconds"),
                "type": "cash",
                "product": deal.get("product") or "",
                "amount": 0,
                "kassa_what": "Отмена закупа",
                "undoes": deal.get("id") or "",
                "comment": "отмена последнего закупа",
            }
        )
    else:
        direction = deal.get("cash_dir")
        if direction == "in":
            reverse_cash_row(deal, "Отмена: " + (deal.get("kassa_what") or "деньги"), "out")
            notes.append("касса −" + fmt_money(deal.get("amount") or 0))
        elif direction == "out":
            reverse_cash_row(deal, "Отмена: " + (deal.get("kassa_what") or "деньги"), "in")
            notes.append("касса +" + fmt_money(deal.get("amount") or 0))
        else:
            append_deal(
                {
                    "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                    "type": "cash",
                    "undoes": deal.get("id") or "",
                    "kassa_what": "Отмена",
                    "comment": "нечего сторнировать по кассе",
                }
            )
            notes.append("по кассе нечего сторнировать")
    _CASH["t"] = 0
    _CASH["data"] = None
    info = local_balance()
    go_menu(
        chat_id,
        "отменил.\n\n"
        + card(deal)
        + "\n\n"
        + "\n".join(notes)
        + "\n\nна руках  <code>"
        + fmt_money(info.get("hand") or 0)
        + "</code>",
    )


def start_edit(chat_id):
    SESSIONS[sid(chat_id)] = {"step": "edit_how", "type": "edit"}
    persist_sess()
    ui(
        chat_id,
        "<b>Изменить лот</b>\n\nкакой правим?",
        kb(
            [
                [btn("📦 Не проданные", "eh:unsold")],
                [btn("🕒 Последние", "eh:all")],
                [btn("🔍 Поиск", "eh:search")],
                cancel_row(),
            ]
        ),
        force_new=True,
    )


def ask_product(chat_id):
    sess(chat_id)["step"] = "product"
    persist_sess()
    ui(
        chat_id,
        screen("buy", "product", "напиши товар, как в таблице.\nнапример: <code>i9 9900k</code>"),
        kb([nav_row()]),
        force_new=True,
    )


def filtered_items(s) -> list:
    items = s.get("unsold") or s.get("lots") or []
    cat = s.get("filter_cat")
    q = (s.get("query") or "").lower().strip()
    out = []
    for it in items:
        if cat and cat != "all":
            if str(it.get("category") or "Другое") != cat:
                continue
        if q:
            blob = " ".join(
                str(it.get(k) or "") for k in ("product", "category", "note", "buyer", "lot")
            ).lower()
            if q not in blob:
                continue
        out.append(it)
    return out


def cat_counts(items) -> dict:
    counts = {}
    for it in items:
        c = str(it.get("category") or "Другое")
        counts[c] = counts.get(c, 0) + 1
    return counts


def show_categories(chat_id, for_sell=True):
    s = sess(chat_id)
    items = s.get("unsold") or []
    counts = cat_counts(items)
    s["step"] = "sell_cat" if for_sell else "edit_cat"
    persist_sess()
    rows = []
    pair = []
    names = [c for c in CATS if counts.get(c)]
    extra = [c for c in sorted(counts) if c not in CATS]
    for name in names + extra:
        pair.append(btn(cat_label(name, counts[name]), "c:%s" % name[:40]))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([btn("все · %s" % len(items), "c:all")])
    if for_sell:
        rows.append([btn("🔍 поиск по слову", "m:search")])
    rows.append(nav_row())
    kind = s.get("type") or "sell"
    step = "sell_cat" if for_sell else "sell_cat"
    title = "категория — потом список того, что висит"
    if not for_sell:
        title = "категория лотов"
    ui(chat_id, screen(kind, step, title), kb(rows))


def item_btn_label(it) -> str:
    name = str(it.get("product") or "?")
    cost = it.get("cost") or 0
    label = "%s · %s" % (name, fmt_money(cost))
    if len(label) > 64:
        name = name[: max(8, 64 - len(fmt_money(cost)) - 5)]
        label = "%s · %s" % (name, fmt_money(cost))
    return label[:64]


def show_item_list(chat_id, page=0, force_new=False):
    s = sess(chat_id)
    items = filtered_items(s)
    kind = s.get("type") or "sell"
    if not items:
        ui(chat_id, screen(kind, "pick", "ничего не нашёл."), kb([[btn("‹ к категориям", "m:cats")], cancel_row()]))
        return
    page = max(0, page)
    max_page = max(0, (len(items) - 1) // PAGE)
    if page > max_page:
        page = max_page
    s["page"] = page
    s["step"] = "pick"
    persist_sess()
    chunk = items[page * PAGE : (page + 1) * PAGE]
    rows = []
    prefix = "u" if s.get("type") == "sell" else "e"
    for it in chunk:
        rows.append([btn(item_btn_label(it), "%s:%s" % (prefix, it.get("row")))])
    nav = []
    if page > 0:
        nav.append(btn("‹", "pg:%d" % (page - 1)))
    nav.append(btn("%d / %d" % (page + 1, max_page + 1), "pg:%d" % page))
    if page < max_page:
        nav.append(btn("›", "pg:%d" % (page + 1)))
    rows.append(nav)
    extra = [btn("‹ категории", "m:cats"), btn("🔍 поиск", "m:search")]
    rows.append(extra)
    rows.append(nav_row())
    q = s.get("query") or ""
    cat = s.get("filter_cat")
    head = "что продаём" if s.get("type") == "sell" else "какой лот"
    bits = ["%s шт." % len(items)]
    if cat and cat != "all":
        bits.append(cat)
    if q:
        bits.append("«" + q + "»")
    ui(
        chat_id,
        screen(kind, "pick", "<b>%s</b>\n%s" % (head, " · ".join(bits))),
        kb(rows),
        force_new=force_new,
    )


def load_unsold_or_fail(chat_id, then="cats") -> bool:
    items = fetch_unsold()
    if items is not None:
        sess(chat_id)["unsold"] = items
        persist_sess()
        if not items:
            send(chat_id, "в таблице нет не проданного товара.", reply_kb())
            return False
        return True
    ui(chat_id, screen("sell", "sell_how", "подгружаю непроданное…"), kb([nav_row()]))

    def work():
        _unsold_refresh(block=True)
        items = _UNSOLD.get("items")
        if items is None:
            ui(
                chat_id,
                screen("sell", "sell_how", "таблица сейчас не отвечает.\nнажми ещё раз."),
                kb(
                    [
                        [btn("📂 по категории", "m:cats")],
                        [btn("🔍 поиск по слову", "m:search")],
                        [btn("📋 все не проданные", "c:all")],
                        nav_row(),
                    ]
                ),
            )
            return
        sess(chat_id)["unsold"] = items
        persist_sess()
        if not items:
            send(chat_id, "в таблице нет не проданного товара.", reply_kb())
            return
        if then == "search":
            ask_search(chat_id)
        elif then == "all":
            show_item_list(chat_id, 0)
        else:
            show_categories(chat_id, for_sell=True)

    bg(work)
    return False


def ask_sell_how(chat_id):
    s = sess(chat_id)
    s["step"] = "sell_how"
    persist_sess()
    _unsold_from_disk()
    _unsold_refresh(block=False)
    ui(
        chat_id,
        screen("sell", "sell_how", "как ищем, что продаём?"),
        kb(
            [
                [btn("📂 по категории", "m:cats")],
                [btn("🔍 поиск по слову", "m:search")],
                [btn("📋 все не проданные", "c:all")],
                nav_row(),
            ]
        ),
        force_new=True,
    )


def ask_search(chat_id):
    sess(chat_id)["step"] = "search"
    persist_sess()
    kind = sess(chat_id).get("type") or "sell"
    ui(
        chat_id,
        screen(kind, "search", "напиши кусок названия.\nнапример: <code>580</code> или <code>xeon</code>"),
        kb([nav_row()]),
        force_new=True,
    )


def ask_buy_category(chat_id):
    s = sess(chat_id)
    s["step"] = "buy_cat"
    guess = guess_category(s.get("product") or "")
    persist_sess()
    rows = [[btn("как в названии: " + guess, "c:" + guess)]]
    pair = []
    for name in CATS:
        if name == guess:
            continue
        pair.append(btn(cat_label(name), "c:" + name))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(nav_row())
    prod = esc(s.get("product") or "")
    ui(
        chat_id,
        screen("buy", "buy_cat", "<b>%s</b>\n\nкатегория?" % prod),
        kb(rows),
    )


def ask_amount(chat_id, force_new=True):
    s = sess(chat_id)
    s["step"] = "amount"
    persist_sess()
    kind = s.get("type")
    if kind == "sell":
        hint = "напиши цену продажи, только число."
        if s.get("cost"):
            hint += "\nзакуп был <code>" + fmt_money(s["cost"]) + "</code>"
        if s.get("product"):
            hint = "<b>" + esc(s["product"]) + "</b>\n\n" + hint
        ui(chat_id, screen("sell", "amount", hint), kb([nav_row()]), force_new=force_new)
        return
    if kind == "cash":
        ui(
            chat_id,
            screen("cash", "amount", "напиши сумму, только число.\nнапример: <code>3000</code>"),
            kb([nav_row()]),
            force_new=force_new,
        )
        return
    ui(
        chat_id,
        screen("buy", "amount", "напиши сумму закупа, только число.\nнапример: <code>8500</code>"),
        kb([nav_row()]),
        force_new=force_new,
    )


def ask_who_deal(chat_id):
    s = sess(chat_id)
    s["step"] = "who"
    persist_sess()
    if s.get("type") == "buy":
        text = screen("buy", "who", "откуда деньги на закуп?")
        rows = [
            [btn("потратил из кассы", "w:me")],
            [btn("Данил заплатил сам", "w:danil")],
            nav_row(),
        ]
    else:
        text = screen("sell", "who", "куда ушли деньги за продажу?")
        rows = [
            [btn("получил в кассу", "w:me")],
            [btn("ушли Данилу напрямую", "w:danil")],
            nav_row(),
        ]
    ui(chat_id, text, kb(rows))


def extras_kb(kind):
    prefix = "ex:%s:" % kind
    return kb(
        [
            [btn("нет", prefix + "no")],
            [btn("да, из кассы", prefix + "cash")],
            [btn("да, Данил платил", prefix + "danil")],
            nav_row(),
        ]
    )


def ask_delivery(chat_id):
    s = sess(chat_id)
    s["step"] = "extras_d"
    persist_sess()
    ui(chat_id, screen(s.get("type"), "extras_d", "доставка была?"), extras_kb("d"))


def ask_consumable(chat_id):
    s = sess(chat_id)
    s["step"] = "extras_c"
    persist_sess()
    ui(
        chat_id,
        screen(s.get("type"), "extras_c", "расходники были?\n<i>пакет, термопаста, кабель, мелкое</i>"),
        extras_kb("c"),
    )


def ask_extra_amount(chat_id, kind):
    s = sess(chat_id)
    s["step"] = "delivery_amt" if kind == "d" else "cons_amt"
    persist_sess()
    if kind == "d":
        title = "сумма доставки, только число."
        step = "delivery_amt"
    else:
        title = "сумма расходников, только число."
        step = "cons_amt"
    ui(chat_id, screen(s.get("type"), step, title), kb([nav_row()]), force_new=True)


def after_extras(chat_id):
    if sess(chat_id).get("type") == "buy":
        ask_lot(chat_id)
    else:
        ask_place(chat_id)


def ask_role(chat_id):
    sess(chat_id)["step"] = "role"
    persist_sess()
    rows = []
    for code, full, _pct in ROLES:
        rows.append([btn(full, "rl:" + code)])
    rows.append([btn("другое / свой текст", "rl:other")])
    rows.append([btn("пропустить", "rl:skip")])
    rows.append(nav_row())
    ui(
        chat_id,
        screen(sess(chat_id).get("type"), "role", "роль Матвея в этой сделке?"),
        kb(rows),
    )


def ask_lot(chat_id):
    sess(chat_id)["step"] = "lot"
    persist_sess()
    ui(
        chat_id,
        screen("buy", "lot", "чей лот?\n<i>чьё железо, не чей карман</i>"),
        kb([[btn("Данил", "l:Данил"), btn("Матвей", "l:Матвей")], nav_row()]),
    )


def ask_place(chat_id):
    sess(chat_id)["step"] = "place"
    persist_sess()
    rows = []
    pair = []
    for code, name in PLACES:
        pair.append(btn(name, "pl:" + code))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(nav_row())
    ui(chat_id, screen("sell", "place", "где продали?"), kb(rows))


def ask_buyer(chat_id):
    sess(chat_id)["step"] = "buyer"
    persist_sess()
    ui(
        chat_id,
        screen(
            "sell",
            "buyer",
            "кому продали?\nтелефон, @telegram, ссылка VK — как удобно.",
        ),
        kb([[btn("пропустить", "b:skip")], nav_row()]),
        force_new=True,
    )


def date_kb():
    rows = []
    row = []
    for delta in range(-3, 4):
        d = date.today() + timedelta(days=delta)
        ds = d.strftime("%d.%m.%Y")
        label = d.strftime("%d.%m")
        if delta == 0:
            label = "• сегодня " + label
        elif delta == -1:
            label = "вчера " + label
        elif delta == 1:
            label = "завтра " + label
        row.append(btn(label, "dt:" + ds))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("📅 другая дата", "dt:other")])
    rows.append(nav_row())
    return kb(rows)


def ask_date(chat_id, force_new=False):
    s = sess(chat_id)
    s["step"] = "date"
    persist_sess()
    ui(
        chat_id,
        screen(s.get("type"), "date", "дата?\nтри дня назад и вперёд, или введи свою."),
        date_kb(),
        force_new=force_new,
    )


def ask_date_custom(chat_id):
    s = sess(chat_id)
    s["step"] = "date_custom"
    persist_sess()
    ui(
        chat_id,
        screen(s.get("type"), "date_custom", "напиши дату: <code>06.09.2026</code> или <code>6.09</code>"),
        kb([nav_row()]),
        force_new=True,
    )


def ask_note(chat_id):
    s = sess(chat_id)
    s["step"] = "note"
    persist_sess()
    ui(
        chat_id,
        screen(s.get("type"), "note", "примечание? можно пропустить."),
        skip_note_kb(),
        force_new=True,
    )


def ask_confirm(chat_id):
    s = sess(chat_id)
    s["step"] = "confirm"
    persist_sess()
    ui(
        chat_id,
        screen(s.get("type"), "confirm", card(s) + "\n\nвсё верно?"),
        kb([[btn("✓  записать", "ok")], nav_row()]),
    )


def lot_card(it: dict) -> str:
    name = esc(it.get("product") or "?")
    rows = ["📦 <b>" + name + "</b>", HR]
    if it.get("category"):
        rows.append(line("Категория", cat_label(str(it["category"]))))
    if it.get("lot"):
        rows.append(line("Лот", esc(it["lot"])))
    if it.get("buy"):
        rows.append(line("Купили", str(it["buy"])))
    if it.get("cost"):
        rows.append(line("Закуп", fmt_money(it["cost"])))
    if it.get("sold"):
        rows.append(line("Продали", str(it.get("sell") or "")))
        if it.get("sale"):
            rows.append(line("Продажа", fmt_money(it["sale"])))
    else:
        rows.append(line("Статус", "висит"))
    if it.get("place"):
        rows.append(line("Где", esc(it["place"])))
    if it.get("buyer"):
        rows.append(line("Кому", esc(it["buyer"])))
    if it.get("delivery"):
        rows.append(line("Доставка", fmt_money(it["delivery"])))
    if it.get("consumable"):
        rows.append(line("Расходники", fmt_money(it["consumable"])))
    if it.get("role"):
        rows.append(line("Роль", esc(it["role"])))
    if it.get("note"):
        rows.append(line("Примечание", esc(it["note"])))
    rows.append(HR)
    rows.append("<i>строка " + str(it.get("row")) + "</i>")
    return "\n".join(rows)


def show_edit_item(chat_id):
    s = sess(chat_id)
    it = s.get("edit_item") or {}
    s["step"] = "edit_item"
    persist_sess()
    rows = [
        [btn("📅 покупка", "xf:buy"), btn("📅 продажа", "xf:sell")],
        [btn("₽ закуп", "xf:cost"), btn("₽ продажа", "xf:sale")],
        [btn("название", "xf:name"), btn("категория", "xf:cat")],
        [btn("где / кому", "xf:place"), btn("примечание", "xf:note")],
        [btn("роль Матвея", "xf:role")],
        [btn("доставка", "xf:deliv"), btn("расходники", "xf:cons")],
        [btn("🗑  удалить лот", "xf:del")],
        cancel_row(),
    ]
    ui(chat_id, lot_card(it) + "\n\nчто меняем?", kb(rows))


def commit(chat_id):
    s = sess(chat_id)
    if not s.get("type"):
        # повторное нажатие после уже записанной сделки: сессия чиста
        go_menu(chat_id, "нет активной сделки — начни заново.")
        return
    deal = {
        "id": datetime.now().strftime("%Y%m%d%H%M%S"),
        "ts": datetime.now().isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "type": s.get("type"),
        "product": s.get("product") or "",
        "amount": s.get("amount") or 0,
        "who": s.get("who"),
        "lot": s.get("lot") or "",
        "date": s.get("date") or today_str(),
        "comment": s.get("product") or "",
        "sheet_row": s.get("sheet_row") or "",
        "cost": s.get("cost") or 0,
        "category": s.get("category") or "",
        "place": s.get("place") or "",
        "buyer": s.get("buyer") or "",
        "note": s.get("note") or "",
        "role": s.get("role") or "",
        "role_pct": s.get("role_pct") if s.get("role_pct") is not None else "",
        "delivery": s.get("delivery") or 0,
        "delivery_pay": s.get("delivery_pay") or "",
        "consumable": s.get("consumable") or 0,
        "consumable_pay": s.get("consumable_pay") or "",
    }
    deal["kassa_what"] = kassa_what(deal)
    deal["cash_dir"] = cash_dir_for(deal)
    append_deal(deal)
    extras = []
    for what, amt, pay in (
        ("Доставка", s.get("delivery") or 0, s.get("delivery_pay")),
        ("Расходник", s.get("consumable") or 0, s.get("consumable_pay")),
    ):
        if amt and pay == "cash":
            extra_d = dict(deal)
            extra_d["id"] = datetime.now().strftime("%Y%m%d%H%M%S")
            extra_d["type"] = "cash"
            extra_d["amount"] = int(amt)
            extra_d["kassa_what"] = what
            extra_d["cash_dir"] = "out"
            extra_d["who"] = "other_out"
            extra_d["comment"] = what + " · " + (deal.get("product") or "")
            append_deal(extra_d)
            extras.append(extra_d)
    if deal.get("type") == "sell":
        _unsold_drop_row(deal.get("sheet_row"))
    _CASH["t"] = 0
    _CASH["data"] = None
    info = local_balance()
    go_menu(
        chat_id,
        "✓  записал\n\n"
        + card(deal)
        + "\n\nна руках  <code>"
        + fmt_money(info.get("hand") or 0)
        + "</code>"
        + "\n<i>пишу в таблицу…</i>"
        + "\n\nесли человек слился — «Отменить последнее»",
    )

    def work():
        wr = sheets_call(dict(deal, action="write"))
        for extra_d in extras:
            push_sheets(extra_d)
        if wr.get("ok"):
            log("commit sheets ok row=%s" % wr.get("sheet_row"))
        elif wr.get("error") not in ("local",):
            send(chat_id, "сделка у бота есть, таблица не приняла — глянь таблицу или повтори.")

    bg(work)


def after_who(chat_id):
    s = sess(chat_id)
    if s.get("type") == "cash":
        ask_amount(chat_id)
    else:
        ask_delivery(chat_id)


def after_date(chat_id):
    ask_note(chat_id)


def set_date_and_continue(chat_id, ds: str):
    s = sess(chat_id)
    kind = s.get("edit_field")
    if s.get("type") == "edit" and kind in ("buy", "sell"):
        field = "buy_date" if kind == "buy" else "sell_date"
        s.setdefault("edit_item", {})[("buy" if kind == "buy" else "sell")] = ds
        s["edit_field"] = None
        persist_sess()
        show_edit_item(chat_id)
        row = s.get("edit_row")
        bg(sheets_call, {"action": "update", "sheet_row": row, "fields": {field: ds}})
        return
    s["date"] = ds
    persist_sess()
    after_date(chat_id)


def apply_edit_text(chat_id, text: str):
    s = sess(chat_id)
    field = s.get("edit_field")
    row = s.get("edit_row")
    fields = {}
    it = s.setdefault("edit_item", {})
    if field == "name":
        if len(text) < 2:
            ui(chat_id, "слишком коротко.", kb([nav_row()]))
            return
        fields["product"] = text[:80]
        it["product"] = text[:80]
    elif field == "cost":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, "напиши число, например 8500", kb([nav_row()]))
            return
        fields["cost"] = n
        it["cost"] = n
    elif field == "sale":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, "напиши число, например 8500", kb([nav_row()]))
            return
        fields["sale"] = n
        it["sale"] = n
        it["sold"] = True
    elif field == "note":
        fields["note"] = text[:200]
        it["note"] = text[:200]
    elif field == "buyer":
        fields["buyer"] = text[:80]
        it["buyer"] = text[:80]
    elif field == "place":
        fields["place"] = text[:40]
        it["place"] = text[:40]
    elif field == "role":
        fields["role"] = text[:80]
        it["role"] = text[:80]
    elif field == "delivery":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, "напиши число, например 400", kb([nav_row()]))
            return
        fields["delivery"] = n
        it["delivery"] = n
    elif field == "consumable":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, screen(s.get("type"), s.get("step") or "amount", "напиши число, например 150"), kb([nav_row()]))
            return
        fields["consumable"] = n
        it["consumable"] = n
    else:
        go_menu(chat_id, "не понял, что правим.")
        return
    s["edit_field"] = None
    persist_sess()
    show_edit_item(chat_id)
    bg(sheets_call, {"action": "update", "sheet_row": row, "fields": fields})


def normalize_cmd(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^\wа-яё /]+", " ", t, flags=re.I)
    return " ".join(t.split())


def show_balance(chat_id, via_ui=False):
    markup = kb([[btn("↻ обновить", "m:bal")], [btn("‹ меню", "m:cancel")]]) if via_ui else reply_kb()
    text = balance_text(peek_balance())
    if via_ui:
        ui(chat_id, text, markup)
    else:
        send(chat_id, text, markup)

    def refresh():
        info = fetch_balance(force=True)
        nxt = balance_text(info)
        if nxt == text:
            return
        if via_ui:
            ui(chat_id, nxt, markup)
        else:
            send(chat_id, nxt, markup)

    bg(refresh)


def on_text(chat_id, text: str):
    text = (text or "").strip()
    cmd = normalize_cmd(text)
    log("txt %s cmd=%s" % (text[:40], cmd[:40]))
    if text in ("/start", "/menu") or cmd in ("меню", "start"):
        go_menu(chat_id)
        return
    if text == "/cancel" or cmd == "отмена":
        go_menu(chat_id, "ок, отменил.")
        return
    if text in ("/balance", "/bal") or cmd in (
        "касса",
        "на руках",
        "сколько на руках",
        "баланс",
    ):
        show_balance(chat_id)
        return
    if text == "/edit" or cmd in ("лот", "изменить лот") or cmd.endswith(" лот"):
        start_edit(chat_id)
        return
    if cmd in ("закуп",):
        start_deal(chat_id, "buy")
        return
    if cmd in ("продажа", "продать"):
        start_deal(chat_id, "sell")
        return
    if cmd in ("деньги", "только деньги"):
        start_cash(chat_id)
        return
    if text == "/undo" or cmd in ("отменить последнее", "отменить действие"):
        start_undo(chat_id)
        return
    s = sess(chat_id)
    step = s.get("step") or "idle"
    if step == "product":
        if len(text) < 2:
            ui(chat_id, screen("buy", "product", "слишком коротко. напиши название товара."), kb([nav_row()]))
            return
        s["product"] = text[:80]
        persist_sess()
        ask_buy_category(chat_id)
        return
    if step == "amount":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, screen(s.get("type"), "amount", "не понял сумму. напиши число, например 8500"), kb([nav_row()]))
            return
        s["amount"] = n
        persist_sess()
        if s.get("type") == "cash":
            ask_date(chat_id)
        else:
            ask_who_deal(chat_id)
        return
    if step == "delivery_amt":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, screen(s.get("type"), "delivery_amt", "напиши число, например 400"), kb([nav_row()]))
            return
        s["delivery"] = n
        persist_sess()
        ask_consumable(chat_id)
        return
    if step == "cons_amt":
        n = parse_amount(text)
        if n is None:
            ui(chat_id, screen(s.get("type"), s.get("step") or "amount", "напиши число, например 150"), kb([nav_row()]))
            return
        s["consumable"] = n
        persist_sess()
        after_extras(chat_id)
        return
    if step == "role_other":
        if len(text) < 2:
            ui(chat_id, screen(s.get("type"), "role_other", "слишком коротко."), kb([nav_row()]))
            return
        s["role"] = text[:80]
        s["role_pct"] = None
        persist_sess()
        ask_date(chat_id)
        return
    if step == "search":
        if len(text) < 1:
            ui(chat_id, screen(s.get("type"), "search", "напиши хотя бы пару символов."), kb([nav_row()]))
            return
        s["query"] = text[:40]
        s["filter_cat"] = s.get("filter_cat") or "all"
        persist_sess()
        if s.get("type") == "edit":
            items = fetch_lots("all", s["query"])
            if items is None:
                ui(chat_id, "подгружаю лоты…")
                q = s["query"]

                def work():
                    _lots_refresh(block=True)
                    got = fetch_lots("all", q) or []
                    ss = sess(chat_id)
                    ss["lots"] = got
                    persist_sess()
                    if not got:
                        ui(chat_id, "ничего не нашёл.", kb([nav_row()]))
                        return
                    show_item_list(chat_id, 0)

                bg(work)
                return
            s["lots"] = items
            persist_sess()
        show_item_list(chat_id, 0)
        return
    if step == "date_custom":
        ds = parse_date(text)
        if not ds:
            ui(chat_id, screen(s.get("type"), "date_custom", "не понял дату. пример: 06.09.2026"), kb([nav_row()]))
            return
        set_date_and_continue(chat_id, ds)
        return
    if step == "note":
        s["note"] = text[:200]
        persist_sess()
        ask_confirm(chat_id)
        return
    if step == "buyer":
        s["buyer"] = text[:80]
        persist_sess()
        ask_role(chat_id)
        return
    if step == "place_other":
        s["place"] = text[:40]
        persist_sess()
        ask_buyer(chat_id)
        return
    if step in (
        "edit_name",
        "edit_cost",
        "edit_sale",
        "edit_note",
        "edit_buyer",
        "edit_place",
        "edit_role",
        "edit_deliv",
        "edit_cons",
    ):
        apply_edit_text(chat_id, text)
        return
    send(chat_id, "нажми кнопку внизу или /start", reply_kb())


def pick_unsold(chat_id, row: int):
    s = sess(chat_id)
    picked = None
    for it in filtered_items(s) or s.get("unsold") or []:
        if int(it.get("row") or 0) == row:
            picked = it
            break
    if not picked:
        ui(chat_id, "этот товар уже не в списке. выбери ещё раз.")
        show_item_list(chat_id, s.get("page") or 0)
        return
    s["sheet_row"] = row
    s["product"] = str(picked.get("product") or "")
    s["lot"] = str(picked.get("lot") or "")
    s["cost"] = picked.get("cost") or 0
    s["category"] = str(picked.get("category") or "")
    persist_sess()
    ask_amount(chat_id)


def pick_edit(chat_id, row: int):
    s = sess(chat_id)
    picked = None
    for it in s.get("lots") or s.get("unsold") or []:
        if int(it.get("row") or 0) == row:
            picked = it
            break
    if not picked:
        ui(chat_id, "подгружаю лот…")

        def work():
            r = sheets_call({"action": "lot", "sheet_row": row}, attempts=1, deadline_s=8)
            got = r.get("item") if r.get("ok") else None
            if not got:
                ui(chat_id, "не нашёл строку.")
                return
            ss = sess(chat_id)
            ss["edit_row"] = row
            ss["edit_item"] = got
            persist_sess()
            show_edit_item(chat_id)

        bg(work)
        return
    s["edit_row"] = row
    s["edit_item"] = picked
    persist_sess()
    show_edit_item(chat_id)


def load_edit_list(chat_id, mode: str):
    items = fetch_lots(mode)
    if not items:
        items = fetch_unsold() or []
        _lots_refresh(block=False)
    if items:
        s = sess(chat_id)
        s["lots"] = items
        s["unsold"] = items
        s["filter_cat"] = "all"
        s["query"] = ""
        persist_sess()
        show_item_list(chat_id, 0)
        return
    ui(chat_id, "подгружаю лоты…")

    def work():
        _lots_refresh(block=True)
        got = fetch_lots(mode) or fetch_unsold() or []
        if not got:
            ui(chat_id, "пусто или таблица не отвечает.")
            go_menu(chat_id)
            return
        ss = sess(chat_id)
        ss["lots"] = got
        ss["unsold"] = got
        ss["filter_cat"] = "all"
        ss["query"] = ""
        persist_sess()
        show_item_list(chat_id, 0)

    bg(work)


_MUT_GUARD: dict = {}


def _mut_recent(chat_id, data, window=5.0) -> bool:
    # страховка от двойного тапа по «записать»/«удалить»/«отменить»:
    # повторный клик в коротком окне игнорируем
    t = time.time()
    last = _MUT_GUARD.get(chat_id)
    if last and last[0] == data and t - last[1] < window:
        return True
    _MUT_GUARD[chat_id] = (data, t)
    return False


def on_callback(chat_id, cb_id, data: str, message_id=None):
    data = data or ""
    if data in ("ok", "xd:yes", "un:yes") and _mut_recent(chat_id, data):
        log("cb dedupe " + data)
        answer_cb(cb_id, "уже обрабатываю…")
        return
    answer_cb(cb_id)
    log("cb " + data + " step=" + str(sess(chat_id).get("step")))
    s = sess(chat_id)
    if message_id:
        s["msg_id"] = message_id
        persist_sess()
    if data == "m:cancel":
        go_menu(chat_id, "ок, отменил.")
        return
    if data == "m:back":
        go_back(chat_id)
        return
    if data == "m:new":
        start_deal(chat_id)
        return
    if data == "m:buy":
        start_deal(chat_id, "buy")
        return
    if data == "m:sell":
        start_deal(chat_id, "sell")
        return
    if data == "m:cash":
        start_cash(chat_id)
        return
    if data == "m:edit":
        start_edit(chat_id)
        return
    if data == "m:bal":
        show_balance(chat_id, via_ui=True)
        return
    if data == "m:undo":
        start_undo(chat_id)
        return
    if data == "un:no":
        go_menu(chat_id, "ок, оставил как было.")
        return
    if data == "un:yes":
        perform_undo(chat_id)
        return
    if data == "un:list":
        show_undo_list(chat_id, sess(chat_id).get("undo_page") or 0)
        return
    if data == "un:last":
        start_undo(chat_id)
        return
    if data.startswith("un:pg:"):
        try:
            page = int(data.split(":")[-1])
        except ValueError:
            page = 0
        show_undo_list(chat_id, page)
        return
    if data.startswith("un:i:"):
        try:
            idx = int(data.split(":")[-1])
        except ValueError:
            show_undo_list(chat_id, 0)
            return
        confirm_undo_pick(chat_id, idx)
        return
    if data == "m:cats":
        if s.get("type") == "edit":
            show_categories(chat_id, for_sell=False)
        else:
            if not s.get("unsold"):
                if not load_unsold_or_fail(chat_id, then="cats"):
                    return
            show_categories(chat_id, for_sell=True)
        return
    if data == "m:search":
        if s.get("type") == "sell" and not s.get("unsold"):
            if not load_unsold_or_fail(chat_id, then="search"):
                return
        ask_search(chat_id)
        return
    if data.startswith("k:"):
        kind = data.split(":", 1)[1]
        s["type"] = kind
        persist_sess()
        if kind == "sell":
            ask_sell_how(chat_id)
        else:
            ask_product(chat_id)
        return
    if data.startswith("c:"):
        cat = data.split(":", 1)[1]
        if s.get("type") == "buy" and s.get("step") == "buy_cat":
            s["category"] = cat
            persist_sess()
            ask_amount(chat_id)
            return
        if s.get("type") == "edit" and s.get("edit_field") == "cat":
            s.setdefault("edit_item", {})["category"] = cat
            s["edit_field"] = None
            persist_sess()
            show_edit_item(chat_id)
            bg(sheets_call, {"action": "update", "sheet_row": s.get("edit_row"), "fields": {"category": cat}})
            return
        if s.get("type") == "sell" and not s.get("unsold"):
            s["filter_cat"] = cat
            s["query"] = s.get("query") or ""
            persist_sess()
            if not load_unsold_or_fail(chat_id, then="all"):
                return
        s["filter_cat"] = cat
        s["query"] = s.get("query") or ""
        persist_sess()
        show_item_list(chat_id, 0)
        return
    if data.startswith("pg:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        show_item_list(chat_id, max(0, page))
        return
    if data.startswith("u:"):
        try:
            row = int(data.split(":", 1)[1])
        except ValueError:
            ui(chat_id, "не понял строку.")
            return
        pick_unsold(chat_id, row)
        return
    if data.startswith("e:"):
        try:
            row = int(data.split(":", 1)[1])
        except ValueError:
            ui(chat_id, "не понял строку.")
            return
        pick_edit(chat_id, row)
        return
    if data.startswith("eh:"):
        how = data.split(":", 1)[1]
        if how == "search":
            ask_search(chat_id)
        else:
            load_edit_list(chat_id, "unsold" if how == "unsold" else "all")
        return
    if data.startswith("w:"):
        s["who"] = data.split(":", 1)[1]
        persist_sess()
        after_who(chat_id)
        return
    if data.startswith("ex:"):
        parts = data.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        pay = parts[2] if len(parts) > 2 else "no"
        if kind == "d":
            if pay == "no":
                s["delivery"] = 0
                s["delivery_pay"] = ""
                persist_sess()
                ask_consumable(chat_id)
            else:
                s["delivery_pay"] = pay
                persist_sess()
                ask_extra_amount(chat_id, "d")
            return
        if kind == "c":
            if pay == "no":
                s["consumable"] = 0
                s["consumable_pay"] = ""
                persist_sess()
                after_extras(chat_id)
            else:
                s["consumable_pay"] = pay
                persist_sess()
                ask_extra_amount(chat_id, "c")
            return
        return
    if data.startswith("rl:"):
        code = data.split(":", 1)[1]
        if s.get("type") == "edit":
            if code == "other":
                s["edit_field"] = "role"
                s["step"] = "edit_role"
                persist_sess()
                ui(chat_id, "напиши роль Матвея.", kb([nav_row()]))
                return
            if code == "skip":
                s.setdefault("edit_item", {})["role"] = ""
                persist_sess()
                show_edit_item(chat_id)
                bg(sheets_call, {"action": "update", "sheet_row": s.get("edit_row"), "fields": {"role": ""}})
                return
            info = ROLE_MAP.get(code)
            full = info[0] if info else code
            pct = info[1] if info else None
            fields = {"role": full}
            if pct is not None:
                fields["role_pct"] = pct
            s.setdefault("edit_item", {})["role"] = full
            persist_sess()
            show_edit_item(chat_id)
            bg(sheets_call, {"action": "update", "sheet_row": s.get("edit_row"), "fields": fields})
            return
        if code == "other":
            s["step"] = "role_other"
            persist_sess()
            ui(chat_id, "напиши роль Матвея своими словами.", kb([nav_row()]))
            return
        if code == "skip":
            s["role"] = ""
            s["role_pct"] = None
            persist_sess()
            ask_date(chat_id)
            return
        info = ROLE_MAP.get(code)
        if info:
            s["role"] = info[0]
            s["role_pct"] = info[1]
        persist_sess()
        ask_date(chat_id)
        return
    if data.startswith("l:"):
        s["lot"] = data.split(":", 1)[1]
        persist_sess()
        ask_role(chat_id)
        return
    if data.startswith("pl:"):
        code = data.split(":", 1)[1]
        if s.get("type") == "edit":
            if code == "other":
                s["edit_field"] = "place"
                s["step"] = "edit_place"
                persist_sess()
                ui(chat_id, "напиши где продали.", kb([nav_row()]))
                return
            name = PLACE_MAP.get(code, code)
            s.setdefault("edit_item", {})["place"] = name
            persist_sess()
            show_edit_item(chat_id)
            bg(sheets_call, {"action": "update", "sheet_row": s.get("edit_row"), "fields": {"place": name}})
            return
        if code == "other":
            s["step"] = "place_other"
            persist_sess()
            ui(chat_id, "напиши где продали.", kb([nav_row()]))
            return
        s["place"] = PLACE_MAP.get(code, code)
        persist_sess()
        ask_buyer(chat_id)
        return
    if data == "b:skip":
        s["buyer"] = ""
        persist_sess()
        ask_role(chat_id)
        return
    if data == "n:skip":
        if s.get("type") == "edit":
            s.setdefault("edit_item", {})["note"] = ""
            persist_sess()
            show_edit_item(chat_id)
            bg(sheets_call, {"action": "update", "sheet_row": s.get("edit_row"), "fields": {"note": ""}})
            return
        s["note"] = ""
        persist_sess()
        ask_confirm(chat_id)
        return
    if data.startswith("dt:"):
        key = data.split(":", 1)[1]
        if key == "other":
            ask_date_custom(chat_id)
            return
        set_date_and_continue(chat_id, key)
        return
    if data.startswith("d:"):
        key = data.split(":", 1)[1]
        s["date"] = today_str() if key == "today" else yesterday_str()
        persist_sess()
        after_date(chat_id)
        return
    if data.startswith("xf:"):
        field = data.split(":", 1)[1]
        s["edit_field"] = field
        persist_sess()
        if field == "del":
            ui(
                chat_id,
                "удалить лот <b>"
                + esc((s.get("edit_item") or {}).get("product") or "")
                + "</b>?\nстрока сотрётся, движения по кассе по этому товару сторнирую.",
                kb([[btn("🗑  да, удалить", "xd:yes")], [btn("‹ нет", "xd:no")]]),
            )
            return
        if field in ("buy", "sell"):
            ask_date(chat_id)
            return
        if field == "cat":
            rows = []
            pair = []
            for name in CATS:
                pair.append(btn(cat_label(name), "c:" + name))
                if len(pair) == 2:
                    rows.append(pair)
                    pair = []
            if pair:
                rows.append(pair)
            rows.append(cancel_row())
            s["step"] = "edit_cat"
            persist_sess()
            ui(chat_id, "новая категория?", kb(rows))
            return
        if field == "place":
            rows = []
            pair = []
            for code, name in PLACES:
                pair.append(btn(name, "pl:" + code))
                if len(pair) == 2:
                    rows.append(pair)
                    pair = []
            if pair:
                rows.append(pair)
            rows.append([btn("кому продан", "xf:buyer")])
            rows.append(cancel_row())
            ui(chat_id, "где продали?", kb(rows))
            return
        if field == "role":
            ask_role(chat_id)
            return
        if field == "deliv":
            s["edit_field"] = "delivery"
            s["step"] = "edit_deliv"
            persist_sess()
            ui(chat_id, "сумма доставки, только число. 0 если нет.", kb([nav_row()]))
            return
        if field == "cons":
            s["edit_field"] = "consumable"
            s["step"] = "edit_cons"
            persist_sess()
            ui(chat_id, "сумма расходников, только число. 0 если нет.", kb([nav_row()]))
            return
        prompts = {
            "cost": "новая цена закупа, только число.",
            "sale": "новая цена продажи, только число.",
            "name": "новое название товара.",
            "note": "новое примечание. или «без примечания».",
            "buyer": "кому: телефон, @telegram, VK.",
        }
        step_map = {
            "cost": "edit_cost",
            "sale": "edit_sale",
            "name": "edit_name",
            "note": "edit_note",
            "buyer": "edit_buyer",
        }
        s["step"] = step_map.get(field, "edit_note")
        persist_sess()
        extra = skip_note_kb() if field == "note" else kb([nav_row()])
        ui(chat_id, prompts.get(field, "напиши новое значение."), extra)
        return
    if data == "xd:no":
        s["edit_field"] = None
        persist_sess()
        show_edit_item(chat_id)
        return
    if data == "xd:yes":
        it = s.get("edit_item") or {}
        row = s.get("edit_row")
        ui(chat_id, "удаляю лот…")
        _unsold_drop_row(row)
        _CASH["t"] = 0
        go_menu(chat_id, "лот убрал у бота, пишу в таблицу…")

        def work():
            r = sheets_call(
                {
                    "action": "delete",
                    "sheet_row": row,
                    "product": it.get("product") or "",
                    "cost": it.get("cost") or 0,
                    "sale": it.get("sale") or 0,
                }
            )
            if r.get("ok"):
                for x in r.get("reversed") or []:
                    append_deal(
                        {
                            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                            "ts": datetime.now().isoformat(timespec="seconds"),
                            "chat_id": chat_id,
                            "type": "cash",
                            "product": x.get("product") or it.get("product") or "",
                            "amount": int(x.get("amount") or 0),
                            "who": "other_in" if x.get("dir") == "in" else "other_out",
                            "date": today_str(),
                            "kassa_what": x.get("what") or "Отмена",
                            "cash_dir": x.get("dir"),
                            "comment": "отмена лота",
                        }
                    )
                nrev = len(r.get("reversed") or [])
                extra = (" касса: сторно %s запис." % nrev) if nrev else ""
                send(chat_id, "лот удалён из таблицы." + extra, reply_kb())
            else:
                send(chat_id, "у бота убрал, таблица не приняла — глянь строку глазами.", reply_kb())

        bg(work)
        return
    if data == "ok":
        commit(chat_id)
        return


def chat_allowed(chat_id) -> bool:
    if not ALLOWED_CHAT_IDS:
        return False
    try:
        return int(chat_id) in ALLOWED_CHAT_IDS
    except (TypeError, ValueError):
        return False


def handle_update(u: dict):
    chat_id = None
    cb_id = None
    if "message" in u:
        chat_id = u["message"].get("chat", {}).get("id")
    elif "callback_query" in u:
        q = u["callback_query"]
        cb_id = q.get("id")
        chat_id = (q.get("message") or {}).get("chat", {}).get("id")
    if chat_id is None:
        return
    if not chat_allowed(chat_id):
        log("deny chat_id=%s" % chat_id)
        if cb_id:
            answer_cb(cb_id)
        send(chat_id, "нет доступа.")
        return
    remember_user(chat_id, u)
    if "message" in u:
        m = u["message"]
        if "text" in m:
            on_text(chat_id, m["text"])
        else:
            send(chat_id, "пришли текстом или нажми кнопку.", reply_kb())
        return
    if "callback_query" in u:
        q = u["callback_query"]
        on_callback(chat_id, q["id"], q.get("data") or "", (q.get("message") or {}).get("message_id"))


def setup_bot():
    api("deleteWebhook", {"drop_pending_updates": False})
    api(
        "setMyCommands",
        {
            "commands": [
                {"command": "start", "description": "Меню"},
                {"command": "edit", "description": "Изменить лот"},
                {"command": "balance", "description": "Касса, сколько на руках"},
                {"command": "undo", "description": "Отменить последнее действие"},
                {"command": "cancel", "description": "Отменить ввод"},
            ]
        },
    )
    log("sheets setup deferred to keepwarm")


_SEEN_UPDATES: set = set()


def loop():
    setup_bot()
    me = api("getMe", {})
    if not me.get("ok"):
        log("getMe failed, retry in 5s: %s" % (me,))
        time.sleep(5)
        return
    log(
        "bot @"
        + me["result"]["username"]
        + " started allowed="
        + ",".join(str(x) for x in sorted(ALLOWED_CHAT_IDS))
    )
    threading.Thread(target=_sheets_keepwarm, name="sheets-warm", daemon=True).start()
    offset = 0
    while True:
        res = api(
            "getUpdates",
            {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
            timeout=40,
        )
        if not res.get("ok"):
            time.sleep(2)
            continue
        for u in res.get("result") or []:
            uid = u["update_id"]
            offset = uid + 1
            if uid in _SEEN_UPDATES:
                log("skip dup update %s" % uid)
                continue
            _SEEN_UPDATES.add(uid)
            if len(_SEEN_UPDATES) > 5000:
                _SEEN_UPDATES.clear()
            try:
                handle_update(u)
            except Exception:
                log(traceback.format_exc())
                try:
                    chat = (u.get("message") or u.get("callback_query", {}).get("message") or {}).get(
                        "chat", {}
                    )
                    if chat.get("id"):
                        send(chat["id"], "сбой на шаге. нажми /start и ещё раз.", reply_kb())
                except Exception:
                    pass


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    log("launch")
    while True:
        try:
            loop()
        except KeyboardInterrupt:
            log("stop")
            break
        except Exception:
            log(traceback.format_exc())
            time.sleep(3)
