# -*- coding: utf-8 -*-
"""Разово: кто писал боту (chat_id). Токен берётся из .env, в git его нет."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parent
env = {}
env_path = root / ".env"
if not env_path.exists():
    raise SystemExit("Нет .env")
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip()
token = env.get("BOT_TOKEN")
if not token:
    raise SystemExit("Нет BOT_TOKEN в .env")


def call(method, payload=None):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return {"ok": False, "http": e.code, "body": body}


query = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
if query:
    if not query.startswith("@"):
        query = "@" + query
    print("GETCHAT", json.dumps(call("getChat", {"chat_id": query}), ensure_ascii=False))

upd = call("getUpdates", {"timeout": 0, "limit": 100})
print("UPDATES_OK", upd.get("ok"), "n", len(upd.get("result") or []))
seen = {}
for u in upd.get("result") or []:
    src = u.get("message") or u.get("callback_query") or {}
    from_u = src.get("from") or {}
    chat = (src.get("chat") or (src.get("message") or {}).get("chat") or {})
    uid = from_u.get("id") or chat.get("id")
    un = from_u.get("username") or chat.get("username")
    name = " ".join(x for x in [from_u.get("first_name"), from_u.get("last_name")] if x)
    if uid:
        seen[str(uid)] = {"id": uid, "username": un, "name": name}
print("SEEN", json.dumps(list(seen.values()), ensure_ascii=False))
