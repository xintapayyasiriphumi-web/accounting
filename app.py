from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, timedelta
import csv, io, os, secrets

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///insidex.db"
).replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"]                  = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"]     = True
app.config["SESSION_COOKIE_SAMESITE"]    = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

db  = SQLAlchemy(app)
BKK = timezone(timedelta(hours=7))

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# ── Models ─────────────────────────────────────────────────────────
class Order(db.Model):
    __tablename__ = "orders"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(BKK))
    customer    = db.Column(db.String(120), nullable=False)
    method      = db.Column(db.String(40),  default="bank")
    note        = db.Column(db.String(255), default="")
    items       = db.relationship("OrderItem", backref="order", cascade="all,delete-orphan")

    @property
    def total_list(self):   return sum(i.list_price   for i in self.items)
    @property
    def total_actual(self): return sum(i.actual_price for i in self.items)

class OrderItem(db.Model):
    __tablename__ = "order_items"
    id           = db.Column(db.Integer, primary_key=True)
    order_id     = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_key  = db.Column(db.String(60),  nullable=False)
    product_name = db.Column(db.String(120), nullable=False)
    list_price   = db.Column(db.Integer, nullable=False)
    actual_price = db.Column(db.Integer, nullable=False)

class Transaction(db.Model):
    __tablename__ = "transactions"
    id           = db.Column(db.Integer, primary_key=True)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(BKK))
    customer     = db.Column(db.String(120))
    product_key  = db.Column(db.String(60))
    product_name = db.Column(db.String(120))
    list_price   = db.Column(db.Integer)
    actual_price = db.Column(db.Integer)
    method       = db.Column(db.String(40), default="bank")
    note         = db.Column(db.String(255), default="")

with app.app_context():
    db.create_all()

# ── Products ───────────────────────────────────────────────────────
PRODUCTS = [
    {"key":"SupportX",         "name":"👑 SupportX",             "price":4999},
    {"key":"Custom Setting",   "name":"🛠️ Custom Setting",       "price":1000},

    {"key":"Max Pack",         "name":"💎 Max Pack",             "price":799},
    {"key":"Performance Pack", "name":"🚀 Performance Pack",     "price":649},
    {"key":"Pro Pack",         "name":"⚡ Pro Pack",             "price":629},

    {"key":"GOATX",            "name":"🐐 G.O.A.T.X",           "price":429},
    {"key":"ULTIMATEXPLUS",    "name":"💠 ULTIMATEXPLUS",        "price":259},
    {"key":"ULTIMATEXXPLUS",   "name":"💎 ULTIMATEX+PLUS",       "price":629},
    {"key":"ULTIMATEX",        "name":"🔥 ULTIMATEX",            "price":399},

    {"key":"SHXV2",            "name":"🚀 SHX V.2",             "price":309},
    {"key":"SHXV1",            "name":"⚡ SHX V.1",             "price":159},
    {"key":"Dota V1",          "name":"🎮 Dota V.1",            "price":159},

    {"key":"Windows OS",       "name":"🖥️ Windows OS",          "price":250},
    {"key":"Windows Addon",    "name":"🔧 Windows Addon",        "price":150},
    {"key":"Windows 10/11",    "name":"💻 Windows 10/11",        "price":99},

    {"key":"Reshade",          "name":"🎨 Reshade",             "price":39},
]
PROD_MAP = {p["key"]: p for p in PRODUCTS}

# ── Auth helpers ────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def order_to_dict(o):
    return {
        "id":           o.id,
        "created_at":   o.created_at.strftime("%Y-%m-%d %H:%M"),
        "customer":     o.customer,
        "method":       o.method,
        "note":         o.note,
        "total_list":   o.total_list,
        "total_actual": o.total_actual,
        "discount":     o.total_list - o.total_actual,
        "items": [{
            "id":           i.id,
            "product_key":  i.product_key,
            "product_name": i.product_name,
            "list_price":   i.list_price,
            "actual_price": i.actual_price,
            "discount":     i.list_price - i.actual_price,
        } for i in o.items],
    }

# ── Auth Routes ────────────────────────────────────────────────────
@app.route("/login")
def login_page():
    if session.get("logged_in"):
        return redirect("/")
    return render_template_string(open("templates/login.html").read())

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    if not ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "ยังไม่ได้ตั้ง ADMIN_PASSWORD ใน Railway"}), 500
    d        = request.json or {}
    username = (d.get("username") or "").strip().lower()
    password = d.get("password") or ""
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return jsonify({"success": False, "error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"}), 401
    session.permanent  = True
    session["logged_in"] = True
    session["username"]  = ADMIN_USERNAME
    return jsonify({"success": True, "username": ADMIN_USERNAME})

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/auth/me")
def api_me():
    if not session.get("logged_in"):
        return jsonify({"logged_in": False, "no_password": not bool(ADMIN_PASSWORD)})
    return jsonify({"logged_in": True, "username": session.get("username", ADMIN_USERNAME)})

# ── API: Products ──────────────────────────────────────────────────
@app.route("/api/products")
@login_required
def api_products():
    return jsonify(PRODUCTS)

# ── API: Orders list ───────────────────────────────────────────────
@app.route("/api/orders", methods=["GET"])
@login_required
def api_list():
    page  = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 15))
    q     = request.args.get("q", "").strip()
    month = request.args.get("month", "")
    query = Order.query.order_by(Order.created_at.desc())
    if q:
        query = query.filter(db.or_(
            Order.customer.ilike(f"%{q}%"),
            Order.note.ilike(f"%{q}%"),
            Order.items.any(OrderItem.product_name.ilike(f"%{q}%")),
        ))
    if month:
        y, m = month.split("-")
        query = query.filter(
            db.extract("year",  Order.created_at) == int(y),
            db.extract("month", Order.created_at) == int(m),
        )
    total = query.count()
    items = query.offset((page-1)*limit).limit(limit).all()
    return jsonify({"total": total, "page": page, "items": [order_to_dict(o) for o in items]})

# ── API: Create order ──────────────────────────────────────────────
@app.route("/api/orders", methods=["POST"])
@login_required
def api_create():
    d        = request.json or {}
    customer = (d.get("customer") or "").strip()
    cart     = d.get("items", [])
    if not customer:
        return jsonify({"success": False, "error": "กรุณาใส่ชื่อลูกค้า"}), 400
    if not cart:
        return jsonify({"success": False, "error": "กรุณาเลือกสินค้าอย่างน้อย 1 รายการ"}), 400
    order = Order(customer=customer, method=d.get("method","bank"), note=(d.get("note") or "").strip())
    db.session.add(order)
    for ci in cart:
        prod = PROD_MAP.get(ci.get("product_key"))
        if not prod:
            db.session.rollback()
            return jsonify({"success": False, "error": f"ไม่พบสินค้า {ci.get('product_key')}"}), 400
        db.session.add(OrderItem(
            order        = order,
            product_key  = prod["key"],
            product_name = prod["name"],
            list_price   = prod["price"],
            actual_price = int(ci.get("actual_price", prod["price"])),
        ))
    db.session.commit()
    return jsonify({"success": True, "id": order.id, "total": order.total_actual})

# ── API: Delete order ──────────────────────────────────────────────
@app.route("/api/orders/<int:oid>", methods=["DELETE"])
@login_required
def api_delete(oid):
    o = Order.query.get_or_404(oid)
    db.session.delete(o)
    db.session.commit()
    return jsonify({"success": True})

# ── API: Patch item price ──────────────────────────────────────────
@app.route("/api/order_items/<int:iid>", methods=["PATCH"])
@login_required
def api_patch_item(iid):
    item = OrderItem.query.get_or_404(iid)
    d    = request.json or {}
    if "actual_price" in d:
        item.actual_price = max(0, int(d["actual_price"]))
    db.session.commit()
    return jsonify({"success": True})

# ── API: Dashboard ─────────────────────────────────────────────────
@app.route("/api/dashboard")
@login_required
def api_dashboard():
    now         = datetime.now(BKK)
    today       = now.date()
    month_start = today.replace(day=1)
    all_orders  = Order.query.all()

    def rev(orders): return sum(o.total_actual for o in orders)

    today_orders = [o for o in all_orders if o.created_at.date() == today]
    month_orders = [o for o in all_orders if o.created_at.date() >= month_start]

    daily = {(now - timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(29, -1, -1)}
    for o in all_orders:
        k = o.created_at.strftime("%Y-%m-%d")
        if k in daily:
            daily[k] += o.total_actual

    all_items = OrderItem.query.all()
    prod_rev  = {p["key"]: {"name": p["name"], "count": 0, "revenue": 0} for p in PRODUCTS}
    for i in all_items:
        if i.product_key in prod_rev:
            prod_rev[i.product_key]["count"]   += 1
            prod_rev[i.product_key]["revenue"] += i.actual_price

    return jsonify({
        "today_rev":      rev(today_orders),
        "today_count":    len(today_orders),
        "month_rev":      rev(month_orders),
        "month_count":    len(month_orders),
        "total_rev":      rev(all_orders),
        "total_count":    len(all_orders),
        "total_discount": sum(o.total_list - o.total_actual for o in all_orders),
        "daily":    [{"date": k, "rev": v} for k, v in daily.items()],
        "products": list(prod_rev.values()),
    })

# ── API: Export CSV ────────────────────────────────────────────────
@app.route("/api/export/csv")
@login_required
def api_export():
    month = request.args.get("month", "")
    query = Order.query.order_by(Order.created_at.desc())
    if month:
        y, m = month.split("-")
        query = query.filter(
            db.extract("year",  Order.created_at) == int(y),
            db.extract("month", Order.created_at) == int(m),
        )
    orders = query.all()
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["วันที่","ลูกค้า","สินค้า","ราคาปกติ","ราคาจริง","ส่วนลด","รวมบิล","ช่องทาง","หมายเหตุ"])
    for o in orders:
        for idx, item in enumerate(o.items):
            w.writerow([
                o.created_at.strftime("%Y-%m-%d %H:%M") if idx==0 else "",
                o.customer   if idx==0 else "",
                item.product_name,
                item.list_price, item.actual_price,
                item.list_price - item.actual_price,
                o.total_actual if idx==0 else "",
                o.method if idx==0 else "",
                o.note   if idx==0 else "",
            ])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.read().encode("utf-8-sig")),
        mimetype="text/csv", as_attachment=True,
        download_name=f"insidex_{month or 'all'}.csv",
    )

# ── API: Backup CSV (for scheduler / Discord Bot) ─────────────────
BACKUP_SECRET = os.environ.get("BACKUP_SECRET", "")

@app.route("/api/backup/csv")
def api_backup():
    secret = request.headers.get("X-Backup-Secret", "")
    if not BACKUP_SECRET or secret != BACKUP_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    now   = datetime.now(BKK)
    month = now.strftime("%Y-%m")
    y, m  = month.split("-")

    query = Order.query.order_by(Order.created_at.asc()).filter(
        db.extract("year",  Order.created_at) == int(y),
        db.extract("month", Order.created_at) == int(m),
    )
    orders = query.all()

    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["วันที่","ลูกค้า","สินค้า","ราคาปกติ","ราคาจริง","ส่วนลด","รวมบิล","ช่องทาง","หมายเหตุ"])
    total_rev = 0
    for o in orders:
        total_rev += o.total_actual
        for idx, item in enumerate(o.items):
            w.writerow([
                o.created_at.strftime("%Y-%m-%d %H:%M") if idx==0 else "",
                o.customer   if idx==0 else "",
                item.product_name,
                item.list_price, item.actual_price,
                item.list_price - item.actual_price,
                o.total_actual if idx==0 else "",
                o.method if idx==0 else "",
                o.note   if idx==0 else "",
            ])

    w.writerow([])
    w.writerow(["รวมเดือน", "", "", "", "", "", total_rev, "", f"{len(orders)} บิล"])

    buf.seek(0)
    csv_bytes = buf.read().encode("utf-8-sig")
    fname = f"insidex_backup_{now.strftime('%Y-%m-%d')}.csv"
    return send_file(
        io.BytesIO(csv_bytes),
        mimetype="text/csv", as_attachment=True,
        download_name=fname,
    )

# ── API: Import / Restore from CSV ────────────────────────────────
@app.route("/api/import/csv", methods=["POST"])
@login_required
def api_import():
    """
    รับไฟล์ CSV (จาก backup) แล้ว import กลับเข้า DB
    - ข้ามแถวที่ข้อมูลซ้ำ (customer + created_at + product_name ตรงกัน)
    - ส่งกลับ: inserted, skipped, errors
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"success": False, "error": "ไม่พบไฟล์"}), 400

    raw   = f.read()
    # strip BOM
    text  = raw.decode("utf-8-sig").strip()
    lines = list(csv.reader(io.StringIO(text)))

    if not lines:
        return jsonify({"success": False, "error": "ไฟล์ว่างเปล่า"}), 400

    header = [h.strip() for h in lines[0]]
    # header ที่คาดหวัง: วันที่, ลูกค้า, สินค้า, ราคาปกติ, ราคาจริง, ส่วนลด, รวมบิล, ช่องทาง, หมายเหตุ
    expected = ["วันที่","ลูกค้า","สินค้า","ราคาปกติ","ราคาจริง","ส่วนลด","รวมบิล","ช่องทาง","หมายเหตุ"]
    if header != expected:
        return jsonify({"success": False, "error": f"header ไม่ตรง — ต้องเป็น: {expected}"}), 400

    inserted = 0
    skipped  = 0
    errors   = []

    # จัดกลุ่ม rows เป็น orders (rows ที่มีวันที่ = order แรก, rows ที่ไม่มีวันที่ = items ต่อเนื่อง)
    # รูปแบบ CSV: แถวแรกของแต่ละ order มีค่าวันที่/ลูกค้า/รวมบิล/ช่องทาง/หมายเหตุ
    #             แถวต่อมาในออเดอร์เดียวกันช่อง วันที่/ลูกค้า จะว่าง

    pending_order  = None   # dict สำหรับ order ปัจจุบัน
    pending_items  = []     # list of item dicts

    def flush_order():
        nonlocal inserted, skipped
        if not pending_order or not pending_items:
            return
        o_dt       = pending_order["created_at"]
        o_customer = pending_order["customer"]
        o_method   = pending_order["method"]
        o_note     = pending_order["note"]

        # ตรวจซ้ำ: หาก order ที่ created_at + customer + จำนวน items เหมือนกันมีอยู่แล้ว → skip
        existing = Order.query.filter_by(customer=o_customer).filter(
            Order.created_at == o_dt
        ).first()
        if existing:
            skipped += 1
            return

        order = Order(
            created_at = o_dt,
            customer   = o_customer,
            method     = o_method,
            note       = o_note,
        )
        db.session.add(order)

        for it in pending_items:
            # หา list_price จาก PROD_MAP (ถ้าหาไม่เจอใช้ค่าใน CSV)
            prod      = next((p for p in PRODUCTS if p["name"] == it["product_name"]), None)
            list_p    = prod["price"] if prod else it["list_price"]
            prod_key  = prod["key"]   if prod else it["product_name"]
            db.session.add(OrderItem(
                order        = order,
                product_key  = prod_key,
                product_name = it["product_name"],
                list_price   = list_p,
                actual_price = it["actual_price"],
            ))

        db.session.commit()
        inserted += 1

    for row_num, row in enumerate(lines[1:], start=2):
        if len(row) < 9:
            continue
        date_str, customer, product_name, list_price_str, actual_price_str, _, total_str, method, note = \
            [c.strip() for c in row[:9]]

        # แถว summary (รวมเดือน) — ข้าม
        if date_str == "รวมเดือน":
            continue

        try:
            list_price   = int(list_price_str)   if list_price_str   else 0
            actual_price = int(actual_price_str) if actual_price_str else 0
        except ValueError:
            errors.append(f"แถว {row_num}: parse ราคาไม่ได้")
            continue

        if date_str:
            # order ใหม่ — flush ของเก่าก่อน
            flush_order()
            pending_order = {}
            pending_items = []
            try:
                pending_order["created_at"] = datetime.strptime(date_str, "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
            except ValueError:
                errors.append(f"แถว {row_num}: format วันที่ไม่ถูกต้อง '{date_str}'")
                pending_order = None
                continue
            pending_order["customer"] = customer
            pending_order["method"]   = method or "bank"
            pending_order["note"]     = note

        if pending_order is not None and product_name:
            pending_items.append({
                "product_name": product_name,
                "list_price":   list_price,
                "actual_price": actual_price,
            })

    # flush อันสุดท้าย
    flush_order()

    return jsonify({
        "success":  True,
        "inserted": inserted,
        "skipped":  skipped,
        "errors":   errors,
        "message":  f"นำเข้าสำเร็จ {inserted} บิล, ข้าม {skipped} บิล (ซ้ำ){', มีข้อผิดพลาด '+str(len(errors))+' แถว' if errors else ''}",
    })

@app.route("/")
@login_required
def index():
    return render_template_string(open("templates/index.html").read())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)