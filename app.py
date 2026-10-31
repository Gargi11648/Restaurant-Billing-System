import os
from decimal import Decimal
from flask import Flask, jsonify, request, send_from_directory
import mysql.connector

app = Flask(__name__, static_folder="static", static_url_path="")


def db():
    # Change these or set them as environment variables
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "restaurant_db"),
    )


def num(v):
    return float(v) if isinstance(v, Decimal) else v


@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.get("/api/state")
def state():
    con = db()
    cur = con.cursor(dictionary=True)
    try:
        cur.execute("SELECT cust_id, cust_name FROM customers ORDER BY cust_id")
        custs = cur.fetchall()
        idx = {c["cust_id"]: i for i, c in enumerate(custs)}

        cur.execute("SELECT item_id AS id, item_name AS name, category AS cat, "
                    "price, stock_qty AS stock FROM menu_items ORDER BY item_id")
        menu = [{k: num(v) for k, v in r.items()} for r in cur.fetchall()]

        cur.execute("SELECT bill_id, cust_id, table_no, UNIX_TIMESTAMP(bill_date)*1000 AS d, "
                    "subtotal, discount, gst, total FROM bills WHERE status='PAID' ORDER BY bill_id")
        bills = {r["bill_id"]: {"id": r["bill_id"], "cust": idx.get(r["cust_id"], 0),
                                "table": r["table_no"], "date": int(r["d"]),
                                "sub": num(r["subtotal"]), "disc": num(r["discount"]),
                                "gst": num(r["gst"]), "total": num(r["total"]), "lines": []}
                 for r in cur.fetchall()}

        cur.execute("SELECT bi.bill_id, m.item_id, m.item_name, m.price, bi.qty, bi.line_total "
                    "FROM bill_items bi JOIN menu_items m ON m.item_id = bi.item_id")
        for r in cur.fetchall():
            b = bills.get(r["bill_id"])
            if b:
                b["lines"].append({"id": r["item_id"], "name": r["item_name"], "price": num(r["price"]),
                                   "qty": r["qty"], "line": num(r["line_total"])})

        cur.execute("SELECT UNIX_TIMESTAMP(log_time)*1000 AS t, action AS msg FROM audit_log ORDER BY log_id")
        audit = [{"t": int(r["t"]), "msg": r["msg"]} for r in cur.fetchall()]

        return jsonify(menu=menu, customers=[c["cust_name"] for c in custs],
                       bills=list(bills.values()), audit=audit)
    finally:
        con.close()


@app.post("/api/bills")
def create_bill():
    d = request.get_json()
    want = {}
    for i in d.get("items", []):
        want[int(i["id"])] = want.get(int(i["id"]), 0) + int(i["qty"])
    if not want:
        return jsonify(error="Add at least one dish"), 400
    con = db()
    cur = con.cursor(dictionary=True)
    try:
        cur.execute("SELECT cust_id FROM customers ORDER BY cust_id")
        cust_id = [r["cust_id"] for r in cur.fetchall()][int(d["cust"])]
        marks = ",".join(["%s"] * len(want))
        cur.execute(f"SELECT item_id, item_name, stock_qty FROM menu_items WHERE item_id IN ({marks})",
                    list(want))
        for r in cur.fetchall():
            if r["stock_qty"] < want[r["item_id"]]:
                return jsonify(error=f"Insufficient stock for {r['item_name']} ({r['stock_qty']} left)"), 400
        # The same stored procedures you wrote in MySQL Workbench
        bill = cur.callproc("sp_create_bill", (cust_id, int(d["table"]), 0))[2]
        for item_id, qty in want.items():
            cur.callproc("sp_add_item", (bill, item_id, qty))
        cur.callproc("sp_finalize_bill", (bill,))
        con.commit()
        return jsonify(bill_id=bill)
    except mysql.connector.Error as e:
        return jsonify(error=e.msg), 400
    finally:
        con.close()


@app.post("/api/menu")
def add_dish():
    d = request.get_json()
    con = db()
    cur = con.cursor()
    try:
        cur.execute("INSERT INTO menu_items (item_name, category, price, stock_qty) VALUES (%s,%s,%s,%s)",
                    (d["name"], d["cat"], d["price"], d["stock"]))
        con.commit()
        return jsonify(ok=True)
    except mysql.connector.Error as e:
        return jsonify(error=e.msg), 400
    finally:
        con.close()


@app.post("/api/restock")
def restock():
    d = request.get_json()
    con = db()
    cur = con.cursor()
    try:
        cur.execute("UPDATE menu_items SET stock_qty = stock_qty + 20 WHERE item_id = %s", (d["id"],))
        cur.execute("INSERT INTO audit_log (action) SELECT CONCAT('Restocked ', item_name, ' +20') "
                    "FROM menu_items WHERE item_id = %s", (d["id"],))
        con.commit()
        return jsonify(ok=True)
    except mysql.connector.Error as e:
        return jsonify(error=e.msg), 400
    finally:
        con.close()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
