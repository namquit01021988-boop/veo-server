"""
VEO TOOL SAAS - PHASE 2 PRODUCTION BACKEND
Stack:
- FastAPI
- PostgreSQL / Render
- PayOS QR
- License + Credit + Device HWID
- Admin dashboard
- Rate limit
- Email Resend / SendGrid
- Tool client auth session

RUN LOCAL:
    pip install -r requirements.txt
    python main.py

ENV REQUIRED:
    DATABASE_URL=postgresql://...
    APP_BASE_URL=https://your-domain.onrender.com
    ADMIN_TOKEN=your_admin_token

    PAYOS_CLIENT_ID=...
    PAYOS_API_KEY=...
    PAYOS_CHECKSUM_KEY=...

OPTIONAL:
    RESEND_API_KEY=...
    SENDGRID_API_KEY=...
    EMAIL_FROM=Veo Tool <noreply@yourdomain.com>
    SECRET_KEY=change_this_long_random_secret
"""

from __future__ import annotations

import os
import hmac
import json
import time
import uuid
import base64
import hashlib
import secrets
import datetime as dt
from decimal import Decimal
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI, Request, HTTPException, Depends, Header, Query, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, Text,
    Numeric, ForeignKey, UniqueConstraint, Index, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship


# =========================================================
# CONFIG
# =========================================================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./veo_tool_phase2.db")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "CHANGE_ME_ADMIN_TOKEN")
SECRET_KEY = os.getenv("SECRET_KEY", "CHANGE_ME_SECRET_KEY_LONG_RANDOM")

PAYOS_CLIENT_ID = os.getenv("PAYOS_CLIENT_ID", "")
PAYOS_API_KEY = os.getenv("PAYOS_API_KEY", "")
PAYOS_CHECKSUM_KEY = os.getenv("PAYOS_CHECKSUM_KEY", "")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "Veo Tool <noreply@example.com>")

# Production-safe defaults
MAX_DEVICES_DEFAULT = int(os.getenv("MAX_DEVICES_DEFAULT", "1"))
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "8"))          # per 10 min / IP
CONSUME_RATE_LIMIT = int(os.getenv("CONSUME_RATE_LIMIT", "60"))     # per 10 min / license
WEBHOOK_TIME_WINDOW_SECONDS = int(os.getenv("WEBHOOK_TIME_WINDOW_SECONDS", "900"))
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "24"))

PLANS = {
    "basic": {
        "name": "Basic",
        "price": 199000,
        "credits": 120,
        "days": 30,
        "max_devices": 1,
    },
    "pro": {
        "name": "Pro",
        "price": 399000,
        "credits": 300,
        "days": 30,
        "max_devices": 1,
    },
    "premium": {
        "name": "Premium",
        "price": 799000,
        "credits": 700,
        "days": 30,
        "max_devices": 2,
    },
    "month_pro": {
        "name": "Monthly Pro",
        "price": 499000,
        "credits": 500,
        "days": 30,
        "max_devices": 2,
        "subscription": True,
    },
}

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# =========================================================
# DATABASE MODELS
# =========================================================

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True)
    order_code = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(255), index=True, nullable=False)
    plan = Column(String(64), nullable=False)
    amount = Column(Numeric(12, 0), nullable=False)
    status = Column(String(32), default="pending", index=True)  # pending/paid/cancelled/expired
    payos_payment_link_id = Column(String(255), nullable=True)
    payos_checkout_url = Column(Text, nullable=True)
    payos_qr_code = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    license = relationship("License", back_populates="order", uselist=False)


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), index=True, nullable=False)
    license_key_hash = Column(String(128), unique=True, index=True, nullable=False)
    license_key_hint = Column(String(32), nullable=False)
    plan = Column(String(64), nullable=False)
    credits = Column(Integer, default=0)
    total_credits = Column(Integer, default=0)
    max_devices = Column(Integer, default=1)
    expires_at = Column(DateTime, index=True, nullable=False)
    blocked = Column(Boolean, default=False, index=True)
    block_reason = Column(Text, nullable=True)
    subscription_status = Column(String(32), default="none")  # none/active/past_due/cancelled
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    updated_at = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

    order = relationship("Order", back_populates="license")
    devices = relationship("LicenseDevice", back_populates="license")
    sessions = relationship("ClientSession", back_populates="license")


class LicenseDevice(Base):
    __tablename__ = "license_devices"

    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), index=True, nullable=False)
    hwid_hash = Column(String(128), index=True, nullable=False)
    device_name = Column(String(255), nullable=True)
    first_ip = Column(String(64), nullable=True)
    last_ip = Column(String(64), nullable=True)
    first_seen = Column(DateTime, default=dt.datetime.utcnow)
    last_seen = Column(DateTime, default=dt.datetime.utcnow)
    trusted = Column(Boolean, default=True)
    revoked = Column(Boolean, default=False)

    license = relationship("License", back_populates="devices")
    __table_args__ = (
        UniqueConstraint("license_id", "hwid_hash", name="uq_license_hwid"),
    )


class AuthLog(Base):
    __tablename__ = "auth_logs"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), index=True, nullable=True)
    license_id = Column(Integer, nullable=True, index=True)
    hwid_hash = Column(String(128), nullable=True)
    ip = Column(String(64), nullable=True, index=True)
    user_agent = Column(Text, nullable=True)
    status = Column(String(32), index=True)  # success/fail/blocked/rate_limited
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class CreditLog(Base):
    __tablename__ = "credit_logs"

    id = Column(Integer, primary_key=True)
    license_id = Column(Integer, index=True, nullable=False)
    action = Column(String(32), index=True)  # consume/refund/add
    amount = Column(Integer, nullable=False)
    before_credit = Column(Integer, nullable=False)
    after_credit = Column(Integer, nullable=False)
    reason = Column(Text, nullable=True)
    request_id = Column(String(128), nullable=True, index=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class RateLimitLog(Base):
    __tablename__ = "rate_limit_logs"

    id = Column(Integer, primary_key=True)
    bucket = Column(String(255), index=True, nullable=False)
    ip = Column(String(64), index=True, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class WebhookLog(Base):
    __tablename__ = "webhook_logs"

    id = Column(Integer, primary_key=True)
    provider = Column(String(64), default="payos")
    event_id = Column(String(255), nullable=True, index=True)
    order_code = Column(String(64), nullable=True, index=True)
    valid_signature = Column(Boolean, default=False)
    processed = Column(Boolean, default=False)
    status = Column(String(32), default="received")
    payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow, index=True)


class ClientSession(Base):
    __tablename__ = "client_sessions"

    id = Column(Integer, primary_key=True)
    session_hash = Column(String(128), unique=True, index=True, nullable=False)
    license_id = Column(Integer, ForeignKey("licenses.id"), index=True, nullable=False)
    hwid_hash = Column(String(128), index=True, nullable=False)
    ip = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)
    expires_at = Column(DateTime, index=True, nullable=False)
    revoked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    last_seen = Column(DateTime, default=dt.datetime.utcnow)

    license = relationship("License", back_populates="sessions")


Index("idx_credit_license_created", CreditLog.license_id, CreditLog.created_at)
Index("idx_auth_ip_created", AuthLog.ip, AuthLog.created_at)


# =========================================================
# APP
# =========================================================

app = FastAPI(title="Veo Tool SaaS Phase 2", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# UTILS
# =========================================================

def db_init() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def now_utc() -> dt.datetime:
    return dt.datetime.utcnow()


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_license_key(key: str) -> str:
    return sha256_text("license:" + key.strip())


def hash_hwid(hwid: str) -> str:
    cleaned = (hwid or "").strip().lower()
    return sha256_text("hwid:" + cleaned)


def hash_session(token: str) -> str:
    return sha256_text("session:" + token)


def generate_license_key(plan: str) -> str:
    prefix = "VEO"
    body = secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:28].upper()
    return f"{prefix}-{plan.upper()}-{body[:7]}-{body[7:14]}-{body[14:21]}-{body[21:28]}"


def generate_order_code() -> str:
    # PayOS orderCode usually numeric. Keep unique and short enough.
    return str(int(time.time() * 1000))[-10:] + str(secrets.randbelow(9000) + 1000)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


def money_int(value: Any) -> int:
    return int(Decimal(str(value)))


def require_admin(x_admin_token: Optional[str] = Header(None)):
    if not ADMIN_TOKEN or ADMIN_TOKEN == "CHANGE_ME_ADMIN_TOKEN":
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN is not configured")
    if not x_admin_token or not constant_time_equal(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return True


def rate_limit_or_429(
    db: Session,
    bucket: str,
    ip: Optional[str],
    limit: int,
    window_seconds: int = 600,
):
    cutoff = now_utc() - dt.timedelta(seconds=window_seconds)
    count = db.query(RateLimitLog).filter(
        RateLimitLog.bucket == bucket,
        RateLimitLog.created_at >= cutoff
    ).count()

    if count >= limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please try again later.")

    db.add(RateLimitLog(bucket=bucket, ip=ip))
    db.commit()


def sign_payload_for_client(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()
    token = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return token + "." + sig


def verify_client_token(token: str) -> Dict[str, Any]:
    try:
        data_part, sig = token.split(".", 1)
        padding = "=" * (-len(data_part) % 4)
        raw = base64.urlsafe_b64decode(data_part + padding)
        expected = hmac.new(SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()
        if not constant_time_equal(sig, expected):
            raise ValueError("bad signature")
        return json.loads(raw.decode())
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token")


def verify_session(
    request: Request,
    db: Session,
    authorization: Optional[str],
    hwid: Optional[str],
) -> License:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer session token")

    token = authorization.split(" ", 1)[1].strip()
    payload = verify_client_token(token)
    session_plain_id = payload.get("sid")
    license_id = payload.get("license_id")
    exp = payload.get("exp")
    hwid_hash_from_token = payload.get("hwid_hash")

    if not session_plain_id or not license_id or not exp:
        raise HTTPException(status_code=401, detail="Invalid session payload")

    if int(exp) < int(time.time()):
        raise HTTPException(status_code=401, detail="Session expired")

    if hwid:
        req_hwid_hash = hash_hwid(hwid)
        if not constant_time_equal(req_hwid_hash, hwid_hash_from_token):
            raise HTTPException(status_code=401, detail="Device mismatch")

    session_hash = hash_session(session_plain_id)
    sess = db.query(ClientSession).filter(ClientSession.session_hash == session_hash).first()
    if not sess or sess.revoked:
        raise HTTPException(status_code=401, detail="Session revoked or not found")

    license_obj = db.query(License).filter(License.id == license_id).first()
    if not license_obj:
        raise HTTPException(status_code=401, detail="License not found")

    if license_obj.blocked:
        raise HTTPException(status_code=403, detail="License blocked")

    if license_obj.expires_at < now_utc():
        raise HTTPException(status_code=403, detail="License expired")

    if not constant_time_equal(sess.hwid_hash, hwid_hash_from_token):
        raise HTTPException(status_code=401, detail="Session device mismatch")

    sess.last_seen = now_utc()
    sess.ip = client_ip(request)
    db.commit()
    return license_obj


# =========================================================
# PAYOS
# =========================================================

def payos_signature_from_data(data: Dict[str, Any]) -> str:
    """
    PayOS checksum usually signs sorted data as key=value&key=value.
    Keep this function isolated so you can adjust if PayOS changes payload shape.
    """
    if not PAYOS_CHECKSUM_KEY:
        return ""

    clean = {}
    for k, v in data.items():
        if k in ("signature", "desc"):
            continue
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            clean[k] = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
        else:
            clean[k] = str(v)

    raw = "&".join(f"{k}={clean[k]}" for k in sorted(clean.keys()))
    return hmac.new(PAYOS_CHECKSUM_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()


def verify_payos_webhook(payload: Dict[str, Any]) -> bool:
    """
    Supports common PayOS format:
    {
      "code": "00",
      "desc": "...",
      "success": true,
      "data": {...},
      "signature": "..."
    }
    """
    signature = payload.get("signature", "")
    data = payload.get("data", payload)

    if not isinstance(data, dict):
        return False

    expected = payos_signature_from_data(data)
    return bool(signature and expected and constant_time_equal(signature, expected))


def create_payos_payment(order: Order) -> Dict[str, Any]:
    """
    Create PayOS payment link.
    If PayOS env is missing, this returns a local checkout placeholder.
    """
    if not (PAYOS_CLIENT_ID and PAYOS_API_KEY and PAYOS_CHECKSUM_KEY):
        checkout_url = f"{APP_BASE_URL}/order/{order.order_code}"
        return {
            "checkoutUrl": checkout_url,
            "qrCode": "",
            "paymentLinkId": "LOCAL_DEV",
        }

    payload = {
        "orderCode": int(order.order_code),
        "amount": money_int(order.amount),
        "description": f"VEO {order.plan}",
        "buyerName": order.email.split("@")[0],
        "buyerEmail": order.email,
        "returnUrl": f"{APP_BASE_URL}/payment/success?order_code={order.order_code}",
        "cancelUrl": f"{APP_BASE_URL}/payment/cancel?order_code={order.order_code}",
    }
    payload["signature"] = payos_signature_from_data(payload)

    url = "https://api-merchant.payos.vn/v2/payment-requests"
    headers = {
        "x-client-id": PAYOS_CLIENT_ID,
        "x-api-key": PAYOS_API_KEY,
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"PayOS error: {r.text}")

    res = r.json()
    data = res.get("data", {})
    return {
        "checkoutUrl": data.get("checkoutUrl"),
        "qrCode": data.get("qrCode"),
        "paymentLinkId": data.get("paymentLinkId"),
        "raw": res,
    }


# =========================================================
# EMAIL
# =========================================================

def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    if RESEND_API_KEY:
        try:
            r = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": EMAIL_FROM,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                    "text": text or subject,
                },
                timeout=15,
            )
            return r.status_code < 400
        except Exception:
            return False

    if SENDGRID_API_KEY:
        try:
            r = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": to}]}],
                    "from": {"email": EMAIL_FROM.split("<")[-1].replace(">", "").strip()},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": html}],
                },
                timeout=15,
            )
            return r.status_code < 400
        except Exception:
            return False

    print("EMAIL_DISABLED:", to, subject)
    print(html)
    return False


def send_license_email(email: str, license_key: str, plan: str, credits: int, expires_at: dt.datetime):
    html = f"""
    <h2>Veo Tool License</h2>
    <p>Cảm ơn bạn đã thanh toán thành công.</p>
    <p><b>Email:</b> {email}</p>
    <p><b>Plan:</b> {plan}</p>
    <p><b>Credit:</b> {credits}</p>
    <p><b>Hết hạn:</b> {expires_at.strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    <p><b>License key:</b></p>
    <pre style="font-size:18px;padding:12px;background:#f2f2f2">{license_key}</pre>
    <p>Không chia sẻ key này cho người khác. Mỗi license bị giới hạn số thiết bị.</p>
    """
    send_email(email, "Veo Tool - License key của bạn", html)


# =========================================================
# BUSINESS LOGIC
# =========================================================

def create_license_for_paid_order(db: Session, order: Order) -> License:
    existing = db.query(License).filter(License.order_id == order.id).first()
    if existing:
        return existing

    plan_conf = PLANS.get(order.plan)
    if not plan_conf:
        raise HTTPException(status_code=400, detail="Invalid plan")

    plain_key = generate_license_key(order.plan)
    key_hash = hash_license_key(plain_key)
    expires_at = now_utc() + dt.timedelta(days=int(plan_conf["days"]))
    subscription_status = "active" if plan_conf.get("subscription") else "none"

    lic = License(
        email=order.email,
        license_key_hash=key_hash,
        license_key_hint=plain_key[-8:],
        plan=order.plan,
        credits=int(plan_conf["credits"]),
        total_credits=int(plan_conf["credits"]),
        max_devices=int(plan_conf.get("max_devices", MAX_DEVICES_DEFAULT)),
        expires_at=expires_at,
        subscription_status=subscription_status,
        order_id=order.id,
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)

    send_license_email(order.email, plain_key, order.plan, lic.credits, expires_at)

    # Keep plain key only in order raw payload email copy for first response? Do not store plain key in production.
    order.raw_payload = json.dumps({
        "license_created": True,
        "license_key_hint": lic.license_key_hint,
        "email_sent": True,
    }, ensure_ascii=False)
    db.commit()
    return lic


def mark_order_paid(db: Session, order_code: str, payload: Optional[Dict[str, Any]] = None) -> Order:
    order = db.query(Order).filter(Order.order_code == str(order_code)).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != "paid":
        order.status = "paid"
        order.paid_at = now_utc()
        if payload:
            order.raw_payload = json.dumps(payload, ensure_ascii=False)
        db.commit()
        db.refresh(order)

    create_license_for_paid_order(db, order)
    return order


# =========================================================
# SCHEMAS
# =========================================================

class CreateOrderIn(BaseModel):
    email: EmailStr
    plan: str


class LoginIn(BaseModel):
    email: EmailStr
    license_key: str
    hwid: str = Field(..., min_length=8)
    device_name: Optional[str] = "Windows PC"


class UsageConsumeIn(BaseModel):
    credits: int = Field(..., ge=1, le=100)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    reason: Optional[str] = "video_generation"
    hwid: Optional[str] = None


class UsageCheckIn(BaseModel):
    hwid: Optional[str] = None


class AdminCreditIn(BaseModel):
    license_id: int
    amount: int
    reason: Optional[str] = "admin_adjust"


class AdminBlockIn(BaseModel):
    license_id: int
    blocked: bool
    reason: Optional[str] = None


class AdminDeviceRevokeIn(BaseModel):
    device_id: int
    revoked: bool = True


# =========================================================
# PUBLIC ROUTES
# =========================================================

@app.on_event("startup")
def on_startup():
    db_init()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <h2>Veo Tool SaaS Backend Phase 2</h2>
    <p>Status: OK</p>
    <p><a href="/shop">Open Shop</a></p>
    """


@app.get("/health")
def health():
    return {"ok": True, "version": "2.0.0", "time": now_utc().isoformat()}


@app.get("/shop", response_class=HTMLResponse)
def shop():
    cards = ""
    for key, p in PLANS.items():
        cards += f"""
        <div style="border:1px solid #ddd;border-radius:14px;padding:20px;margin:12px;width:260px">
            <h3>{p['name']}</h3>
            <p><b>{p['price']:,} VND</b></p>
            <p>{p['credits']} credits / {p['days']} ngày</p>
            <p>{p.get('max_devices', 1)} thiết bị</p>
            <form method="post" action="/shop/create">
                <input name="email" placeholder="Email của bạn" required style="padding:10px;width:95%">
                <input type="hidden" name="plan" value="{key}">
                <button style="margin-top:12px;padding:10px 16px">Thanh toán</button>
            </form>
        </div>
        """
    return f"""
    <html><head><title>Veo Tool Shop</title></head>
    <body style="font-family:Arial;max-width:1100px;margin:40px auto">
        <h1>Veo Tool Shop</h1>
        <div style="display:flex;flex-wrap:wrap">{cards}</div>
    </body></html>
    """


@app.post("/shop/create")
def shop_create_form(
    email: EmailStr = Form(...),
    plan: str = Form(...),
    db: Session = Depends(get_db),
):
    data = CreateOrderIn(email=email, plan=plan)
    res = create_order(data, db)
    return RedirectResponse(res["checkout_url"], status_code=303)


@app.post("/orders/create")
def create_order(data: CreateOrderIn, db: Session = Depends(get_db)):
    if data.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    plan_conf = PLANS[data.plan]
    order = Order(
        order_code=generate_order_code(),
        email=str(data.email).lower(),
        plan=data.plan,
        amount=int(plan_conf["price"]),
        status="pending",
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    pay = create_payos_payment(order)
    order.payos_checkout_url = pay.get("checkoutUrl")
    order.payos_qr_code = pay.get("qrCode")
    order.payos_payment_link_id = pay.get("paymentLinkId")
    order.raw_payload = json.dumps(pay.get("raw", pay), ensure_ascii=False)
    db.commit()

    return {
        "order_code": order.order_code,
        "email": order.email,
        "plan": order.plan,
        "amount": money_int(order.amount),
        "checkout_url": order.payos_checkout_url,
        "qr_code": order.payos_qr_code,
    }


@app.get("/order/{order_code}", response_class=HTMLResponse)
def order_page(order_code: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_code == order_code).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    qr = ""
    if order.payos_qr_code:
        qr = f'<img src="{order.payos_qr_code}" style="max-width:300px">'
    return f"""
    <html><body style="font-family:Arial;max-width:720px;margin:40px auto">
        <h2>Đơn hàng #{order.order_code}</h2>
        <p>Email: <b>{order.email}</b></p>
        <p>Gói: <b>{order.plan}</b></p>
        <p>Số tiền: <b>{money_int(order.amount):,} VND</b></p>
        <p>Trạng thái: <b>{order.status}</b></p>
        <p><a href="{order.payos_checkout_url or '#'}">Mở trang thanh toán PayOS</a></p>
        {qr}
    </body></html>
    """


@app.get("/payment/success", response_class=HTMLResponse)
def payment_success(order_code: str):
    return f"""
    <h2>Thanh toán thành công</h2>
    <p>Đơn hàng: {order_code}</p>
    <p>Nếu thanh toán đã xác nhận, license sẽ được gửi về email.</p>
    """


@app.get("/payment/cancel", response_class=HTMLResponse)
def payment_cancel(order_code: str):
    return f"<h2>Thanh toán bị hủy</h2><p>Đơn hàng: {order_code}</p>"


@app.post("/payos/webhook")
async def payos_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    valid = verify_payos_webhook(payload)

    data = payload.get("data", payload)
    order_code = str(data.get("orderCode") or data.get("order_code") or "")
    event_id = str(data.get("paymentLinkId") or data.get("reference") or order_code or uuid.uuid4())

    log = WebhookLog(
        provider="payos",
        event_id=event_id,
        order_code=order_code,
        valid_signature=valid,
        payload=json.dumps(payload, ensure_ascii=False),
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    if not valid:
        log.status = "invalid_signature"
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid PayOS signature")

    success = payload.get("success")
    code = payload.get("code")
    desc = str(payload.get("desc", "")).lower()
    amount = data.get("amount")
    status_ok = success is True or code == "00" or "success" in desc or "paid" in desc

    if not order_code:
        log.status = "missing_order_code"
        db.commit()
        raise HTTPException(status_code=400, detail="Missing orderCode")

    order = db.query(Order).filter(Order.order_code == order_code).first()
    if not order:
        log.status = "order_not_found"
        db.commit()
        raise HTTPException(status_code=404, detail="Order not found")

    if amount is not None and money_int(amount) != money_int(order.amount):
        log.status = "amount_mismatch"
        db.commit()
        raise HTTPException(status_code=400, detail="Amount mismatch")

    if status_ok:
        mark_order_paid(db, order_code, payload)
        log.processed = True
        log.status = "processed_paid"
        db.commit()
        return {"ok": True, "message": "paid processed"}

    log.status = "ignored_not_paid"
    db.commit()
    return {"ok": True, "message": "ignored"}


# Backward compatibility route, disabled by default.
@app.get("/admin/pay")
def fake_pay_disabled():
    raise HTTPException(status_code=403, detail="Fake payment route disabled in production")


# =========================================================
# TOOL CLIENT ROUTES
# =========================================================

@app.post("/license/login")
def license_login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")

    try:
        rate_limit_or_429(db, f"login:{ip}", ip, LOGIN_RATE_LIMIT, 600)
    except HTTPException:
        db.add(AuthLog(email=str(data.email), ip=ip, user_agent=ua, status="rate_limited", reason="too many login attempts"))
        db.commit()
        raise

    key_hash = hash_license_key(data.license_key)
    lic = db.query(License).filter(
        License.email == str(data.email).lower(),
        License.license_key_hash == key_hash,
    ).first()

    hwid_hash = hash_hwid(data.hwid)

    if not lic:
        db.add(AuthLog(email=str(data.email), hwid_hash=hwid_hash, ip=ip, user_agent=ua, status="fail", reason="invalid email or key"))
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or license key")

    if lic.blocked:
        db.add(AuthLog(email=lic.email, license_id=lic.id, hwid_hash=hwid_hash, ip=ip, user_agent=ua, status="blocked", reason=lic.block_reason))
        db.commit()
        raise HTTPException(status_code=403, detail="License blocked")

    if lic.expires_at < now_utc():
        db.add(AuthLog(email=lic.email, license_id=lic.id, hwid_hash=hwid_hash, ip=ip, user_agent=ua, status="fail", reason="expired"))
        db.commit()
        raise HTTPException(status_code=403, detail="License expired")

    device = db.query(LicenseDevice).filter(
        LicenseDevice.license_id == lic.id,
        LicenseDevice.hwid_hash == hwid_hash,
    ).first()

    if device and device.revoked:
        db.add(AuthLog(email=lic.email, license_id=lic.id, hwid_hash=hwid_hash, ip=ip, user_agent=ua, status="blocked", reason="device revoked"))
        db.commit()
        raise HTTPException(status_code=403, detail="This device has been revoked")

    active_device_count = db.query(LicenseDevice).filter(
        LicenseDevice.license_id == lic.id,
        LicenseDevice.revoked == False,
    ).count()

    if not device:
        if active_device_count >= lic.max_devices:
            db.add(AuthLog(email=lic.email, license_id=lic.id, hwid_hash=hwid_hash, ip=ip, user_agent=ua, status="blocked", reason="device limit exceeded"))
            db.commit()
            raise HTTPException(status_code=403, detail="Device limit exceeded")

        device = LicenseDevice(
            license_id=lic.id,
            hwid_hash=hwid_hash,
            device_name=data.device_name,
            first_ip=ip,
            last_ip=ip,
        )
        db.add(device)
    else:
        device.last_ip = ip
        device.last_seen = now_utc()
        device.device_name = data.device_name or device.device_name

    session_plain_id = secrets.token_urlsafe(32)
    expires_ts = int(time.time() + SESSION_TTL_HOURS * 3600)
    sess = ClientSession(
        session_hash=hash_session(session_plain_id),
        license_id=lic.id,
        hwid_hash=hwid_hash,
        ip=ip,
        user_agent=ua,
        expires_at=dt.datetime.utcfromtimestamp(expires_ts),
    )
    db.add(sess)

    token_payload = {
        "sid": session_plain_id,
        "license_id": lic.id,
        "email": lic.email,
        "hwid_hash": hwid_hash,
        "exp": expires_ts,
    }
    token = sign_payload_for_client(token_payload)

    db.add(AuthLog(email=lic.email, license_id=lic.id, hwid_hash=hwid_hash, ip=ip, user_agent=ua, status="success", reason="login ok"))
    db.commit()

    return {
        "ok": True,
        "session_token": token,
        "session_expires_at": sess.expires_at.isoformat(),
        "license": {
            "email": lic.email,
            "plan": lic.plan,
            "credits": lic.credits,
            "expires_at": lic.expires_at.isoformat(),
            "max_devices": lic.max_devices,
            "device_count": active_device_count if device.id else active_device_count + 1,
            "blocked": lic.blocked,
        },
        "security": {
            "hwid_bound": True,
            "anti_share": True,
            "rate_limit": True,
        },
    }


@app.post("/usage/check")
def usage_check(
    data: UsageCheckIn,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    lic = verify_session(request, db, authorization, data.hwid)
    return {
        "ok": True,
        "email": lic.email,
        "plan": lic.plan,
        "credits": lic.credits,
        "expires_at": lic.expires_at.isoformat(),
        "blocked": lic.blocked,
        "subscription_status": lic.subscription_status,
    }


@app.post("/usage/consume")
def usage_consume(
    data: UsageConsumeIn,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    lic = verify_session(request, db, authorization, data.hwid)
    ip = client_ip(request)

    rate_limit_or_429(db, f"consume:license:{lic.id}", ip, CONSUME_RATE_LIMIT, 600)

    # Idempotency: same request_id should not double-charge.
    existing_log = db.query(CreditLog).filter(
        CreditLog.license_id == lic.id,
        CreditLog.request_id == data.request_id,
        CreditLog.action == "consume",
    ).first()
    if existing_log:
        return {
            "ok": True,
            "idempotent": True,
            "credits": existing_log.after_credit,
            "request_id": data.request_id,
        }

    if lic.credits < data.credits:
        raise HTTPException(status_code=402, detail="Not enough credits")

    before = lic.credits
    after = before - data.credits
    lic.credits = after
    lic.updated_at = now_utc()

    log = CreditLog(
        license_id=lic.id,
        action="consume",
        amount=data.credits,
        before_credit=before,
        after_credit=after,
        reason=data.reason,
        request_id=data.request_id,
        ip=ip,
    )
    db.add(log)
    db.commit()

    return {
        "ok": True,
        "credits": after,
        "used": data.credits,
        "request_id": data.request_id,
    }


@app.post("/license/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ", 1)[1].strip()
    payload = verify_client_token(token)
    sid = payload.get("sid")
    sess = db.query(ClientSession).filter(ClientSession.session_hash == hash_session(sid)).first()
    if sess:
        sess.revoked = True
        db.commit()
    return {"ok": True}


@app.post("/license/heartbeat")
def heartbeat(
    data: UsageCheckIn,
    request: Request,
    db: Session = Depends(get_db),
    authorization: Optional[str] = Header(None),
):
    lic = verify_session(request, db, authorization, data.hwid)
    return {
        "ok": True,
        "credits": lic.credits,
        "expires_at": lic.expires_at.isoformat(),
        "server_time": now_utc().isoformat(),
    }


# =========================================================
# USER DASHBOARD
# =========================================================

@app.get("/user", response_class=HTMLResponse)
def user_dashboard_login():
    return """
    <html><body style="font-family:Arial;max-width:600px;margin:50px auto">
    <h2>Veo Tool User Dashboard</h2>
    <form method="get" action="/user/dashboard">
        <input name="email" placeholder="Email" style="padding:10px;width:95%"><br><br>
        <input name="license_key" placeholder="License key" style="padding:10px;width:95%"><br><br>
        <button style="padding:10px 18px">Xem tài khoản</button>
    </form>
    </body></html>
    """


@app.get("/user/dashboard", response_class=HTMLResponse)
def user_dashboard(email: EmailStr, license_key: str, db: Session = Depends(get_db)):
    lic = db.query(License).filter(
        License.email == str(email).lower(),
        License.license_key_hash == hash_license_key(license_key),
    ).first()
    if not lic:
        return HTMLResponse("<h3>Sai email hoặc license key</h3>", status_code=401)

    devices = db.query(LicenseDevice).filter(LicenseDevice.license_id == lic.id).all()
    logs = db.query(CreditLog).filter(CreditLog.license_id == lic.id).order_by(CreditLog.created_at.desc()).limit(20).all()

    dev_rows = "".join(
        f"<tr><td>{d.device_name or ''}</td><td>{d.last_ip or ''}</td><td>{d.last_seen}</td><td>{'revoked' if d.revoked else 'active'}</td></tr>"
        for d in devices
    )
    log_rows = "".join(
        f"<tr><td>{l.created_at}</td><td>{l.action}</td><td>{l.amount}</td><td>{l.after_credit}</td><td>{l.reason or ''}</td></tr>"
        for l in logs
    )
    return f"""
    <html><body style="font-family:Arial;max-width:1000px;margin:40px auto">
        <h2>Tài khoản Veo Tool</h2>
        <p>Email: <b>{lic.email}</b></p>
        <p>Gói: <b>{lic.plan}</b></p>
        <p>Credit còn lại: <b>{lic.credits}</b> / {lic.total_credits}</p>
        <p>Hết hạn: <b>{lic.expires_at}</b></p>
        <p>Trạng thái: <b>{"BLOCKED" if lic.blocked else "ACTIVE"}</b></p>
        <h3>Thiết bị</h3>
        <table border="1" cellpadding="8" cellspacing="0">
            <tr><th>Device</th><th>IP cuối</th><th>Lần cuối</th><th>Status</th></tr>
            {dev_rows}
        </table>
        <h3>Lịch sử credit</h3>
        <table border="1" cellpadding="8" cellspacing="0">
            <tr><th>Time</th><th>Action</th><th>Amount</th><th>After</th><th>Reason</th></tr>
            {log_rows}
        </table>
    </body></html>
    """


# =========================================================
# ADMIN ROUTES
# =========================================================

@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    db: Session = Depends(get_db),
    _: bool = Depends(require_admin),
):
    total_orders = db.query(Order).count()
    paid_orders = db.query(Order).filter(Order.status == "paid").count()
    revenue = db.query(func.coalesce(func.sum(Order.amount), 0)).filter(Order.status == "paid").scalar()
    licenses = db.query(License).order_by(License.created_at.desc()).limit(50).all()
    orders = db.query(Order).order_by(Order.created_at.desc()).limit(50).all()

    lic_rows = ""
    for l in licenses:
        lic_rows += f"""
        <tr>
            <td>{l.id}</td><td>{l.email}</td><td>{l.plan}</td><td>{l.credits}</td>
            <td>{l.expires_at}</td><td>{'BLOCKED' if l.blocked else 'ACTIVE'}</td><td>{l.license_key_hint}</td>
        </tr>
        """

    order_rows = ""
    for o in orders:
        order_rows += f"""
        <tr><td>{o.order_code}</td><td>{o.email}</td><td>{o.plan}</td>
        <td>{money_int(o.amount):,}</td><td>{o.status}</td><td>{o.created_at}</td></tr>
        """

    return f"""
    <html><body style="font-family:Arial;max-width:1200px;margin:30px auto">
        <h1>Admin Dashboard - Phase 2</h1>
        <p>Orders: <b>{total_orders}</b> | Paid: <b>{paid_orders}</b> | Revenue: <b>{money_int(revenue):,} VND</b></p>
        <h2>Licenses</h2>
        <table border="1" cellpadding="7" cellspacing="0">
            <tr><th>ID</th><th>Email</th><th>Plan</th><th>Credits</th><th>Expires</th><th>Status</th><th>Hint</th></tr>
            {lic_rows}
        </table>
        <h2>Orders</h2>
        <table border="1" cellpadding="7" cellspacing="0">
            <tr><th>Order</th><th>Email</th><th>Plan</th><th>Amount</th><th>Status</th><th>Created</th></tr>
            {order_rows}
        </table>
    </body></html>
    """


@app.get("/admin/api/summary")
def admin_summary(db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    revenue = db.query(func.coalesce(func.sum(Order.amount), 0)).filter(Order.status == "paid").scalar()
    return {
        "orders": db.query(Order).count(),
        "paid_orders": db.query(Order).filter(Order.status == "paid").count(),
        "licenses": db.query(License).count(),
        "blocked_licenses": db.query(License).filter(License.blocked == True).count(),
        "revenue": money_int(revenue),
        "active_sessions": db.query(ClientSession).filter(
            ClientSession.revoked == False,
            ClientSession.expires_at > now_utc(),
        ).count(),
    }


@app.get("/admin/api/orders")
def admin_orders(db: Session = Depends(get_db), _: bool = Depends(require_admin), limit: int = 100):
    rows = db.query(Order).order_by(Order.created_at.desc()).limit(limit).all()
    return [
        {
            "order_code": o.order_code,
            "email": o.email,
            "plan": o.plan,
            "amount": money_int(o.amount),
            "status": o.status,
            "created_at": o.created_at.isoformat(),
            "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        } for o in rows
    ]


@app.get("/admin/api/licenses")
def admin_licenses(db: Session = Depends(get_db), _: bool = Depends(require_admin), limit: int = 100):
    rows = db.query(License).order_by(License.created_at.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "email": l.email,
            "plan": l.plan,
            "credits": l.credits,
            "total_credits": l.total_credits,
            "max_devices": l.max_devices,
            "expires_at": l.expires_at.isoformat(),
            "blocked": l.blocked,
            "block_reason": l.block_reason,
            "license_key_hint": l.license_key_hint,
            "subscription_status": l.subscription_status,
        } for l in rows
    ]


@app.post("/admin/api/block")
def admin_block(data: AdminBlockIn, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    lic = db.query(License).filter(License.id == data.license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found")
    lic.blocked = data.blocked
    lic.block_reason = data.reason
    db.commit()
    return {"ok": True, "license_id": lic.id, "blocked": lic.blocked}


@app.post("/admin/api/credit")
def admin_credit(data: AdminCreditIn, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    lic = db.query(License).filter(License.id == data.license_id).first()
    if not lic:
        raise HTTPException(status_code=404, detail="License not found")
    before = lic.credits
    after = before + data.amount
    if after < 0:
        raise HTTPException(status_code=400, detail="Credit cannot be negative")
    lic.credits = after
    lic.total_credits = max(lic.total_credits, after)
    db.add(CreditLog(
        license_id=lic.id,
        action="add" if data.amount >= 0 else "consume",
        amount=abs(data.amount),
        before_credit=before,
        after_credit=after,
        reason=data.reason,
        request_id="admin-" + str(uuid.uuid4()),
        ip=client_ip(request),
    ))
    db.commit()
    return {"ok": True, "before": before, "after": after}


@app.get("/admin/api/devices/{license_id}")
def admin_devices(license_id: int, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    rows = db.query(LicenseDevice).filter(LicenseDevice.license_id == license_id).all()
    return [
        {
            "id": d.id,
            "device_name": d.device_name,
            "first_ip": d.first_ip,
            "last_ip": d.last_ip,
            "first_seen": d.first_seen.isoformat(),
            "last_seen": d.last_seen.isoformat(),
            "trusted": d.trusted,
            "revoked": d.revoked,
        } for d in rows
    ]


@app.post("/admin/api/device/revoke")
def admin_revoke_device(data: AdminDeviceRevokeIn, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    dev = db.query(LicenseDevice).filter(LicenseDevice.id == data.device_id).first()
    if not dev:
        raise HTTPException(status_code=404, detail="Device not found")
    dev.revoked = data.revoked

    # revoke all sessions of this device
    sessions = db.query(ClientSession).filter(
        ClientSession.license_id == dev.license_id,
        ClientSession.hwid_hash == dev.hwid_hash,
    ).all()
    for s in sessions:
        s.revoked = True
    db.commit()
    return {"ok": True, "device_id": dev.id, "revoked": dev.revoked}


@app.post("/admin/api/order/{order_code}/mark-paid")
def admin_mark_paid(order_code: str, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    order = mark_order_paid(db, order_code, {"source": "admin_manual_mark_paid"})
    return {"ok": True, "order_code": order.order_code, "status": order.status}


# =========================================================
# CLIENT HELPER: HWID EXAMPLE
# =========================================================

@app.get("/client/hwid-example.py", response_class=PlainTextResponse)
def hwid_example():
    return r
