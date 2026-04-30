import sqlite3
import secrets
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

DB_NAME = "veo_server.db"
ADMIN_TOKEN = "NAMQUIT_ADMIN_123"

app = FastAPI(title="Veo Tool API Server")


PLAN_CONFIG = {
    "free": {
        "name": "Free",
        "credit": 5,
        "days": 7,
        "price": "0 VNĐ",
        "concurrent": 1,
        "prompt_limit": 20
    },
    "starter": {
        "name": "Starter",
        "credit": 50,
        "days": 30,
        "price": "49,000 VNĐ",
        "concurrent": 1,
        "prompt_limit": 50
    },
    "basic": {
        "name": "Basic",
        "credit": 200,
        "days": 30,
        "price": "149,000 VNĐ",
        "concurrent": 3,
        "prompt_limit": 150
    },
    "pro": {
        "name": "Pro",
        "credit": 800,
        "days": 30,
        "price": "399,000 VNĐ",
        "concurrent": 9,
        "prompt_limit": 300
    }
}


def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


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


@app.get("/")
def home():
    return {
        "message": "Veo Tool API Server is running",
        "version": "credit-model-v1"
    }


@app.get("/plans")
def plans():
    return {
        "plans": PLAN_CONFIG
    }


@app.post("/admin/create-license")
def create_license(data: CreateLicenseRequest, x_admin_token: str = Header(default="")):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Sai admin token")

    if data.plan not in PLAN_CONFIG:
        raise HTTPException(status_code=400, detail="Gói cước không hợp lệ")

    plan = PLAN_CONFIG[data.plan]

    license_key = "VEO-" + secrets.token_hex(8).upper()
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
        data.email,
        license_key,
        data.plan,
        plan["credit"],
        "active",
        now.isoformat(),
        expires_at.isoformat()
    ))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "email": data.email,
        "license_key": license_key,
        "plan": data.plan,
        "plan_name": plan["name"],
        "credit": plan["credit"],
        "days": plan["days"],
        "price": plan["price"],
        "expires_at": expires_at.isoformat()
    }


@app.post("/auth/login")
def login(data: LoginRequest):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT * FROM licenses
    WHERE email = ? AND license_key = ?
    """, (data.email, data.license_key))

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
    """, (data.email, data.license_key))

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
    """, (data.email, data.license_key))

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
    """, (new_credit, data.email, data.license_key))

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
    """, (data.email, data.license_key))

    row = cur.fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Không tìm thấy license")

    new_credit = row["credit"] + data.credit

    cur.execute("""
    UPDATE licenses
    SET credit = ?
    WHERE email = ? AND license_key = ?
    """, (new_credit, data.email, data.license_key))

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
    """, (data.email, data.license_key))

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
        data.email,
        data.license_key
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
    """, (data.email, data.license_key))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": "Đã khóa tài khoản"
    }


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
    """, (data.email, data.license_key))

    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": "Đã mở khóa tài khoản"
    }
