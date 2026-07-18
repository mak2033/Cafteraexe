# -*- coding: utf-8 -*-
"""
نظام مبيعات كفتيريا السكن - كلية عجلون
تطبيق ويندوز مستقل - يعمل على ويندوز 7 و 10 و 11
النسخة الثانية: واجهة بلاطات مربعة، المأكولات يمين والمشروبات شمال،
نافذة أصناف معاد بناؤها (تحديد متعدد، تعديل موحد للاسم والسعر).
يحفظ المبيعات في cafeteria.db بجانب البرنامج ولا يغير بياناتك السابقة.
"""

import os
import sys
import sqlite3
import csv
from datetime import date, datetime

import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "كفتيريا السكن - كلية عجلون"
RECEIPT_WIDTH = 576  # عرض ورق 80 ملم على دقة 203dpi

DRINK_COLOR, DRINK_HOVER = "#1F4E5F", "#2E6B85"
FOOD_COLOR, FOOD_HOVER = "#375623", "#4E7A32"
BG = "#ECEFF1"
ACCENT = "#FFF2CC"
TILE = 104  # ضلع البلاطة المربعة بالبكسل

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


# ---------------------------------------------------------------- قاعدة البيانات

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
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    if con.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0:
        for i, (name, price, cat) in enumerate(DEFAULT_ITEMS):
            con.execute(
                "INSERT INTO items(name, price, category, sort) VALUES(?,?,?,?)",
                (name, price, cat, i),
            )
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
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    con.commit()
    con.close()


def record_sale(item_id):
    today = date.today().isoformat()
    con = db()
    item = con.execute(
        "SELECT name, price, category FROM items WHERE id=?", (item_id,)
    ).fetchone()
    order_no = con.execute(
        "SELECT COALESCE(MAX(order_no),0)+1 FROM sales WHERE day=?", (today,)
    ).fetchone()[0]
    con.execute(
        "INSERT INTO sales(item_id, item_name, price, category, day, order_no, ts) "
        "VALUES(?,?,?,?,?,?,?)",
        (item_id, item[0], item[1], item[2], today, order_no,
         datetime.now().strftime("%H:%M:%S")),
    )
    con.commit()
    con.close()
    return order_no, item[0], item[1]


def undo_last_sale():
    today = date.today().isoformat()
    con = db()
    row = con.execute(
        "SELECT id, order_no, item_name FROM sales WHERE day=? "
        "ORDER BY id DESC LIMIT 1", (today,)
    ).fetchone()
    if row:
        con.execute("DELETE FROM sales WHERE id=?", (row[0],))
        con.commit()
    con.close()
    return row


def daily_summary():
    con = db()
    rows = con.execute(
        """
        SELECT day,
               SUM(CASE WHEN category=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN category=2 THEN 1 ELSE 0 END),
               COUNT(*),
               SUM(price)
        FROM sales GROUP BY day ORDER BY day DESC
        """
    ).fetchall()
    con.close()
    return rows


def day_details(day):
    con = db()
    rows = con.execute(
        """
        SELECT item_name, COUNT(*), SUM(price)
        FROM sales WHERE day=?
        GROUP BY item_name ORDER BY COUNT(*) DESC
        """, (day,)
    ).fetchall()
    con.close()
    return rows


# ---------------------------------------------------------------- القسيمة والطباعة

def ar(text):
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _find_font(size):
    from PIL import ImageFont
    candidates = [
        r"C:\Windows\Fonts\tahomabd.ttf", r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_receipt_image(order_no, item_name, price):
    from PIL import Image, ImageDraw

    header = get_setting("header", APP_TITLE)
    f_big = _find_font(52)
    f_mid = _find_font(38)
    f_sml = _find_font(30)

    img = Image.new("L", (RECEIPT_WIDTH, 640), 255)
    d = ImageDraw.Draw(img)
    y = 18

    def center(text, font, gap=14):
        nonlocal y
        t = ar(text)
        w = d.textlength(t, font=font)
        d.text(((RECEIPT_WIDTH - w) / 2, y), t, font=font, fill=0)
        y += font.size + gap

    def line(gap=14):
        nonlocal y
        d.line((20, y, RECEIPT_WIDTH - 20, y), fill=0, width=3)
        y += gap

    center(header, f_mid)
    center(datetime.now().strftime("%d/%m/%Y  %H:%M"), f_sml)
    line()
    center("رقم الطلب", f_sml, gap=6)
    center(str(order_no), f_big, gap=20)
    line()
    center(item_name, f_mid)
    center("السعر: %.2f د.أ" % price, f_sml)
    line()
    center("شكراً لكم", f_sml)

    return img.crop((0, 0, RECEIPT_WIDTH, y + 20))


def print_receipt(img):
    import win32print
    import win32ui
    from PIL import ImageWin

    printer = get_setting("printer") or win32print.GetDefaultPrinter()
    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer)
    horz = hdc.GetDeviceCaps(8)
    w, h = img.size
    scale = horz / float(w)
    hdc.StartDoc("receipt")
    hdc.StartPage()
    ImageWin.Dib(img.convert("RGB")).draw(
        hdc.GetHandleOutput(), (0, 0, int(w * scale), int(h * scale))
    )
    hdc.EndPage()
    hdc.EndDoc()
    hdc.DeleteDC()


# ---------------------------------------------------------------- حوار الصنف

class ItemDialog(tk.Toplevel):
    """نافذة موحدة لإضافة صنف أو تعديل اسمه وسعره وقسمه."""

    def __init__(self, master, on_save, item=None):
        super().__init__(master)
        self.on_save = on_save
        self.item = item
        self.title("تعديل صنف" if item else "إضافة صنف")
        self.configure(bg="white")
        self.resizable(False, False)
        self.grab_set()

        pad = {"padx": 16, "pady": 4}
        if item:
            tk.Label(
                self, bg=ACCENT, font=("Tahoma", 10), justify="right",
                text="الحالي:  %s  -  %.2f د.أ" % (item["name"], item["price"]),
            ).pack(fill="x", ipady=6)

        tk.Label(self, text="اسم الصنف:", bg="white", anchor="e",
                 font=("Tahoma", 11)).pack(fill="x", **pad)
        self.name_var = tk.StringVar(value=item["name"] if item else "")
        tk.Entry(self, textvariable=self.name_var, justify="right",
                 font=("Tahoma", 12)).pack(fill="x", **pad)

        tk.Label(self, text="السعر بالدينار:", bg="white", anchor="e",
                 font=("Tahoma", 11)).pack(fill="x", **pad)
        self.price_var = tk.StringVar(
            value=("%.2f" % item["price"]) if item else "")
        tk.Entry(self, textvariable=self.price_var, justify="right",
                 font=("Tahoma", 12)).pack(fill="x", **pad)

        tk.Label(self, text="القسم:", bg="white", anchor="e",
                 font=("Tahoma", 11)).pack(fill="x", **pad)
        self.cat_var = tk.StringVar(
            value="مأكولات" if item and item["category"] == 2 else "مشروبات")
        ttk.Combobox(self, textvariable=self.cat_var, state="readonly",
                     values=["مشروبات", "مأكولات"], justify="right",
                     font=("Tahoma", 11)).pack(fill="x", **pad)

        bar = tk.Frame(self, bg="white")
        bar.pack(pady=12)
        tk.Button(bar, text="حفظ", command=self.save, width=10,
                  font=("Tahoma", 11, "bold"), bg=DRINK_COLOR, fg="white",
                  relief="flat").pack(side="right", padx=6)
        tk.Button(bar, text="إلغاء", command=self.destroy, width=10,
                  font=("Tahoma", 11), relief="groove").pack(side="right")
        self.bind("<Return>", lambda e: self.save())

    def save(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("تنبيه", "اكتب اسم الصنف.", parent=self)
            return
        try:
            price = float(self.price_var.get().replace(",", "."))
            if price < 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("تنبيه", "اكتب سعراً صحيحاً مثل 0.50",
                                   parent=self)
            return
        cat = 2 if self.cat_var.get() == "مأكولات" else 1
        self.on_save(name, price, cat)
        self.destroy()


# ---------------------------------------------------------------- الواجهة

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.geometry("1010x760")
        self.minsize(960, 700)
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Treeview", rowheight=26, font=("Tahoma", 10))
        style.configure("Treeview.Heading", font=("Tahoma", 10, "bold"))

        self._build_topbar()
        self.board = tk.Frame(self, bg=BG)
        self.board.pack(expand=True)
        self.rebuild_buttons()

    # ---- الشريط العلوي وشريط الحالة
    def _build_topbar(self):
        bar = tk.Frame(self, bg=DRINK_COLOR)
        bar.pack(fill="x")
        tk.Label(bar, text=APP_TITLE, font=("Tahoma", 15, "bold"),
                 bg=DRINK_COLOR, fg="white").pack(side="right", padx=14, pady=8)
        for text, cmd in [
            ("ملخص المبيعات", self.show_summary),
            ("تراجع عن آخر طلب", self.undo),
            ("الأصناف", self.manage_items),
            ("الإعدادات", self.settings_win),
        ]:
            b = tk.Button(bar, text=text, font=("Tahoma", 10, "bold"),
                          command=cmd, bg=DRINK_COLOR, fg="white",
                          activebackground=DRINK_HOVER,
                          activeforeground="white", relief="flat",
                          padx=10, cursor="hand2")
            b.pack(side="left", padx=3, pady=6)
            b.bind("<Enter>", lambda e, w=b: w.config(bg=DRINK_HOVER))
            b.bind("<Leave>", lambda e, w=b: w.config(bg=DRINK_COLOR))
        self.status = tk.Label(self, text="جاهز", anchor="e",
                               font=("Tahoma", 12, "bold"), bg=ACCENT,
                               padx=10, pady=5)
        self.status.pack(fill="x")

    # ---- بلاطات الأصناف: المأكولات يمين، المشروبات شمال
    def rebuild_buttons(self):
        for w in self.board.winfo_children():
            w.destroy()
        con = db()
        items = con.execute(
            "SELECT id, name, price, category FROM items "
            "WHERE active=1 ORDER BY sort, id"
        ).fetchall()
        con.close()

        # العمود 0 (شمال) للمشروبات، العمود 1 (يمين) للمأكولات
        sections = [
            (0, 1, "المشروبات", DRINK_COLOR, DRINK_HOVER),
            (1, 2, "المأكولات", FOOD_COLOR, FOOD_HOVER),
        ]
        for col, cat, title, color, hover in sections:
            sec = tk.Frame(self.board, bg=BG)
            sec.grid(row=0, column=col, sticky="n", padx=10, pady=8)
            tk.Label(sec, text=title, bg=color, fg="white",
                     font=("Tahoma", 13, "bold"), pady=5).pack(fill="x")
            grid = tk.Frame(sec, bg=BG)
            grid.pack(pady=(6, 0))
            cat_items = [it for it in items if it[3] == cat]
            cols = 4
            for i, (iid, name, price, _c) in enumerate(cat_items):
                self._tile(grid, i // cols, i % cols,
                           iid, name, price, color, hover)

    def _tile(self, parent, r, c, iid, name, price, color, hover):
        holder = tk.Frame(parent, width=TILE, height=TILE, bg=BG)
        holder.grid(row=r, column=c, padx=3, pady=3)
        holder.grid_propagate(False)
        b = tk.Button(
            holder, text="%s\n%.2f د.أ" % (name, price),
            font=("Tahoma", 10, "bold"), bg=color, fg="white",
            activebackground=hover, activeforeground="white",
            wraplength=TILE - 14, relief="flat", cursor="hand2",
            command=lambda: self.sell(iid),
        )
        b.place(relwidth=1, relheight=1)
        b.bind("<Enter>", lambda e: b.config(bg=hover))
        b.bind("<Leave>", lambda e: b.config(bg=color))

    # ---- تسجيل طلب
    def sell(self, item_id):
        order_no, name, price = record_sale(item_id)
        self.status.config(
            text="سُجّل الطلب رقم %d : %s (%.2f د.أ)" % (order_no, name, price)
        )
        try:
            img = build_receipt_image(order_no, name, price)
        except Exception as e:
            messagebox.showwarning("القسيمة", "تعذر تجهيز القسيمة:\n%s" % e)
            return
        if get_setting("auto_print", "1") == "1" and sys.platform == "win32":
            try:
                print_receipt(img)
                return
            except Exception as e:
                messagebox.showwarning(
                    "الطباعة",
                    "سُجّل الطلب لكن الطباعة فشلت:\n%s\nستظهر القسيمة على الشاشة." % e,
                )
        self.preview(img, order_no)

    def preview(self, img, order_no):
        from PIL import ImageTk
        win = tk.Toplevel(self)
        win.title("قسيمة الطلب %d" % order_no)
        photo = ImageTk.PhotoImage(img.resize((img.width // 2, img.height // 2)))
        lbl = tk.Label(win, image=photo)
        lbl.image = photo
        lbl.pack(padx=10, pady=10)

    def undo(self):
        row = undo_last_sale()
        if row:
            self.status.config(text="حُذف الطلب رقم %d (%s)" % (row[1], row[2]))
        else:
            messagebox.showinfo("تراجع", "لا توجد طلبات اليوم.")

    # ---- ملخص المبيعات (الأعمدة من اليمين لليسار)
    def show_summary(self):
        win = tk.Toplevel(self)
        win.title("ملخص المبيعات")
        win.geometry("740x500")
        cols = ("revenue", "orders", "food", "drinks", "day")
        heads = ("الإيراد (د.أ)", "عدد الطلبات", "مأكولات", "مشروبات", "التاريخ")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        for c, h in zip(cols, heads):
            tree.heading(c, text=h)
            tree.column(c, anchor="center", width=130)
        rows = daily_summary()
        tot_orders = tot_rev = 0
        for day, dr, fo, n, rev in rows:
            disp = "%s/%s/%s" % (day[8:10], day[5:7], day[0:4])
            tree.insert("", "end",
                        values=("%.2f" % rev, n, fo, dr, disp), tags=(day,))
            tot_orders += n
            tot_rev += rev
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(
            win,
            text="أيام العمل: %d   إجمالي الطلبات: %d   الإيراد الكلي: %.2f د.أ"
            % (len(rows), tot_orders, tot_rev),
            font=("Tahoma", 12, "bold"), bg=ACCENT, pady=5,
        ).pack(fill="x", padx=8)

        def details(_event=None):
            sel = tree.selection()
            if not sel:
                return
            day = tree.item(sel[0], "tags")[0]
            dwin = tk.Toplevel(win)
            dwin.title("تفاصيل يوم %s" % tree.item(sel[0], "values")[4])
            dwin.geometry("460x420")
            dtree = ttk.Treeview(dwin, columns=("r", "c", "n"), show="headings")
            for c, h, wd in (("r", "الإيراد", 100), ("c", "العدد", 80),
                             ("n", "الصنف", 230)):
                dtree.heading(c, text=h)
                dtree.column(c, anchor="center", width=wd)
            dtree.column("n", anchor="e")
            for name, cnt, rev in day_details(day):
                dtree.insert("", "end", values=("%.2f" % rev, cnt, name))
            dtree.pack(fill="both", expand=True, padx=8, pady=8)

        tree.bind("<Double-1>", details)
        btns = tk.Frame(win)
        btns.pack(pady=6)
        tk.Button(btns, text="تفاصيل اليوم المحدد", command=details,
                  font=("Tahoma", 11)).pack(side="right", padx=6)
        tk.Button(btns, text="تصدير CSV", command=self.export_csv,
                  font=("Tahoma", 11)).pack(side="right", padx=6)

    def export_csv(self):
        path = os.path.join(app_dir(), "سجل-المبيعات.csv")
        con = db()
        rows = con.execute(
            "SELECT day, order_no, ts, item_name, price FROM sales ORDER BY id"
        ).fetchall()
        con.close()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["التاريخ", "رقم الطلب", "الوقت", "الصنف", "السعر"])
            w.writerows(rows)
        messagebox.showinfo("تصدير", "حُفظ الملف:\n%s" % path)

    # ---- إدارة الأصناف
    def manage_items(self):
        win = tk.Toplevel(self)
        win.title("الأصناف")
        win.geometry("620x560")
        win.configure(bg="white")

        tk.Label(win, bg=ACCENT, font=("Tahoma", 10), pady=4,
                 text="يمكن تحديد أكثر من صنف بالسحب أو مع الضغط على Ctrl،"
                      " وبالنقر المزدوج يفتح التعديل").pack(fill="x")

        # الصنف في أقصى اليمين ثم السعر فالقسم فالظهور في أقصى الشمال
        cols = ("act", "cat", "price", "name")
        heads = ("ظاهر", "القسم", "السعر", "الصنف")
        widths = (70, 100, 90, 250)
        tree = ttk.Treeview(win, columns=cols, show="headings",
                            selectmode="extended")
        for c, h, wd in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, anchor="center", width=wd)
        tree.column("name", anchor="e")
        tree.tag_configure("off", foreground="#999999")
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        def refresh():
            tree.delete(*tree.get_children())
            con = db()
            for iid, name, price, cat, act in con.execute(
                "SELECT id, name, price, category, active FROM items "
                "ORDER BY category, sort, id"
            ):
                tree.insert(
                    "", "end", iid=str(iid),
                    values=("نعم" if act else "لا",
                            "مشروبات" if cat == 1 else "مأكولات",
                            "%.2f" % price, name),
                    tags=() if act else ("off",),
                )
            con.close()

        def selected_ids():
            return [int(s) for s in tree.selection()]

        def edit(_event=None):
            ids = selected_ids()
            if not ids:
                messagebox.showinfo("تعديل", "حدد صنفاً أولاً.", parent=win)
                return
            iid = ids[0]
            con = db()
            row = con.execute(
                "SELECT name, price, category FROM items WHERE id=?", (iid,)
            ).fetchone()
            con.close()
            item = {"name": row[0], "price": row[1], "category": row[2]}

            def save(name, price, cat):
                con = db()
                con.execute(
                    "UPDATE items SET name=?, price=?, category=? WHERE id=?",
                    (name, price, cat, iid))
                con.commit()
                con.close()
                refresh()
                self.rebuild_buttons()

            ItemDialog(win, save, item)

        def add_item():
            def save(name, price, cat):
                con = db()
                top = con.execute(
                    "SELECT COALESCE(MAX(sort),0)+1 FROM items WHERE category=?",
                    (cat,)).fetchone()[0]
                con.execute(
                    "INSERT INTO items(name, price, category, sort) "
                    "VALUES(?,?,?,?)", (name, price, cat, top))
                con.commit()
                con.close()
                refresh()
                self.rebuild_buttons()

            ItemDialog(win, save)

        def toggle():
            ids = selected_ids()
            if not ids:
                messagebox.showinfo("إخفاء/إظهار", "حدد صنفاً أو أكثر.",
                                    parent=win)
                return
            con = db()
            for iid in ids:
                con.execute("UPDATE items SET active=1-active WHERE id=?",
                            (iid,))
            con.commit()
            con.close()
            refresh()
            self.rebuild_buttons()
            for iid in ids:  # إبقاء التحديد بعد التبديل
                tree.selection_add(str(iid))

        tree.bind("<Double-1>", edit)
        btns = tk.Frame(win, bg="white")
        btns.pack(pady=8)
        for text, cmd in [("إضافة صنف", add_item), ("تعديل", edit),
                          ("إخفاء/إظهار", toggle)]:
            tk.Button(btns, text=text, command=cmd, width=12,
                      font=("Tahoma", 11, "bold"), bg=DRINK_COLOR, fg="white",
                      activebackground=DRINK_HOVER, activeforeground="white",
                      relief="flat", cursor="hand2").pack(side="right", padx=5)
        refresh()

    # ---- الإعدادات
    def settings_win(self):
        win = tk.Toplevel(self)
        win.title("الإعدادات")
        win.geometry("460x270")
        win.configure(bg="white")

        tk.Label(win, text="اسم الكفتيريا على القسيمة:", anchor="e",
                 bg="white", font=("Tahoma", 11)).pack(fill="x", padx=12,
                                                       pady=(12, 2))
        header_var = tk.StringVar(value=get_setting("header", APP_TITLE))
        tk.Entry(win, textvariable=header_var, justify="right",
                 font=("Tahoma", 11)).pack(fill="x", padx=12)

        tk.Label(win, text="الطابعة:", anchor="e", bg="white",
                 font=("Tahoma", 11)).pack(fill="x", padx=12, pady=(12, 2))
        printers = []
        if sys.platform == "win32":
            try:
                import win32print
                printers = [p[2] for p in win32print.EnumPrinters(
                    win32print.PRINTER_ENUM_LOCAL
                    | win32print.PRINTER_ENUM_CONNECTIONS)]
            except Exception:
                pass
        printer_var = tk.StringVar(value=get_setting("printer", ""))
        ttk.Combobox(win, textvariable=printer_var,
                     values=[""] + printers).pack(fill="x", padx=12)
        tk.Label(win, text="(اتركها فارغة لاستخدام الطابعة الافتراضية)",
                 bg="white", font=("Tahoma", 9), fg="#666").pack()

        auto_var = tk.IntVar(value=int(get_setting("auto_print", "1")))
        tk.Checkbutton(win, text="طباعة القسيمة تلقائياً عند كل طلب",
                       bg="white", variable=auto_var,
                       font=("Tahoma", 11)).pack(pady=8)

        def save():
            set_setting("header", header_var.get().strip() or APP_TITLE)
            set_setting("printer", printer_var.get().strip())
            set_setting("auto_print", auto_var.get())
            win.destroy()

        tk.Button(win, text="حفظ", command=save, font=("Tahoma", 11, "bold"),
                  bg=DRINK_COLOR, fg="white", relief="flat",
                  padx=24).pack(pady=8)


if __name__ == "__main__":
    init_db()
    App().mainloop()
