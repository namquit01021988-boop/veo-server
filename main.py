import sqlite3
import secrets
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

DB_NAME = "veo_server.db"
ADMIN_TOKEN = "NAMQUIT_ADMIN_123"

app = FastAPI(title="Veo Tool API Server")


PLAN_CONFIG = {
    "free": {
        "name": "Free",
        "credit": 5,
        "days": 7,
        "price": 0,
        "price_text": "0 VNĐ",
        "concurrent": 1,
        "prompt_limit": 20,
        "description": "Dùng thử 5 credit"
    },
    "starter": {
        "name": "Starter",
        "credit": 50,
        "days": 30,
        "price": 49000,
        "price_text": "49,000 VNĐ",
        "concurrent": 1,
        "prompt_limit": 50,
        "description": "Phù hợp người mới bắt đầu"
    },
    "basic": {
        "name": "Basic",
        "credit": 200,
        "days": 30,
        "price": 149000,
        "price_text": "149,000 VNĐ",
        "concurrent": 3,
        "prompt_limit": 150,
        "description": "Gói phổ biến nhất"
    },
    "pro": {
        "name": "Pro",
        "credit": 800,
        "days": 30,
        "price": 399000,
        "price_text": "399,000 VNĐ",
        "concurrent": 9,
        "prompt_limit": 300,
        "description": "Dành cho người làm số lượng lớn"
    }
}


def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def now_text():
    return datetime.now().isoformat()


def make_license_key():
    return "VEO-" + secrets.token_hex(8).upper()


def make_order_code():
    return "ORDER-" + secrets.token_hex(5).upper()


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS licenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        license_key TEXT UNIQUE NOT NULL,
        plan TEXT NOT NULL,
        credit INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL,
        plan TEXT NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT NOT NULL,
        license_key TEXT,
        created_at TEXT NOT NULL,
        paid_at TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


class CreateLicenseRequest(BaseModel):
    email: str
    plan: str = "free"


class LoginRequest(BaseModel):
    email: str
    license_key: str


class ConsumeCreditRequest(BaseModel):
    email: str
    license_key: str
    credit_cost: int


class AddCreditRequest(BaseModel):
    email: str
    license_key: str
    credit: int


class ChangePlanRequest(BaseModel):
    email: str
    license_key: str
    plan: str


class CreateOrderRequest(BaseModel):
    email: str
    plan: str


class ConfirmOrderRequest(BaseModel):
    order_code: str


def create_license_for_order(email: str, plan_key: str):
    if plan_key not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ")

    plan = PLAN_CONFIG[plan_key]
    license_key = make_license_key()
    now = datetime.now()
    expires_at = now + timedelta(days=plan["days"])

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO licenses (
        email, license_key, plan, credit, status, created_at, expires_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        email,
        license_key,
        plan_key,
        plan["credit"],
        "active",
        now.isoformat(),
        expires_at.isoformat()
    ))

    conn.commit()
    conn.close()

    return {
        "email": email,
        "license_key": license_key,
        "plan": plan_key,
        "plan_name": plan["name"],
        "credit": plan["credit"],
        "days": plan["days"],
        "expires_at": expires_at.isoformat()
    }


def plan_card(plan_key, plan):
    return f"""
<div class="card">
    <h2>{plan["name"]}</h2>
    <div class="feature green">Thời gian sử dụng: {plan["days"]} ngày</div>
    <div class="feature purple">{plan["credit"]} credit</div>
    <div class="feature orange">Xử lý {plan["concurrent"]} video cùng lúc</div>
    <div class="feature">Prompt tối đa/lần: {plan["prompt_limit"]}</div>
    <div class="price">{plan["price_text"]}</div>
    <input class="email" id="email-{plan_key}" placeholder="Nhập email nhận license">
    <button onclick="createOrder('{plan_key}')">Chọn gói này</button>
</div>
"""


@app.get("/")
def home():
    return {
        "message": "Veo Tool API Server is running",
        "version": "shop-orders-v1"
    }


@app.get("/plans")
def plans():
    return {"plans": PLAN_CONFIG}


@app.get("/shop", response_class=HTMLResponse)
def shop():
    starter = PLAN_CONFIG["starter"]
    basic = PLAN_CONFIG["basic"]
    pro = PLAN_CONFIG["pro"]

    html = f"""
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Veo Tool - Mua gói</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            margin: 0;
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #eef5ff, #f5f3ff);
            color: #0f172a;
        }}
        .wrap {{
            max-width: 1100px;
            margin: 0 auto;
            padding: 32px 16px;
        }}
        h1 {{
            text-align: center;
            font-size: 34px;
            margin-bottom: 8px;
        }}
        .sub {{
            text-align: center;
            color: #475569;
            margin-bottom: 32px;
        }}
        .plans {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 18px;
        }}
        .card {{
            background: white;
            border: 1px solid #dbeafe;
            border-radius: 18px;
            padding: 22px;
            box-shadow: 0 12px 30px rgba(15,23,42,.08);
        }}
        .card h2 {{
            text-align: center;
            margin: 0 0 12px;
            font-size: 24px;
        }}
        .price {{
            text-align: center;
            font-size: 28px;
            font-weight: bold;
            margin: 18px 0;
            color: #7c3aed;
        }}
        .feature {{
            background: #f1f5f9;
            margin: 8px 0;
            padding: 10px;
            border-radius: 12px;
            text-align: center;
        }}
        .feature.green {{ background: #dcfce7; color: #065f46; }}
        .feature.purple {{ background: #ede9fe; color: #4c1d95; }}
        .feature.orange {{ background: #fef3c7; color: #92400e; }}
        .email {{
            width: 100%;
            box-sizing: border-box;
            padding: 12px;
            border: 2px solid #c084fc;
            border-radius: 12px;
            margin-top: 14px;
            font-size: 15px;
        }}
        button {{
            width: 100%;
            padding: 13px;
            border: none;
            border-radius: 12px;
            background: #22c55e;
            color: white;
            font-weight: bold;
            font-size: 16px;
            margin-top: 12px;
            cursor: pointer;
        }}
        button:hover {{ background: #16a34a; }}
        .result {{
            margin-top: 24px;
            background: white;
            border-radius: 16px;
            padding: 18px;
            display: none;
            box-shadow: 0 12px 30px rgba(15,23,42,.08);
        }}
        .code {{
            font-family: monospace;
            background: #0f172a;
            color: #22c55e;
            padding: 10px;
            border-radius: 8px;
            overflow-wrap: anywhere;
        }}
        @media (max-width: 850px) {{
            .plans {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <h1>Veo Tool - Mua gói cước</h1>
        <div class="sub">Chọn gói, nhập email, tạo đơn hàng. Bước thanh toán tự động sẽ nối ở bước tiếp theo.</div>

        <div class="plans">
            {plan_card("starter", starter)}
            {plan_card("basic", basic)}
            {plan_card("pro", pro)}
        </div>

        <div id="result" class="result"></div>
    </div>

<script>
async function createOrder(plan) {{
    const email = document.getElementById("email-" + plan).value.trim();
    if (!email) {{
        alert("Vui lòng nhập email");
        return;
    }}

    const res = await fetch("/api/create-order", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ email, plan }})
    }});

    const data = await res.json();

    if (!res.ok) {{
        alert(data.detail || "Không tạo được đơn hàng");
        return;
    }}

    const result = document.getElementById("result");
    result.style.display = "block";
    result.innerHTML = `
        <h2>Đã tạo đơn hàng</h2>
        <p><b>Mã đơn:</b></p>
        <div class="code">${{data.order_code}}</div>
        <p><b>Email:</b> ${{data.email}}</p>
        <p><b>Gói:</b> ${{data.plan_name}}</p>
        <p><b>Số tiền:</b> ${{data.amount_text}}</p>
        <p><b>Trạng thái:</b> Chờ thanh toán</p>
        <p>Bước tiếp theo sẽ nối payOS để khách quét QR thanh toán và hệ thống tự cấp key.</p>
        <p>Link kiểm tra đơn:</p>
        <div class="code">${{window.location.origin}}/order/${{data.order_code}}</div>
    `;
    result.scrollIntoView({{ behavior: "smooth" }});
}}
</script>
</body>
</html>
"""
    return html


@app.post("/api/create-order")
def create_order(data: CreateOrderRequest):
    if data.plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ")

    if data.plan == "free":
        raise HTTPException(status_code=400, detail="Không tạo đơn hàng cho gói free")

    email = data.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Email không hợp lệ")

    plan = PLAN_CONFIG[data.plan]
    order_code = make_order_code()

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO orders (
        order_code, email, plan, amount, status, license_key, created_at, paid_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_code,
        email,
        data.plan,
        plan["price"],
        "pending",
        None,
        now_text(),
        None
    ))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "order_code": order_code,
        "email": email,
        "plan": data.plan,
        "plan_name": plan["name"],
        "amount": plan["price"],
        "amount_text": plan["price_text"],
        "status": "pending"
    }


@app.get("/order/{order_code}", response_class=HTMLResponse)
def order_page(order_code: str):
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM orders WHERE order_code = ?", (order_code,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return HTMLResponse("<h2>Không tìm thấy đơn hàng</h2>", status_code=404)

    plan = PLAN_CONFIG.get(row["plan"], {})
    license_html = ""

    if row["status"] == "paid" and row["license_key"]:
        license_html = f"""
        <h3>License key của bạn:</h3>
        <div class="code">{row["license_key"]}</div>
        <p>Dùng email <b>{row["email"]}</b> và license key trên để đăng nhập tool.</p>
        """
    else:
        license_html = """
        <p>Đơn hàng đang chờ thanh toán/xác nhận.</p>
        <p>Bước tiếp theo sẽ nối thanh toán tự động để key tự hiện sau khi chuyển khoản.</p>
        """

    html = f"""
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Đơn hàng {order_code}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: Arial, sans-serif; background:#eef5ff; padding:24px; }}
        .box {{ max-width:720px; margin:0 auto; background:white; border-radius:16px; padding:24px; box-shadow:0 12px 30px rgba(15,23,42,.08); }}
        .code {{ font-family:monospace; background:#0f172a; color:#22c55e; padding:12px; border-radius:8px; overflow-wrap:anywhere; }}
        .status {{ display:inline-block; padding:8px 12px; border-radius:999px; background:#ede9fe; color:#4c1d95; font-weight:bold; }}
    </style>
</head>
<body>
    <div class="box">
        <h1>Thông tin đơn hàng</h1>
        <p><b>Mã đơn:</b></p>
        <div class="code">{row["order_code"]}</div>
        <p><b>Email:</b> {row["email"]}</p>
        <p><b>Gói:</b> {plan.get("name", row["plan"])}</p>
        <p><b>Số tiền:</b> {plan.get("price_text", row["amount"])}</p>
        <p><b>Trạng thái:</b> <span class="status">{row["status"]}</span></p>
        {license_html}
    </div>
</body>
</html>
"""
    return html


@app.get("/admin/orders")
def list_orders(x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100")
    rows = cur.fetchall()
    conn.close()

    return {
        "success": True,
        "orders": [dict(row) for row in rows]
    }


@app.post("/admin/confirm-order")
def confirm_order(data: ConfirmOrderRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM orders WHERE order_code = ?", (data.order_code,))
    order = cur.fetchone()

    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    if order["status"] == "paid" and order["license_key"]:
        conn.close()
        return {
            "success": True,
            "message": "Đơn hàng đã được xác nhận trước đó",
            "order_code": order["order_code"],
            "license_key": order["license_key"]
        }

    conn.close()

    license_data = create_license_for_order(order["email"], order["plan"])

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    UPDATE orders
    SET status = ?, license_key = ?, paid_at = ?
    WHERE order_code = ?
    """, (
        "paid",
        license_data["license_key"],
        now_text(),
        data.order_code
    ))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "order_code": data.order_code,
        "email": order["email"],
        "plan": order["plan"],
        "license_key": license_data["license_key"],
        "credit": license_data["credit"],
        "expires_at": license_data["expires_at"]
    }


@app.post("/admin/create-license")
def create_license(data: CreateLicenseRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    license_data = create_license_for_order(data.email.strip().lower(), data.plan)

    return {
        "success": True,
        **license_data,
        "price": PLAN_CONFIG[data.plan]["price_text"]
    }


@app.post("/auth/login")
def login(data: LoginRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Email hoặc license key không đúng")

    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        raise HTTPException(status_code=403, detail="Gói cước đã hết hạn")

    plan_info = PLAN_CONFIG.get(row["plan"], {})

    return {
        "success": True,
        "email": row["email"],
        "plan": row["plan"],
        "plan_name": plan_info.get("name", row["plan"]),
        "credit": row["credit"],
        "status": row["status"],
        "expires_at": row["expires_at"],
        "concurrent": plan_info.get("concurrent", 1),
        "prompt_limit": plan_info.get("prompt_limit", 20)
    }


@app.post("/usage/check")
def check_usage(data: ConsumeCreditRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="License không hợp lệ")

    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        raise HTTPException(status_code=403, detail="Gói cước đã hết hạn")

    if row["credit"] < data.credit_cost:
        raise HTTPException(status_code=402, detail="Không đủ credit")

    return {
        "success": True,
        "credit_available": row["credit"],
        "credit_cost": data.credit_cost,
        "credit_after": row["credit"] - data.credit_cost
    }


@app.post("/usage/consume")
def consume_credit(data: ConsumeCreditRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="License không hợp lệ")

    if row["status"] != "active":
        conn.close()
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        conn.close()
        raise HTTPException(status_code=403, detail="Gói cước đã hết hạn")

    if row["credit"] < data.credit_cost:
        conn.close()
        raise HTTPException(status_code=402, detail="Không đủ credit")

    new_credit = row["credit"] - data.credit_cost

    cur.execute("""
    UPDATE licenses
    SET credit = ?
    WHERE email = ? AND license_key = ?
    """, (new_credit, data.email.strip().lower(), data.license_key.strip()))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "credit_before": row["credit"],
        "credit_cost": data.credit_cost,
        "credit_after": new_credit
    }


@app.post("/admin/add-credit")
def add_credit(data: AddCreditRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy license")

    new_credit = row["credit"] + data.credit

    cur.execute("""
    UPDATE licenses
    SET credit = ?
    WHERE email = ? AND license_key = ?
    """, (new_credit, data.email.strip().lower(), data.license_key.strip()))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "email": data.email,
        "credit_before": row["credit"],
        "credit_added": data.credit,
        "credit_after": new_credit
    }


@app.post("/admin/change-plan")
def change_plan(data: ChangePlanRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    if data.plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ")

    plan = PLAN_CONFIG[data.plan]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy license")

    expires_at = datetime.now() + timedelta(days=plan["days"])

    cur.execute("""
    UPDATE licenses
    SET plan = ?, credit = ?, expires_at = ?, status = ?
    WHERE email = ? AND license_key = ?
    """, (
        data.plan,
        plan["credit"],
        expires_at.isoformat(),
        "active",
        data.email.strip().lower(),
        data.license_key.strip()
    ))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "email": data.email,
        "new_plan": data.plan,
        "credit": plan["credit"],
        "expires_at": expires_at.isoformat()
    }


@app.post("/admin/lock")
def lock_license(data: LoginRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    UPDATE licenses
    SET status = 'locked'
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    conn.commit()
    conn.close()

    return {"success": True, "message": "Đã khóa tài khoản"}


@app.post("/admin/unlock")
def unlock_license(data: LoginRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    UPDATE licenses
    SET status = 'active'
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    conn.commit()
    conn.close()

    return {"success": True, "message": "Đã mở khóa tài khoản"}
