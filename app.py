from flask import Flask, request, jsonify, render_template_string, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
import csv, io, os, json

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///insidex.db"
).replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
BKK = timezone(timedelta(hours=7))

# ── Models ─────────────────────────────────────────────────────────
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

# ── API: Products ──────────────────────────────────────────────────
@app.route("/api/products")
def api_products():
    return jsonify(PRODUCTS)

# ── API: Orders list ───────────────────────────────────────────────
@app.route("/api/orders", methods=["GET"])
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
def api_delete(oid):
    o = Order.query.get_or_404(oid)
    db.session.delete(o)
    db.session.commit()
    return jsonify({"success": True})

# ── API: Update order item price ────────────────────────────────────
@app.route("/api/order_items/<int:iid>", methods=["PATCH"])
def api_patch_item(iid):
    item = OrderItem.query.get_or_404(iid)
    d    = request.json or {}
    if "actual_price" in d:
        item.actual_price = max(0, int(d["actual_price"]))
    db.session.commit()
    return jsonify({"success": True})

# ── API: Dashboard ─────────────────────────────────────────────────
@app.route("/api/dashboard")
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
def index():
    return render_template_string(open("templates/index.html").read())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
