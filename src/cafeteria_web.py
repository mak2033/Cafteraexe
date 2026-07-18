# -*- coding: utf-8 -*-
"""
كفتيريا برو - نظام مبيعات كفتيريا السكن / كلية عجلون
واجهة حديثة (ويب محلي داخل نافذة Edge) فوق نفس قاعدة البيانات cafeteria.db
يعمل على ويندوز 10 و 11. لا يحتاج إنترنت للعمل اليومي.
التشغيل: python كفتيريا-برو.py   أو بعد التحويل: Cafeteria.exe
"""

import os
import sys
import json
import time
import socket
import sqlite3
import threading
import subprocess
import webbrowser
import base64
import io
import csv as csv_mod
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_TITLE = "كفتيريا السكن - كلية عجلون"
RECEIPT_WIDTH = 576

DEFAULT_ITEMS = [
    ("قهوه", 0.50, 1), ("قهوه عميد", 0.60, 1), ("شاي", 0.25, 1),
    ("شاي كرك", 0.50, 1), ("نسكافيه", 0.50, 1), ("كبتشينو", 0.50, 1),
    ("هوت شوكليت", 0.50, 1), ("موكاتشينو", 0.50, 1), ("موكا بارد", 0.50, 1),
    ("سحلب", 0.50, 1), ("زهورات", 0.25, 1), ("زنجبيل وحليب", 0.50, 1),
    ("اعشاب مشكل", 0.25, 1), ("بوشار", 0.50, 1), ("نكهات + ثلج", 0.50, 1),
    ("كاسه + ثلج", 0.25, 1), ("عدس", 0.50, 1), ("كوكتيل صغير", 1.00, 1),
    ("كوكتيل كبير", 1.50, 1),
    ("فلافل فرنسي", 0.60, 2), ("فلافل شراك", 0.60, 2), ("فلافل الي", 0.35, 2),
    ("بطاطا شراك", 1.00, 2), ("بطاطا شراك مع جبن", 1.25, 2),
    ("بطاطا فرنسي", 0.85, 2), ("بطاطا فرنسي مع جبن", 1.00, 2),
    ("برجر كلاسيك لحمة", 2.00, 2), ("برجر مشروم لحمة", 2.25, 2),
    ("برجر ايطالي لحمة", 2.25, 2), ("برجر جاج كلاسيك", 2.00, 2),
    ("برجر جاج حار", 2.00, 2), ("هدق كلاسيك صغير", 0.75, 2),
    ("هدق كبير", 1.50, 2), ("زنجر صاج عادي كبير", 2.00, 2),
    ("ذرة ومينيز", 0.50, 2),
]


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DB_PATH = os.path.join(app_dir(), "cafeteria.db")


# ------------------------------------------------------------------ البيانات

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            category INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            sort INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sales(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            item_name TEXT NOT NULL,
            price REAL NOT NULL,
            category INTEGER NOT NULL,
            day TEXT NOT NULL,
            order_no INTEGER NOT NULL,
            ts TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT
        );
        """
    )
    if con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
        for i, (n, p, c) in enumerate(DEFAULT_ITEMS):
            con.execute(
                "INSERT INTO items(name,price,category,sort) VALUES(?,?,?,?)",
                (n, p, c, i))
    con.commit()
    con.close()


def get_setting(key, default=""):
    con = db()
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    con.close()
    return row[0] if row else default


def set_setting(key, value):
    con = db()
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()
    con.close()


def today_stats(con=None):
    own = con is None
    if own:
        con = db()
    day = date.today().isoformat()
    n, rev = con.execute(
        "SELECT COUNT(DISTINCT category || '-' || order_no), "
        "COALESCE(SUM(price),0) FROM sales WHERE day=?", (day,)).fetchone()
    nxt_d = con.execute(
        "SELECT COALESCE(MAX(order_no),0)+1 FROM sales "
        "WHERE day=? AND category=1", (day,)).fetchone()[0]
    nxt_f = con.execute(
        "SELECT COALESCE(MAX(order_no),0)+1 FROM sales "
        "WHERE day=? AND category=2", (day,)).fetchone()[0]
    if own:
        con.close()
    return {"orders": n, "revenue": round(rev, 2),
            "next_drink": nxt_d, "next_food": nxt_f}


def record_order(lines):
    """lines=[{id,qty}] → يقسم السلة لطلبين مستقلين: مشروبات ومأكولات،
    لكل قسم عدّاد يومي خاص به."""
    day = date.today().isoformat()
    ts = datetime.now().strftime("%H:%M:%S")
    con = db()
    groups = {1: [], 2: []}
    for ln in lines:
        it = con.execute(
            "SELECT name, price, category FROM items WHERE id=?",
            (int(ln["id"]),)).fetchone()
        if it:
            groups[it[2]].append(
                (int(ln["id"]), it[0], it[1], max(1, int(ln.get("qty", 1)))))
    results = []
    for cat in (1, 2):
        if not groups[cat]:
            continue
        order_no = con.execute(
            "SELECT COALESCE(MAX(order_no),0)+1 FROM sales "
            "WHERE day=? AND category=?", (day, cat)).fetchone()[0]
        rows, total = [], 0.0
        for iid, name, price, qty in groups[cat]:
            for _ in range(qty):
                con.execute(
                    "INSERT INTO sales(item_id,item_name,price,category,day,"
                    "order_no,ts) VALUES(?,?,?,?,?,?,?)",
                    (iid, name, price, cat, day, order_no, ts))
            rows.append((name, qty, price))
            total += qty * price
        results.append({"cat": cat, "order_no": order_no,
                        "rows": rows, "total": round(total, 2)})
    con.commit()
    stats = today_stats(con)
    con.close()
    return results, stats


def undo_last_order():
    day = date.today().isoformat()
    con = db()
    last = con.execute(
        "SELECT order_no, category FROM sales WHERE day=? "
        "ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    if not last:
        con.close()
        return None
    order_no, cat = last
    cnt = con.execute(
        "SELECT COUNT(*) FROM sales WHERE day=? AND order_no=? AND category=?",
        (day, order_no, cat)).fetchone()[0]
    con.execute(
        "DELETE FROM sales WHERE day=? AND order_no=? AND category=?",
        (day, order_no, cat))
    con.commit()
    stats = today_stats(con)
    con.close()
    return {"order_no": order_no, "count": cnt, "stats": stats,
            "label": "المشروبات" if cat == 1 else "المأكولات"}


def all_items():
    con = db()
    rows = con.execute(
        "SELECT id, name, price, category, active FROM items "
        "ORDER BY category DESC, sort, id").fetchall()
    con.close()
    return [
        {"id": r[0], "name": r[1], "price": r[2], "cat": r[3], "active": r[4]}
        for r in rows
    ]


def daily_summary():
    con = db()
    rows = con.execute(
        """SELECT day,
                  SUM(CASE WHEN category=1 THEN 1 ELSE 0 END),
                  SUM(CASE WHEN category=2 THEN 1 ELSE 0 END),
                  COUNT(*), COUNT(DISTINCT order_no), SUM(price)
           FROM sales GROUP BY day ORDER BY day DESC""").fetchall()
    con.close()
    return rows


def day_details(day):
    con = db()
    rows = con.execute(
        """SELECT item_name, COUNT(*), SUM(price) FROM sales
           WHERE day=? GROUP BY item_name ORDER BY COUNT(*) DESC""",
        (day,)).fetchall()
    con.close()
    return rows


# ------------------------------------------------------------------ القسيمة

def ar(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return str(text)


def _font(size):
    from PIL import ImageFont
    for p in (r"C:\Windows\Fonts\tahomabd.ttf", r"C:\Windows\Fonts\tahoma.ttf",
              r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_receipt_image(order_no, rows, total, section):
    """قسيمة مضغوطة لأقصى حد لتوفير الورق."""
    from PIL import Image, ImageDraw
    f_num, f_head, f_sml = _font(56), _font(26), _font(24)
    H = 170 + 32 * len(rows) + 80
    img = Image.new("L", (RECEIPT_WIDTH, H), 255)
    d = ImageDraw.Draw(img)
    y = 6

    def center(t, f, gap=4):
        nonlocal y
        t = ar(t)
        d.text(((RECEIPT_WIDTH - d.textlength(t, font=f)) / 2, y),
               t, font=f, fill=0)
        y += f.size + gap

    def hr(gap=5):
        nonlocal y
        d.line((16, y, RECEIPT_WIDTH - 16, y), fill=0, width=2)
        y += gap

    center(get_setting("header", APP_TITLE), f_head, gap=2)
    # سطر واحد: القسم يميناً والتاريخ والوقت شمالاً
    lbl = ar(section)
    d.text((RECEIPT_WIDTH - 16 - d.textlength(lbl, font=f_sml), y),
           lbl, font=f_sml, fill=0)
    d.text((16, y), datetime.now().strftime("%d/%m %H:%M"), font=f_sml, fill=0)
    y += f_sml.size + 4
    hr()
    center(str(order_no), f_num, gap=6)
    hr()
    for name, qty, unit in rows:
        label = ar(("%s ×%d" % (name, qty)) if qty > 1 else name)
        d.text((RECEIPT_WIDTH - 20 - d.textlength(label, font=f_sml), y),
               label, font=f_sml, fill=0)
        d.text((20, y), "%.2f" % (qty * unit), font=f_sml, fill=0)
        y += 32
    # الإجمالي فقط عند تعدد الوحدات، السطر الواحد سعره ظاهر أصلاً
    if not (len(rows) == 1 and rows[0][1] == 1):
        hr()
        center("الإجمالي: %.2f د.أ" % total, f_sml, gap=2)
    return img.crop((0, 0, RECEIPT_WIDTH, y + 8))


def missing_deps():
    """يعيد قائمة المكتبات الناقصة اللازمة للطباعة."""
    need = [("PIL", "pillow"), ("arabic_reshaper", "arabic-reshaper"),
            ("bidi", "python-bidi")]
    if sys.platform == "win32":
        need.append(("win32print", "pywin32"))
    miss = []
    for mod, pkg in need:
        try:
            __import__(mod)
        except Exception:
            miss.append(pkg)
    return miss


def deps_hint():
    """رسالة عربية واضحة إن نقصت مكتبة، وإلا سلسلة فارغة."""
    miss = missing_deps()
    if not miss:
        return ""
    return ("لا تعمل الطباعة لأن مكتبات ناقصة: " + "، ".join(miss) +
            ". افتح موجه الأوامر cmd والصق هذا السطر ثم أعد فتح البرنامج:  "
            "pip install pillow arabic-reshaper python-bidi pywin32")


def friendly_print_error(exc):
    """يحوّل خطأ بايثون الخام إلى إرشاد مفهوم."""
    msg = str(exc)
    if isinstance(exc, ModuleNotFoundError) or "No module named" in msg:
        return deps_hint() or ("مكتبة ناقصة: " + msg)
    return "تعذّرت الطباعة: " + msg


def print_receipt(img):
    """يطبع حسب الطريقة المختارة: ويندوز عادية أو حرارية ESC/POS مباشرة."""
    if get_setting("print_mode", "gdi") == "escpos":
        print_escpos(img)
    else:
        print_gdi(img)


def print_gdi(img):
    import win32print
    import win32ui
    from PIL import ImageWin
    printer = get_setting("printer") or win32print.GetDefaultPrinter()
    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer)
    horz = hdc.GetDeviceCaps(8)
    w, h = img.size
    s = horz / float(w)
    hdc.StartDoc("receipt")
    hdc.StartPage()
    ImageWin.Dib(img.convert("RGB")).draw(
        hdc.GetHandleOutput(), (0, 0, int(w * s), int(h * s)))
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()


def escpos_data(img):
    """يحوّل صورة القسيمة إلى أوامر ESC/POS نقطية مع قصّ الورقة."""
    from PIL import Image
    img = img.convert("L").point(lambda v: 0 if v < 160 else 255, "1")
    w, h = img.size
    wb = (w + 7) // 8
    raw = img.tobytes()                      # في وضع "1": البت 1 = أبيض
    inv = bytes(b ^ 0xFF for b in raw)       # ESC/POS: البت 1 = طباعة
    out = bytearray(b"\x1b@")                # تهيئة
    out += b"\x1dv0\x00"                     # GS v 0: صورة نقطية
    out += bytes((wb & 255, wb >> 8, h & 255, h >> 8))
    out += inv
    out += b"\n\n\n"
    out += b"\x1dV\x42\x00"                  # GS V B: قصّ جزئي (يتجاهله من لا يدعمه)
    return bytes(out)


def print_escpos(img):
    import win32print
    printer = get_setting("printer") or win32print.GetDefaultPrinter()
    h = win32print.OpenPrinter(printer)
    try:
        win32print.StartDocPrinter(h, 1, ("receipt", None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, escpos_data(img))
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


def list_printers():
    """يعيد (قائمة الطابعات، الافتراضية، سبب الفراغ إن وُجد)."""
    if sys.platform != "win32":
        return [], "", "التطبيق لا يعمل على ويندوز حالياً"
    names, default, reason = [], "", ""
    try:
        import win32print
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            default = ""
        flags = (win32print.PRINTER_ENUM_LOCAL
                 | win32print.PRINTER_ENUM_CONNECTIONS
                 | win32print.PRINTER_ENUM_NETWORK
                 | win32print.PRINTER_ENUM_SHARED)
        seen = set()
        for level in (4, 2, 1):        # المستوى 4 أشمل، وإن فشل ننزل
            try:
                for p in win32print.EnumPrinters(flags, None, level):
                    nm = p.get("pPrinterName") if isinstance(p, dict) else p[2]
                    if nm and nm not in seen:
                        seen.add(nm)
                        names.append(nm)
                if names:
                    break
            except Exception:
                continue
        if default and default not in seen:
            names.insert(0, default)
    except ImportError:
        # بدون pywin32: نسأل ويندوز عبر PowerShell
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Printer | Select-Object -ExpandProperty Name"],
                stderr=subprocess.DEVNULL, timeout=8)
            names = [ln.strip() for ln in
                     out.decode("utf-8", "ignore").splitlines() if ln.strip()]
        except Exception:
            names = []
        reason = ("مكتبة الطباعة pywin32 غير مثبتة. "
                  "ثبّتها بأمر: pip install pywin32")
    if not names and not reason:
        reason = ("لم يعثر ويندوز على أي طابعة مثبتة. "
                  "أضف الطابعة من إعدادات ويندوز ثم افتح هذه النافذة ثانية.")
    return names, default, reason


def img_to_data_url(img):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ الواجهة

PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>كفتيريا السكن - كلية عجلون</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@500;600;700;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F5F7FA; --card:#FFFFFF; --text:#1F2937; --muted:#6B7280;
  --primary:#2563EB; --primary2:#1D4ED8; --drink:#3B82F6; --food:#22C55E;
  --accent:#F59E0B; --danger:#EF4444; --line:#E5E7EB;
  --drinkbg:#EFF6FF; --foodbg:#F0FDF4;
  --shadow:0 1px 2px rgba(16,24,40,.06),0 4px 14px rgba(16,24,40,.08);
  --shadow-lg:0 8px 28px rgba(16,24,40,.16);
  --r:16px;
  --head:132px;          /* ارتفاع الشريط العلوي + لوحة الطلب المطوية */
  --bar:56px;            /* ارتفاع الشريط العلوي وحده */
  --tray:300px;          /* عرض عمود الطلب على اليسار */
  --fs-nm:16px; --fs-pr:19px;   /* تُضبط تلقائياً حسب عدد الأصناف */
}
body.dark{
  --bg:#111827; --card:#1F2937; --text:#F9FAFB; --muted:#9CA3AF;
  --line:#374151; --drinkbg:#1E3A5F; --foodbg:#14532D;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 14px rgba(0,0,0,.45);
  --shadow-lg:0 8px 28px rgba(0,0,0,.6);
}
*{box-sizing:border-box;margin:0}
html,body{height:100%;overflow:hidden}
body{
  background:var(--bg);color:var(--text);
  font-family:'Cairo','IBM Plex Sans Arabic','Segoe UI',Tahoma,sans-serif;
  transition:background .2s,color .2s;
}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit}
input,select{font-family:inherit;font-size:15px;color:var(--text)}

/* ---------- الشريط العلوي ---------- */
header{
  position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:14px;
  padding:10px 18px;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  background:color-mix(in srgb,var(--card) 78%,transparent);
  border-bottom:1px solid var(--line);
}
.brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px}
.brand .dot{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;
  background:linear-gradient(135deg,var(--primary),var(--drink));color:#fff;font-size:19px}
.brand small{display:block;font-size:11px;color:var(--muted);font-weight:600}
.search{flex:1;max-width:440px;position:relative}
.search input{
  width:100%;padding:10px 40px 10px 14px;border-radius:12px;border:1px solid var(--line);
  background:var(--card);outline:none;transition:border .2s,box-shadow .2s}
.search input:focus{border-color:var(--primary);box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 20%,transparent)}
.search svg{position:absolute;right:12px;top:50%;transform:translateY(-50%);color:var(--muted)}
.spacer{flex:1}
.chip{
  display:flex;align-items:center;gap:8px;padding:7px 13px;border-radius:999px;
  background:var(--card);border:1px solid var(--line);font-size:13px;font-weight:700;
  box-shadow:var(--shadow)}
.chip b{color:var(--primary)}
.next{background:linear-gradient(135deg,var(--drink),var(--primary2));color:#fff;border:none}
.next b{color:#fff;font-size:16px}
.next.nf{background:linear-gradient(135deg,var(--food),#15803D)}
.iconbtn{
  width:40px;height:40px;border-radius:12px;display:grid;place-items:center;
  background:var(--card);border:1px solid var(--line);transition:transform .2s,background .2s}
.iconbtn:hover{transform:translateY(-1px);background:color-mix(in srgb,var(--primary) 8%,var(--card))}
#clock{font-size:12.5px;color:var(--muted);font-weight:700;min-width:118px;text-align:center}

/* ---------- الأقسام ---------- */
/* ---------- التخطيط: قسمان ثابتان بلا تمرير ---------- */
.wrap{display:flex;gap:14px;padding:10px 16px 14px;align-items:stretch;
  height:calc(100vh - var(--head));overflow:hidden;
  margin-left:0;transition:margin-left .25s}
body.tray-open .wrap{margin-left:var(--tray)}
.sec{flex:1;display:flex;flex-direction:column;min-width:0}
.sec h3{
  font-size:19px;font-weight:800;color:#fff;padding:9px 16px;border-radius:14px 14px 0 0;
  display:flex;justify-content:space-between;align-items:center}
.sec h3 span{font-size:13px;font-weight:700;opacity:.85}
.sec[data-c="2"] h3{background:linear-gradient(135deg,var(--food),#15803D)}
.sec[data-c="1"] h3{background:linear-gradient(135deg,var(--drink),var(--primary2))}
.grid{
  flex:1;display:grid;gap:8px;padding:9px;background:var(--card);
  border-radius:0 0 18px 18px;box-shadow:var(--shadow);align-content:stretch;overflow:hidden}

/* بلاطة صنف: نص فقط */
.p{
  background:var(--bg);border-radius:14px;padding:6px 8px;position:relative;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
  border:2px solid transparent;transition:transform .2s,box-shadow .2s,border-color .2s;
  user-select:none;overflow:hidden;min-height:0}
.p:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg)}
.p:active{transform:scale(.96)}
.p[data-cat="2"]{border-color:color-mix(in srgb,var(--food) 22%,transparent)}
.p[data-cat="1"]{border-color:color-mix(in srgb,var(--drink) 22%,transparent)}
.p[data-cat="2"]:hover{border-color:var(--food)}
.p[data-cat="1"]:hover{border-color:var(--drink)}
.p .nm{font-weight:700;font-size:var(--fs-nm);line-height:1.25;text-align:center;
  overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.p .pr{font-weight:800;font-size:var(--fs-pr);line-height:1.1}
.p[data-cat="1"] .pr{color:var(--drink)}
.p[data-cat="2"] .pr{color:var(--food)}
.p .pr small{font-size:.62em;font-weight:700;color:var(--muted);margin-inline-start:3px}
.p.hit{outline:3px solid var(--accent);outline-offset:-3px}

/* ---------- لوحة الطلب: عمود ثابت على اليسار ---------- */
aside{
  position:fixed;top:var(--bar);bottom:0;left:0;width:var(--tray);z-index:30;
  background:var(--card);box-shadow:6px 0 26px rgba(16,24,40,.10);
  display:flex;flex-direction:column;
  transform:translateX(calc(-1 * var(--tray)));transition:transform .25s}
aside.open{transform:translateX(0)}
body.dark aside{box-shadow:6px 0 26px rgba(0,0,0,.5)}
.handle{
  display:flex;align-items:center;gap:12px;padding:12px 16px;
  border-bottom:1px solid var(--line)}
.handle .ttl{font-weight:800;font-size:17px}
.handle .cnt{
  background:var(--accent);color:#fff;border-radius:999px;padding:2px 11px;
  font-size:14px;font-weight:800;min-width:28px;text-align:center}
.handle .sum{margin-inline-start:auto;font-weight:800;font-size:19px;color:var(--primary)}
.handle .arw{display:none}
#lines{flex:1;overflow-y:auto;padding:0 12px;min-height:0}
.ln{
  display:flex;align-items:center;gap:8px;padding:10px 4px;
  border-bottom:1px dashed var(--line);animation:pop .2s}
@keyframes pop{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
.ln .in{flex:1;min-width:0}
.ln .nm{font-weight:700;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ln .un{font-size:12px;color:var(--muted)}
.qty{display:flex;align-items:center;gap:5px}
.qty button{
  width:30px;height:30px;border-radius:10px;background:var(--bg);font-weight:800;
  font-size:17px;transition:background .2s}
.qty button:hover{background:color-mix(in srgb,var(--primary) 14%,var(--bg))}
.qty b{min-width:22px;text-align:center;font-size:16px}
.ln .tot{font-weight:800;font-size:15px;min-width:50px;text-align:left}
.ln .rm{color:var(--danger);font-size:13px;font-weight:800;padding:5px 8px;border-radius:8px}
.ln .rm:hover{background:color-mix(in srgb,var(--danger) 12%,transparent)}
.empty{color:var(--muted);text-align:center;padding:30px 10px;font-size:15px}
.actions{display:flex;flex-direction:column;gap:9px;padding:12px 14px;border-top:1px solid var(--line)}
.actrow{display:flex;gap:9px}
.actrow .btn{flex:1}
.btn{
  padding:12px 14px;border-radius:13px;font-weight:800;font-size:15px;
  transition:transform .2s,box-shadow .2s,background .2s}
.btn:active{transform:scale(.97)}
.pay{
  width:100%;padding:15px;font-size:18px;color:#fff;border-radius:14px;
  background:linear-gradient(135deg,var(--primary),var(--primary2));
  box-shadow:0 6px 18px color-mix(in srgb,var(--primary) 45%,transparent)}
.pay:hover{box-shadow:0 8px 24px color-mix(in srgb,var(--primary) 60%,transparent)}
.pay:disabled{opacity:.45;box-shadow:none;cursor:not-allowed}
.ghost{background:var(--bg)}
.ghost:hover{background:color-mix(in srgb,var(--primary) 10%,var(--bg))}
.warn{background:color-mix(in srgb,var(--danger) 12%,var(--bg));color:var(--danger)}
.warn:hover{background:color-mix(in srgb,var(--danger) 20%,var(--bg))}

/* زر إظهار الطلب حين يكون العمود مطويّاً */
.trayfab{
  position:fixed;bottom:18px;left:18px;z-index:20;display:flex;align-items:center;gap:10px;
  padding:12px 18px;border-radius:999px;color:#fff;font-weight:800;font-size:16px;
  background:linear-gradient(135deg,var(--primary),var(--primary2));
  box-shadow:0 8px 22px color-mix(in srgb,var(--primary) 50%,transparent);
  transition:transform .2s,opacity .2s}
.trayfab:hover{transform:translateY(-2px)}
.trayfab .cnt{background:var(--accent);border-radius:999px;padding:1px 10px;font-size:14px}
aside.open ~ .trayfab{opacity:0;pointer-events:none}

/* ---------- نوافذ ---------- */
.ovl{
  position:fixed;inset:0;background:rgba(15,23,42,.45);backdrop-filter:blur(3px);
  display:none;place-items:center;z-index:60;padding:20px}
.ovl.show{display:grid}
.modal{
  background:var(--card);border-radius:20px;box-shadow:var(--shadow-lg);
  width:min(720px,100%);max-height:88vh;display:flex;flex-direction:column;
  animation:up .2s}
@keyframes up{from{opacity:0;transform:translateY(14px)}to{opacity:1}}
.modal header{position:static;background:none;border-bottom:1px solid var(--line);
  padding:14px 18px;font-weight:800;font-size:16px;display:flex;justify-content:space-between;backdrop-filter:none}
.modal .body{padding:14px 18px;overflow-y:auto}
.x{font-size:20px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{color:var(--muted);font-size:12px;text-align:center;padding:8px 6px;border-bottom:1px solid var(--line)}
td{padding:9px 6px;text-align:center;border-bottom:1px solid var(--line)}
tr.rowbtn:hover{background:color-mix(in srgb,var(--primary) 6%,transparent);cursor:pointer}
td.nm{text-align:right;font-weight:700}
.off td{color:var(--muted)}
.tag{font-size:11px;padding:3px 9px;border-radius:999px;font-weight:800}
.tag.d{background:var(--drinkbg);color:var(--drink)}
.tag.f{background:var(--foodbg);color:var(--food)}
.field{margin-bottom:12px}
.field label{display:block;font-size:12.5px;font-weight:700;color:var(--muted);margin-bottom:5px}
.field input,.field select{
  width:100%;padding:10px 12px;border-radius:12px;border:1px solid var(--line);
  background:var(--bg);outline:none}
.field input:focus,.field select:focus{border-color:var(--primary)}
.mrow{display:flex;gap:10px;justify-content:flex-start;padding:12px 18px;border-top:1px solid var(--line);flex-wrap:wrap}
.prim{background:var(--primary);color:#fff}
.prim:hover{background:var(--primary2)}
.rcimg{max-width:100%;border-radius:12px;border:1px solid var(--line);margin-bottom:12px}
.perr{background:color-mix(in srgb,var(--danger) 10%,transparent);color:var(--danger);
  font-size:12.5px;font-weight:700;border-radius:10px;padding:8px 12px;margin-bottom:10px}
.bigno{text-align:center;font-size:15px;color:var(--muted);font-weight:700}
.bigno b{display:block;font-size:56px;color:var(--primary);line-height:1.1}

/* إشعار */
#toast{
  position:fixed;bottom:22px;right:50%;transform:translate(50%,80px);z-index:80;
  background:var(--text);color:var(--card);padding:11px 22px;border-radius:999px;
  font-weight:700;font-size:14px;opacity:0;transition:all .25s;box-shadow:var(--shadow-lg)}
#toast.show{opacity:1;transform:translate(50%,0)}

@media (max-width:820px){
  body.tray-open .wrap{margin-left:0}
  aside{width:min(340px,88vw)}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
:focus-visible{outline:3px solid var(--primary);outline-offset:2px}
</style>
</head>
<body>

<header>
  <div class="brand"><div class="dot">ك</div>
    <div>كفتيريا السكن<small>كلية عجلون</small></div></div>
  <div class="search">
    <input id="q" placeholder="ابحث عن صنف...  ( / )" autocomplete="off">
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
  </div>
  <div class="spacer"></div>
  <div id="clock"></div>
  <div class="chip next" title="رقم طلب المشروبات التالي">المشروبات <b id="stNextD">#1</b></div>
  <div class="chip next nf" title="رقم طلب المأكولات التالي">المأكولات <b id="stNextF">#1</b></div>
  <button class="iconbtn" id="darkBtn" title="الوضع الليلي">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg></button>
  <button class="iconbtn" onclick="openSummary()" title="ملخص المبيعات">
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg></button>
  <button class="iconbtn" onclick="openItems()" title="الأصناف">
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/></svg></button>
  <button class="iconbtn" onclick="openSettings()" title="الإعدادات">
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.01a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55h.01a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.01a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z"/></svg></button>
</header>

<div class="wrap">
  <section class="sec" data-c="2">
    <h3>المأكولات <span id="cntF"></span></h3>
    <div class="grid" id="gridF"></div>
  </section>
  <section class="sec" data-c="1">
    <h3>المشروبات <span id="cntD"></span></h3>
    <div class="grid" id="gridD"></div>
  </section>
</div>

<aside id="tray">
  <div class="handle">
    <span class="ttl">الطلب الحالي</span>
    <span class="cnt" id="lineCount">0</span>
    <span class="sum"><span id="cartTotal">0.00</span> د.أ</span>
  </div>
  <div id="lines"></div>
  <div class="actions">
    <button class="btn pay" id="payBtn" onclick="pay()">دفع وطباعة  (F9)</button>
    <div class="actrow">
      <button class="btn ghost" onclick="clearCart()">طلب جديد</button>
      <button class="btn warn" onclick="undoLast()">تراجع</button>
      <button class="btn ghost" onclick="closeTray()">إخفاء</button>
    </div>
  </div>
</aside>
<button class="trayfab" onclick="openTray()">
  الطلب <span class="cnt" id="fabCnt">0</span>
  <span id="fabSum">0.00</span> د.أ
</button>

<!-- نافذة القسيمة -->
<div class="ovl" id="receiptOvl">
  <div class="modal" style="width:min(420px,100%)">
    <header>تم تسجيل الطلب<button class="x" onclick="hide('receiptOvl')">✕</button></header>
    <div class="body" id="rcBody"></div>
    <div class="mrow"><button class="btn prim" onclick="hide('receiptOvl')">تم</button></div>
  </div>
</div>

<!-- ملخص المبيعات -->
<div class="ovl" id="sumOvl">
  <div class="modal">
    <header>ملخص المبيعات<button class="x" onclick="hide('sumOvl')">✕</button></header>
    <div class="body" id="sumBody"></div>
    <div class="mrow">
      <button class="btn prim" onclick="location='/api/export.csv'">تصدير CSV</button>
    </div>
  </div>
</div>

<!-- الأصناف -->
<div class="ovl" id="itemsOvl">
  <div class="modal">
    <header>الأصناف<button class="x" onclick="hide('itemsOvl')">✕</button></header>
    <div class="body" id="itemsBody"></div>
    <div class="mrow">
      <button class="btn prim" onclick="editItem(null)">+ إضافة صنف</button>
      <button class="btn ghost" onclick="toggleSel()">إخفاء / إظهار المحدد</button>
    </div>
  </div>
</div>

<!-- تحرير صنف -->
<div class="ovl" id="editOvl">
  <div class="modal" style="width:min(400px,100%)">
    <header id="editTitle">تعديل صنف<button class="x" onclick="hide('editOvl')">✕</button></header>
    <div class="body">
      <div class="field"><label>اسم الصنف</label><input id="eName"></div>
      <div class="field"><label>السعر بالدينار</label><input id="ePrice" inputmode="decimal"></div>
      <div class="field"><label>القسم</label>
        <select id="eCat"><option value="2">مأكولات</option><option value="1">مشروبات</option></select></div>
    </div>
    <div class="mrow"><button class="btn prim" onclick="saveItem()">حفظ</button>
      <button class="btn ghost" onclick="hide('editOvl')">إلغاء</button></div>
  </div>
</div>

<!-- الإعدادات -->
<div class="ovl" id="setOvl">
  <div class="modal" style="width:min(440px,100%)">
    <header>الإعدادات<button class="x" onclick="hide('setOvl')">✕</button></header>
    <div class="body">
      <div id="depBanner" style="display:none;background:color-mix(in srgb,var(--danger) 12%,var(--card));
        border:1px solid var(--danger);color:var(--danger);border-radius:12px;
        padding:11px 13px;font-size:13px;font-weight:700;margin-bottom:14px;line-height:1.6"></div>
      <div class="field"><label>اسم الكفتيريا على القسيمة</label><input id="sHeader"></div>
      <div class="field"><label>الطابعة (فارغة = الافتراضية)</label>
        <div style="display:flex;gap:8px">
          <select id="sPrinter" style="flex:1"></select>
          <button class="btn ghost" style="padding:8px 14px" onclick="reloadPrinters()">تحديث</button>
        </div>
        <div id="sPrinterNote" style="font-size:12px;color:var(--muted);margin-top:6px"></div>
      </div>
      <div class="field"><label>طريقة الطباعة</label>
        <select id="sMode">
          <option value="gdi">ويندوز عادية (الافتراضية)</option>
          <option value="escpos">حرارية مباشرة ESC/POS (جرّبها إن لم تطبع الأولى)</option>
        </select></div>
      <div class="field">
        <button class="btn ghost" style="width:100%" onclick="testPrint()">طباعة تجريبية الآن</button>
        <div id="sTestNote" style="font-size:12.5px;margin-top:6px"></div>
      </div>
      <div class="field"><label>عدد نسخ قسيمة المأكولات</label>
        <input id="sCopies" type="number" min="1" max="5" step="1"></div>
      <div class="field"><label style="display:flex;gap:8px;align-items:center;font-size:14px;color:var(--text)">
        <input type="checkbox" id="sAuto" style="width:17px;height:17px"> طباعة القسيمة تلقائياً عند الدفع</label></div>
    </div>
    <div class="mrow"><button class="btn prim" onclick="saveSettings()">حفظ</button></div>
  </div>
</div>

<div id="toast"></div>

<script>
"use strict";
let ITEMS=[], CART={}, Q="";
const fmt=n=>(+n).toFixed(2);

async function api(url,data){
  const r=await fetch(url,data?{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(data)}:undefined);
  return r.json();
}
function toast(t){const e=document.getElementById("toast");e.textContent=t;
  e.classList.add("show");clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove("show"),2200);}
function show(id){document.getElementById(id).classList.add("show");}
function hide(id){document.getElementById(id).classList.remove("show");}

/* ---------- تحميل الحالة ---------- */
async function load(){
  const s=await api("/api/state");
  ITEMS=s.items; setStats(s.stats);
  renderGrid(); renderCart();
}
function setStats(st){
  stNextD.textContent="#"+st.next_drink; stNextF.textContent="#"+st.next_food;
}

/* ---------- شبكة المنتجات: قسمان، كل الأصناف بلا تمرير ---------- */
function itemsOf(cat){
  return ITEMS.filter(it=>it.active && it.cat===cat)
              .filter(it=>!Q||it.name.includes(Q));
}
function fill(el, list){
  el.innerHTML=list.length?list.map(it=>
    `<button class="p" data-cat="${it.cat}" onclick="add(${it.id})">
       <span class="nm">${it.name}</span>
       <span class="pr">${fmt(it.price)}<small>د.أ</small></span>
     </button>`).join("")
   :`<div class="empty" style="grid-column:1/-1;align-self:center">لا توجد نتائج مطابقة</div>`;
}
function renderGrid(){
  const D=itemsOf(1), F=itemsOf(2);
  cntD.textContent=D.length+" صنف"; cntF.textContent=F.length+" صنف";
  fill(gridD,D); fill(gridF,F);
  layout();
}
/* يوزّع الأصناف على أعمدة/صفوف تملأ المساحة، ويكبّر الخط لأقصى حد يتّسع فعلياً */
function layout(){
  // ارتفاع منطقة العرض = الشاشة ناقص الشريط العلوي فقط (لوحة الطلب صارت جانبية)
  const hd=document.querySelector("header").offsetHeight;
  document.documentElement.style.setProperty("--head",(hd+8)+"px");
  document.documentElement.style.setProperty("--bar",hd+"px");
  const n=Math.max(itemsOf(1).length, itemsOf(2).length, 1);
  const box=gridD.getBoundingClientRect();
  const W=box.width-18, H=box.height-18, gap=8;
  let best=null;
  for(let cols=1;cols<=8;cols++){
    const rows=Math.ceil(n/cols);
    const w=(W-(cols-1)*gap)/cols, h=(H-(rows-1)*gap)/rows;
    if(w<74||h<48) continue;
    const score=Math.min(w/1.5,h);          // نفضّل البلاطات القريبة من المربع
    if(!best||score>best.score) best={cols,rows,score,w,h};
  }
  if(!best) best={cols:4,rows:Math.ceil(n/4),w:W/4,h:H/Math.ceil(n/4)};
  for(const g of [gridD,gridF]){
    g.style.gridTemplateColumns=`repeat(${best.cols},1fr)`;
    g.style.gridTemplateRows=`repeat(${best.rows},1fr)`;
  }
  setFont(best.w, best.h);
}
/* خط كبير للجميع، والأسماء الطويلة وحدها تصغر داخل بلاطاتها */
function setFont(w,h){
  const tiles=[...document.querySelectorAll(".p")];
  if(!tiles.length) return;
  const clipped=(p)=>{
    const nm=p.querySelector(".nm");
    return nm.scrollHeight>nm.clientHeight+1||nm.scrollWidth>nm.clientWidth+1||
           p.scrollHeight>p.clientHeight+1;
  };
  const apply=(size)=>{
    document.documentElement.style.setProperty("--fs-nm",size+"px");
    document.documentElement.style.setProperty("--fs-pr",Math.round(size*1.2)+"px");
    let bad=0;
    for(const p of tiles){
      p.querySelector(".nm").style.fontSize="";
      if(clipped(p)) bad++;
    }
    return bad;
  };
  // أكبر مقاس يظل معه ثلثا الأصناف على الأقل غير مقصوصين
  const allow=Math.max(1,Math.round(tiles.length/3));
  let lo=13, hi=40, fit=13;
  while(lo<=hi){
    const mid=(lo+hi)>>1;
    if(apply(mid)<=allow){fit=mid;lo=mid+1;}else{hi=mid-1;}
  }
  apply(fit);
  // الآن نعالج الطوال فرادى دون المساس بالبقية
  for(const p of tiles){
    const nm=p.querySelector(".nm");
    let s=fit;
    while(s>12&&clipped(p)){s--;nm.style.fontSize=s+"px";}
  }
}
addEventListener("resize",()=>{clearTimeout(window._rz);window._rz=setTimeout(layout,120);});

/* ---------- السلة ---------- */
function add(id){CART[id]=(CART[id]||0)+1;renderCart();openTray();}
function inc(id,d){CART[id]=(CART[id]||0)+d;if(CART[id]<=0)delete CART[id];renderCart();}
function rm(id){delete CART[id];renderCart();}
function clearCart(){CART={};renderCart();closeTray();}
function openTray(){
  tray.classList.add("open"); document.body.classList.add("tray-open");
  requestAnimationFrame(layout);
}
function closeTray(){
  tray.classList.remove("open"); document.body.classList.remove("tray-open");
  requestAnimationFrame(layout);
}
function cartRows(){return Object.entries(CART).map(([id,q])=>{
  const it=ITEMS.find(x=>x.id==id);return {it,q};}).filter(r=>r.it);}
function renderCart(){
  const rows=cartRows();
  const units=rows.reduce((a,r)=>a+r.q,0);
  lineCount.textContent=units;
  const total=fmt(rows.reduce((a,r)=>a+r.q*r.it.price,0));
  cartTotal.textContent=total;
  fabCnt.textContent=units; fabSum.textContent=total;
  lines.innerHTML=rows.length?rows.map(({it,q})=>
    `<div class="ln">
       <div class="in"><div class="nm">${it.name}</div>
         <div class="un">${fmt(it.price)} د.أ</div></div>
       <div class="qty">
         <button onclick="inc(${it.id},1)">+</button><b>${q}</b>
         <button onclick="inc(${it.id},-1)">−</button></div>
       <div class="tot">${fmt(q*it.price)}</div>
       <button class="rm" title="حذف" onclick="rm(${it.id})">حذف</button>
     </div>`).join("")
   :`<div class="empty">اضغط على أي صنف لإضافته للطلب</div>`;
  payBtn.disabled=!rows.length;
}

/* ---------- الدفع ---------- */
async function pay(){
  const rows=cartRows(); if(!rows.length) return;
  payBtn.disabled=true;
  const r=await api("/api/order",{lines:rows.map(({it,q})=>({id:it.id,qty:q}))});
  setStats(r.stats); CART={}; renderCart();
  const withImg=r.orders.filter(o=>o.image);
  if(withImg.length){
    rcBody.innerHTML=r.orders.map(o=>`
      <div class="bigno">${o.label}${o.copies>1?` (${o.copies} نسخ)`:""}<b>${o.order_no}</b></div>
      ${o.print_error?`<div class="perr">لم تُطبع تلقائياً: ${o.print_error}</div>`:""}
      ${o.image?`<img class="rcimg" src="${o.image}" alt="">`:""}`).join("");
    show("receiptOvl");
    if(r.orders.some(o=>o.print_error)) toast("تنبيه: فشلت الطباعة، القسيمة على الشاشة");
  }else{
    toast("طُبع: "+r.orders.map(o=>
      o.label+" #"+o.order_no+(o.copies>1?" ×"+o.copies:"")).join(" · ")+" ✓");
  }
}
async function undoLast(){
  const r=await api("/api/undo",{});
  if(r.ok){setStats(r.stats);toast("حُذف طلب "+r.label+" رقم "+r.order_no);}
  else toast("لا توجد طلبات اليوم");
}

/* ---------- الملخص ---------- */
async function openSummary(){
  const r=await api("/api/summary");
  sumBody.innerHTML=`
   <table><tr><th>التاريخ</th><th>مشروبات</th><th>مأكولات</th><th>وحدات</th><th>طلبات</th><th>الإيراد (د.أ)</th></tr>
   ${r.rows.map(x=>`<tr class="rowbtn" onclick="openDay('${x[0]}','${x[6]}')">
     <td class="nm">${x[6]}</td><td>${x[1]}</td><td>${x[2]}</td><td>${x[3]}</td><td>${x[4]}</td><td><b>${fmt(x[5])}</b></td></tr>`).join("")}
   </table>
   <p style="margin-top:12px;font-weight:800">أيام العمل: ${r.rows.length} &nbsp;|&nbsp;
     إجمالي الطلبات: ${r.tot_orders} &nbsp;|&nbsp; الإيراد الكلي: ${fmt(r.tot_rev)} د.أ</p>
   <p style="color:var(--muted);font-size:12px">اضغط على أي يوم لعرض تفاصيله</p>`;
  show("sumOvl");
}
async function openDay(day,disp){
  const r=await api("/api/day?d="+day);
  sumBody.innerHTML=`
   <button class="btn ghost" onclick="openSummary()" style="margin-bottom:10px">→ رجوع</button>
   <h3 style="margin-bottom:8px">تفاصيل يوم ${disp}</h3>
   <table><tr><th>الصنف</th><th>العدد</th><th>الإيراد</th></tr>
   ${r.rows.map(x=>`<tr><td class="nm">${x[0]}</td><td>${x[1]}</td><td>${fmt(x[2])}</td></tr>`).join("")}
   </table>`;
}

/* ---------- الأصناف ---------- */
async function openItems(){
  itemsBody.innerHTML=`
   <table><tr><th></th><th>الصنف</th><th>السعر</th><th>القسم</th><th>الحالة</th><th></th></tr>
   ${ITEMS.map(it=>`
    <tr class="${it.active?"":"off"}">
      <td><input type="checkbox" class="sel" value="${it.id}" style="width:16px;height:16px"></td>
      <td class="nm">${it.name}</td><td>${fmt(it.price)}</td>
      <td><span class="tag ${it.cat===1?"d":"f"}">${it.cat===1?"مشروبات":"مأكولات"}</span></td>
      <td>${it.active?"ظاهر":"مخفي"}</td>
      <td><button class="btn ghost" style="padding:5px 13px" onclick="editItem(${it.id})">تعديل</button></td>
    </tr>`).join("")}
   </table>`;
  show("itemsOvl");
}
let EDIT_ID=null;
function editItem(id){
  EDIT_ID=id;
  const it=id?ITEMS.find(x=>x.id===id):null;
  editTitle.firstChild.textContent=it?("تعديل: "+it.name+" ("+fmt(it.price)+" د.أ)"):"إضافة صنف";
  eName.value=it?it.name:""; ePrice.value=it?fmt(it.price):"";
  eCat.value=it?String(it.cat):"2";
  show("editOvl"); eName.focus();
}
async function saveItem(){
  const name=eName.value.trim(), price=parseFloat(ePrice.value.replace(",", "."));
  if(!name){toast("اكتب اسم الصنف");return;}
  if(!(price>=0)){toast("اكتب سعراً صحيحاً مثل 0.50");return;}
  const r=await api("/api/item",{id:EDIT_ID,name,price,cat:+eCat.value});
  ITEMS=r.items; hide("editOvl"); openItems(); renderGrid(); toast("تم الحفظ ✓");
}
async function toggleSel(){
  const ids=[...document.querySelectorAll(".sel:checked")].map(e=>+e.value);
  if(!ids.length){toast("حدد صنفاً أو أكثر أولاً");return;}
  const r=await api("/api/toggle",{ids});
  ITEMS=r.items; openItems(); renderGrid();
  toast("تم تغيير حالة "+ids.length+" صنف");
}

/* ---------- الإعدادات ---------- */
function fillPrinters(r){
  const def=r.default_printer?` (${r.default_printer})`:"";
  sPrinter.innerHTML=`<option value="">الطابعة الافتراضية${def}</option>`+
    r.printers.map(p=>`<option ${p===r.printer?"selected":""}>${p}</option>`).join("");
  if(r.printers_reason){
    sPrinterNote.textContent=r.printers_reason;
    sPrinterNote.style.color="var(--danger)";
  }else{
    sPrinterNote.textContent="عُثر على "+r.printers.length+" طابعة.";
    sPrinterNote.style.color="var(--muted)";
  }
}
async function openSettings(){
  const r=await api("/api/settings");
  sHeader.value=r.header;
  fillPrinters(r);
  sMode.value=r.print_mode||"gdi";
  sAuto.checked=r.auto_print; sCopies.value=r.food_copies;
  sTestNote.textContent="";
  if(r.deps_hint){depBanner.style.display="block";depBanner.textContent=r.deps_hint;}
  else{depBanner.style.display="none";}
  show("setOvl");
}
async function testPrint(){
  sTestNote.style.color="var(--muted)";
  sTestNote.textContent="جارٍ إرسال قسيمة اختبار...";
  const r=await api("/api/testprint",{printer:sPrinter.value,print_mode:sMode.value});
  if(r.ok){
    sTestNote.style.color="var(--food)";
    sTestNote.textContent="أُرسلت للطابعة. إن لم تخرج ورقة خلال ثوانٍ جرّب الطريقة الأخرى من قائمة طريقة الطباعة ثم اضغط الزر ثانية.";
  }else{
    sTestNote.style.color="var(--danger)";
    sTestNote.textContent=r.error;
  }
}
async function reloadPrinters(){
  sPrinterNote.textContent="جارٍ البحث عن الطابعات...";
  sPrinterNote.style.color="var(--muted)";
  const r=await api("/api/settings");
  fillPrinters(r);
}
async function saveSettings(){
  await api("/api/settings",{header:sHeader.value.trim(),
    printer:sPrinter.value,auto_print:sAuto.checked?1:0,
    print_mode:sMode.value,
    food_copies:+sCopies.value||2});
  hide("setOvl"); toast("حُفظت الإعدادات ✓");
}

/* ---------- عام ---------- */
q.addEventListener("input",e=>{Q=e.target.value.trim();renderGrid();});
document.addEventListener("keydown",e=>{
  if(e.key==="/"&&document.activeElement!==q){e.preventDefault();q.focus();}
  if(e.key==="F9"){e.preventDefault();pay();}
  if(e.key==="Escape")document.querySelectorAll(".ovl.show").forEach(o=>o.classList.remove("show"));
});
const MOON='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
const SUN='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
darkBtn.onclick=()=>{
  document.body.classList.toggle("dark");
  const on=document.body.classList.contains("dark");
  darkBtn.innerHTML=on?SUN:MOON; localStorage.setItem("pos_theme",on?"dark":"light");
};
if(localStorage.getItem("pos_theme")==="dark"){document.body.classList.add("dark");darkBtn.innerHTML=SUN;}
setInterval(()=>{clock.textContent=new Date().toLocaleString("ar-JO",
  {weekday:"long",hour:"2-digit",minute:"2-digit",day:"2-digit",month:"2-digit"});},1000);
setInterval(()=>fetch("/api/ping",{method:"POST"}).catch(()=>{}),2000);
load();
</script>
</body></html>
"""


# ------------------------------------------------------------------ الخادم

LAST_PING = time.time() + 30  # مهلة بدء


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, data, ctype="application/json; charset=utf-8", code=200,
              extra=None):
        body = data if isinstance(data, bytes) else json.dumps(
            data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # ---------------- GET
    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/":
            self._send(PAGE.encode(), "text/html; charset=utf-8")
        elif p.path == "/api/state":
            self._send({"items": all_items(), "stats": today_stats()})
        elif p.path == "/api/summary":
            rows = []
            tot_o = tot_r = 0
            for day, dr, fo, units, orders, rev in daily_summary():
                disp = "%s/%s/%s" % (day[8:10], day[5:7], day[0:4])
                rows.append([day, dr, fo, units, orders, rev, disp])
                tot_o += orders
                tot_r += rev
            self._send({"rows": rows, "tot_orders": tot_o,
                        "tot_rev": round(tot_r, 2)})
        elif p.path == "/api/day":
            d = parse_qs(p.query).get("d", [""])[0]
            self._send({"rows": day_details(d)})
        elif p.path == "/api/settings":
            printers, default, reason = list_printers()
            self._send({"header": get_setting("header", APP_TITLE),
                        "printer": get_setting("printer", ""),
                        "auto_print": get_setting("auto_print", "1") == "1",
                        "food_copies": get_setting("food_copies", "2"),
                        "print_mode": get_setting("print_mode", "gdi"),
                        "printers": printers,
                        "default_printer": default,
                        "printers_reason": reason,
                        "deps_hint": deps_hint()})
        elif p.path == "/api/export.csv":
            con = db()
            rows = con.execute(
                "SELECT day, order_no, ts, item_name, price "
                "FROM sales ORDER BY id").fetchall()
            con.close()
            buf = io.StringIO()
            w = csv_mod.writer(buf)
            w.writerow(["التاريخ", "رقم الطلب", "الوقت", "الصنف", "السعر"])
            w.writerows(rows)
            self._send(("\ufeff" + buf.getvalue()).encode("utf-8"),
                       "text/csv; charset=utf-8",
                       extra={"Content-Disposition":
                              'attachment; filename="sales.csv"'})
        else:
            self._send({"error": "not found"}, code=404)

    # ---------------- POST
    def do_POST(self):
        global LAST_PING
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            data = {}
        p = urlparse(self.path).path

        if p == "/api/ping":
            LAST_PING = time.time()
            self._send({"ok": True})

        elif p == "/api/order":
            lines = data.get("lines") or []
            if not lines:
                self._send({"ok": False}, code=400)
                return
            results, stats = record_order(lines)
            auto = get_setting("auto_print", "1") == "1"
            try:
                food_copies = max(1, int(get_setting("food_copies", "2")))
            except ValueError:
                food_copies = 2
            out_orders = []
            hint = deps_hint()
            for r in results:
                section = "المشروبات" if r["cat"] == 1 else "المأكولات"
                copies = 1 if r["cat"] == 1 else food_copies
                o = {"cat": r["cat"], "order_no": r["order_no"],
                     "label": section, "copies": copies}
                img = None
                try:
                    img = build_receipt_image(
                        r["order_no"], r["rows"], r["total"], section)
                except Exception as e:
                    o["print_error"] = friendly_print_error(e)
                if img is not None:
                    if auto and sys.platform == "win32":
                        try:
                            for _ in range(copies):
                                print_receipt(img)
                        except Exception as e:
                            o["image"] = img_to_data_url(img)
                            o["print_error"] = friendly_print_error(e)
                    else:
                        o["image"] = img_to_data_url(img)
                out_orders.append(o)
            self._send({"ok": True, "orders": out_orders,
                        "stats": stats, "hint": hint})

        elif p == "/api/undo":
            r = undo_last_order()
            self._send({"ok": bool(r), **(r or {})})

        elif p == "/api/item":
            name = (data.get("name") or "").strip()
            price = float(data.get("price") or 0)
            cat = 1 if int(data.get("cat") or 2) == 1 else 2
            con = db()
            if data.get("id"):
                con.execute(
                    "UPDATE items SET name=?, price=?, category=? WHERE id=?",
                    (name, price, cat, int(data["id"])))
            else:
                top = con.execute(
                    "SELECT COALESCE(MAX(sort),0)+1 FROM items "
                    "WHERE category=?", (cat,)).fetchone()[0]
                con.execute(
                    "INSERT INTO items(name,price,category,sort) "
                    "VALUES(?,?,?,?)", (name, price, cat, top))
            con.commit()
            con.close()
            self._send({"ok": True, "items": all_items()})

        elif p == "/api/toggle":
            ids = [int(i) for i in (data.get("ids") or [])]
            con = db()
            for i in ids:
                con.execute(
                    "UPDATE items SET active=1-active WHERE id=?", (i,))
            con.commit()
            con.close()
            self._send({"ok": True, "items": all_items()})

        elif p == "/api/settings":
            set_setting("header", data.get("header") or APP_TITLE)
            set_setting("printer", data.get("printer") or "")
            set_setting("auto_print", 1 if data.get("auto_print") else 0)
            mode = data.get("print_mode")
            set_setting("print_mode", "escpos" if mode == "escpos" else "gdi")
            try:
                set_setting("food_copies",
                            max(1, min(5, int(data.get("food_copies") or 2))))
            except (TypeError, ValueError):
                set_setting("food_copies", 2)
            self._send({"ok": True})

        elif p == "/api/testprint":
            # يحفظ الإعدادات المرسلة مؤقتاً ثم يجرب طباعة قسيمة اختبار
            if "printer" in data:
                set_setting("printer", data.get("printer") or "")
            if "print_mode" in data:
                set_setting("print_mode",
                            "escpos" if data["print_mode"] == "escpos" else "gdi")
            hint = deps_hint()
            if hint:
                self._send({"ok": False, "error": hint})
            else:
                try:
                    img = build_receipt_image(
                        99, [("تجربة طباعة ناجحة", 1, 0.0)], 0.0, "اختبار")
                    print_receipt(img)
                    self._send({"ok": True})
                except Exception as e:
                    self._send({"ok": False, "error": friendly_print_error(e)})

        else:
            self._send({"error": "not found"}, code=404)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def open_window(url):
    """يفتح الواجهة كنافذة تطبيق عبر Edge أو Chrome، وإلا المتصفح الافتراضي."""
    candidates = [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        exe = os.path.expandvars(c)
        if os.path.exists(exe):
            try:
                subprocess.Popen(
                    [exe, "--app=%s" % url, "--window-size=1440,860",
                     "--disable-features=TranslateUI"])
                return
            except Exception:
                pass
    webbrowser.open(url)


def main():
    init_db()
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    open_window("http://127.0.0.1:%d/" % port)
    # يغلق البرنامج تلقائياً بعد إغلاق النافذة (انقطاع نبض الواجهة)
    try:
        while True:
            time.sleep(2)
            if time.time() - LAST_PING > 10:
                break
    except KeyboardInterrupt:
        pass
    server.shutdown()


if __name__ == "__main__":
    main()
