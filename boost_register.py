#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
רישום אוטומטי לשיעורים בסטודיו Miryam Politi Fitness (דרך פלטפורמת lee.co.il / Boost).

הסקריפט שולף את לוח השיעורים, מזהה את השיעורים שהגדרת מראש בקובץ config.json,
ונרשם אליהם ברגע שהרישום נפתח - בלי שתצטרכי לזכור כלום.

שימוש:
    python boost_register.py --list          # הצגת כל השיעורים הזמינים ב-14 הימים הקרובים
    python boost_register.py --test-auth      # בדיקה שההתחברות (הטוקן) תקינה
    python boost_register.py --dry-run        # מה הסקריפט *היה* רושם - בלי לרשום בפועל
    python boost_register.py --once           # ריצה אחת: רישום לכל שיעור מטרה שנפתח
    python boost_register.py --watch           # ריצה מתמשכת: בודק כל X שניות ורושם ברגע שנפתח

כל ההגדרות (טוקן, שיעורי מטרה, תדירות בדיקה) נמצאות ב-config.json שליד הקובץ הזה.
"""

import os
import json
import sys
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Jerusalem")
except Exception:
    TZ = None

# ודא שהעברית מוצגת נכון בחלון הפקודה של Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# ------------------------------------------------------------------
# קבועים - נכונים לסטודיו Miryam Politi Fitness. אין צורך לשנות.
# ------------------------------------------------------------------
API_BASE = "https://rest.lee.co.il"
API_KEY = "mSjKwbvjsqFTbJyqoiQVItBcuZBzndkL"

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
LOG_PATH = HERE / "register_log.txt"

# מספר יום בשבוע -> שם עברי (Python: שני=0 ... ראשון=6)
HEB_DAYS = {
    6: "ראשון", 0: "שני", 1: "שלישי", 2: "רביעי",
    3: "חמישי", 4: "שישי", 5: "שבת",
}
# שם עברי -> מספר יום בשבוע של Python (weekday())
HEB_DAY_TO_NUM = {v: k for k, v in HEB_DAYS.items()}


# ------------------------------------------------------------------
# תשתית: יומן, קונפיג, קריאות רשת
# ------------------------------------------------------------------
def log(msg):
    """כותב הודעה גם למסך וגם לקובץ היומן, עם חותמת זמן."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _bool_env(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on", "כן")


# ברירת מחדל לשיעורי המטרה (ראשון ושני 08:15) - משמשת במצב ענן אם לא הוגדר אחרת
DEFAULT_TARGETS = [
    {"weekday": "ראשון", "time": "08:15", "name": ""},
    {"weekday": "שני", "time": "08:15", "name": ""},
]


def load_config():
    """טוען הגדרות. מצב ענן: אם קיים משתנה סביבה BOOST_TOKEN, ההגדרות נבנות
    ממשתני סביבה (סודות GitHub). אחרת - מקובץ config.json המקומי."""
    if os.environ.get("BOOST_TOKEN"):
        targets_raw = os.environ.get("BOOST_TARGETS", "").strip()
        try:
            targets = json.loads(targets_raw) if targets_raw else DEFAULT_TARGETS
        except json.JSONDecodeError:
            log("אזהרה: BOOST_TARGETS אינו JSON תקין - משתמש בברירת המחדל (ראשון ושני 08:15).")
            targets = DEFAULT_TARGETS
        return {
            "token": os.environ["BOOST_TOKEN"].strip(),
            "company": int(os.environ.get("BOOST_COMPANY", "386935")),
            "source": int(os.environ.get("BOOST_SOURCE", "1")),
            "clientId": int(os.environ.get("BOOST_CLIENT_ID", "430781221")),
            "poll_interval_seconds": int(os.environ.get("BOOST_POLL_INTERVAL", "60")),
            "check_calendar": _bool_env("BOOST_CHECK_CALENDAR", True),
            "calendar_ics_url": os.environ.get("BOOST_CALENDAR_ICS_URL", "").strip(),
            "register_if_calendar_unavailable": _bool_env("BOOST_REGISTER_IF_CAL_UNAVAILABLE", True),
            "class_duration_minutes": int(os.environ.get("BOOST_CLASS_DURATION", "60")),
            "targets": targets,
        }

    if not CONFIG_PATH.exists():
        log(f"שגיאה: קובץ ההגדרות לא נמצא: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("token") or cfg["token"].startswith("<"):
        log("שגיאה: לא הוגדר טוקן ב-config.json. ראי את קובץ README (שלב ההתחברות).")
        sys.exit(1)
    return cfg


def _api_request(path, token, payload=None, method="POST"):
    """קריאה ל-API של lee (POST עם גוף JSON, או GET). מחזיר את שדה ה-data, או זורק חריגה."""
    url = API_BASE + path
    headers = {
        "apikey": API_KEY,
        "language": "he",
        "authorization": "Bearer " + token,
        "Accept": "application/json",
    }
    body = None
    if method == "POST":
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {raw[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"בעיית רשת: {e.reason}")

    data = json.loads(raw)
    if not data.get("success") or data.get("code") != 200:
        raise RuntimeError(f"ה-API החזיר שגיאה: {data.get('message', raw[:200])}")
    return data.get("data", {})


def api_post(path, token, payload):
    return _api_request(path, token, payload, method="POST")


def api_get(path, token):
    return _api_request(path, token, method="GET")


# ------------------------------------------------------------------
# לוגיקה עסקית
# ------------------------------------------------------------------
def fetch_schedule(cfg):
    """שולף את כל השיעורים בחלון ה-14 ימים. מחזיר רשימת dict-ים מנורמלים."""
    today = date.today()
    payload = {
        "company": str(cfg["company"]),
        "source": str(cfg["source"]),
        "fromLee": True,
        "startDate": today.isoformat(),
        "endDate": today.isoformat(),  # ה-API מתעלם וממילא מחזיר 14 יום
    }
    data = api_post("/services/get-class-schedule/", cfg["token"], payload)
    classes = data.get("classes", []) or []
    out = []
    for c in classes:
        try:
            d = datetime.strptime(c["startDate"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        out.append({
            "id": c["id"],
            "name": (c.get("className") or "").strip(),
            "date": d,
            "weekday": HEB_DAYS.get(d.weekday(), "?"),
            "time": (c.get("startTime") or "")[:5],  # "07:15:00" -> "07:15"
            "end_time": (c.get("endTime") or "")[:5],
            "full": bool(c.get("isClassFull")),
            "registered": c.get("clientRegister"),
            "capacity": c.get("maxClient"),
            "free_class": bool(c.get("freeClass")),
            "need_subscription": c.get("need_subscription"),
            "open_order": c.get("openOrder"),
        })
    out.sort(key=lambda x: (x["date"], x["time"]))
    return out


def get_my_registered_ids(cfg):
    """מחזיר קבוצת מזהי שיעורים שכבר רשומה אליהם - למניעת רישום כפול.
    המזהה (classStudioDateId) תואם לשדה id בלוח השיעורים."""
    path = f"/account/events/get-registrations?company={cfg['company']}&source={cfg['source']}"
    try:
        data = api_get(path, cfg["token"])
    except RuntimeError:
        return set()  # במקרה של כשל - נמשיך; ה-API ממילא ידחה רישום כפול
    ids = set()
    for ev in (data.get("events") or []):
        cid = ev.get("classStudioDateId")
        if cid is not None:
            ids.add(cid)
    return ids


def _class_window(cfg, cls):
    """מחזיר (start, end) כאובייקטי datetime מודעי-אזור-זמן עבור השיעור."""
    h, m = int(cls["time"][:2]), int(cls["time"][3:5])
    start = datetime(cls["date"].year, cls["date"].month, cls["date"].day, h, m, tzinfo=TZ)
    if cls.get("end_time") and len(cls["end_time"]) == 5:
        eh, em = int(cls["end_time"][:2]), int(cls["end_time"][3:5])
        end = datetime(cls["date"].year, cls["date"].month, cls["date"].day, eh, em, tzinfo=TZ)
    else:
        end = start + timedelta(minutes=int(cfg.get("class_duration_minutes", 60)))
    return start, end


def is_free_at(cfg, cls):
    """בודק ביומן Google (דרך הכתובת הסודית) אם המשתמשת פנויה בזמן השיעור.
    מחזיר (free: bool, reason: str)."""
    url = cfg.get("calendar_ics_url", "")
    if not cfg.get("check_calendar") or not url or url.startswith("<"):
        return True, "בדיקת יומן כבויה"

    fail_open = cfg.get("register_if_calendar_unavailable", True)
    try:
        import icalendar
        import recurring_ical_events
    except ImportError:
        msg = "רכיב בדיקת היומן לא מותקן (icalendar)"
        return (True, msg + " — נרשמת בכל זאת") if fail_open else (False, msg + " — דילגתי")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "boost-register/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        cal = icalendar.Calendar.from_ical(raw)
        start, end = _class_window(cfg, cls)
        occurrences = recurring_ical_events.of(cal).between(start, end)
    except Exception as e:
        msg = f"לא הצלחתי לקרוא את היומן ({str(e)[:80]})"
        return (True, msg + " — נרשמת בכל זאת") if fail_open else (False, msg + " — דילגתי")

    for ev in occurrences:
        # התעלמות מאירועים שמסומנים "פנוי", אירועים שבוטלו, וסירובים
        if str(ev.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = ev.get("DTSTART")
        # אירוע "יום שלם" (תאריך בלי שעה) - לא נחשב חסימה של שעת בוקר ספציפית
        if dtstart is not None and not isinstance(dtstart.dt, datetime):
            continue
        summary = str(ev.get("SUMMARY", "") or "אירוע ללא שם")
        return False, f"תפוסה — '{summary}' ביומן"

    return True, "פנויה ביומן"


def class_matches_target(cls, targets):
    """האם השיעור תואם אחת מהגדרות המטרה? מחזיר את הגדרת המטרה התואמת או None."""
    for t in targets:
        if t.get("weekday") and t["weekday"] != cls["weekday"]:
            continue
        if t.get("time") and t["time"] != cls["time"]:
            continue
        name = t.get("name", "").strip()
        if name and name not in cls["name"]:
            continue
        return t
    return None


def register_class(cfg, cls):
    """רושם לשיעור בודד. מחזיר (הצליח?, הודעה).

    שיעור עם מנוי (רוב השיעורים בסטודיו) נרשם בשני שלבים, בדיוק כמו הממשק:
      1. get-class-purchase-options   -> מחזיר classStudioActId ושומר מקום זמני
      2. register-to-class-with-clientActivity  -> הרישום בפועל, מנצל את המנוי הפעיל
    התהליך הזה לא נוגע בכרטיס אשראי ולא רוכש מנוי - רק מנצל מנוי קיים.
    """
    company, source = cfg["company"], cfg["source"]
    try:
        if cls["free_class"]:
            data = api_post("/services/register-to-free-class", cfg["token"], {
                "company": company, "source": source,
                "classId": cls["id"], "clientId": cfg["clientId"],
            })
        else:
            # שלב 1: קבלת מזהה הרישום (classStudioActId) ומזהה המנוי הפעיל, ושמירת מקום
            opts = api_post("/services/get-class-purchase-options/", cfg["token"], {
                "classStudioDate": cls["id"], "orderType": 1,
                "company": company, "source": source, "fromLee": True,
            })
            act_id = opts.get("classStudioActId")
            active_subs = (((opts.get("membershipsData") or {})
                            .get("userSubscriptions") or {}).get("active") or [])
            sub_id = active_subs[0].get("id") if active_subs else None
            if not act_id:
                return False, "לא התקבל מזהה רישום מהשרת (classStudioActId)"
            if not sub_id:
                return False, "לא נמצא מנוי פעיל בחשבון — יש לחדש מנוי או להירשם ידנית"
            # שלב 2: רישום בפועל באמצעות המנוי הפעיל
            data = api_post("/services/register-to-class-with-clientActivity/", cfg["token"], {
                "company": company, "source": source,
                "classStudioActId": act_id, "clientActivityId": sub_id,
            })
    except RuntimeError as e:
        return False, str(e)
    if data.get("success"):
        return True, data.get("text") or data.get("message") or "נרשמת בהצלחה"
    return False, f"הרישום נכשל: {data.get('message', '')} (reasonId={data.get('reasonId')})"


# ------------------------------------------------------------------
# פעולות (modes)
# ------------------------------------------------------------------
def do_list(cfg):
    classes = fetch_schedule(cfg)
    log(f"נמצאו {len(classes)} שיעורים ב-14 הימים הקרובים:")
    cur = None
    for c in classes:
        if c["date"] != cur:
            cur = c["date"]
            print(f"\n  {c['weekday']}  {c['date'].strftime('%d.%m.%Y')}")
        status = "מלא" if c["full"] else f"פנוי ({c['registered']}/{c['capacity']})"
        print(f"     {c['time']}  {c['name']:<20} [{status}]")


def do_run(cfg, dry_run=False):
    """מעבר יחיד: נסה לרשום לכל שיעור מטרה שנפתח וטרם רשומה אליו."""
    targets = cfg.get("targets", [])
    if not targets:
        log("אזהרה: לא הוגדרו שיעורי מטרה ב-config.json (targets ריק).")
        return 0

    classes = fetch_schedule(cfg)
    my_ids = get_my_registered_ids(cfg)
    registered_now = 0

    for cls in classes:
        t = class_matches_target(cls, targets)
        if not t:
            continue
        label = f"{cls['weekday']} {cls['date'].strftime('%d.%m')} {cls['time']} {cls['name']}"

        if cls["id"] in my_ids:
            continue  # כבר רשומה - דילוג שקט
        if cls["full"]:
            log(f"  [{label}] מלא כרגע - מדלג (אפשר להוסיף רישום להמתנה בהמשך).")
            continue

        # בדיקת יומן: נרשמים רק אם פנויה בזמן השיעור
        free, reason = is_free_at(cfg, cls)
        if not free:
            log(f"  ⏭️  מדלגת על {label} — {reason}")
            continue

        if dry_run:
            log(f"  [DRY-RUN] הייתי נרשמת ל: {label}  ({reason})")
            registered_now += 1
            continue

        ok, msg = register_class(cfg, cls)
        if ok:
            log(f"  ✅ נרשמת ל: {label}  — {msg}")
            registered_now += 1
        else:
            log(f"  ❌ לא נרשמת ל: {label}  — {msg}")

    if registered_now == 0:
        log("אין שיעורי מטרה חדשים שנפתחו לרישום כרגע.")
    return registered_now


def do_watch(cfg):
    interval = int(cfg.get("poll_interval_seconds", 60))
    log(f"מצב מעקב הופעל. בודק כל {interval} שניות. עצירה: Ctrl+C.")
    try:
        while True:
            try:
                do_run(cfg, dry_run=False)
            except RuntimeError as e:
                log(f"שגיאה זמנית: {e} — ממשיך בבדיקה הבאה.")
            time.sleep(interval)
    except KeyboardInterrupt:
        log("מצב מעקב הופסק ידנית.")


def do_test_auth(cfg):
    try:
        classes = fetch_schedule(cfg)
        log(f"✅ ההתחברות תקינה. נשלפו {len(classes)} שיעורים. הטוקן עובד.")
    except RuntimeError as e:
        log(f"❌ ההתחברות נכשלה: {e}")
        log("ייתכן שהטוקן פג תוקף. ראי README - שלב ההתחברות מחדש.")


# ------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="רישום אוטומטי לשיעורי Miryam Politi Fitness")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="הצגת השיעורים הזמינים")
    g.add_argument("--test-auth", action="store_true", help="בדיקת תקינות ההתחברות")
    g.add_argument("--dry-run", action="store_true", help="הדמיה בלי רישום בפועל")
    g.add_argument("--once", action="store_true", help="מעבר יחיד עם רישום בפועל")
    g.add_argument("--watch", action="store_true", help="ריצה מתמשכת עם רישום בפועל")
    args = p.parse_args()

    cfg = load_config()
    if args.list:
        do_list(cfg)
    elif args.test_auth:
        do_test_auth(cfg)
    elif args.dry_run:
        do_run(cfg, dry_run=True)
    elif args.once:
        do_run(cfg, dry_run=False)
    elif args.watch:
        do_watch(cfg)


if __name__ == "__main__":
    main()
