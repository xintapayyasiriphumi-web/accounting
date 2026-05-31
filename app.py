from flask import Flask, request, jsonify, render_template_string, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
import csv, io, os

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///insidex.db"
).replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
BKK = timezone(timedelta(hours=7))

# ── Models ────────────────────────────────────────────
class Transaction(db.Model):
    __tablename__ = "transactions"
    id          = db.Column(db.Integer, primary_key=True)
    created_at  = db.Column(db.DateTime, default=lambda: datetime.now(BKK))
    customer    = db.Column(db.String(120), nullable=False)
    product_key = db.Column(db.String(60),  nullable=False)
    product_name= db.Column(db.String(120), nullable=False)
    list_price  = db.Column(db.Integer,     nullable=False)  # ราคาปกติ
    actual_price= db.Column(db.Integer,     nullable=False)  # ราคาที่รับจริง
    method      = db.Column(db.String(40),  default="bank")
    note        = db.Column(db.String(255), default="")

with app.app_context():
    db.create_all()

# ── Products ──────────────────────────────────────────
PRODUCTS = [
    {"key":"GOATX",         "name":"🐐 G.O.A.T.X",        "price":429},
    {"key":"ULTIMATEXPLUS",  "name":"💎 ULTIMATEXPLUS",     "price":259},
    {"key":"ULTIMATEXXPLUS", "name":"💎 ULTIMATEX+PLUS",    "price":629},
    {"key":"ULTIMATEX",      "name":"🔥 ULTIMATEX",         "price":399},
    {"key":"SHXV2",          "name":"🚀 Shx V.2",           "price":309},
    {"key":"SHXV1",          "name":"⚡ Shx V.1",           "price":159},
]

# ── API ───────────────────────────────────────────────
@app.route("/api/products")
def api_products():
    return jsonify(PRODUCTS)

@app.route("/api/transactions", methods=["GET"])
def api_list():
    page  = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    q     = request.args.get("q", "").strip()
    month = request.args.get("month", "")  # YYYY-MM

    query = Transaction.query.order_by(Transaction.created_at.desc())
    if q:
        query = query.filter(
            db.or_(
                Transaction.customer.ilike(f"%{q}%"),
                Transaction.product_name.ilike(f"%{q}%"),
                Transaction.note.ilike(f"%{q}%"),
            )
        )
    if month:
        y, m = month.split("-")
        query = query.filter(
            db.extract("year",  Transaction.created_at) == int(y),
            db.extract("month", Transaction.created_at) == int(m),
        )

    total  = query.count()
    items  = query.offset((page-1)*limit).limit(limit).all()
    return jsonify({
        "total": total,
        "page":  page,
        "items": [{
            "id":           t.id,
            "created_at":   t.created_at.strftime("%Y-%m-%d %H:%M"),
            "customer":     t.customer,
            "product_key":  t.product_key,
            "product_name": t.product_name,
            "list_price":   t.list_price,
            "actual_price": t.actual_price,
            "discount":     t.list_price - t.actual_price,
            "method":       t.method,
            "note":         t.note,
        } for t in items],
    })

@app.route("/api/transactions", methods=["POST"])
def api_create():
    d = request.json or {}
    prod = next((p for p in PRODUCTS if p["key"] == d.get("product_key")), None)
    if not prod:
        return jsonify({"success": False, "error": "ไม่พบสินค้า"}), 400
    if not d.get("customer"):
        return jsonify({"success": False, "error": "กรุณาใส่ชื่อลูกค้า"}), 400

    t = Transaction(
        customer     = d["customer"].strip(),
        product_key  = prod["key"],
        product_name = prod["name"],
        list_price   = prod["price"],
        actual_price = int(d.get("actual_price", prod["price"])),
        method       = d.get("method", "bank"),
        note         = d.get("note", "").strip(),
    )
    db.session.add(t)
    db.session.commit()
    return jsonify({"success": True, "id": t.id})

@app.route("/api/transactions/<int:tid>", methods=["DELETE"])
def api_delete(tid):
    t = Transaction.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/transactions/<int:tid>", methods=["PATCH"])
def api_update(tid):
    t = Transaction.query.get_or_404(tid)
    d = request.json or {}
    if "actual_price" in d: t.actual_price = int(d["actual_price"])
    if "note"         in d: t.note         = d["note"]
    if "customer"     in d: t.customer     = d["customer"]
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/dashboard")
def api_dashboard():
    now   = datetime.now(BKK)
    today = now.date()
    month_start = today.replace(day=1)

    all_tx = Transaction.query.all()

    def rev(txs): return sum(t.actual_price for t in txs)

    today_tx  = [t for t in all_tx if t.created_at.date() == today]
    month_tx  = [t for t in all_tx if t.created_at.date() >= month_start]

    # daily for last 30 days
    daily = {}
    for i in range(29, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily[d] = 0
    for t in all_tx:
        k = t.created_at.strftime("%Y-%m-%d")
        if k in daily:
            daily[k] += t.actual_price

    # per product
    prod_rev = {}
    for p in PRODUCTS:
        prod_rev[p["key"]] = {"name": p["name"], "count": 0, "revenue": 0}
    for t in all_tx:
        if t.product_key in prod_rev:
            prod_rev[t.product_key]["count"]   += 1
            prod_rev[t.product_key]["revenue"] += t.actual_price

    return jsonify({
        "today_rev":   rev(today_tx),
        "today_count": len(today_tx),
        "month_rev":   rev(month_tx),
        "month_count": len(month_tx),
        "total_rev":   rev(all_tx),
        "total_count": len(all_tx),
        "total_discount": sum(t.list_price - t.actual_price for t in all_tx),
        "daily":  [{"date": k, "rev": v} for k, v in daily.items()],
        "products": list(prod_rev.values()),
    })

@app.route("/api/export/csv")
def api_export():
    month = request.args.get("month", "")
    query = Transaction.query.order_by(Transaction.created_at.desc())
    if month:
        y, m = month.split("-")
        query = query.filter(
            db.extract("year",  Transaction.created_at) == int(y),
            db.extract("month", Transaction.created_at) == int(m),
        )
    items = query.all()

    buf = io.StringIO()
    buf.write("\ufeff")  # BOM for Thai Excel
    w = csv.writer(buf)
    w.writerow(["วันที่","ลูกค้า","สินค้า","ราคาปกติ","ราคาจริง","ส่วนลด","ช่องทาง","หมายเหตุ"])
    for t in items:
        w.writerow([
            t.created_at.strftime("%Y-%m-%d %H:%M"),
            t.customer, t.product_name,
            t.list_price, t.actual_price,
            t.list_price - t.actual_price,
            t.method, t.note,
        ])

    buf.seek(0)
    fname = f"insidex_{month or 'all'}.csv"
    return send_file(
        io.BytesIO(buf.read().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=fname,
    )

@app.route("/")
def index():
    return render_template_string(open("templates/index.html").read())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=False)
