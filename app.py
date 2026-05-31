from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, timedelta
import csv, io, os, secrets
import urllib.request, urllib.error, json as _json

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

# ── Admin credentials from env ─────────────────────────────────────
# ตั้งใน Railway:  ADMIN_USERNAME  (default: admin)
#                  ADMIN_PASSWORD  (required ถ้าไม่ตั้ง login ไม่ได้)
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip().lower()
ADMIN_PASSWORD      = os.environ.get("ADMIN_PASSWORD", "").strip()
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

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

# backward-compat
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
    {"key":"SupportX",         "name":"SupportX",          "price":4999},
    {"key":"Custom Setting",   "name":"Custom Setting",     "price":1000},
    {"key":"Max Pack",         "name":"Max Pack",           "price":799},
    {"key":"Performance Pack", "name":"Performance Pack",   "price":649},
    {"key":"Pro Pack",         "name":"Pro Pack",           "price":629},
    {"key":"GOATX",            "name":"🐐 G.O.A.T.X",       "price":429},
    {"key":"ULTIMATEXPLUS",    "name":"💎 ULTIMATEXPLUS",    "price":259},
    {"key":"ULTIMATEXXPLUS",   "name":"💎 ULTIMATEX+PLUS",   "price":629},
    {"key":"ULTIMATEX",        "name":"🔥 ULTIMATEX",        "price":399},
    {"key":"SHXV2",            "name":"🚀 Shx V.2",          "price":309},
    {"key":"SHXV1",            "name":"⚡ Shx V.1",          "price":159},
    {"key":"Reshade",          "name":"Reshade",            "price":39},
]
PROD_MAP = {p["key"]: p for p in PRODUCTS}


# ── Discord Webhook helper ─────────────────────────────────────────
def send_order_webhook(order):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        method_emoji = {"bank": "🏦", "truemoney": "💚", "other": "📦"}.get(order.method, "💳")
        items_text   = "\n".join(
            f"• {i.product_name} — **{i.actual_price:,} ฿**"
            + (f" ~~{i.list_price:,}~~ " if i.list_price != i.actual_price else "")
            for i in order.items
        )
        discount = order.total_list - order.total_actual
        now_str  = datetime.now(BKK).strftime("%d/%m/%Y %H:%M")

        embed = {
            "title": f"🛒  ออเดอร์ใหม่ #{order.id}",
            "color": 0x8b5cf6,
            "fields": [
                {"name": "👤 ลูกค้า",        "value": f"@{order.customer}",         "inline": True},
                {"name": f"{method_emoji} ช่องทาง", "value": order.method,          "inline": True},
                {"name": "📦 สินค้า",         "value": items_text or "—",           "inline": False},
                {"name": "💰 รวมจ่าย",        "value": f"**{order.total_actual:,} ฿**"
                    + (f"  (ลด {discount:,} ฿)" if discount > 0 else ""), "inline": True},
                {"name": "🕐 เวลา",           "value": now_str,                     "inline": True},
            ],
            "footer": {"text": "INSIDEX Accounting"},
            "timestamp": datetime.now(BKK).isoformat(),
        }
        if order.note:
            embed["fields"].append({"name": "📝 หมายเหตุ", "value": order.note, "inline": False})

        payload = _json.dumps({"embeds": [embed]}).encode("utf-8")
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[WEBHOOK] failed: {e}")

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
    send_order_webhook(order)
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




# ── API: Import Legacy CSV (Google Sheet format) ──────────────────
# Format: วันที่,จำนวนเงิน (บาท),ชื่อลูกค้า,รายการ,หมายเหตุ ( ธนาคาร / wallet )
@app.route("/api/import/legacy_csv", methods=["POST"])
@login_required
def api_import_legacy():
    import csv as csvlib, io as io2

    # รับทั้ง raw body และ multipart form
    if request.files.get("file"):
        raw = request.files["file"].read().decode("utf-8-sig")
    else:
        raw = request.get_data(as_text=False).decode("utf-8-sig")

    # strip BOM ทุกชั้น
    raw = raw.lstrip("\ufeff")
    if not raw.strip():
        return jsonify({"success": False, "error": "ไฟล์ว่าง"})

    reader = csvlib.DictReader(io2.StringIO(raw))
    rows = list(reader)

    # detect header
    raw_headers = reader.fieldnames or []
    def find_col(keywords):
        for h in raw_headers:
            if any(k in h for k in keywords):
                return h
        return None

    col_date   = find_col(["วันที่","date"])
    col_amount = find_col(["จำนวน","amount","บาท"])
    col_cust   = find_col(["ชื่อ","ลูกค้า","customer","name"])
    col_prod   = find_col(["รายการ","สินค้า","product","item"])
    col_method = find_col(["หมาย","bank","wallet","method","ช่องทาง"])

    missing = [n for n, c in [("วันที่", col_date), ("จำนวนเงิน", col_amount),
                               ("ชื่อลูกค้า", col_cust), ("รายการ", col_prod)] if not c]
    if missing:
        return jsonify({"success": False, "error": f"ไม่พบคอลัมน์: {missing}"})

    imported = skipped = failed = 0
    for row in rows:
        try:
            date_raw   = (row.get(col_date)   or "").strip()
            amount_raw = (row.get(col_amount)  or "0").strip().replace(",", "")
            customer   = (row.get(col_cust)    or "").strip()
            product    = (row.get(col_prod)    or "").strip()
            method_raw = (row.get(col_method)  or "bank").strip().lower()

            if not date_raw or not customer:
                continue

            # parse DD/MM/YYYY or YYYY-MM-DD
            if "/" in date_raw:
                parts = date_raw.split("/")
                if len(parts) == 3:
                    d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
                    if y < 100: y += 2000
                    dt = datetime(y, m, d, 12, 0, tzinfo=BKK)
                else:
                    continue
            else:
                dt = datetime.strptime(date_raw[:10], "%Y-%m-%d").replace(
                    hour=12, tzinfo=BKK)

            actual = int(float(amount_raw)) if amount_raw else 0
            method = "truemoney" if "wallet" in method_raw else "bank"

            # dedup: same customer + same date (day level)
            exists = Order.query.filter(
                Order.customer == customer,
                db.func.date(Order.created_at) == dt.date(),
                Order.items.any(OrderItem.product_name == product),
            ).first()
            if exists:
                skipped += 1
                continue

            # match product
            prod_obj = next(
                (p for p in PRODUCTS if
                 p["name"].lower() == product.lower() or
                 p["key"].lower()  == product.lower()),
                {"key": "CUSTOM", "name": product, "price": actual}
            )

            order = Order(customer=customer, method=method, note="", created_at=dt)
            db.session.add(order)
            db.session.add(OrderItem(
                order        = order,
                product_key  = prod_obj["key"],
                product_name = product,
                list_price   = prod_obj["price"],
                actual_price = actual,
            ))
            db.session.commit()
            imported += 1

        except Exception as e:
            db.session.rollback()
            failed += 1

    return jsonify({"success": True, "imported": imported, "skipped": skipped, "failed": failed})


# ── API: Bulk import backup CSV (ส่งครั้งเดียวทั้งหมด) ─────────────
@app.route("/api/import/bulk", methods=["POST"])
@login_required
def api_import_bulk():
    items   = request.json or []
    if not items:
        return jsonify({"success": False, "error": "ไม่มีข้อมูล"})

    imported = skipped = failed = 0
    for d in items:
        try:
            customer = (d.get("customer") or "").strip()
            product  = (d.get("product")  or "").strip()
            date_str = (d.get("date")     or "").strip()
            actual   = int(d.get("actual", 0))
            method   = d.get("method", "bank")
            note     = (d.get("note") or "").strip()

            if not customer or not date_str:
                failed += 1; continue

            try:
                dt = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
            except ValueError:
                failed += 1; continue

            # dedup
            exists = Order.query.filter(
                Order.customer == customer,
                Order.created_at >= dt.replace(second=0,  microsecond=0),
                Order.created_at <  dt.replace(second=59, microsecond=999999),
            ).first()
            if exists:
                skipped += 1; continue

            prod = next((p for p in PRODUCTS if p["name"] == product or p["key"] == product),
                        {"key": "CUSTOM", "name": product, "price": actual})

            order = Order(customer=customer, method=method, note=note, created_at=dt)
            db.session.add(order)
            db.session.add(OrderItem(
                order=order, product_key=prod["key"], product_name=prod["name"],
                list_price=prod["price"], actual_price=actual,
            ))
            imported += 1
        except Exception:
            db.session.rollback()
            failed += 1

    if imported > 0:
        db.session.commit()

    return jsonify({"success": True, "imported": imported, "skipped": skipped, "failed": failed})

# ── API: Import order from CSV backup ─────────────────────────────
@app.route("/api/import/order", methods=["POST"])
@login_required
def api_import_order():
    d        = request.json or {}
    customer = (d.get("customer") or "").strip()
    product  = (d.get("product")  or "").strip()
    date_str = (d.get("date")     or "").strip()   # "YYYY-MM-DD HH:MM"
    actual   = int(d.get("actual", 0))
    method   = d.get("method", "bank")
    note     = (d.get("note") or "").strip()

    if not customer or not date_str:
        return jsonify({"success": False, "error": "ข้อมูลไม่ครบ"})

    # parse datetime
    try:
        dt = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
    except ValueError:
        return jsonify({"success": False, "error": f"รูปแบบวันที่ผิด: {date_str}"})

    # dedup check — same customer + same minute
    exists = Order.query.filter(
        Order.customer == customer,
        Order.created_at >= dt.replace(second=0, microsecond=0),
        Order.created_at <  dt.replace(second=59, microsecond=999999),
    ).first()
    if exists:
        return jsonify({"success": False, "skipped": True, "reason": "ซ้ำ"})

    # find or create product key
    prod = next((p for p in PRODUCTS if p["name"] == product or p["key"] == product), None)
    if not prod:
        # ใช้ custom product
        prod = {"key": "CUSTOM", "name": product, "price": actual}

    order = Order(customer=customer, method=method, note=note, created_at=dt)
    db.session.add(order)
    db.session.add(OrderItem(
        order        = order,
        product_key  = prod["key"],
        product_name = prod["name"],
        list_price   = prod["price"],
        actual_price = actual,
    ))
    db.session.commit()
    return jsonify({"success": True, "id": order.id})


# ── API: Test webhook ──────────────────────────────────────────────
@app.route("/api/webhook/test", methods=["POST"])
@login_required
def api_webhook_test():
    d   = request.json or {}
    url = (d.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "ไม่มี URL"})
    try:
        embed = {
            "title": "🧪  ทดสอบ INSIDEX Webhook",
            "description": "Webhook เชื่อมต่อสำเร็จ! จะแจ้งเตือนทุกครั้งที่มีออเดอร์ใหม่",
            "color": 0x10b981,
            "footer": {"text": "INSIDEX Accounting"},
            "timestamp": datetime.now(BKK).isoformat(),
        }
        payload = _json.dumps({"embeds": [embed]}).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# ── API: Backup CSV (for Discord Bot) ─────────────────────────────
# ใช้ BACKUP_SECRET header แทน session เพราะ bot เรียกจาก server อื่น
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

    # summary row
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

@app.route("/")
@login_required
def index():
    return render_template_string(open("templates/index.html").read())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)