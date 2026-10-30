"""
SpareEat bag monitor - emails you when a bag opens at a business you follow.

Uses only the Python standard library, so it runs as-is on any server.

  python monitor.py                 run forever (polls every interval_minutes)
  python monitor.py --once          check once, print status, exit
  python monitor.py test-email      send a test email to check SMTP settings
  python monitor.py find <text>     search businesses by name to get their box Id
                                    (needs your SpareEat login in config.json)
  python monitor.py --ci            GitHub Actions mode: check every minute for CI_MINUTES
                                    (default 4), save state, exit

Config comes from config.json (local, gitignored) or config.ci.json (committed, no secrets).
Email credentials can also come from env vars EMAIL_USER / EMAIL_APP_PASSWORD / EMAIL_TO.

API facts (from the captured traffic):
  POST https://api.spareeat.com/api/Search/Box              {"Id", "Latitude", "Longitude"}  - no login needed
       -> ItemsLeft, PickupDate, PickupTimeFrom/To, Price, HebrewRestaurantName ...
  POST https://api.spareeat.com/api/Users/Login             {"Email", "Password"} -> {"Token"}
  POST https://api.spareeat.com/api/Search/GetSearchResults {"Token", "Latitude", "Longitude"} -> all boxes
"""
import json
import os
import smtplib
import sys
import time
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).parent
_cfg_file = HERE / "config.json"
if not _cfg_file.exists():
    _cfg_file = HERE / "config.ci.json"
CONFIG = json.loads(_cfg_file.read_text(encoding="utf-8"))
CONFIG.setdefault("email", {})
for _key, _env in (("user", "EMAIL_USER"), ("app_password", "EMAIL_APP_PASSWORD"), ("to", "EMAIL_TO")):
    if os.environ.get(_env):
        CONFIG["email"][_key] = os.environ[_env]
if os.environ.get("NTFY_TOPIC"):
    CONFIG["ntfy_topic"] = os.environ["NTFY_TOPIC"]
STATE_FILE = Path(os.environ.get("STATE_FILE", HERE / "state.json"))
API = "https://api.spareeat.com/api"
HEADERS = {"content-type": "application/json; charset=UTF-8", "user-agent": "okhttp/4.12.0"}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def post(path, body, timeout=20):
    req = urllib.request.Request(f"{API}/{path}", data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get_box(box_id):
    return post("Search/Box", {"Id": box_id, "Latitude": CONFIG["latitude"], "Longitude": CONFIG["longitude"]})


# ---------- notifications ----------

def send_email(subject, body):
    e = CONFIG["email"]
    if not e.get("app_password"):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, e["user"], e["to"]
    msg.set_content(body)
    with smtplib.SMTP_SSL(e.get("smtp_host", "smtp.gmail.com"), e.get("smtp_port", 465), timeout=30) as s:
        s.login(e["user"], e["app_password"])
        s.send_message(msg)


def send_ntfy(title, body):
    topic = CONFIG.get("ntfy_topic")
    if not topic:
        return
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                                 headers={"Title": title.encode("utf-8"), "Priority": "high"}, method="POST")
    urllib.request.urlopen(req, timeout=20).close()


def notify(subject, body):
    for fn in (send_email, send_ntfy):
        try:
            fn(subject, body)
        except Exception as ex:  # one channel failing shouldn't kill the other
            log(f"notification via {fn.__name__} failed: {ex}")


def describe(b):
    name = b.get("HebrewRestaurantName") or b.get("RestaurantName")
    return (f"{name} - {b.get('BoxName', '')}\n"
            f"נשארו: {b.get('ItemsLeft')} | מחיר: {b.get('Price')}₪ (במקום {b.get('PriceBeforeDiscount')}₪)\n"
            f"איסוף: {str(b.get('PickupDate', ''))[:10]} {str(b.get('PickupTimeFrom', ''))[:5]}-{str(b.get('PickupTimeTo', ''))[:5]}\n"
            f"כתובת: {b.get('HebrewAddress') or b.get('Address', '')}")


# ---------- main loop ----------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def check_all(state, announce=True):
    for box_id in CONFIG["box_ids"]:
        try:
            b = get_box(box_id)
        except Exception as ex:
            log(f"box {box_id}: request failed ({ex})")
            continue
        left = b.get("ItemsLeft") or 0
        key = str(box_id)
        prev = state.get(key, {})
        name = b.get("HebrewRestaurantName") or b.get("RestaurantName")
        was_open = prev.get("left", 0) > 0 and prev.get("date") == b.get("PickupDate")

        if left > 0 and not was_open and announce and prev:
            log(f"OPEN: {name} ({left} left) -> notifying")
            notify(f"🥐 נפתח סל ב{name}!", describe(b))
        elif left != prev.get("left"):
            log(f"{name}: {left} left")

        state[key] = {"left": left, "date": b.get("PickupDate"), "name": name}
        time.sleep(1)  # be gentle with their server
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_forever():
    state = load_state()
    interval = CONFIG.get("interval_minutes", 3) * 60
    log(f"watching {len(CONFIG['box_ids'])} boxes every {interval // 60} min")
    while True:
        check_all(state)
        time.sleep(interval)


def run_ci():
    """One GitHub Actions job: poll every minute for a few minutes, then exit (state is committed by the workflow)."""
    state = load_state()
    end = time.time() + float(os.environ.get("CI_MINUTES", 4)) * 60
    while True:
        check_all(state)
        if time.time() + 60 > end:
            break
        time.sleep(60)


def run_once():
    for box_id in CONFIG["box_ids"]:
        b = get_box(box_id)
        print(f"[{box_id}]", describe(b).replace("\n", " | "))


def find(text):
    acct = CONFIG.get("spareeat_login") or {}
    if not acct.get("email"):
        sys.exit("Add spareeat_login.email/password to config.json to use 'find'.")
    token = post("Users/Login", {"Email": acct["email"], "Password": acct["password"]})["Token"]
    res = post("Search/GetSearchResults", {"Token": token, "Latitude": CONFIG["latitude"],
                                           "Longitude": CONFIG["longitude"]}, timeout=60)["Results"]
    text = text.lower()
    for r in res:
        names = f"{r.get('RestaurantName', '')} {r.get('HebrewRestaurantName', '')}".lower()
        if text in names:
            print(f"Id={r['ProductId']:>6}  {r.get('HebrewRestaurantName')} / {r.get('RestaurantName')}"
                  f"  | {r.get('BoxName')} | left={r.get('ItemsLeft')} | {r.get('Distance', 0):.1f} km")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        run_forever()
    elif args[0] == "--ci":
        run_ci()
    elif args[0] == "--once":
        run_once()
    elif args[0] == "test-email":
        notify("SpareEat monitor - test", "If you got this, notifications work.")
        log("test notification sent")
    elif args[0] == "find" and len(args) > 1:
        find(" ".join(args[1:]))
    else:
        print(__doc__)
