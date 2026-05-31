from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, timedelta
import csv, io, os, json, secrets

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///insidex.db"
).replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

db = SQLAlchemy(app)
BKK = timezone(timedelta(hours=7))

# ── Models ─────────────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = "users"
    id         = db.Column(db.Integer, primary_key=True)
    username   = db.Column(db.String(80), unique=True, nullable=False)
    password   = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(BKK))

# ── Auth helpers ────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def current_user():
    return User.query.get(session.get("user_id"))

# Order = 1 บิล (1 ลูกค้า อาจมีหลายสินค้า)
class Order(db.Model):
    __tablename__ = "orders"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(BKK))
    customer    = db.Column(db.String(120), nullable=False)
    method      = db.Column(db.String(40),  default="bank")
    note        = db.Column(db.String(255), default="")
    items       = db.relationship("OrderItem", backref="order", cascade="all,delete-orphan")

    @property
    def total_list(self):
        return sum(i.list_price for i in self.items)

    @property
    def total_actual(self):
        return sum(i.actual_price for i in self.items)

class OrderItem(db.Model):
    __tablename__ = "order_items"
    id           = db.Column(db.Integer, primary_key=True)
    order_id     = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    product_key  = db.Column(db.String(60),  nullable=False)
    product_name = db.Column(db.String(120), nullable=False)
    list_price   = db.Column(db.Integer, nullable=False)
    actual_price = db.Column(db.Integer, nullable=False)

# backward-compat: keep old Transaction table readable
class Transaction(db.Model):
    __tablename__ = "transactions"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(BKK))
    customer    = db.Column(db.String(120))
    product_key = db.Column(db.String(60))
    product_name= db.Column(db.String(120))
    list_price  = db.Column(db.Integer)
    actual_price= db.Column(db.Integer)
    method      = db.Column(db.String(40), default="bank")
    note        = db.Column(db.String(255), default="")

with app.app_context():
    db.create_all()

# ── Products ───────────────────────────────────────────────────────
PRODUCTS = [
    {"key":"Max Pack",          "name":"Max Pack",       "price":799},
    {"key":"Performance Pack",          "name":"Performance Pack",       "price":649},
    {"key":"Pro Pack",          "name":"Pro Pack",       "price":629},
    {"key":"GOATX",          "name":"🐐 G.O.A.T.X",       "price":429},
    {"key":"ULTIMATEXPLUS",   "name":"💎 ULTIMATEXPLUS",    "price":259},
    {"key":"ULTIMATEXXPLUS",  "name":"💎 ULTIMATEX+PLUS",   "price":629},
    {"key":"ULTIMATEX",       "name":"🔥 ULTIMATEX",        "price":399},
    {"key":"SHXV2",           "name":"🚀 Shx V.2",          "price":309},
    {"key":"SHXV1",           "name":"⚡ Shx V.1",          "price":159},
]
PROD_MAP = {p["key"]: p for p in PRODUCTS}

# ── Helpers ────────────────────────────────────────────────────────
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
    if session.get("user_id"):
        return redirect("/")
    return render_template_string(open("templates/login.html").read())

@app.route("/api/auth/login", methods=["POST"])
def api_login():
    d        = request.json or {}
    username = (d.get("username") or "").strip().lower()
    password = d.get("password") or ""
    user     = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password, password):
        return jsonify({"success": False, "error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"}), 401
    session.permanent = True
    session["user_id"]  = user.id
    session["username"] = user.username
    return jsonify({"success": True, "username": user.username})

@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})

@app.route("/api/auth/me")
def api_me():
    if not session.get("user_id"):
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "username": session.get("username")})

@app.route("/api/auth/change_password", methods=["POST"])
@login_required
def api_change_password():
    d        = request.json or {}
    old_pw   = d.get("old_password", "")
    new_pw   = d.get("new_password", "")
    user     = current_user()
    if not check_password_hash(user.password, old_pw):
        return jsonify({"success": False, "error": "รหัสผ่านเดิมไม่ถูกต้อง"}), 400
    if len(new_pw) < 6:
        return jsonify({"success": False, "error": "รหัสผ่านใหม่ต้องมีอย่างน้อย 6 ตัวอักษร"}), 400
    user.password = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({"success": True})

# สร้าง admin ครั้งแรก (ใช้ได้แค่ถ้ายังไม่มี user เลย)
@app.route("/api/auth/setup", methods=["POST"])
def api_setup():
    if User.query.count() > 0:
        return jsonify({"success": False, "error": "มี admin อยู่แล้ว"}), 403
    d        = request.json or {}
    username = (d.get("username") or "admin").strip().lower()
    password = d.get("password") or ""
    if len(password) < 6:
        return jsonify({"success": False, "error": "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร"}), 400
    user = User(username=username, password=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    return jsonify({"success": True, "username": username})

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
        query = query.filter(
            db.or_(
                Order.customer.ilike(f"%{q}%"),
                Order.note.ilike(f"%{q}%"),
                Order.items.any(OrderItem.product_name.ilike(f"%{q}%")),
            )
        )
    if month:
        y, m = month.split("-")
        query = query.filter(
            db.extract("year",  Order.created_at) == int(y),
            db.extract("month", Order.created_at) == int(m),
        )

    total = query.count()
    items = query.offset((page-1)*limit).limit(limit).all()
    return jsonify({"total": total, "page": page, "items": [order_to_dict(o) for o in items]})

# ── API: Create order (multi-item) ─────────────────────────────────
@app.route("/api/orders", methods=["POST"])
@login_required
def api_create():
    d        = request.json or {}
    customer = (d.get("customer") or "").strip()
    cart     = d.get("items", [])   # [{product_key, actual_price}]

    if not customer:
        return jsonify({"success": False, "error": "กรุณาใส่ชื่อลูกค้า"}), 400
    if not cart:
        return jsonify({"success": False, "error": "กรุณาเลือกสินค้าอย่างน้อย 1 รายการ"}), 400

    order = Order(
        customer = customer,
        method   = d.get("method", "bank"),
        note     = (d.get("note") or "").strip(),
    )
    db.session.add(order)

    for ci in cart:
        prod = PROD_MAP.get(ci.get("product_key"))
        if not prod:
            db.session.rollback()
            return jsonify({"success": False, "error": f"ไม่พบสินค้า {ci.get('product_key')}"}), 400
        item = OrderItem(
            order        = order,
            product_key  = prod["key"],
            product_name = prod["name"],
            list_price   = prod["price"],
            actual_price = int(ci.get("actual_price", prod["price"])),
        )
        db.session.add(item)

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

# ── API: Update order item price ────────────────────────────────────
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

    all_orders = Order.query.all()

    def total_rev(orders): return sum(o.total_actual for o in orders)

    today_orders = [o for o in all_orders if o.created_at.date() == today]
    month_orders = [o for o in all_orders if o.created_at.date() >= month_start]

    # daily 30 days
    daily = {}
    for i in range(29, -1, -1):
        daily[(now - timedelta(days=i)).strftime("%Y-%m-%d")] = 0
    for o in all_orders:
        k = o.created_at.strftime("%Y-%m-%d")
        if k in daily:
            daily[k] += o.total_actual

    # per product (from items)
    all_items = OrderItem.query.all()
    prod_rev = {p["key"]: {"name": p["name"], "count": 0, "revenue": 0} for p in PRODUCTS}
    for i in all_items:
        if i.product_key in prod_rev:
            prod_rev[i.product_key]["count"]   += 1
            prod_rev[i.product_key]["revenue"] += i.actual_price

    return jsonify({
        "today_rev":      total_rev(today_orders),
        "today_count":    len(today_orders),
        "month_rev":      total_rev(month_orders),
        "month_count":    len(month_orders),
        "total_rev":      total_rev(all_orders),
        "total_count":    len(all_orders),
        "total_discount": sum(o.total_list - o.total_actual for o in all_orders),
        "daily":   [{"date": k, "rev": v} for k, v in daily.items()],
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
    w.writerow(["วันที่","ลูกค้า","สินค้า","ราคาปกติ/รายการ","ราคาจริง/รายการ","ส่วนลด/รายการ","รวมบิล","ช่องทาง","หมายเหตุ"])
    for o in orders:
        for idx, item in enumerate(o.items):
            w.writerow([
                o.created_at.strftime("%Y-%m-%d %H:%M") if idx == 0 else "",
                o.customer if idx == 0 else "",
                item.product_name,
                item.list_price,
                item.actual_price,
                item.list_price - item.actual_price,
                o.total_actual if idx == 0 else "",
                o.method if idx == 0 else "",
                o.note if idx == 0 else "",
            ])

    buf.seek(0)
    return send_file(
        io.BytesIO(buf.read().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"insidex_{month or 'all'}.csv",
    )

@app.route("/")
@login_required
def index():
    return render_template_string(open("templates/index.html").read())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)