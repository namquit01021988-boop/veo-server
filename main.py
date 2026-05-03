import os
import time
import secrets
import hashlib
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from typing import Optional

import sqlite3

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:
    psycopg2 = None
    RealDictCursor = None

try:
    from payos import PayOS
    from payos.types import CreatePaymentLinkRequest
except Exception:
    PayOS = None
    CreatePaymentLinkRequest = None


# =========================================================
# CONFIG
# =========================================================

DB_NAME = "veo_server.db"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "CHANGE_ME_ADMIN_TOKEN")
BASE_URL = os.environ.get("BASE_URL", "https://veo-server-65zd.onrender.com").rstrip("/")

PAYOS_CLIENT_ID = os.environ.get("PAYOS_CLIENT_ID", "")
PAYOS_API_KEY = os.environ.get("PAYOS_API_KEY", "")
PAYOS_CHECKSUM_KEY = os.environ.get("PAYOS_CHECKSUM_KEY", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER or "no-reply@veotool.local")
SMTP_TLS = os.environ.get("SMTP_TLS", "true").lower() != "false"

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
        "device_limit": 1,
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
        "device_limit": 1,
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
        "device_limit": 2,
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
        "device_limit": 3,
        "description": "Dành cho người làm số lượng lớn"
    }
}


# =========================================================
# DB LAYER: PostgreSQL nếu có DATABASE_URL, fallback SQLite local
# =========================================================

def using_postgres() -> bool:
    return bool(DATABASE_URL)


def get_conn():
    if using_postgres():
        if psycopg2 is None:
            raise RuntimeError("Thiếu psycopg2-binary. Hãy thêm psycopg2-binary vào requirements.txt")
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def adapt_sql(sql: str) -> str:
    if using_postgres():
        return sql.replace("?", "%s")
    return sql


def row_to_dict(row):
    if not row:
        return None
    return dict(row)


def rows_to_dict(rows):
    return [dict(r) for r in rows]


def db_execute(sql: str, params=(), fetchone=False, fetchall=False):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(adapt_sql(sql), params)
        result = None
        if fetchone:
            result = row_to_dict(cur.fetchone())
        elif fetchall:
            result = rows_to_dict(cur.fetchall())
        conn.commit()
        return result
    finally:
        conn.close()


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    if using_postgres():
        cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            id SERIAL PRIMARY KEY,
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
            id SERIAL PRIMARY KEY,
            order_code TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            plan TEXT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            license_key TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT,
            payment_url TEXT,
            payos_data TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS license_devices (
            id SERIAL PRIMARY KEY,
            license_key TEXT NOT NULL,
            device_id TEXT NOT NULL,
            device_name TEXT,
            ip TEXT,
            user_agent TEXT,
            status TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(license_key, device_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_logs (
            id SERIAL PRIMARY KEY,
            email TEXT,
            license_key TEXT,
            device_id TEXT,
            ip TEXT,
            user_agent TEXT,
            success INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS credit_logs (
            id SERIAL PRIMARY KEY,
            email TEXT,
            license_key TEXT,
            credit_before INTEGER,
            credit_cost INTEGER,
            credit_after INTEGER,
            ip TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payment_url TEXT")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS payos_data TEXT")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS paid_at TEXT")
        cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS license_key TEXT")

    else:
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
            paid_at TEXT,
            payment_url TEXT,
            payos_data TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS license_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT NOT NULL,
            device_id TEXT NOT NULL,
            device_name TEXT,
            ip TEXT,
            user_agent TEXT,
            status TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            UNIQUE(license_key, device_id)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            license_key TEXT,
            device_id TEXT,
            ip TEXT,
            user_agent TEXT,
            success INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS credit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            license_key TEXT,
            credit_before INTEGER,
            credit_cost INTEGER,
            credit_after INTEGER,
            ip TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL
        )
        """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# MODELS
# =========================================================

class CreateLicenseRequest(BaseModel):
    email: str
    plan: str = "free"


class LoginRequest(BaseModel):
    email: str
    license_key: str
    device_id: Optional[str] = None
    device_name: Optional[str] = None


class ConsumeCreditRequest(BaseModel):
    email: str
    license_key: str
    credit_cost: int
    device_id: Optional[str] = None


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


# =========================================================
# HELPERS
# =========================================================

def now_text() -> str:
    return datetime.now().isoformat()


def make_license_key() -> str:
    return "VEO-" + secrets.token_hex(8).upper()


def make_order_code() -> str:
    return str(int(time.time() * 1000))


def format_money(amount: int) -> str:
    return f"{amount:,} VNĐ"


def get_client_ip(request: Optional[Request]) -> str:
    if not request:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def get_user_agent(request: Optional[Request]) -> str:
    if not request:
        return ""
    return request.headers.get("user-agent", "")


def make_device_id(data_device_id: Optional[str], request: Optional[Request]) -> str:
    if data_device_id:
        return data_device_id.strip()

    header_device = request.headers.get("x-device-id", "").strip() if request else ""
    if header_device:
        return header_device

    # Fallback để tool cũ vẫn chạy: fingerprint tạm từ IP + user-agent.
    # Bản tool chuyên nghiệp nên gửi device_id cố định từ máy khách.
    raw = f"{get_client_ip(request)}|{get_user_agent(request)}"
    return "AUTO-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def get_payos_client():
    if PayOS is None or CreatePaymentLinkRequest is None:
        raise HTTPException(status_code=500, detail="Server chưa cài thư viện payos. Hãy thêm 'payos' vào requirements.txt và deploy lại.")

    if not PAYOS_CLIENT_ID or not PAYOS_API_KEY or not PAYOS_CHECKSUM_KEY:
        raise HTTPException(status_code=500, detail="Thiếu PAYOS_CLIENT_ID / PAYOS_API_KEY / PAYOS_CHECKSUM_KEY trong Environment Variables.")

    return PayOS(client_id=PAYOS_CLIENT_ID, api_key=PAYOS_API_KEY, checksum_key=PAYOS_CHECKSUM_KEY)


def log_auth(email: str, license_key: str, device_id: str, request: Optional[Request], success: bool, reason: str):
    try:
        db_execute("""
        INSERT INTO auth_logs (email, license_key, device_id, ip, user_agent, success, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email,
            license_key,
            device_id,
            get_client_ip(request),
            get_user_agent(request),
            1 if success else 0,
            reason,
            now_text()
        ))
    except Exception as e:
        print("log_auth error:", e)


def log_credit(email: str, license_key: str, before: int, cost: int, after: int, request: Optional[Request]):
    try:
        db_execute("""
        INSERT INTO credit_logs (email, license_key, credit_before, credit_cost, credit_after, ip, user_agent, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            email,
            license_key,
            before,
            cost,
            after,
            get_client_ip(request),
            get_user_agent(request),
            now_text()
        ))
    except Exception as e:
        print("log_credit error:", e)


def bind_or_check_device(row: dict, data: LoginRequest, request: Optional[Request]):
    plan_info = PLAN_CONFIG.get(row["plan"], {})
    device_limit = int(plan_info.get("device_limit", 1))
    device_id = make_device_id(data.device_id, request)
    device_name = data.device_name or "Unknown device"

    existing = db_execute("""
    SELECT * FROM license_devices
    WHERE license_key = ? AND device_id = ?
    """, (row["license_key"], device_id), fetchone=True)

    if existing:
        if existing["status"] != "active":
            raise HTTPException(status_code=403, detail="Thiết bị này đã bị khóa")

        db_execute("""
        UPDATE license_devices
        SET last_seen = ?, ip = ?, user_agent = ?
        WHERE license_key = ? AND device_id = ?
        """, (
            now_text(),
            get_client_ip(request),
            get_user_agent(request),
            row["license_key"],
            device_id
        ))
        return device_id

    count_row = db_execute("""
    SELECT COUNT(*) AS total
    FROM license_devices
    WHERE license_key = ? AND status = 'active'
    """, (row["license_key"],), fetchone=True)

    total_devices = int(count_row["total"] or 0)

    if total_devices >= device_limit:
        raise HTTPException(status_code=403, detail=f"License đã đạt giới hạn {device_limit} thiết bị")

    db_execute("""
    INSERT INTO license_devices (
        license_key, device_id, device_name, ip, user_agent, status, first_seen, last_seen
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["license_key"],
        device_id,
        device_name,
        get_client_ip(request),
        get_user_agent(request),
        "active",
        now_text(),
        now_text()
    ))

    return device_id


def send_license_email(email: str, license_key: str, plan_name: str, credit: int, expires_at: str):
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        print("SMTP chưa cấu hình, bỏ qua gửi email.")
        return False

    subject = "License key Veo Tool của bạn"
    body = f"""Xin chào,

Cảm ơn bạn đã mua Veo Tool.

Thông tin đăng nhập:
Email: {email}
License key: {license_key}
Gói: {plan_name}
Credit: {credit}
Hạn dùng: {expires_at}

Bạn mở tool Windows và nhập email + license key trên để đăng nhập.

Trân trọng,
Veo Tool
"""

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_TLS:
            server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    return True


def create_license_for_order(email: str, plan_key: str):
    if plan_key not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ")

    plan = PLAN_CONFIG[plan_key]
    license_key = make_license_key()
    now = datetime.now()
    expires_at = now + timedelta(days=plan["days"])

    db_execute("""
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

    try:
        send_license_email(
            email=email,
            license_key=license_key,
            plan_name=plan["name"],
            credit=plan["credit"],
            expires_at=expires_at.isoformat()
        )
    except Exception as e:
        print("Send email error:", e)

    return {
        "email": email,
        "license_key": license_key,
        "plan": plan_key,
        "plan_name": plan["name"],
        "credit": plan["credit"],
        "days": plan["days"],
        "expires_at": expires_at.isoformat()
    }


def mark_order_paid_and_create_license(order_code: str):
    order = db_execute("SELECT * FROM orders WHERE order_code = ?", (str(order_code),), fetchone=True)

    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng")

    if order["status"] == "paid" and order.get("license_key"):
        return {
            "success": True,
            "message": "Đơn hàng đã thanh toán trước đó",
            "order_code": order["order_code"],
            "email": order["email"],
            "plan": order["plan"],
            "license_key": order["license_key"]
        }

    license_data = create_license_for_order(order["email"], order["plan"])

    db_execute("""
    UPDATE orders
    SET status = ?, license_key = ?, paid_at = ?
    WHERE order_code = ?
    """, (
        "paid",
        license_data["license_key"],
        now_text(),
        str(order_code)
    ))

    return {
        "success": True,
        "message": "Đã thanh toán và cấp license",
        "order_code": str(order_code),
        "email": order["email"],
        "plan": order["plan"],
        "license_key": license_data["license_key"],
        "credit": license_data["credit"],
        "expires_at": license_data["expires_at"]
    }


def check_payos_and_update_order(order_code: str):
    order = db_execute("SELECT * FROM orders WHERE order_code = ?", (str(order_code),), fetchone=True)
    if not order:
        return None

    if order["status"] == "paid":
        return order

    try:
        payos_client = get_payos_client()
        payment_info = None

        if hasattr(payos_client, "payment_requests"):
            payment_info = payos_client.payment_requests.get(int(order_code))
        elif hasattr(payos_client, "get_payment_link_information"):
            payment_info = payos_client.get_payment_link_information(int(order_code))
        elif hasattr(payos_client, "getPaymentLinkInformation"):
            payment_info = payos_client.getPaymentLinkInformation(int(order_code))

        payment_status = None

        if payment_info:
            payment_status = getattr(payment_info, "status", None) or getattr(payment_info, "paymentLinkStatus", None)

            if isinstance(payment_info, dict):
                payment_status = payment_info.get("status") or payment_info.get("paymentLinkStatus") or payment_status

        if str(payment_status).upper() == "PAID":
            mark_order_paid_and_create_license(str(order_code))
            return db_execute("SELECT * FROM orders WHERE order_code = ?", (str(order_code),), fetchone=True)

    except Exception as e:
        print("PayOS status check error:", e)

    return order


def plan_card(plan_key, plan):
    return f"""
<div class="card">
    <div class="badge">{plan["description"]}</div>
    <h2>{plan["name"]}</h2>
    <div class="feature green">Thời gian sử dụng: {plan["days"]} ngày</div>
    <div class="feature purple">{plan["credit"]} credit</div>
    <div class="feature orange">Xử lý {plan["concurrent"]} video cùng lúc</div>
    <div class="feature">Prompt tối đa/lần: {plan["prompt_limit"]}</div>
    <div class="feature">Giới hạn thiết bị: {plan["device_limit"]}</div>
    <div class="price">{plan["price_text"]}</div>
    <input class="email" id="email-{plan_key}" placeholder="Nhập email nhận license">
    <button onclick="createOrder('{plan_key}')">Chọn gói này</button>
</div>
"""


def admin_ok(token: str):
    return token == ADMIN_TOKEN and token not in ["", "CHANGE_ME_ADMIN_TOKEN"]


# =========================================================
# PUBLIC ROUTES
# =========================================================

@app.get("/")
def home():
    return {
        "message": "Veo Tool API Server is running",
        "version": "phase1-secure-postgres-device-logs",
        "database": "postgresql" if using_postgres() else "sqlite-local"
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
            max-width: 1160px;
            margin: 0 auto;
            padding: 34px 16px;
        }}
        h1 {{
            text-align: center;
            font-size: 36px;
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
        .badge {{
            text-align:center;
            font-size:13px;
            color:#0369a1;
            background:#e0f2fe;
            padding:8px;
            border-radius:999px;
            margin-bottom:12px;
        }}
        .card h2 {{
            text-align: center;
            margin: 0 0 12px;
            font-size: 25px;
        }}
        .price {{
            text-align: center;
            font-size: 30px;
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
        @media (max-width: 850px) {{
            .plans {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <h1>Veo Tool - Mua gói cước</h1>
        <div class="sub">Thanh toán QR payOS. Server lưu PostgreSQL, tự cấp license, giới hạn thiết bị và ghi log bảo mật.</div>

        <div class="plans">
            {plan_card("starter", starter)}
            {plan_card("basic", basic)}
            {plan_card("pro", pro)}
        </div>
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

    window.location.href = "/order/" + data.order_code;
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

    payos_client = get_payos_client()

    description = f"VEO{order_code[-8:]}"
    return_url = f"{BASE_URL}/order/{order_code}"
    cancel_url = f"{BASE_URL}/order/{order_code}"

    payment_request = CreatePaymentLinkRequest(
        order_code=int(order_code),
        amount=plan["price"],
        description=description[:25],
        items=[{"name": f"Veo Tool {plan['name']}", "quantity": 1, "price": plan["price"]}],
        cancel_url=cancel_url,
        return_url=return_url
    )

    try:
        payment_link = payos_client.payment_requests.create(payment_request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tạo thanh toán payOS: {e}")

    checkout_url = getattr(payment_link, "checkout_url", None) or getattr(payment_link, "checkoutUrl", None)
    qr_code = getattr(payment_link, "qr_code", None) or getattr(payment_link, "qrCode", None)

    if isinstance(payment_link, dict):
        checkout_url = payment_link.get("checkoutUrl") or payment_link.get("checkout_url") or checkout_url
        qr_code = payment_link.get("qrCode") or payment_link.get("qr_code") or qr_code

    db_execute("""
    INSERT INTO orders (
        order_code, email, plan, amount, status, license_key, created_at, paid_at, payment_url, payos_data
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_code,
        email,
        data.plan,
        plan["price"],
        "pending",
        None,
        now_text(),
        None,
        checkout_url,
        str(payment_link)
    ))

    return {
        "success": True,
        "order_code": order_code,
        "email": email,
        "plan": data.plan,
        "plan_name": plan["name"],
        "amount": plan["price"],
        "amount_text": plan["price_text"],
        "status": "pending",
        "payment_url": checkout_url,
        "qr_code": qr_code
    }


@app.get("/order/{order_code}", response_class=HTMLResponse)
def order_page(order_code: str):
    row = check_payos_and_update_order(str(order_code))

    if not row:
        return HTMLResponse("<h2>Không tìm thấy đơn hàng</h2>", status_code=404)

    plan = PLAN_CONFIG.get(row["plan"], {})
    status = row["status"]

    if status == "paid" and row.get("license_key"):
        payment_html = f"""
        <div class="success">✅ Thanh toán thành công</div>
        <h3>License key của bạn:</h3>
        <div class="code">{row["license_key"]}</div>
        <p>Dùng email <b>{row["email"]}</b> và license key trên để đăng nhập tool.</p>
        <p>Hệ thống cũng đã gửi license về email nếu SMTP được cấu hình.</p>
        """
    else:
        payment_url = row.get("payment_url") or ""
        payment_html = f"""
        <div class="pending">⏳ Đơn hàng đang chờ thanh toán</div>
        <p>Quét QR trong khung bên dưới hoặc bấm mở trang thanh toán payOS.</p>

        <div class="paybox">
            <iframe src="{payment_url}" class="payframe"></iframe>
        </div>

        <p>
            <a class="btn" href="{payment_url}" target="_blank">Mở trang thanh toán payOS</a>
            <a class="btn gray" href="/order/{order_code}">Tôi đã thanh toán - kiểm tra lại</a>
        </p>

        <p class="note">Sau khi thanh toán xong, bấm “Tôi đã thanh toán - kiểm tra lại”. Server sẽ tự hỏi payOS và tự cấp license.</p>
        """

    html = f"""
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Đơn hàng {order_code}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            font-family: Arial, sans-serif;
            background:#eef5ff;
            padding:24px;
            color:#0f172a;
        }}
        .box {{
            max-width:900px;
            margin:0 auto;
            background:white;
            border-radius:18px;
            padding:26px;
            box-shadow:0 12px 30px rgba(15,23,42,.08);
        }}
        .code {{
            font-family:monospace;
            background:#0f172a;
            color:#22c55e;
            padding:12px;
            border-radius:8px;
            overflow-wrap:anywhere;
        }}
        .status {{
            display:inline-block;
            padding:8px 12px;
            border-radius:999px;
            background:#ede9fe;
            color:#4c1d95;
            font-weight:bold;
        }}
        .success {{
            background:#dcfce7;
            color:#166534;
            padding:12px;
            border-radius:12px;
            font-weight:bold;
            margin-top:16px;
        }}
        .pending {{
            background:#fef3c7;
            color:#92400e;
            padding:12px;
            border-radius:12px;
            font-weight:bold;
            margin-top:16px;
        }}
        .paybox {{
            margin-top:16px;
            border:1px solid #dbeafe;
            border-radius:16px;
            overflow:hidden;
            background:#f8fafc;
        }}
        .payframe {{
            width:100%;
            height:720px;
            border:0;
        }}
        .btn {{
            display:inline-block;
            margin-top:14px;
            margin-right:8px;
            padding:12px 16px;
            background:#22c55e;
            color:white;
            border-radius:12px;
            text-decoration:none;
            font-weight:bold;
        }}
        .btn.gray {{
            background:#64748b;
        }}
        .note {{
            color:#475569;
            font-size:14px;
        }}
    </style>
</head>
<body>
    <div class="box">
        <h1>Thông tin đơn hàng</h1>
        <p><b>Mã đơn:</b></p>
        <div class="code">{row["order_code"]}</div>
        <p><b>Email:</b> {row["email"]}</p>
        <p><b>Gói:</b> {plan.get("name", row["plan"])}</p>
        <p><b>Số tiền:</b> {format_money(row["amount"])}</p>
        <p><b>Trạng thái:</b> <span class="status">{status}</span></p>
        {payment_html}
    </div>
</body>
</html>
"""
    return html


@app.post("/webhook/payos")
async def payos_webhook(request: Request):
    payos_client = get_payos_client()

    try:
        body = await request.body()
        verified = payos_client.webhooks.verify(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook không hợp lệ: {e}")

    order_code = getattr(verified, "order_code", None) or getattr(verified, "orderCode", None)

    if isinstance(verified, dict):
        order_code = verified.get("orderCode") or verified.get("order_code") or order_code

    if not order_code:
        raise HTTPException(status_code=400, detail="Webhook thiếu orderCode")

    result = mark_order_paid_and_create_license(str(order_code))

    return {
        "success": True,
        "message": "OK",
        "order_code": str(order_code),
        "license_key": result.get("license_key")
    }


# =========================================================
# ADMIN DASHBOARD + ADMIN API
# =========================================================

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(token: str = ""):
    if not admin_ok(token):
        return HTMLResponse("""
        <h2>Veo Admin Login</h2>
        <p>Nhập ADMIN_TOKEN đã đặt trong Render Environment.</p>
        <form>
            <input name="token" placeholder="Admin token" style="padding:10px;width:360px">
            <button style="padding:10px">Đăng nhập</button>
        </form>
        """, status_code=401)

    orders = db_execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True)
    licenses = db_execute("SELECT * FROM licenses ORDER BY id DESC LIMIT 100", fetchall=True)
    devices = db_execute("SELECT * FROM license_devices ORDER BY id DESC LIMIT 100", fetchall=True)
    auth_logs = db_execute("SELECT * FROM auth_logs ORDER BY id DESC LIMIT 100", fetchall=True)
    credit_logs = db_execute("SELECT * FROM credit_logs ORDER BY id DESC LIMIT 100", fetchall=True)

    total_revenue = sum(o["amount"] for o in orders if o["status"] == "paid")
    paid_count = sum(1 for o in orders if o["status"] == "paid")
    pending_count = sum(1 for o in orders if o["status"] != "paid")

    order_rows = ""
    for o in orders:
        plan_name = PLAN_CONFIG.get(o["plan"], {}).get("name", o["plan"])
        license_value = o.get("license_key") or "-"
        confirm_link = f"/admin/confirm-order-ui?token={token}&order_code={o['order_code']}"
        order_rows += f"""
        <tr>
            <td>{o['order_code']}</td>
            <td>{o['email']}</td>
            <td>{plan_name}</td>
            <td>{format_money(o['amount'])}</td>
            <td><b>{o['status']}</b></td>
            <td class="mono">{license_value}</td>
            <td><a href="/order/{o['order_code']}" target="_blank">Xem</a> | <a href="{confirm_link}">Duyệt</a></td>
        </tr>
        """

    license_rows = ""
    for l in licenses:
        plan_name = PLAN_CONFIG.get(l["plan"], {}).get("name", l["plan"])
        lock_link = f"/admin/set-license-status?token={token}&email={l['email']}&license_key={l['license_key']}&status=locked"
        unlock_link = f"/admin/set-license-status?token={token}&email={l['email']}&license_key={l['license_key']}&status=active"
        license_rows += f"""
        <tr>
            <td>{l['email']}</td>
            <td class="mono">{l['license_key']}</td>
            <td>{plan_name}</td>
            <td>{l['credit']}</td>
            <td>{l['status']}</td>
            <td>{l['expires_at']}</td>
            <td><a href="{lock_link}">Khóa</a> | <a href="{unlock_link}">Mở</a></td>
        </tr>
        """

    device_rows = ""
    for d in devices:
        lock_device = f"/admin/set-device-status?token={token}&license_key={d['license_key']}&device_id={d['device_id']}&status=locked"
        unlock_device = f"/admin/set-device-status?token={token}&license_key={d['license_key']}&device_id={d['device_id']}&status=active"
        device_rows += f"""
        <tr>
            <td class="mono">{d['license_key']}</td>
            <td class="mono">{d['device_id']}</td>
            <td>{d.get('device_name') or '-'}</td>
            <td>{d['status']}</td>
            <td>{d.get('ip') or '-'}</td>
            <td>{d['last_seen']}</td>
            <td><a href="{lock_device}">Khóa</a> | <a href="{unlock_device}">Mở</a></td>
        </tr>
        """

    auth_rows = ""
    for a in auth_logs:
        auth_rows += f"""
        <tr>
            <td>{a['created_at']}</td>
            <td>{a.get('email') or '-'}</td>
            <td class="mono">{a.get('device_id') or '-'}</td>
            <td>{'OK' if a['success'] else 'FAIL'}</td>
            <td>{a.get('reason') or '-'}</td>
            <td>{a.get('ip') or '-'}</td>
        </tr>
        """

    credit_rows = ""
    for c in credit_logs:
        credit_rows += f"""
        <tr>
            <td>{c['created_at']}</td>
            <td>{c.get('email') or '-'}</td>
            <td class="mono">{c.get('license_key') or '-'}</td>
            <td>{c.get('credit_before')}</td>
            <td>{c.get('credit_cost')}</td>
            <td>{c.get('credit_after')}</td>
        </tr>
        """

    html = f"""
<!doctype html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <title>Veo Admin Dashboard</title>
    <style>
        body {{ font-family: Arial, sans-serif; background:#f1f5f9; padding:24px; color:#0f172a; }}
        h1 {{ margin-top:0; }}
        .cards {{ display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin-bottom:20px; }}
        .card {{ background:white; border-radius:16px; padding:18px; box-shadow:0 8px 22px rgba(15,23,42,.06); }}
        .num {{ font-size:26px; font-weight:bold; color:#7c3aed; }}
        table {{ width:100%; border-collapse:collapse; background:white; border-radius:14px; overflow:hidden; margin-bottom:28px; }}
        th, td {{ padding:10px; border-bottom:1px solid #e2e8f0; text-align:left; font-size:13px; vertical-align: top; }}
        th {{ background:#e0f2fe; }}
        .mono {{ font-family:monospace; color:#16a34a; word-break: break-all; }}
        a {{ color:#2563eb; font-weight:bold; text-decoration:none; }}
    </style>
</head>
<body>
    <h1>🔐 Veo Admin Dashboard</h1>
    <p>DB: <b>{'PostgreSQL' if using_postgres() else 'SQLite local'}</b></p>

    <div class="cards">
        <div class="card"><div>Doanh thu paid</div><div class="num">{format_money(total_revenue)}</div></div>
        <div class="card"><div>Đơn paid</div><div class="num">{paid_count}</div></div>
        <div class="card"><div>Đơn pending</div><div class="num">{pending_count}</div></div>
        <div class="card"><div>License</div><div class="num">{len(licenses)}</div></div>
        <div class="card"><div>Thiết bị</div><div class="num">{len(devices)}</div></div>
    </div>

    <h2>Đơn hàng gần đây</h2>
    <table>
        <tr><th>Mã đơn</th><th>Email</th><th>Gói</th><th>Số tiền</th><th>Trạng thái</th><th>License</th><th>Thao tác</th></tr>
        {order_rows}
    </table>

    <h2>License gần đây</h2>
    <table>
        <tr><th>Email</th><th>License</th><th>Gói</th><th>Credit</th><th>Trạng thái</th><th>Hạn dùng</th><th>Thao tác</th></tr>
        {license_rows}
    </table>

    <h2>Thiết bị đã kích hoạt</h2>
    <table>
        <tr><th>License</th><th>Device ID</th><th>Tên máy</th><th>Trạng thái</th><th>IP</th><th>Last seen</th><th>Thao tác</th></tr>
        {device_rows}
    </table>

    <h2>Log đăng nhập</h2>
    <table>
        <tr><th>Thời gian</th><th>Email</th><th>Device</th><th>Kết quả</th><th>Lý do</th><th>IP</th></tr>
        {auth_rows}
    </table>

    <h2>Log trừ credit</h2>
    <table>
        <tr><th>Thời gian</th><th>Email</th><th>License</th><th>Trước</th><th>Trừ</th><th>Sau</th></tr>
        {credit_rows}
    </table>
</body>
</html>
"""
    return html


@app.get("/admin/confirm-order-ui")
def admin_confirm_order_ui(token: str, order_code: str):
    if not admin_ok(token):
        raise HTTPException(status_code=403, detail="Sai admin token")

    mark_order_paid_and_create_license(order_code)
    return RedirectResponse(url=f"/admin?token={token}")


@app.get("/admin/set-license-status")
def admin_set_license_status(token: str, email: str, license_key: str, status: str):
    if not admin_ok(token):
        raise HTTPException(status_code=403, detail="Sai admin token")

    if status not in ["active", "locked"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ")

    db_execute("""
    UPDATE licenses
    SET status = ?
    WHERE email = ? AND license_key = ?
    """, (status, email.strip().lower(), license_key.strip()))

    return RedirectResponse(url=f"/admin?token={token}")


@app.get("/admin/set-device-status")
def admin_set_device_status(token: str, license_key: str, device_id: str, status: str):
    if not admin_ok(token):
        raise HTTPException(status_code=403, detail="Sai admin token")

    if status not in ["active", "locked"]:
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ")

    db_execute("""
    UPDATE license_devices
    SET status = ?
    WHERE license_key = ? AND device_id = ?
    """, (status, license_key.strip(), device_id.strip()))

    return RedirectResponse(url=f"/admin?token={token}")


@app.get("/admin/orders")
def list_orders(x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    rows = db_execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True)
    return {"success": True, "orders": rows}


@app.post("/admin/confirm-order")
def confirm_order(data: ConfirmOrderRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    return mark_order_paid_and_create_license(data.order_code)


@app.get("/admin/pay/{order_code}")
def fake_pay_disabled(order_code: str):
    # Giai đoạn 1 bảo mật: khóa endpoint fake pay public.
    # Duyệt thủ công chỉ dùng /admin/confirm-order hoặc dashboard có token.
    raise HTTPException(status_code=404, detail="Endpoint disabled for security")


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


@app.post("/admin/add-credit")
def add_credit(data: AddCreditRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    row = db_execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()), fetchone=True)

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy license")

    new_credit = row["credit"] + data.credit

    db_execute("""
    UPDATE licenses
    SET credit = ?
    WHERE email = ? AND license_key = ?
    """, (new_credit, data.email.strip().lower(), data.license_key.strip()))

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

    row = db_execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()), fetchone=True)

    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy license")

    expires_at = datetime.now() + timedelta(days=plan["days"])

    db_execute("""
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

    db_execute("""
    UPDATE licenses
    SET status = 'locked'
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    return {"success": True, "message": "Đã khóa tài khoản"}


@app.post("/admin/unlock")
def unlock_license(data: LoginRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    db_execute("""
    UPDATE licenses
    SET status = 'active'
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()))

    return {"success": True, "message": "Đã mở khóa tài khoản"}


# =========================================================
# TOOL LOGIN + USAGE
# =========================================================

@app.post("/auth/login")
def login(data: LoginRequest, request: Request):
    device_id = make_device_id(data.device_id, request)

    row = db_execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()), fetchone=True)

    if not row:
        log_auth(data.email.strip().lower(), data.license_key.strip(), device_id, request, False, "wrong_email_or_license")
        raise HTTPException(status_code=401, detail="Email hoặc license key không đúng")

    if row["status"] != "active":
        log_auth(row["email"], row["license_key"], device_id, request, False, "license_locked")
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        log_auth(row["email"], row["license_key"], device_id, request, False, "expired")
        raise HTTPException(status_code=403, detail="Gói cước đã hết hạn")

    try:
        device_id = bind_or_check_device(row, data, request)
    except HTTPException as e:
        log_auth(row["email"], row["license_key"], device_id, request, False, str(e.detail))
        raise

    plan_info = PLAN_CONFIG.get(row["plan"], {})
    log_auth(row["email"], row["license_key"], device_id, request, True, "ok")

    return {
        "success": True,
        "email": row["email"],
        "plan": row["plan"],
        "plan_name": plan_info.get("name", row["plan"]),
        "credit": row["credit"],
        "status": row["status"],
        "expires_at": row["expires_at"],
        "concurrent": plan_info.get("concurrent", 1),
        "prompt_limit": plan_info.get("prompt_limit", 20),
        "device_id": device_id,
        "device_limit": plan_info.get("device_limit", 1)
    }


@app.get("/login")
def login_get(email: str, license_key: str, request: Request, device_id: Optional[str] = None):
    req = LoginRequest(email=email, license_key=license_key, device_id=device_id, device_name="Browser test")
    return login(req, request)


@app.post("/usage/check")
def check_usage(data: ConsumeCreditRequest, request: Request):
    row = db_execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()), fetchone=True)

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
def consume_credit(data: ConsumeCreditRequest, request: Request):
    row = db_execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email.strip().lower(), data.license_key.strip()), fetchone=True)

    if not row:
        raise HTTPException(status_code=401, detail="License không hợp lệ")

    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="Tài khoản đã bị khóa")

    if datetime.fromisoformat(row["expires_at"]) < datetime.now():
        raise HTTPException(status_code=403, detail="Gói cước đã hết hạn")

    if row["credit"] < data.credit_cost:
        raise HTTPException(status_code=402, detail="Không đủ credit")

    new_credit = row["credit"] - data.credit_cost

    db_execute("""
    UPDATE licenses
    SET credit = ?
    WHERE email = ? AND license_key = ?
    """, (new_credit, data.email.strip().lower(), data.license_key.strip()))

    log_credit(row["email"], row["license_key"], row["credit"], data.credit_cost, new_credit, request)

    return {
        "success": True,
        "credit_before": row["credit"],
        "credit_cost": data.credit_cost,
        "credit_after": new_credit
    }


# ==============================
# RUN SERVER (LOCAL)
# ==============================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
