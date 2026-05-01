from fastapi import FastAPI
import sqlite3
import secrets
from datetime import datetime, timedelta

app = FastAPI()

DB = "veo_server.db"

# ===== CONFIG NGÂN HÀNG =====
BANK_ID = "MB"
ACCOUNT_NO = "123456789"
ACCOUNT_NAME = "NGUYEN VAN A"


# =====================
# INIT DB
# =====================
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS licenses (
        email TEXT,
        license_key TEXT,
        plan TEXT,
        credit INTEGER,
        expires_at TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_code TEXT,
        email TEXT,
        plan TEXT,
        amount INTEGER,
        status TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()


# =====================
# PLANS
# =====================
PLANS = {
    "starter": {"price": 49000, "credit": 50, "days": 30},
    "basic": {"price": 149000, "credit": 200, "days": 30},
    "pro": {"price": 399000, "credit": 800, "days": 30},
}


# =====================
# SHOP
# =====================
@app.get("/shop")
def shop():
    return """
    <h2>Veo Tool - Mua gói</h2>

    <form action="/create_order">
        Email: <input name="email"/><br><br>

        <select name="plan">
            <option value="starter">Starter - 49K</option>
            <option value="basic">Basic - 149K</option>
            <option value="pro">Pro - 399K</option>
        </select>

        <br><br>
        <button type="submit">Mua ngay</button>
    </form>
    """


# =====================
# CREATE ORDER
# =====================
@app.get("/create_order")
def create_order(email: str, plan: str):
    order_code = "ORDER-" + secrets.token_hex(5).upper()

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
        (order_code, email, plan, PLANS[plan]["price"], "pending")
    )

    conn.commit()
    conn.close()

    return {
        "order_code": order_code,
        "order_url": f"/order/{order_code}"
    }


# =====================
# VIEW ORDER + QR
# =====================
@app.get("/order/{order_code}")
def view_order(order_code: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT * FROM orders WHERE order_code=?", (order_code,))
    row = c.fetchone()

    if not row:
        return {"error": "Không tìm thấy đơn"}

    order_code, email, plan, amount, status = row

    # QR VietQR
    qr_url = f"https://img.vietqr.io/image/{BANK_ID}-{ACCOUNT_NO}-compact.png?amount={amount}&addInfo={order_code}&accountName={ACCOUNT_NAME}"

    html = f"""
    <h2>Thanh toán đơn hàng</h2>

    <p><b>Mã đơn:</b> {order_code}</p>
    <p>Email: {email}</p>
    <p>Gói: {plan}</p>
    <p>Số tiền: {amount} VND</p>

    <h3>Quét QR để thanh toán</h3>
    <img src="{qr_url}" width="300"/>

    <p><b>Nội dung chuyển khoản:</b> {order_code}</p>

    <p>⏳ Sau khi chuyển khoản, bấm nút kiểm tra:</p>

    <a href="/check_payment/{order_code}">
    👉 Tôi đã chuyển khoản
    </a>
    """

    if status == "paid":
        c.execute("SELECT * FROM licenses WHERE email=?", (email,))
        lic = c.fetchone()

        if lic:
            html += f"""
            <h3>🎉 License của bạn:</h3>
            <p>{lic[1]}</p>
            """

    conn.close()
    return html


# =====================
# CHECK PAYMENT (GIẢ LẬP AUTO)
# =====================
@app.get("/check_payment/{order_code}")
def check_payment(order_code: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute("SELECT * FROM orders WHERE order_code=?", (order_code,))
    row = c.fetchone()

    if not row:
        return {"error": "Không tìm thấy đơn"}

    order_code, email, plan, amount, status = row

    if status == "paid":
        return {"message": "Đã thanh toán"}

    # 🔥 TẠM THỜI: giả lập khách đã chuyển khoản
    c.execute("UPDATE orders SET status='paid' WHERE order_code=?", (order_code,))

    license_key = "VEO-" + secrets.token_hex(6).upper()
    expires = datetime.now() + timedelta(days=PLANS[plan]["days"])

    c.execute(
        "INSERT INTO licenses VALUES (?, ?, ?, ?, ?)",
        (email, license_key, plan, PLANS[plan]["credit"], expires.isoformat())
    )

    conn.commit()
    conn.close()

    return {
        "message": "Thanh toán OK",
        "license_key": license_key
    }


# =====================
# LOGIN
# =====================
@app.get("/login")
def login(email: str, license_key: str):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute(
        "SELECT * FROM licenses WHERE email=? AND license_key=?",
        (email, license_key)
    )

    row = c.fetchone()
    conn.close()

    if not row:
        return {"success": False}

    return {
        "success": True,
        "plan": row[2],
        "credit": row[3]
    }
