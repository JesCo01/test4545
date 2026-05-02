import os
import base64
import json
import ast
import sqlite3
import time
import math
import random
import unicodedata
from pathlib import Path
from datetime import datetime, timedelta, timezone
from flask import Flask, send_from_directory, jsonify, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from flask_cors import CORS
from dotenv import load_dotenv
from sqlalchemy.orm import load_only
from sqlalchemy.exc import OperationalError, IntegrityError
import logging
import subprocess
import threading
import re
import atexit
from urllib.parse import unquote_plus
import requests
import smtplib
import imaplib
import email as email_lib
from email.mime.text import MIMEText
from email.header import decode_header
from html import unescape
from obt_nombre import get_nombre

                           
                              
                           
LAST_ERRORS = []                                                              

                           
                          
                           
load_dotenv(override=True)

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

                                 
DB_PATH = DATA_DIR / "cards.db"

MAX_EXTRA_FIELDS = int(os.getenv("MAX_EXTRA_FIELDS", "50"))
MAX_GENERATE_COUNT = int(os.getenv("MAX_GENERATE_COUNT", "50000"))
JOB_PAGE = os.getenv("JOB_PAGE", "test/index.html").strip().lstrip("/")
MAX_DASH_RESULTS = int(os.getenv("MAX_DASH_RESULTS", "20000"))
DEBT_CUTOFF_DATE_STR = os.getenv("DEBT_CUTOFF_DATE", "2026-02-20")
AVAILABLE_MIN_DATE_STR = os.getenv("AVAILABLE_MIN_DATE", DEBT_CUTOFF_DATE_STR)
NO_DATE_REVIEWED = int(os.getenv("NO_DATE_REVIEWED", "6"))
PROCESSING_REVIEWED = 7
VALIDATED_REVIEWED = 8
REGISTER_REVIEWED = 9
PROCESSING_TTL_MINUTES = int(os.getenv("PROCESSING_TTL_MINUTES", "15"))
PROCESSING_MAX_ATTEMPTS = int(os.getenv("PROCESSING_MAX_ATTEMPTS", "3"))
PROCESSING_EXPIRED_FAIL_REVIEWED = int(os.getenv("PROCESSING_EXPIRED_FAIL_REVIEWED", "12"))
PROCESSING_DEFAULT_RETURN_REVIEWED = int(os.getenv("PROCESSING_DEFAULT_RETURN_REVIEWED", "4"))

                                
EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").strip()
EMAIL_SUBJECT = "FLOW"
#OTP_EMAIL_DOMAIN = os.getenv("OTP_EMAIL_DOMAIN", "zvinfinity.com").strip().lower()
OTP_EMAIL_DOMAIN = os.getenv("OTP_EMAIL_DOMAIN", "argnmail.com").strip().lower()
OTP_IMAP_MAX_SCAN = int(os.getenv("OTP_IMAP_MAX_SCAN", "40"))
V4_PASSWORD_SPECIAL = "+-/*@_&$"
V4_PASSWORD_END_SYMBOLS = "*"

                                                                      
                                                               
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

                              
CLOUDFLARE_URL = None
CF_PROC = None

PORT = int(os.getenv("PORT", 8110))
STATIC_DIR = os.getenv("STATIC_DIR", "static")

origins_env = os.getenv("ALLOW_ORIGINS", "*")
ALLOW_ORIGINS = [o.strip() for o in origins_env.split(",")]

                                               
logging.getLogger("werkzeug").setLevel(logging.ERROR)

                           
                      
                           
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.config["SECRET_KEY"] = "secret_key_super_segura!"
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH.as_posix()}"

                         
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"check_same_thread": False}}

CORS(
    app,
    resources={r"/*": {"origins": ALLOW_ORIGINS}},
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "DELETE", "OPTIONS"],
)

db = SQLAlchemy(app)

                                                               
                                                            
                                                                           
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    manage_session=False,
    allow_upgrades=False,
    transports=["polling"],
)

                           
         
                           

                                          
class Credencial(db.Model):
    __tablename__ = "credencial"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False)
    password = db.Column(db.String(150), nullable=False)
    status = db.Column(db.String(20), default="pending")
    assigned_at = db.Column(db.DateTime, nullable=True)
    extra_fields = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())


                                                       
class Resultado(db.Model):
    __tablename__ = "resultado"
    id = db.Column(db.Integer, primary_key=True)
    original_id = db.Column(db.Integer)
    username = db.Column(db.String(150))
    password = db.Column(db.String(150))
    result_status = db.Column(db.String(20))
    reviewed = db.Column(db.Integer, default=0)                                           
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
    nombre = db.Column(db.String(255), nullable=True)
    extra_fields = db.Column(db.JSON, nullable=False, default=dict)
    processing_started_at = db.Column(db.DateTime, nullable=True)
    processing_expires_at = db.Column(db.DateTime, nullable=True)
    processing_owner = db.Column(db.String(120), nullable=True)
    processing_attempts = db.Column(db.Integer, nullable=False, default=0)
    processing_return_state = db.Column(db.Integer, nullable=True)


                                                                        
class History(db.Model):
    __tablename__ = "history"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), nullable=False, index=True)
    password = db.Column(db.String(150), nullable=False)
    added_at = db.Column(db.DateTime, server_default=db.func.now())
    extra_fields = db.Column(db.JSON, nullable=False, default=dict)

    __table_args__ = (
        db.UniqueConstraint("username", "password", name="uq_hist_username_password"),
    )

class JsonDocument(db.Model):
    __tablename__ = "json_document"
    id = db.Column(db.Integer, primary_key=True)
    namespace = db.Column(db.String(80), nullable=False, index=True, default="default")
    doc_key = db.Column(db.String(120), nullable=False, index=True)
    data = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("namespace", "doc_key", name="uq_json_document_namespace_key"),
    )


class EmailPoolAccount(db.Model):
    __tablename__ = "email_pool_account"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False)
    email_lower = db.Column(db.String(255), nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)
    recovery_email_1 = db.Column(db.String(255), nullable=True)
    recovery_email_2 = db.Column(db.String(255), nullable=True)
    recovery_code_1 = db.Column(db.String(255), nullable=True)
    recovery_code_2 = db.Column(db.String(255), nullable=True)
    extra_1 = db.Column(db.String(255), nullable=True)
    extra_2 = db.Column(db.String(255), nullable=True)
    extra_3 = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="sin_uso", index=True)
    provider = db.Column(db.String(40), nullable=False, default="other")
    assigned_result_id = db.Column(db.Integer, nullable=True, index=True)
    assigned_username = db.Column(db.String(150), nullable=True, index=True)
    assigned_at = db.Column(db.DateTime, nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("email_lower", name="uq_email_pool_account_email_lower"),
    )


def _json_safe(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


_SENSITIVE_URLENCODED_KEYS = {
    "password",
    "passwd",
    "pass",
    "pwd",
    "username",
    "user",
    "email",
}


def _redact_urlencoded_body(raw_body: str) -> str:

    if not raw_body:
        return raw_body

    parts = str(raw_body).split("&")
    for idx, part in enumerate(parts):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        try:
            key = unquote_plus(k).strip().lower()
        except Exception:
            key = str(k).strip().lower()
        if key in _SENSITIVE_URLENCODED_KEYS:
            parts[idx] = f"{k}={v}"
    return "&".join(parts)


def _redact_sensitive_fields(data: dict) -> dict:
    """
    Copia un dict y redacciona valores de keys sensibles. Mantiene el resto igual.
    """
    if not isinstance(data, dict):
        return {}

    out = {}
    for k, v in data.items():
        key = str(k).strip().lower()
        if key in _SENSITIVE_URLENCODED_KEYS:
            val_str = "" if v is None else str(v)
            out[k] = f"{val_str}"
        else:
            out[k] = v
    return out


def _normalize_extra_fields(extra_fields, max_fields=MAX_EXTRA_FIELDS):
    if extra_fields is None:
        return {}

    parsed = extra_fields
    if isinstance(extra_fields, str):
        try:
            parsed = json.loads(extra_fields)
        except Exception:
            try:
                parsed = ast.literal_eval(extra_fields)
            except Exception:
                return {}

    if not isinstance(parsed, dict):
        return {}

    try:
        max_limit = int(max_fields) if max_fields is not None else MAX_EXTRA_FIELDS
    except Exception:
        max_limit = MAX_EXTRA_FIELDS
    if max_limit <= 0:
        max_limit = None

    normalized = {}
    for key, value in parsed.items():
        key_str = str(key).strip()
        if not key_str:
            continue
        normalized[key_str] = _json_safe(value)
        if max_limit and len(normalized) >= max_limit:
            break
    return normalized


def _extract_extra_fields(payload, reserved_keys=None, max_fields=MAX_EXTRA_FIELDS):
    reserved = set(reserved_keys or [])
    if not isinstance(payload, dict):
        return {}

    merged = {}
    explicit = payload.get("extra_fields")
    if explicit is not None:
        merged.update(_normalize_extra_fields(explicit, max_fields=max_fields))

    for key, value in payload.items():
        if key in reserved or key == "extra_fields":
            continue
        merged[str(key)] = _json_safe(value)

    return _normalize_extra_fields(merged, max_fields=max_fields)


def _merge_extra_fields(*maps):
    merged = {}
    for current in maps:
        merged.update(_normalize_extra_fields(current))
    return _normalize_extra_fields(merged)


_STANDARD_SUCCESS_RESULT_STATUSES = ("success", "used")
_POSITIVE_RESULT_STATUSES = ("success", "used", "lucas")

_STATUS_INPUT_ALIASES = {
    "success": "success",
    "succes": "success",
    "lucas": "lucas",
    "used": "used",
    "fail": "fail",
    "failed": "fail",
    "none": "none",
    "error": "error",
}


def _normalize_status_input(value, *, keep_unknown=False):
    raw = str(value or "").strip().lower()
    if not raw:
        return ""

    normalized = _STATUS_INPUT_ALIASES.get(raw)
    if normalized:
        return normalized

    return raw if keep_unknown else ""


_EMAIL_POOL_STATUS_ALIASES = {
    "sin_uso": "sin_uso",
    "sinuso": "sin_uso",
    "unused": "sin_uso",
    "new": "sin_uso",
    "available": "sin_uso",
    "pendiente": "pendiente",
    "pending": "pendiente",
    "in_use": "pendiente",
    "reserved": "pendiente",
    "usado": "usado",
    "used": "usado",
    "done": "usado",
    "fallo": "fallo",
    "failed": "fallo",
    "fail": "fallo",
    "error": "fallo",
}


def _normalize_email_pool_status(value, *, keep_unknown=False):
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = _EMAIL_POOL_STATUS_ALIASES.get(raw)
    if normalized:
        return normalized
    return raw if keep_unknown else ""


_EMAIL_SOURCE_ALIASES = {
    "generated": "generated",
    "auto": "generated",
    "default": "generated",
    "pool": "pool",
    "pool_email": "pool",
    "pool_hotmail": "pool",
    "pool_outlook": "pool",
    "pool_gmail": "pool",
    "pool_microsoft": "pool",
    "hotmail": "pool",
    "outlook": "pool",
    "gmail": "pool",
}


def _normalize_email_source_input(value):
    raw = str(value or "").strip().lower()
    if not raw:
        return "generated"
    return _EMAIL_SOURCE_ALIASES.get(raw, "")


def _normalize_pool_email_value(value):
    email_text = str(value or "").strip().lower()
    if (
        not email_text
        or "@" not in email_text
        or email_text.count("@") != 1
        or any(ch.isspace() for ch in email_text)
    ):
        return ""
    local, _, domain = email_text.partition("@")
    if not local or not domain or "." not in domain:
        return ""
    return f"{local}@{domain}"


def _email_pool_provider_from_email(email_value):
    domain = str(email_value or "").strip().lower().partition("@")[2]
    if domain in {"hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "live.com", "msn.com"}:
        return "microsoft"
    if domain in {"gmail.com", "googlemail.com"}:
        return "gmail"
    return "other"


def _default_email_pool_status_for_reviewed(reviewed):
    if reviewed in (13, 14):
        return "usado"
    if reviewed in (10, 11, 12):
        return "fallo"
    if reviewed in (0, 1, 3, 4, 5, 6, 7, 8, 9, 15):
        return "pendiente"
    return ""


def _set_email_pool_status_by_id(account_id, status, *, result_id=None, username=None):
    normalized = _normalize_email_pool_status(status)
    if not normalized:
        return False

    now = datetime.now()
    updates = {"status": normalized}
    if normalized == "sin_uso":
        updates.update(
            {
                "assigned_result_id": None,
                "assigned_username": None,
                "assigned_at": None,
                "used_at": None,
            }
        )
    elif normalized == "pendiente":
        updates.update(
            {
                "assigned_result_id": result_id,
                "assigned_username": username,
                "assigned_at": now,
            }
        )
    elif normalized == "usado":
        updates.update(
            {
                "used_at": now,
                "assigned_result_id": result_id if result_id is not None else None,
                "assigned_username": username if username is not None else None,
            }
        )
    elif normalized == "fallo":
        updates.update(
            {
                "used_at": now,
                "assigned_result_id": result_id if result_id is not None else None,
                "assigned_username": username if username is not None else None,
            }
        )

    rows = (
        EmailPoolAccount.query
        .filter(EmailPoolAccount.id == account_id)
        .update(updates, synchronize_session=False)
    )
    return rows > 0


def _claim_next_email_pool_account(result_id=None, username=None):
    now = datetime.now()
    for _ in range(25):
        candidate = (
            EmailPoolAccount.query
            .filter(EmailPoolAccount.status == "sin_uso")
            .order_by(EmailPoolAccount.id.asc())
            .first()
        )
        if not candidate:
            return None

        rows = (
            EmailPoolAccount.query
            .filter(
                EmailPoolAccount.id == candidate.id,
                EmailPoolAccount.status == "sin_uso",
            )
            .update(
                {
                    "status": "pendiente",
                    "assigned_result_id": result_id,
                    "assigned_username": username,
                    "assigned_at": now,
                },
                synchronize_session=False,
            )
        )
        if rows > 0:
            db.session.flush()
            return db.session.get(EmailPoolAccount, candidate.id)
    return None


def _reusable_email_pool_account_for_result(row):
    if not row:
        return None
    extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
    raw_pool_id = extras.get("v4_pool_email_id", extras.get("pool_email_id"))
    try:
        pool_id = int(raw_pool_id) if raw_pool_id is not None else None
    except (TypeError, ValueError):
        pool_id = None
    if not pool_id:
        return None

    account = db.session.get(EmailPoolAccount, pool_id)
    if not account or account.status != "pendiente":
        return None
    if account.assigned_result_id and account.assigned_result_id != row.id:
        return None
    if account.assigned_username and row.username and account.assigned_username != row.username:
        return None
    if not account.assigned_result_id or not account.assigned_username:
        _set_email_pool_status_by_id(
            account.id,
            "pendiente",
            result_id=row.id,
            username=row.username,
        )
        db.session.flush()
        account = db.session.get(EmailPoolAccount, account.id)
    return account


def _sync_email_pool_for_result(row, *, reviewed=None, explicit_status=None):
    if not row:
        return {"updated": False, "pool_id": None, "pool_status": None}

    pool_status = (
        _normalize_email_pool_status(explicit_status)
        if explicit_status is not None
        else _default_email_pool_status_for_reviewed(reviewed if reviewed is not None else row.reviewed)
    )
    if not pool_status:
        return {"updated": False, "pool_id": None, "pool_status": None}

    extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
    inferred_pool_id = extras.get("v4_pool_email_id", extras.get("pool_email_id"))
    pool_id = None
    try:
        pool_id = int(inferred_pool_id) if inferred_pool_id is not None else None
    except (TypeError, ValueError):
        pool_id = None

    if not pool_id:
        linked = (
            EmailPoolAccount.query
            .filter(EmailPoolAccount.assigned_result_id == row.id)
            .order_by(EmailPoolAccount.id.desc())
            .first()
        )
        if linked:
            pool_id = linked.id

    if not pool_id:
        return {"updated": False, "pool_id": None, "pool_status": None}

    updated = _set_email_pool_status_by_id(
        pool_id,
        pool_status,
        result_id=row.id,
        username=row.username,
    )
    if not updated:
        return {"updated": False, "pool_id": pool_id, "pool_status": None}

    extras["v4_pool_email_id"] = pool_id
    extras["v4_pool_email_status"] = pool_status
    row.extra_fields = _merge_extra_fields(row.extra_fields, extras)
    return {"updated": True, "pool_id": pool_id, "pool_status": pool_status}


_VALID_REVIEW_STATES = (0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15)
_REVIEW_STATE_STRING_ALIASES = {
    "0": 0,
    "pending": 0,
    "none": 0,
    "unreviewed": 0,
    "sin": 0,
    "sin_revisar": 0,
    "1": 1,
    "revisado": 1,
    "reviewed": 1,
    "done": 1,
    "2": 3,
    "3": 3,
    "disponible": 3,
    "available": 3,
    "4": 4,
    "listo": 4,
    "closed": 4,
    "cerrado": 4,
    "5": 5,
    "deuda": 5,
    "debt": 5,
    "6": 6,
    "sin_fecha": 6,
    "sin fecha": 6,
    "no_date": 6,
    "7": 7,
    "procesando": 7,
    "processing": 7,
    "in_progress": 7,
    "in progress": 7,
    "8": 8,
    "validado": 8,
    "validated": 8,
    "9": 9,
    "registrar": 9,
    "register": 9,
    "10": 10,
    "fallo_add": 10,
    "fallo add": 10,
    "fail_add": 10,
    "fail add": 10,
    "11": 11,
    "fall_nun": 11,
    "fall nun": 11,
    "fail_nun": 11,
    "fail nun": 11,
    "12": 12,
    "fallo_total": 12,
    "fallo total": 12,
    "fail_total": 12,
    "fail total": 12,
    "13": 13,
    "registrado": 13,
    "registered": 13,
    "14": 14,
    "bueno": 14,
    "good": 14,
    "15": 15,
    "re_registrar": 15,
    "re_register": 15,
    "reregistrar": 15,
}


def _normalize_review_state_value(value, *, allow_none=False, bool_legacy=False):
    if value is None:
        return None if allow_none else 0
    if isinstance(value, bool):
        return (1 if value else 0) if bool_legacy else (None if allow_none else 0)
    try:
        num = int(value)
        if num == 2:
            return 3
        if num in _VALID_REVIEW_STATES:
            return num
    except Exception:
        pass
    if isinstance(value, str):
        normalized = _REVIEW_STATE_STRING_ALIASES.get(value.strip().lower())
        if normalized in _VALID_REVIEW_STATES:
            return normalized
    return None if allow_none else 0


def _processing_ttl_delta():
    try:
        minutes = int(PROCESSING_TTL_MINUTES)
    except Exception:
        minutes = 15
    return timedelta(minutes=max(1, minutes))


def _normalize_processing_return_state(value, default=PROCESSING_DEFAULT_RETURN_REVIEWED):
    try:
        state = int(value)
    except (TypeError, ValueError):
        state = default
    if state == PROCESSING_REVIEWED:
        return default
    if state == 2:
        return 3
    if state in _VALID_REVIEW_STATES:
        return state
    return default


def _processing_payload(row):
    return {
        "processing_started_at": row.processing_started_at.isoformat() if row.processing_started_at else None,
        "processing_expires_at": row.processing_expires_at.isoformat() if row.processing_expires_at else None,
        "processing_owner": row.processing_owner,
        "processing_attempts": row.processing_attempts or 0,
        "processing_return_state": row.processing_return_state,
    }


def _clear_processing_fields(row, *, reset_attempts=False):
    row.processing_started_at = None
    row.processing_expires_at = None
    row.processing_owner = None
    row.processing_return_state = None
    if reset_attempts:
        row.processing_attempts = 0


def _start_processing_fields(row, *, return_state=None, owner=None, now=None, increment_attempts=True):
    now = now or datetime.now()
    row.reviewed = PROCESSING_REVIEWED
    row.processing_started_at = now
    row.processing_expires_at = now + _processing_ttl_delta()
    row.processing_owner = (str(owner).strip() if owner is not None else None) or None
    row.processing_return_state = _normalize_processing_return_state(
        return_state if return_state is not None else row.processing_return_state
    )
    if increment_attempts:
        row.processing_attempts = int(row.processing_attempts or 0) + 1


def _apply_review_state(row, review_state, *, owner=None, return_state=None):
    if review_state == PROCESSING_REVIEWED:
        current_state = row.reviewed if row.reviewed != PROCESSING_REVIEWED else row.processing_return_state
        _start_processing_fields(
            row,
            return_state=return_state if return_state is not None else current_state,
            owner=owner,
            increment_attempts=row.reviewed != PROCESSING_REVIEWED,
        )
    else:
        row.reviewed = review_state
        _clear_processing_fields(row, reset_attempts=True)


def _claim_processing_result(row, expected_reviewed, *, owner=None):
    if not row:
        return None
    now = datetime.now()
    return_state = _normalize_processing_return_state(expected_reviewed)
    next_attempts = int(row.processing_attempts or 0) + 1
    rows = (
        Resultado.query
        .filter(Resultado.id == row.id, Resultado.reviewed == expected_reviewed)
        .update(
            {
                "reviewed": PROCESSING_REVIEWED,
                "processing_started_at": now,
                "processing_expires_at": now + _processing_ttl_delta(),
                "processing_owner": (str(owner).strip() if owner is not None else None) or None,
                "processing_attempts": next_attempts,
                "processing_return_state": return_state,
            },
            synchronize_session=False,
        )
    )
    if rows <= 0:
        return None
    db.session.flush()
    db.session.expire(row)
    db.session.refresh(row)
    return row


def _undo_processing_claim(row):
    if not row or row.reviewed != PROCESSING_REVIEWED:
        return
    return_state = _normalize_processing_return_state(row.processing_return_state)
    row.reviewed = return_state
    row.processing_attempts = max(0, int(row.processing_attempts or 0) - 1)
    _clear_processing_fields(row, reset_attempts=False)


def _release_expired_processing(now=None):
    now = now or datetime.now()
    rows = (
        Resultado.query
        .filter(Resultado.reviewed == PROCESSING_REVIEWED)
        .filter(Resultado.processing_expires_at.isnot(None))
        .filter(Resultado.processing_expires_at < now)
        .order_by(Resultado.id.asc())
        .limit(1000)
        .all()
    )
    changed = 0
    for row in rows:
        attempts = int(row.processing_attempts or 0)
        if attempts > PROCESSING_MAX_ATTEMPTS:
            target_state = _normalize_processing_return_state(
                PROCESSING_EXPIRED_FAIL_REVIEWED,
                default=12,
            )
        else:
            target_state = _normalize_processing_return_state(row.processing_return_state)
        row.reviewed = target_state
        try:
            _sync_email_pool_for_result(row, reviewed=target_state)
        except Exception:
            pass
        _clear_processing_fields(row, reset_attempts=False)
        changed += 1
    if changed:
        db.session.commit()
    return changed


def _restore_lucas_statuses_from_extras():
    restored = {"credencial": 0, "resultado": 0}

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        for table_name, status_column in (
            ("credencial", "status"),
            ("resultado", "result_status"),
        ):
            rows = conn.execute(
                f"SELECT id, {status_column}, extra_fields FROM {table_name} "
                f"WHERE {status_column} IN ('success', 'used')"
            ).fetchall()

            for row in rows:
                extras = _normalize_extra_fields(row["extra_fields"], max_fields=0)
                tipo_exito = str(extras.get("tipo_exito") or "").strip().lower()
                if tipo_exito != "lucas":
                    continue

                conn.execute(
                    f"UPDATE {table_name} SET {status_column} = 'lucas' WHERE id = ?",
                    (row["id"],),
                )
                restored[table_name] += 1

        conn.commit()

    return restored


def _get_cutoff_dates():
    try:
        debt_cutoff = datetime.strptime(DEBT_CUTOFF_DATE_STR, "%Y-%m-%d").date()
    except Exception:
        debt_cutoff = datetime(2026, 2, 20).date()
    try:
        avail_min = datetime.strptime(AVAILABLE_MIN_DATE_STR, "%Y-%m-%d").date()
    except Exception:
        avail_min = debt_cutoff
    return debt_cutoff, avail_min


def _extract_due_date_from_extras(extra_fields):
    if not isinstance(extra_fields, dict):
        return None
    for key in ("formattedFirstDueDate", "dueDate", "vencimiento"):
        if key in extra_fields:
            return _parse_due_date(extra_fields.get(key))
    return None


def _contains_bad_username_marker(value):
    text = str(value or "").strip().lower()
    if not text:
        return True
    compact = re.sub(r"[\s._-]+", "", text)
    if compact in {"n/a", "na", "nd", "n/d", "none", "null", "nonro", "sinnro", "nonumero", "sinnumero"}:
        return True
    for bad in ("no nro", "sin nro", "no numero", "sin numero"):
        if bad.replace(" ", "") in compact:
            return True
    return False


def _split_extra_values(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(_split_extra_values(item))
        return out
    if isinstance(value, dict):
        out = []
        for item in value.values():
            out.extend(_split_extra_values(item))
        return out
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[|,;\n\r\t]+", text) if part and part.strip()]


def _extract_numeric_usernames(extra_fields, max_items=12):
    extras = _normalize_extra_fields(extra_fields)
    keys = (
        "usernames",
        "username_list",
        "aliases",
        "email_usernames",
        "v4_usernames",
        "linea4",
        "line4",
        "line_last4",
        "v4_phone_last4",
        "phone_last4",
    )
    candidates = []
    for key in keys:
        if key in extras:
            candidates.extend(_split_extra_values(extras.get(key)))

    result = []
    seen = set()
    for raw in candidates:
        if _contains_bad_username_marker(raw):
            continue
        digits = re.sub(r"\D", "", str(raw))
        if len(digits) < 4:
            continue
        last4 = digits[-4:]
        if last4 in seen:
            continue
        seen.add(last4)
        result.append(last4)
        if len(result) >= max_items:
            break
    return result


def _password_has_seq(text):
    src = str(text or "")
    for i in range(len(src) - 2):
        seg = src[i : i + 3]
        if seg.isdigit():
            a, b, c = (ord(ch) - ord("0") for ch in seg)
        elif seg.isalpha():
            a, b, c = (ord(ch.lower()) - ord("a") for ch in seg)
        else:
            continue
        if (b == a + 1 and c == b + 1) or (b == a - 1 and c == b - 1):
            return True
    return False


def _password_ok(text):
    value = str(text or "")
    if len(value) < 8 or len(value) > 15:
        return False
    if not re.fullmatch(rf"[A-Za-z0-9{re.escape(V4_PASSWORD_SPECIAL)}]+", value):
        return False
    if not re.search(r"[A-Za-z]", value):
        return False
    if not re.search(r"\d", value):
        return False
    if not any(ch in V4_PASSWORD_SPECIAL for ch in value):
        return False
    if re.search(r"\s", value):
        return False
    if re.search(r"(.)\1", value):
        return False
    if _password_has_seq(value):
        return False
    return True


def _password_has_star_suffix_only(text):
    value = str(text or "")
    if not value.endswith("*"):
        return False
    if value.count("*") != 1:
        return False
    core = value[:-1]
    if not core:
        return False
    return re.fullmatch(r"[A-Za-z0-9]+", core) is not None


def _password_ascii_letters(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z]+", "", text)


def _password_name_token(nombre=None, username=None):
    raw_name = str(nombre or "").strip()
    candidates = []

    if raw_name and not _nombre_placeholder(raw_name):
        surname_part, has_comma, names_part = raw_name.partition(",")
        if has_comma:
            candidates.extend(re.findall(r"[A-Za-zÀ-ÿÑñ]+", surname_part))
            candidates.extend(re.findall(r"[A-Za-zÀ-ÿÑñ]+", names_part))
        else:
            candidates.extend(re.findall(r"[A-Za-zÀ-ÿÑñ]+", raw_name))

    user_letters = _password_ascii_letters(username)
    if user_letters:
        candidates.append(user_letters)

    for candidate in candidates:
        token = _password_ascii_letters(candidate)
        if not token:
            continue
        token = token[:11]
        return token[:1].upper() + token[1:].lower()

    return "User"


def _password_name_rule_ok(text):
    return _password_ok(text)


def _generate_v4_password(nombre=None, username=None):
    rng = random.SystemRandom()
    letters_pool = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    digits_pool = "0123456789"
    specials_pool = V4_PASSWORD_SPECIAL
    full_pool = letters_pool + digits_pool + specials_pool

    for _ in range(600):
        length = rng.randint(8, 15)
        chars = [
            rng.choice(letters_pool),
            rng.choice(digits_pool),
            rng.choice(specials_pool),
        ]
        while len(chars) < length:
            chars.append(rng.choice(full_pool))
        rng.shuffle(chars)
        candidate = "".join(chars)
        if _password_ok(candidate):
            return candidate

    fallback = "G7@k4_M9"
    if _password_ok(fallback):
        return fallback
    raise RuntimeError("No se pudo generar una password valida con las reglas definidas")


def _normalize_email_local_token(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "", text.lower())
    return text


def _nombre_placeholder(value):
    compact = _normalize_email_local_token(value)
    return compact in {
        "",
        "noencontrado",
        "sinnombre",
        "nodisponible",
        "desconocido",
        "none",
        "null",
        "na",
    }


def _email_local_from_nombre(nombre=None, username=None):
    raw_name = str(nombre or "").strip()
    if raw_name and not _nombre_placeholder(raw_name):
        surname_part, _, names_part = raw_name.partition(",")
        surname = _normalize_email_local_token(surname_part)
        first_name = ""
        if names_part.strip():
            first_name = _normalize_email_local_token(names_part.strip().split()[0])

        if first_name and surname:
            return f"{first_name}{surname}"
        if surname:
            return surname
        if first_name:
            return first_name

    local = _normalize_email_local_token(username)
    return local or "user"


def _mail_from_username(username, nombre=None):
    local = _email_local_from_nombre(nombre=nombre, username=username)
    domain = OTP_EMAIL_DOMAIN or "zvinfinity.com"
    return f"{local}@{domain}"


def _looks_like_username_generated_email(email_value, username):
    email_text = str(email_value or "").strip().lower()
    if "@" not in email_text:
        return False
    local, _, domain = email_text.partition("@")
    expected_domain = (OTP_EMAIL_DOMAIN or "zvinfinity.com").strip().lower()
    return local == _normalize_email_local_token(username) and domain == expected_domain


def _has_v4_flow_progress(extras):
    for key in (
        "v4_step2_email",
        "v4_step2_done_at",
        "v4_otp_at",
        "v4_otp_code",
        "v4_otp_uid",
        "v4_step3_done_at",
        "v4_step4_password_done_at",
        "v4_step5_done_at",
    ):
        if not _is_missing_token(extras.get(key)):
            return True
    return False


def _ensure_v4_credentials_for_result(row, extra_fields, max_fields=MAX_EXTRA_FIELDS):
    extras = _normalize_extra_fields(extra_fields, max_fields=max_fields)
    current_email = str(extras.get("v4_generated_email") or "").strip()
    current_password = str(extras.get("v4_generated_password") or "").strip()
    changed = False
    changed = _set_if_missing(extras, "nombre", row.nombre) or changed
    changed = _set_if_missing(extras, "nombre_completo", row.nombre) or changed
    desired_email = _mail_from_username(row.username, nombre=row.nombre)
    should_upgrade_legacy_email = (
        bool(current_email)
        and desired_email.lower() != current_email.lower()
        and _looks_like_username_generated_email(current_email, row.username)
        and not _has_v4_flow_progress(extras)
    )

    if not current_email or should_upgrade_legacy_email:
        current_email = desired_email
        extras["v4_generated_email"] = current_email
        changed = True
    if (
        not current_password
        or not _password_name_rule_ok(current_password)
    ):
        current_password = _generate_v4_password(nombre=row.nombre, username=row.username)
        extras["v4_generated_password"] = current_password
        changed = True
    if changed:
        extras["v4_generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return extras, current_email, current_password, changed


def _is_missing_token(value):
    text = str(value or "").strip()
    if not text:
        return True
    compact = re.sub(r"[\s._-]+", "", text.lower())
    return compact in {
        "-", "--", "na", "n/a", "nd", "n/d", "null", "none",
        "sindato", "sindatos", "sininfo", "noaplica", "noaplica"
    }


def _norm_alias_key(value):
    text = str(value or "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9]+", "", text).lower()
    return text


def _values_by_aliases(extras, aliases):
    if not isinstance(extras, dict):
        return []
    alias_norm = {_norm_alias_key(a) for a in aliases}
    out = []
    for key, value in extras.items():
        if _norm_alias_key(key) in alias_norm:
            out.extend(_split_extra_values(value))
    return out


def _normalize_doc(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 7 or len(digits) > 8:
        return ""
    if digits.startswith("0"):
        return ""
    return digits


def _normalize_sex(value):
    s = str(value or "").strip().lower()
    if not s:
        return ""
    if s in {"1", "2", "3"}:
        return s
    if s in {"m", "male", "hombre"} or s.startswith("masc"):
        return "1"
    if s in {"f", "female", "mujer"} or s.startswith("fem"):
        return "2"
    if s in {"x", "otro", "other", "no-binario", "non-binary", "nonbinary"}:
        return "3"
    return ""


def _digits_height(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return ""
    trimmed = digits.lstrip("0")
    return trimmed or digits


def _extract_altura_from_extras(extras):
    direct_aliases = (
        "v4_altura", "altura", "height", "altura_cm", "nro", "numero", "number", "address_number",
        "Altura", "ALTURA", "Número", "Numero", "Nro"
    )
    for raw in _values_by_aliases(extras, direct_aliases):
        if _is_missing_token(raw):
            continue
        altura = _digits_height(raw)
        if altura:
            return altura

    bag_aliases = {"address", "direccion", "domicilio", "Dirección", "Direccion"}
    for key, bag in extras.items():
        if _norm_alias_key(key) not in {_norm_alias_key(a) for a in bag_aliases}:
            continue
        if isinstance(bag, dict):
            for sub_k, sub_v in bag.items():
                if _norm_alias_key(sub_k) in {"altura", "height", "nro", "numero", "number"} and not _is_missing_token(sub_v):
                    altura = _digits_height(sub_v)
                    if altura:
                        return altura

    address_aliases = (
        "address", "direccion", "direction", "address_line", "address_full", "street", "dom", "dom_full",
        "domicilio", "domicilio_full", "Dirección", "Direccion"
    )
    chunks = _values_by_aliases(extras, address_aliases)
    address_text = " | ".join([c for c in chunks if not _is_missing_token(c)])
    if not address_text:
        return ""

    patterns = (
        r"/\s*ALT\s*/\s*([0-9]{1,6})",
        r"\bALT(?:URA)?\b[^0-9]{0,12}([0-9]{1,6})",
        r"\b(?:NRO|NUMERO|NUM)\b[^0-9]{0,12}([0-9]{1,6})",
    )
    for pattern in patterns:
        m = re.search(pattern, address_text, flags=re.IGNORECASE)
        if m and m.group(1):
            return _digits_height(m.group(1))
    return ""


def _extract_doc_from_result(row, extras):
    for val in (
        row.username,
        extras.get("doc"), extras.get("documento"), extras.get("document"), extras.get("dni"),
        extras.get("docNumber"), extras.get("documentNumber"), extras.get("document_number"),
    ):
        doc = _normalize_doc(val)
        if doc:
            return doc
    return ""


def _extract_sex_from_result(extras):
    for val in (
        extras.get("sex"), extras.get("sexo"), extras.get("gender"), extras.get("genero"),
        extras.get("genderTypeId"), extras.get("gender_type_id"), extras.get("genderType"),
        extras.get("other_data"), extras.get("v4_sex")
    ):
        sex = _normalize_sex(val)
        if sex:
            return sex
    return ""


def _extract_phone4_from_result(extras, usernames_numeric):
    keys = (
        "phone", "telefono", "mobile", "line", "linea", "celular",
        "phone_full", "v4_phone_last4", "phone_last4", "line_last4"
    )
    for key in keys:
        if key not in extras:
            continue
        if _is_missing_token(extras.get(key)):
            continue
        digits = re.sub(r"\D", "", str(extras.get(key)))
        if len(digits) >= 4:
            return digits[-4:]

    if usernames_numeric:
        first = str(usernames_numeric[0]).strip()
        digits = re.sub(r"\D", "", first)
        if len(digits) >= 4:
            return digits[:4]
    return ""


def _extract_address_text(extras):
    address_aliases = (
        "address", "direccion", "direction", "address_line", "address_full", "street", "dom", "dom_full",
        "domicilio", "domicilio_full", "Dirección", "Direccion"
    )
    chunks = _values_by_aliases(extras, address_aliases)

    cleaned = []
    seen = set()
    for value in chunks:
        text = str(value or "").strip()
        if not text or _is_missing_token(text):
            continue
        low = text.lower()
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(text)
    if not cleaned:
        return ""
    return " | ".join(cleaned[:3])


def _set_if_missing(extras, key, value):
    if value in (None, ""):
        return False
    current = extras.get(key)
    if current is None or _is_missing_token(current):
        extras[key] = value
        return True
    return False


def _merge_missing_fields(base, incoming, max_fields=MAX_EXTRA_FIELDS):
    out = _normalize_extra_fields(base, max_fields=max_fields)
    src = _normalize_extra_fields(incoming, max_fields=max_fields)
    changed = False
    for key, value in src.items():
        if key not in out:
            out[key] = value
            changed = True
            continue
        if _is_missing_token(out.get(key)) and not _is_missing_token(value):
            out[key] = value
            changed = True
    return _normalize_extra_fields(out, max_fields=max_fields), changed


def _compose_full_extras_for_username(username, primary_extras=None, max_fields=MAX_EXTRA_FIELDS):
    merged = _normalize_extra_fields(primary_extras, max_fields=max_fields)

    if not username:
        return merged

    result_rows = (
        Resultado.query
        .filter(Resultado.username == username)
        .order_by(Resultado.id.desc())
        .limit(40)
        .all()
    )
    for row in result_rows:
        merged, _ = _merge_missing_fields(merged, row.extra_fields, max_fields=max_fields)

    cred_rows = (
        Credencial.query
        .filter(Credencial.username == username)
        .order_by(Credencial.id.desc())
        .limit(20)
        .all()
    )
    for row in cred_rows:
        merged, _ = _merge_missing_fields(merged, row.extra_fields, max_fields=max_fields)

    hist_rows = (
        History.query
        .filter(History.username == username)
        .order_by(History.id.desc())
        .limit(20)
        .all()
    )
    for row in hist_rows:
        merged, _ = _merge_missing_fields(merged, row.extra_fields, max_fields=max_fields)

    return merged


def _hydrate_ready_fields(row, extra_fields, usernames_numeric, max_fields=MAX_EXTRA_FIELDS):
    extras = _normalize_extra_fields(extra_fields, max_fields=max_fields)
    changed = False

    doc = _extract_doc_from_result(row, extras)
    sex = _extract_sex_from_result(extras)
    altura = _extract_altura_from_extras(extras)
    phone4 = _extract_phone4_from_result(extras, usernames_numeric)
    address = _extract_address_text(extras)

    changed = _set_if_missing(extras, "doc", doc) or changed
    changed = _set_if_missing(extras, "v4_doc", doc) or changed
    changed = _set_if_missing(extras, "gender", sex) or changed
    changed = _set_if_missing(extras, "sex", sex) or changed
    changed = _set_if_missing(extras, "v4_sex", sex) or changed
    changed = _set_if_missing(extras, "altura", altura) or changed
    changed = _set_if_missing(extras, "v4_altura", altura) or changed
    changed = _set_if_missing(extras, "phone_last4", phone4) or changed
    changed = _set_if_missing(extras, "v4_phone_last4", phone4) or changed
    changed = _set_if_missing(extras, "address", address) or changed
    changed = _set_if_missing(extras, "v4_address", address) or changed

    if extras.get("usernames") != usernames_numeric:
        extras["usernames"] = usernames_numeric
        changed = True
    if extras.get("v4_usernames") != usernames_numeric:
        extras["v4_usernames"] = usernames_numeric
        changed = True

    return {
        "extras": extras,
        "changed": changed,
        "doc": doc,
        "sex": sex,
        "altura": altura,
        "phone4": phone4,
        "address": address,
        "usernames": usernames_numeric,
    }


def _project_result_for_ui(row, hydrate_ready=False, max_fields=MAX_EXTRA_FIELDS):
    row_extras = _normalize_extra_fields((row.extra_fields or {}), max_fields=max_fields)
    extras = _compose_full_extras_for_username(row.username, row_extras, max_fields=max_fields)
    usernames_numeric = _extract_numeric_usernames(extras)
    changed = extras != row_extras

    if hydrate_ready and usernames_numeric:
        hydrated = _hydrate_ready_fields(row, extras, usernames_numeric, max_fields=max_fields)
        extras = hydrated["extras"]
        changed = changed or hydrated["changed"]
        doc = hydrated["doc"]
        sex = hydrated["sex"]
        altura = hydrated["altura"]
        phone4 = hydrated["phone4"]
        address = hydrated["address"]
        usernames_numeric = hydrated["usernames"]
    else:
        doc = _extract_doc_from_result(row, extras)
        sex = _extract_sex_from_result(extras)
        altura = _extract_altura_from_extras(extras)
        phone4 = _extract_phone4_from_result(extras, usernames_numeric)
        address = _extract_address_text(extras)

    extras, generated_email, generated_password, changed_creds = _ensure_v4_credentials_for_result(
        row,
        extras,
        max_fields=max_fields,
    )
    changed = changed or changed_creds

    payload = {
        "id": row.id,
        "original_id": row.original_id,
        "username": row.username,
        "password": row.password,
        "status": row.result_status,
        "reviewed": row.reviewed or 0,
        "nombre": row.nombre,
        "doc": doc,
        "sex": sex,
        "altura": altura,
        "phone4": phone4,
        "address": address,
        "usernames_numeric": usernames_numeric,
        "generated_email": generated_email,
        "generated_password": generated_password,
        "extra_fields": extras,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }
    payload.update(_processing_payload(row))
    return payload, changed, extras


def _normalize_debt_status_for_results():
    """
    Reclasifica resultados success/used/lucas con reviewed en {3,5,NO_DATE_REVIEWED} según fecha de vencimiento:
    - due_date <= debt_cutoff -> reviewed=5 (deuda)
    - due_date > avail_min   -> reviewed=3 (disponible)
    - sin fecha              -> reviewed=NO_DATE_REVIEWED (por defecto 6)
    """
    debt_cutoff, avail_min = _get_cutoff_dates()
    rows = Resultado.query.filter(
        Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES),
        Resultado.reviewed.in_([3, 5, NO_DATE_REVIEWED]),
    ).all()
    changed = 0
    for row in rows:
        due = _extract_due_date_from_extras(row.extra_fields or {})
        if not due:
            if row.reviewed != NO_DATE_REVIEWED:
                row.reviewed = NO_DATE_REVIEWED
                changed += 1
            continue
        if due <= debt_cutoff:
            if row.reviewed != 5:
                row.reviewed = 5
                changed += 1
        elif due > avail_min:
            if row.reviewed != 3:
                row.reviewed = 3
                changed += 1
    if changed:
        db.session.commit()
    return changed


def _normalize_result_fields(payload, cred=None):
    """
    Normaliza alias comunes que llegan desde workers o front:
    - doc / documento / document -> doc
    - gender / genero / other_data -> gender
    - address / direccion / direction -> address
    - totalAmount / amount / monto / total -> totalAmount
    - paymentReference / payment_reference / reference / ref -> paymentReference
    """
    src = payload or {}
    extras = {}

    def _pick(*keys):
        for k in keys:
            val = src.get(k)
            if val not in (None, ""):
                return val
        return None

    doc_val = _pick("doc", "documento", "document")
    if not doc_val and cred is not None:
        doc_val = getattr(cred, "username", None)
    if doc_val:
        extras["doc"] = doc_val

    gender_val = _pick("gender", "genero", "other_data")
    if gender_val:
        extras["gender"] = gender_val

    address_val = _pick("address", "direccion", "direction")
    if address_val:
        extras["address"] = address_val

    amount_val = _pick("totalAmount", "amount", "monto", "total")
    if amount_val not in (None, ""):
        extras["totalAmount"] = amount_val

    pref_val = _pick("paymentReference", "payment_reference", "reference", "ref")
    if pref_val:
        extras["paymentReference"] = pref_val

    return extras


def _parse_dt(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _parse_due_date(value):
    """
    Intenta parsear fechas de vencimiento desde extra_fields.
    Formatos soportados: YYYY-MM-DD, DD/MM/YYYY, YYYY/MM/DD.
    Retorna datetime.date o None.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    return None


def _normalize_number(value):
    text = str(value or "").strip()
    if not text or not text.isdigit():
        return None
    return int(text)


def _chunked(seq, size=800):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _existing_usernames(usernames):
    if not usernames:
        return set()

    existing = set()
    for chunk in _chunked(usernames):
        cred_rows = db.session.query(Credencial.username).filter(Credencial.username.in_(chunk)).all()
        hist_rows = db.session.query(History.username).filter(History.username.in_(chunk)).all()
        existing.update(row[0] for row in cred_rows)
        existing.update(row[0] for row in hist_rows)
    return existing


def _existing_pool_email_lowers(email_lowers):
    if not email_lowers:
        return set()

    existing = set()
    for chunk in _chunked(email_lowers):
        rows = (
            db.session.query(EmailPoolAccount.email_lower)
            .filter(EmailPoolAccount.email_lower.in_(chunk))
            .all()
        )
        existing.update(row[0] for row in rows)
    return existing


def _string_or_none(value):
    text = str(value or "").strip()
    return text if text else None


def _normalize_optional_email_or_none(value):
    text = str(value or "").strip()
    if not text:
        return None
    norm = _normalize_pool_email_value(text)
    return norm if norm else ""


def _email_pool_account_to_dict(row):
    if not row:
        return {}
    return {
        "id": row.id,
        "email": row.email,
        "password": row.password,
        "recovery_email_1": row.recovery_email_1,
        "recovery_email_2": row.recovery_email_2,
        "recovery_code_1": row.recovery_code_1,
        "recovery_code_2": row.recovery_code_2,
        "extra_1": row.extra_1,
        "extra_2": row.extra_2,
        "extra_3": row.extra_3,
        "status": row.status,
        "provider": row.provider,
        "assigned_result_id": row.assigned_result_id,
        "assigned_username": row.assigned_username,
        "assigned_at": row.assigned_at.isoformat() if row.assigned_at else None,
        "used_at": row.used_at.isoformat() if row.used_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _sqlite_table_exists(conn, table_name):
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _sqlite_column_exists(conn, table_name, column_name):
    cur = conn.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cur.fetchall())


def _ensure_unified_schema_columns():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_pool_account (
                id INTEGER NOT NULL PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                email_lower VARCHAR(255) NOT NULL,
                password VARCHAR(255) NOT NULL,
                recovery_email_1 VARCHAR(255),
                recovery_email_2 VARCHAR(255),
                recovery_code_1 VARCHAR(255),
                recovery_code_2 VARCHAR(255),
                extra_1 VARCHAR(255),
                extra_2 VARCHAR(255),
                extra_3 VARCHAR(255),
                status VARCHAR(20) NOT NULL DEFAULT 'sin_uso',
                provider VARCHAR(40) NOT NULL DEFAULT 'other',
                assigned_result_id INTEGER,
                assigned_username VARCHAR(150),
                assigned_at DATETIME,
                used_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns_to_add = {
            "credencial": [
                ("extra_fields", "TEXT"),
                ("created_at", "DATETIME"),
                ("updated_at", "DATETIME"),
            ],
            "resultado": [
                ("extra_fields", "TEXT"),
                ("nombre", "TEXT"),
                ("processing_started_at", "DATETIME"),
                ("processing_expires_at", "DATETIME"),
                ("processing_owner", "TEXT"),
                ("processing_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("processing_return_state", "INTEGER"),
            ],
            "history": [
                ("extra_fields", "TEXT"),
            ],
            "email_pool_account": [
                ("email", "TEXT"),
                ("email_lower", "TEXT"),
                ("password", "TEXT"),
                ("recovery_email_1", "TEXT"),
                ("recovery_email_2", "TEXT"),
                ("recovery_code_1", "TEXT"),
                ("recovery_code_2", "TEXT"),
                ("extra_1", "TEXT"),
                ("extra_2", "TEXT"),
                ("extra_3", "TEXT"),
                ("status", "TEXT"),
                ("provider", "TEXT"),
                ("assigned_result_id", "INTEGER"),
                ("assigned_username", "TEXT"),
                ("assigned_at", "DATETIME"),
                ("used_at", "DATETIME"),
                ("created_at", "DATETIME"),
                ("updated_at", "DATETIME"),
            ],
        }
        for table_name, columns in columns_to_add.items():
            if not _sqlite_table_exists(conn, table_name):
                continue
            for column_name, column_def in columns:
                if not _sqlite_column_exists(conn, table_name, column_name):
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

        for table_name, column_name in (
            ("credencial", "extra_fields"),
            ("resultado", "extra_fields"),
            ("history", "extra_fields"),
        ):
            if _sqlite_table_exists(conn, table_name) and _sqlite_column_exists(conn, table_name, column_name):
                conn.execute(f"UPDATE {table_name} SET {column_name} = '{{}}' WHERE {column_name} IS NULL")

        if _sqlite_table_exists(conn, "credencial"):
            if _sqlite_column_exists(conn, "credencial", "created_at"):
                conn.execute("UPDATE credencial SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
            if _sqlite_column_exists(conn, "credencial", "updated_at"):
                conn.execute("UPDATE credencial SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL")
            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_credencial_username ON credencial(username)")
            except Exception:
                                                                               
                                                
                pass

        if _sqlite_table_exists(conn, "resultado"):
            if _sqlite_column_exists(conn, "resultado", "processing_attempts"):
                conn.execute(
                    "UPDATE resultado SET processing_attempts = 0 "
                    "WHERE processing_attempts IS NULL"
                )
            if (
                _sqlite_column_exists(conn, "resultado", "processing_started_at")
                and _sqlite_column_exists(conn, "resultado", "processing_expires_at")
                and _sqlite_column_exists(conn, "resultado", "processing_return_state")
            ):
                ttl_minutes = max(1, int(PROCESSING_TTL_MINUTES or 15))
                now_dt = datetime.now()
                now_text = now_dt.strftime("%Y-%m-%d %H:%M:%S")
                expires_text = (now_dt + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "UPDATE resultado "
                    "SET processing_started_at = COALESCE(processing_started_at, ?), "
                    "processing_expires_at = COALESCE(processing_expires_at, ?), "
                    "processing_return_state = COALESCE(processing_return_state, ?) "
                    "WHERE reviewed = 7",
                    (now_text, expires_text, PROCESSING_DEFAULT_RETURN_REVIEWED),
                )
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS ix_resultado_reviewed ON resultado(reviewed)")
            except Exception:
                pass
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_resultado_processing_expiry "
                    "ON resultado(reviewed, processing_expires_at)"
                )
            except Exception:
                pass

        if _sqlite_table_exists(conn, "email_pool_account"):
            if _sqlite_column_exists(conn, "email_pool_account", "email_lower"):
                conn.execute(
                    "UPDATE email_pool_account SET email_lower = lower(trim(email)) "
                    "WHERE (email_lower IS NULL OR trim(email_lower) = '') AND email IS NOT NULL"
                )
            if _sqlite_column_exists(conn, "email_pool_account", "status"):
                conn.execute(
                    "UPDATE email_pool_account SET status = 'sin_uso' "
                    "WHERE status IS NULL OR trim(status) = ''"
                )
            try:
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_pool_account_email_lower "
                    "ON email_pool_account(email_lower)"
                )
            except Exception:
                pass
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_email_pool_account_status "
                    "ON email_pool_account(status)"
                )
            except Exception:
                pass

        conn.commit()








                           
                                              
                           

def absorber_datos_y_limpiar_txt():
    """Importa data.txt al pool (si existe) y lo borra."""
    ruta = DATA_DIR / "data.txt"
    if not ruta.exists():
        return
    try:
        lines = ruta.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        print(f"[IMPORT] Error leyendo data.txt: {e}")
        return
    combos = set()
    nuevos_pool = []
    nuevos_history = []
    for line in lines:
        if not line or ":" not in line:
            continue
        user, pwd = line.split(":", 1)
        user = user.strip(); pwd = pwd.strip()
        if not user:
            continue
        if (user, pwd) in combos:
            continue
        if History.query.filter_by(username=user).first():
            continue
        combos.add((user, pwd))
        extras = {"doc": user, "gender": pwd}
        nuevos_pool.append(Credencial(username=user, password=pwd, status="generated", extra_fields=extras))
        nuevos_history.append(History(username=user, password=pwd, extra_fields=extras))
    try:
        if nuevos_pool:
            db.session.add_all(nuevos_history)
            db.session.add_all(nuevos_pool)
            db.session.commit()
            emit_dashboard_update()
        ruta.unlink(missing_ok=True)
    except Exception as e:
        db.session.rollback()
        print(f"[IMPORT] Error al guardar: {e}")


def get_stats_data(limit_results=MAX_DASH_RESULTS, limit_numbers=500, status_filter=None, page=1, page_size=0, review_filter=None):
    """
    Devuelve snapshot del dashboard.
    - limit_results se aplica por estado para no recortar los SUCCESS cuando hay muchos FAIL.
    - status_filter permite pedir solo un estado en particular (success, lucas, fail, none, error, used, all).
    """
                                          
    try:
        _release_expired_processing()
    except Exception:
        pass
    try:
        _normalize_debt_status_for_results()
    except Exception:
        pass
    try:
        pendientes = Credencial.query.filter(Credencial.status.in_(["generated", "pending"])).count()
        en_uso_list = Credencial.query.filter_by(status="in_use").all()
        workers_count = len(en_uso_list)
        current_processing = [c.username for c in en_uso_list if c.username]

                                      
        normalized = _normalize_status_input(status_filter, keep_unknown=True) or "all"
        status_map = {
            "success": list(_STANDARD_SUCCESS_RESULT_STATUSES),
            "used": list(_STANDARD_SUCCESS_RESULT_STATUSES),
            "lucas": ["lucas"],
            "fail": ["fail"],
            "none": ["none"],
            "error": ["error"],
            "all": ["success", "used", "lucas", "fail", "none", "error"],
        }
        if normalized not in status_map:
            normalized = "all"

        def fetch_results(statuses):
            base_query = (
                Resultado.query
                .filter(Resultado.result_status.in_(statuses))
                .order_by(Resultado.timestamp.desc())
            )

            if review_filter in _VALID_REVIEW_STATES:
                base_query = base_query.filter(Resultado.reviewed == review_filter)

            total_items = base_query.count()

                                                                                                           
            effective_page_size = int(page_size) if page_size and int(page_size) > 0 else 0
            if effective_page_size <= 0 and limit_results and int(limit_results) > 0:
                effective_page_size = int(limit_results)

            current_page = max(1, int(page)) if effective_page_size > 0 else 1
            offset = (current_page - 1) * effective_page_size if effective_page_size > 0 else 0

            query = base_query
            if effective_page_size > 0:
                query = query.offset(offset).limit(effective_page_size)
            elif limit_results and int(limit_results) > 0:
                query = query.limit(max(1, int(limit_results)))

            items = query.all()

            total_pages = 1
            if effective_page_size > 0:
                total_pages = max(1, math.ceil(total_items / effective_page_size))

            return items, {
                "total_items": total_items,
                "page": current_page,
                "page_size": effective_page_size if effective_page_size > 0 else len(items),
                "total_pages": total_pages,
            }

        pagination = {"total_items": 0, "page": 1, "page_size": len(status_map.get(normalized, [])), "total_pages": 1}

        if normalized == "all":
            combined = []
                                                                                                               
                                                                                                             
            success_items, pagination = fetch_results(list(_STANDARD_SUCCESS_RESULT_STATUSES))
            combined.extend(success_items)
            for sts in [["lucas"], ["fail"], ["none"], ["error"]]:
                items, _ = fetch_results(sts)
                combined.extend(items)
            resultados_query = combined
        else:
            resultados_query, pagination = fetch_results(status_map[normalized])

        lista_resultados = []
        for r in resultados_query:
            ts = r.timestamp
            item = {
                "id": r.id,
                "original_id": r.original_id,
                "username": r.username,
                "password": r.password,
                "status": r.result_status,
                "reviewed": r.reviewed if r.reviewed is not None else 0,
                "extra_fields": _normalize_extra_fields(r.extra_fields),
                "timestamp": ts.strftime("%H:%M:%S") if ts else "--:--",
                "_ts": ts or datetime.min,
            }
            item.update(_processing_payload(r))
            lista_resultados.append(item)

                                                                            
        lista_resultados.sort(key=lambda x: x["_ts"], reverse=True)
        for r in lista_resultados:
            r.pop("_ts", None)

                                             
        numeros_recientes = []
        if limit_numbers and int(limit_numbers) > 0:
            numeros_query = (
                Credencial.query
                .filter(Credencial.status.in_(["generated", "pending", "in_use"]))
                .order_by(Credencial.id.desc())
                .limit(max(1, int(limit_numbers)))
                .all()
            )
            for c in numeros_query:
                ts = c.updated_at or c.created_at
                numeros_recientes.append({
                    "id": c.id,
                    "number": c.username,
                    "status": c.status,
                    "timestamp": ts.strftime("%H:%M:%S") if ts else "--:--",
                    "extra_fields": _normalize_extra_fields(c.extra_fields),
                })

        usados = Credencial.query.filter_by(status="used").count()
        lucas = Credencial.query.filter_by(status="lucas").count()
        fallidos = Credencial.query.filter_by(status="fail").count()
        nulos = Credencial.query.filter_by(status="none").count()
        errores = Credencial.query.filter_by(status="error").count()
        return {
            "stats": {
                "pendientes": pendientes,
                "en_proceso_count": workers_count,
                "en_proceso_nombres": current_processing,
                "usados": usados,
                "lucas": lucas,
                "fallidos": fallidos,
                "none": nulos,
                "errores": errores,
                "workers_conectados": workers_count,
                "alertas_recientes": LAST_ERRORS,
            },
            "results": lista_resultados,
            "numbers_recent": numeros_recientes,
            "pagination": pagination,
        }
    except Exception as e:
        print(f"[ERROR STATS] {e}")
        return None
def emit_dashboard_update():
                                                          
    data = get_stats_data(limit_results=0, limit_numbers=0, status_filter="success", page=1, page_size=40)
    if data:
        socketio.emit("dashboard_update", data)



def background_maintenance_tasks():
    with app.app_context():
        while True:
            try:
                try:
                    released_processing = _release_expired_processing()
                    if released_processing:
                        emit_dashboard_update()
                except Exception as e_proc:
                    print(f"[BG TASK] release_processing error: {e_proc}")

                try:
                    _normalize_debt_status_for_results()
                except Exception as e_norm:
                    print(f"[BG TASK] normalize_debt_status error: {e_norm}")

                absorber_datos_y_limpiar_txt()

                                           
                timeout_limit = datetime.now() - timedelta(minutes=2)
                zombies = Credencial.query.filter(
                    Credencial.status == "in_use",
                    Credencial.assigned_at < timeout_limit,
                ).all()

                if zombies:
                    count = 0
                    for z in zombies:
                        z.status = "generated"
                        z.assigned_at = None
                        count += 1

                    db.session.commit()
                    if count > 0:
                        emit_dashboard_update()

            except Exception as e:
                print(f"[BG ERROR] {e}")
                db.session.rollback()

            socketio.sleep(2)


def verificar_limpieza_pool():
                                                                    
    return

                           
            
                           
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(STATIC_DIR, "favicon.ico", mimetype="image/vnd.microsoft.icon")


@app.route("/")
def obtener_trabajo():
    try:
        candidate = (
            Credencial.query
            .filter(Credencial.status.in_(["generated", "pending"]))
            .order_by(Credencial.id.asc())
            .first()
        )
    except OperationalError:
        return jsonify({"status": "error", "message": "DB Error"}), 500

    if not candidate:
        return jsonify({"status": "empty_pool", "message": "Esperando..."}), 200

                          
    timestamp_now = datetime.now()
    Credencial.query.filter(Credencial.id == candidate.id).update({
        "status": "in_use",
        "assigned_at": timestamp_now,
    })
    db.session.commit()
    emit_dashboard_update()


    return jsonify({
        "status": "job_found",
        "id": candidate.id,
        "extra_fields": candidate.extra_fields or {},
        "us": candidate.username,
        "pwd": candidate.password
    })
def obtener_trabajox():
    try:
        candidate = (
            Credencial.query
            .filter(Credencial.status.in_(["generated", "pending"]))
            .order_by(Credencial.id.asc())
            .first()
        )
    except OperationalError:
        return jsonify({"status": "error", "message": "DB Error"}), 500

    if not candidate:
        return jsonify({"status": "empty_pool", "message": "Esperando..."}), 200

    timestamp_now = datetime.now()
    rows = Credencial.query.filter(
        Credencial.id == candidate.id,
        Credencial.status.in_(["generated", "pending"]),
    ).update(
        {
            "status": "in_use",
            "assigned_at": timestamp_now,
        }
    )
    db.session.commit()

    if rows == 0:
        return obtener_trabajo()

    emit_dashboard_update()

    u_b64 = base64.b64encode(candidate.username.encode()).decode()
    p_b64 = base64.b64encode(candidate.password.encode()).decode()
    base_target = url_for("static", filename=JOB_PAGE, _external=True)
    target_url = f"{base_target}?username={u_b64}&password={p_b64}&id={candidate.id}"

    return jsonify(
        {
            "status": "job_found",
            "url": target_url,
            "id": candidate.id,
            "extra_fields": candidate.extra_fields or {},
        }
    )


@app.route("/update_status", methods=["POST"])
def update_status():
    data = request.json or {}
    cid = data.get("id")
    status = _normalize_status_input(data.get("status"))

    if not cid:
        return jsonify({"error": "Falta ID"}), 400

    status_map = {
        "success": "used",
        "used": "used",
        "lucas": "lucas",
        "fail": "fail",
        "none": "none",
        "error": "error",
    }
    if status not in status_map:
        return jsonify({"error": "Status invalido. Usa: success|lucas|used|fail|none|error"}), 400

    try:
        cred = db.session.get(Credencial, cid)
        if not cred:
            return jsonify({"error": "No encontrada"}), 404

        final_status = status_map[status]
        
                                                                   
        payload_reviewed = data.get("reviewed")
        if payload_reviewed is None and "extra_fields" in data:
            payload_reviewed = data["extra_fields"].get("reviewed")
            
        try:
            reviewed_val = int(payload_reviewed) if payload_reviewed is not None else 0
        except ValueError:
            reviewed_val = 0

        payload_extras = _extract_extra_fields(data, reserved_keys={"id", "status", "reviewed"})
        normalized_extras = _normalize_result_fields(data, cred)
        cred.extra_fields = _merge_extra_fields(cred.extra_fields, normalized_extras, payload_extras)
        cred.status = final_status
        cred.assigned_at = None

                                                
        nombre_extraido = None
        if final_status in {"used", "lucas"}:
                                                       
            dni_afip = _extract_doc_from_result(cred, cred.extra_fields)
            sex_afip = _extract_sex_from_result(cred.extra_fields)
            if dni_afip and sex_afip:
                                         
                genero_map = {"1": "M", "2": "F"}
                genero_afip = genero_map.get(sex_afip)
                if genero_afip:
                                                                   
                    nombre_extraido = get_nombre(dni_afip, genero_afip)

        res = Resultado(
            original_id=cred.id,
            username=cred.username,
            password=cred.password,
            result_status=final_status,
            reviewed=reviewed_val,
            nombre=nombre_extraido,
            extra_fields=cred.extra_fields or {},
        )
        db.session.add(res)
        db.session.commit()
        _normalize_debt_status_for_results()
        emit_dashboard_update()
        verificar_limpieza_pool()

        return jsonify({"msg": "OK", "final_status": final_status}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/requeue", methods=["POST"])
def api_requeue():
    """
    Marca credenciales procesadas como disponibles nuevamente.
    - id / cred_id: requeue una credencial puntual.
    - result_id: requeue segun la fila de resultados asociada.
    - all: True => requeue masivo para estados procesados (used, lucas, fail, none, error por defecto).
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.json or {}
    target_status = str(data.get("reset_to", "generated")).strip().lower() or "generated"
    if target_status not in {"generated", "pending"}:
        return jsonify({"error": "reset_to invalido. Usa: generated|pending"}), 400

    def _normalize_status_list(raw):
        allowed = {"generated", "pending", "in_use", "used", "lucas", "fail", "none", "error", "success"}
        if raw is None:
            return []
        if isinstance(raw, str):
            raw_list = [raw]
        elif isinstance(raw, (list, tuple, set)):
            raw_list = list(raw)
        else:
            return []
        normalized = []
        for item in raw_list:
            status_value = _normalize_status_input(item, keep_unknown=True)
            if status_value in allowed:
                normalized.append(status_value)
        return normalized

    default_statuses = ["success", "used", "lucas", "fail", "none", "error"]
    raw_statuses = data.get("only_statuses") or data.get("statuses")
    statuses_filter = _normalize_status_list(raw_statuses)
    if raw_statuses is not None and not statuses_filter:
        return jsonify({"error": "only_statuses invalido"}), 400
    if not statuses_filter:
        statuses_filter = default_statuses

    cred_id_raw = data.get("cred_id") or data.get("id")
    result_id_raw = data.get("result_id")
    ids_list = data.get("ids")
    apply_all = bool(data.get("all"))

    try:
        cred_id = int(cred_id_raw) if cred_id_raw is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "id invalido"}), 400

    try:
        result_id = int(result_id_raw) if result_id_raw is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "result_id invalido"}), 400

    updated_ids = []

    try:
        if cred_id:
            cred = db.session.get(Credencial, cred_id)
            if not cred:
                return jsonify({"error": "credencial no encontrada"}), 404
            cred.status = target_status
            cred.assigned_at = None
            updated_ids.append(cred.id)

        elif ids_list:
            if not isinstance(ids_list, (list, tuple, set)):
                return jsonify({"error": "ids debe ser lista"}), 400
            ids_normalized = []
            for item in ids_list:
                try:
                    val = int(item)
                except (TypeError, ValueError):
                    continue
                if val > 0:
                    ids_normalized.append(val)

            if not ids_normalized:
                return jsonify({"error": "ids vacio"}), 400

            creds = Credencial.query.filter(Credencial.id.in_(ids_normalized)).all()
            if not creds:
                return jsonify({"error": "credenciales no encontradas"}), 404
            for cred in creds:
                cred.status = target_status
                cred.assigned_at = None
                updated_ids.append(cred.id)

        elif result_id:
            res = db.session.get(Resultado, result_id)
            if not res:
                return jsonify({"error": "resultado no encontrado"}), 404

            cred = None
            if res.original_id:
                cred = db.session.get(Credencial, res.original_id)
            if not cred and res.username:
                cred = Credencial.query.filter_by(username=res.username).first()

            if not cred:
                return jsonify({"error": "credencial asociada no encontrada"}), 404

            cred.status = target_status
            cred.assigned_at = None
            updated_ids.append(cred.id)

        elif apply_all:
            creds = Credencial.query.filter(Credencial.status.in_(statuses_filter)).all()
            if not creds:
                return jsonify(
                    {
                        "status": "ok",
                        "updated": 0,
                        "message": "No hubo credenciales para reciclar con los estados dados.",
                    }
                ), 200

            for cred in creds:
                cred.status = target_status
                cred.assigned_at = None
                updated_ids.append(cred.id)

        else:
            return jsonify({"error": "Falta id, result_id o all=true"}), 400

                                                                                          
        if updated_ids:
            Resultado.query.filter(
                Resultado.original_id.in_(updated_ids),
                Resultado.result_status.in_(statuses_filter),
            ).update({"result_status": "requeued"}, synchronize_session=False)

        if result_id:
            res_target = db.session.get(Resultado, result_id)
            if res_target:
                res_target.result_status = "requeued"

        db.session.commit()
        if updated_ids:
            emit_dashboard_update()

        return (
            jsonify(
                {
                    "status": "ok",
                    "reset_to": target_status,
                    "updated": len(updated_ids),
                    "ids": updated_ids,
                }
            ),
            200,
        )

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/result_review", methods=["POST"])
def api_result_review():
    """
    Marca un resultado como revisado / no revisado / disponible.
    Body: { "result_id": int, "reviewed": bool, "review_state": int|str }
    review_state: 0=sin revisar, 1=revisado legacy, 3=revisado, 4=listo, 5=deuda, 6=sin fecha, 7=procesando, 8=validado, 9=registrar, 10=fallo add, 11=fall nun, 12=fallo total, 13=registrado, 14=bueno, 15=re_registrar
    (acepta 2 como alias de disponible).
    Strings válidos: "none","pending","unreviewed","revisado","reviewed","done",
    "disponible","available","listo","closed","cerrado","deuda","debt","sin_fecha","no_date","procesando","processing","validado","validated","registrar","register","bueno","good","re_registrar","reregistrar"
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.json or {}
    rid = data.get("result_id")
    reviewed_raw = data.get("reviewed")
    state_raw = data.get("review_state")
    processing_owner = data.get("processing_owner") or data.get("owner") or data.get("claimed_by")
    processing_return_state = data.get("processing_return_state")

    try:
        rid_int = int(rid)
    except (TypeError, ValueError):
        return jsonify({"error": "result_id invalido"}), 400
                       
    review_state = _normalize_review_state_value(
        state_raw if state_raw is not None else reviewed_raw,
        allow_none=False,
        bool_legacy=True,
    )

    try:
        res = db.session.get(Resultado, rid_int)
        if not res:
            return jsonify({"error": "resultado no encontrado"}), 404
        _apply_review_state(
            res,
            review_state,
            owner=processing_owner,
            return_state=processing_return_state,
        )
        pool_sync = _sync_email_pool_for_result(res, reviewed=review_state)
        db.session.commit()
        emit_dashboard_update()
        payload = {
            "status": "ok",
            "id": rid_int,
            "reviewed": res.reviewed,
            "email_pool_id": pool_sync.get("pool_id"),
            "email_pool_status": pool_sync.get("pool_status"),
        }
        payload.update(_processing_payload(res))
        return jsonify(payload), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/result_review/by_username", methods=["POST"])
def api_result_review_by_username():
    """
    Actualiza resultados filtrando por username/DNI sin requerir ID.
    Campos soportados:
      - username (str, obligatorio)
      - apply_all (bool): si true actualiza todos los matches; default solo el mas reciente.
      - prefer_queue (bool): default true. Si apply_all=false, prioriza fila success/used/lucas.
      - where_result_status (str|list): filtra por estado(s) antes de actualizar.
      - review_state / reviewed: mismos aliases que /api/result_review (0,1,3,4,5,6,7,8,9,10,11,12,13,14,15; 2==3).
      - result_status (str): nuevo estado logico (success/fail/used/lucas/none/custom).
      - extra_fields (obj): se fusiona; replace_extra_fields=true para sobrescribir.
      - touch_timestamp (bool): si true actualiza timestamp a ahora.
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username requerido"}), 400

    def _as_bool(v, default=False):
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in {"1", "true", "yes", "si", "on"}:
            return True
        if s in {"0", "false", "no", "off"}:
            return False
        return default

    apply_all = _as_bool(data.get("apply_all", False), default=False)
    prefer_queue = _as_bool(data.get("prefer_queue", True), default=True)
    sync_registered_all = _as_bool(data.get("sync_registered_all", True), default=True)
    replace_extra = _as_bool(data.get("replace_extra_fields", False), default=False)
    touch_ts = _as_bool(data.get("touch_timestamp", False), default=False)
    where_result_status_raw = data.get("where_result_status")
    review_state_raw = data.get("review_state")
    reviewed_raw = data.get("reviewed")
    processing_owner = data.get("processing_owner") or data.get("owner") or data.get("claimed_by")
    processing_return_state = data.get("processing_return_state")
    result_status = _normalize_status_input(data.get("result_status"), keep_unknown=True) if data.get("result_status") is not None else None
    new_extras = data.get("extra_fields") if isinstance(data.get("extra_fields"), dict) else None

    def _normalize_status_filter(raw_val):
        if raw_val is None:
            return []
        values = raw_val if isinstance(raw_val, (list, tuple, set)) else [raw_val]
        out = []
        for v in values:
            s = _normalize_status_input(v, keep_unknown=True)
            if not s:
                continue
            if s not in out:
                out.append(s)
        return out

    where_result_statuses = _normalize_status_filter(where_result_status_raw)

    new_review_state = _normalize_review_state_value(
        review_state_raw if review_state_raw is not None else reviewed_raw,
        allow_none=True,
        bool_legacy=True,
    )
    force_registered_sync = bool(
        sync_registered_all
        and (new_review_state == 13)
        and (result_status is None)
        and (not where_result_statuses)
        and (not apply_all)
    )

    try:
        base_by_user = Resultado.query.filter(Resultado.username == username)
        base_q = base_by_user
        if where_result_statuses:
            base_q = base_q.filter(Resultado.result_status.in_(where_result_statuses))

        registered_result_status = "success"
        if new_review_state == 13 and result_status is None:
            latest_positive = (
                base_by_user
                .filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
                .order_by(Resultado.id.desc())
                .first()
            )
            if latest_positive and latest_positive.result_status:
                registered_result_status = str(latest_positive.result_status).strip().lower() or "success"

        if apply_all:
            rows = base_q.order_by(Resultado.id.desc()).all()
        elif force_registered_sync:
                                                                                                                 
            rows = base_by_user.order_by(Resultado.id.desc()).all()
        else:
            picked = None
            if where_result_statuses:
                picked = base_q.order_by(Resultado.id.desc()).first()
            elif prefer_queue:
                picked = (
                    base_by_user
                    .filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
                    .order_by(Resultado.id.desc())
                    .first()
                )
            if not picked:
                picked = base_q.order_by(Resultado.id.desc()).first()
            rows = [picked] if picked else []

        if not rows:
            return jsonify({"error": "not_found", "username": username}), 404

        updated = []
        for row in rows:
            if result_status:
                row.result_status = str(result_status).strip().lower()
            elif new_review_state == 13:
                                                                                                       
                current_status = str(row.result_status or "").strip().lower()
                if current_status not in _POSITIVE_RESULT_STATUSES:
                    row.result_status = registered_result_status
            if new_review_state is not None:
                _apply_review_state(
                    row,
                    new_review_state,
                    owner=processing_owner,
                    return_state=processing_return_state,
                )
            if new_extras:
                if replace_extra:
                    row.extra_fields = new_extras
                else:
                    row.extra_fields = _merge_extra_fields(new_extras, row.extra_fields or {})
            if touch_ts:
                row.timestamp = datetime.now(timezone.utc)

            pool_sync = {"pool_id": None, "pool_status": None}
            if new_review_state is not None:
                pool_sync = _sync_email_pool_for_result(row, reviewed=new_review_state)

            item = {
                "id": row.id,
                "username": row.username,
                "reviewed": row.reviewed,
                "result_status": row.result_status,
                "email_pool_id": pool_sync.get("pool_id"),
                "email_pool_status": pool_sync.get("pool_status"),
                "extra_fields": row.extra_fields or {}
            }
            item.update(_processing_payload(row))
            updated.append(item)

        db.session.commit()
        emit_dashboard_update()
        return jsonify({"status": "ok", "count": len(updated), "items": updated}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail/otp", methods=["POST"])
def api_mail_otp():
    """
    Busca el OTP más reciente para un correo reenviado a Gmail.
    Body JSON:
      {
        "email": "codex@correo.com"
      }
    o:
      {
        "local_part": "codex"
      }
    Respuesta inmediata:
      {
        "status": "ok",
        "email": "codex@correo.com",
        "code": "787130",
        "imap_uid": "12345",
        "subject": "Registro de usuario",
        "from": "Personal <noreplyru@idp.personal.com.ar>",
        "date": "Wed, 4 Mar 2026 15:44:16 -0300 (ART)",
        "purge_started": false
      }
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    target_email = _normalize_target_email(
        email_value=data.get("email"),
        local_part=data.get("local_part"),
    )
    if not target_email:
        return jsonify({"error": "email o local_part requerido"}), 400
    try:
        otp_data = _fetch_latest_otp_email(target_email)
        if not otp_data:
            return jsonify({"error": "otp_not_found", "email": target_email}), 404
        return jsonify({"status": "ok", **otp_data, "purge_started": False}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/mail/otp/consume", methods=["POST"])
def api_mail_otp_consume():
    """
    Borra el correo OTP ya utilizado.
    Body JSON:
      {
        "imap_uid": "12345"
      }
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    imap_uid = str(data.get("imap_uid") or "").strip()
    if not imap_uid:
        return jsonify({"error": "imap_uid requerido"}), 400

    try:
        deleted = _delete_email_by_uid(imap_uid)
        if not deleted:
            return jsonify({"error": "delete_failed", "imap_uid": imap_uid}), 404
        return jsonify({"status": "ok", "deleted": True, "imap_uid": imap_uid}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate_consecutive", methods=["POST"])
def api_generate_consecutive():
    data = request.json or {}
    base_number = _normalize_number(data.get("base_number"))
    count = _normalize_number(data.get("count"))

    if base_number is None:
        return jsonify({"error": "base_number invalido. Debe ser numerico."}), 400
    if count is None or count <= 0:
        return jsonify({"error": "count invalido. Debe ser un entero > 0."}), 400
    if count > MAX_GENERATE_COUNT:
        return jsonify({"error": f"count excede el maximo permitido ({MAX_GENERATE_COUNT})."}), 400

                                                           
    if len(str(base_number + 1)) < 7:
        return jsonify({"error": "base_number debe producir usuarios de al menos 7 caracteres."}), 400

    candidates = [str(base_number + i) for i in range(1, count + 1)]
    existing = _existing_usernames(candidates)
    extras_from_payload = _extract_extra_fields(data, reserved_keys={"base_number", "count"})

    nuevos_pool = []
    nuevos_history = []
    for number in candidates:
        if number in existing:
            continue
        extra_fields = _merge_extra_fields(
            extras_from_payload,
            {
                "number": number,
                "generated_from_base": str(base_number),
                "sequence_mode": "consecutive_plus_one",
            },
        )
        nuevos_pool.append(
            Credencial(
                username=number,
                password=number,
                status="generated",
                extra_fields=extra_fields,
            )
        )
        nuevos_history.append(
            History(
                username=number,
                password=number,
                extra_fields=extra_fields,
            )
        )

    created = len(nuevos_pool)
    skipped = len(candidates) - created

    try:
        if nuevos_pool:
            db.session.add_all(nuevos_history)
            db.session.add_all(nuevos_pool)
            db.session.commit()
            emit_dashboard_update()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Se detectaron duplicados durante la insercion."}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "status": "ok",
            "base_number": str(base_number),
            "requested_count": count,
            "created": created,
            "skipped_duplicates": skipped,
            "range": {
                "from": str(base_number + 1),
                "to": str(base_number + count),
            },
        }
    ), 200


@app.route("/api/import_custom_list", methods=["POST"])
def api_import_custom_list():
    data = request.json or {}
    raw_list = data.get("list_text")
    
                                     
    if raw_list is None:
        return jsonify({"error": "list_text requerido"}), 400
    if not isinstance(raw_list, str):
        return jsonify({"error": "list_text debe ser texto"}), 400

    lines = raw_list.splitlines()
    if len(lines) > MAX_GENERATE_COUNT:
        return jsonify({"error": f"La lista excede el maximo permitido ({MAX_GENERATE_COUNT} lineas)."}), 400

    extras_from_payload = _extract_extra_fields(data, reserved_keys={"list_text"})
    parsed_by_username = {}                                                                        
    invalid_lines = 0
    duplicated_in_payload = 0

                                                         
    for line in lines:
        raw = (line or "").strip()
        if not raw:
            continue

                                                     
                                       
        username_part, sep, other_part = raw.partition(":")
        
                                      
        username_str = username_part.strip()
        
                                                     
        if (not username_str) or len(username_str) < 7:
            invalid_lines += 1
            continue

                                                                   
        if username_str in parsed_by_username:
            duplicated_in_payload += 1
            continue

        other_data = other_part.strip() if sep else ""
        parsed_by_username[username_str] = other_data

    if not parsed_by_username:
        return jsonify({"error": "No hay lineas validas para importar."}), 400

                                                             
    existing = _existing_usernames(list(parsed_by_username.keys()))

    nuevos_pool = []
    nuevos_history = []
    
                                      
    for username, other_data in parsed_by_username.items():
        if username in existing:
            continue

                                                                                    
        pass_value = other_data if other_data else username
        
        extra_fields = _merge_extra_fields(
            extras_from_payload,
            {
                "input_username": username,
                "other_data": other_data,
                "source": "custom_list_text",
                "input_format": "user:pass",
                "doc": username,
                "gender": other_data,
            },
        )

        nuevos_pool.append(
            Credencial(
                username=username,
                password=pass_value,
                status="generated",
                extra_fields=extra_fields,
            )
        )
        nuevos_history.append(
            History(
                username=username,
                password=pass_value,
                extra_fields=extra_fields,
            )
        )

    created = len(nuevos_pool)
    skipped_existing = len(parsed_by_username) - created

    try:
        if nuevos_pool:
            db.session.add_all(nuevos_history)
            db.session.add_all(nuevos_pool)
            db.session.commit()
            emit_dashboard_update()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Se detectaron duplicados durante la insercion."}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "status": "ok",
            "total_lines": len(lines),
            "valid_unique_items": len(parsed_by_username),
            "created": created,
            "skipped_existing": skipped_existing,
            "invalid_lines": invalid_lines,
            "duplicated_in_payload": duplicated_in_payload,
        }
    ), 200


@app.route("/api/email_pool/import", methods=["POST"])
def api_email_pool_import():
    """
    Importa cuentas de correo al pool.
    Formatos soportados por línea:
      - email|password
      - email:password
      - email|password|recovery_email_1|recovery_email_2|recovery_code_1|recovery_code_2|extra_1|extra_2|extra_3
    """
    data = request.get_json(silent=True) or {}
    raw_list = data.get("list_text")
    if raw_list is None:
        raw_list = data.get("emails")

    if isinstance(raw_list, list):
        raw_list = "\n".join(str(item or "") for item in raw_list)

    if raw_list is None:
        return jsonify({"error": "list_text o emails requerido"}), 400
    if not isinstance(raw_list, str):
        return jsonify({"error": "list_text/emails debe ser texto o lista"}), 400

    lines = raw_list.splitlines()
    if len(lines) > MAX_GENERATE_COUNT:
        return jsonify({"error": f"La lista excede el maximo permitido ({MAX_GENERATE_COUNT} lineas)."}), 400

    default_status = _normalize_email_pool_status(data.get("status") or "sin_uso")
    if default_status not in {"sin_uso", "pendiente", "usado", "fallo"}:
        return jsonify({"error": "status invalido. Usa: sin_uso|pendiente|usado|fallo"}), 400

    parsed_by_email = {}
    invalid_lines = 0
    duplicated_in_payload = 0

    for line in lines:
        raw = str(line or "").strip()
        if not raw:
            continue

        recovery_email_1 = None
        recovery_email_2 = None
        recovery_code_1 = None
        recovery_code_2 = None
        extra_1 = None
        extra_2 = None
        extra_3 = None

        if "|" in raw:
            parts = [str(p or "").strip() for p in raw.split("|")]
            if len(parts) < 2:
                invalid_lines += 1
                continue
            email_part = parts[0]
            password_part = parts[1]
            recovery_email_1 = parts[2] if len(parts) > 2 else None
            recovery_email_2 = parts[3] if len(parts) > 3 else None
            recovery_code_1 = parts[4] if len(parts) > 4 else None
            recovery_code_2 = parts[5] if len(parts) > 5 else None
            extra_1 = parts[6] if len(parts) > 6 else None
            extra_2 = parts[7] if len(parts) > 7 else None
            extra_3 = "|".join(parts[8:]).strip() if len(parts) > 8 else None
        elif ":" in raw:
            email_part, _, password_part = raw.partition(":")
        else:
            invalid_lines += 1
            continue

        email_norm = _normalize_pool_email_value(email_part)
        pass_value = str(password_part or "").strip()
        if not email_norm or not pass_value:
            invalid_lines += 1
            continue

        rec_email_1_norm = _normalize_optional_email_or_none(recovery_email_1)
        if rec_email_1_norm == "":
            invalid_lines += 1
            continue
        rec_email_2_norm = _normalize_optional_email_or_none(recovery_email_2)
        if rec_email_2_norm == "":
            invalid_lines += 1
            continue

        email_lower = email_norm.lower()
        if email_lower in parsed_by_email:
            duplicated_in_payload += 1
            continue

        parsed_by_email[email_lower] = {
            "email": email_norm,
            "password": pass_value,
            "provider": _email_pool_provider_from_email(email_norm),
            "recovery_email_1": rec_email_1_norm,
            "recovery_email_2": rec_email_2_norm,
            "recovery_code_1": _string_or_none(recovery_code_1),
            "recovery_code_2": _string_or_none(recovery_code_2),
            "extra_1": _string_or_none(extra_1),
            "extra_2": _string_or_none(extra_2),
            "extra_3": _string_or_none(extra_3),
        }

    if not parsed_by_email:
        return jsonify({"error": "No hay lineas validas para importar."}), 400

    existing = _existing_pool_email_lowers(list(parsed_by_email.keys()))
    new_rows = []
    now = datetime.now()

    for email_lower, payload in parsed_by_email.items():
        if email_lower in existing:
            continue
        assigned_at = now if default_status == "pendiente" else None
        used_at = now if default_status == "usado" else None
        new_rows.append(
            EmailPoolAccount(
                email=payload["email"],
                email_lower=email_lower,
                password=payload["password"],
                recovery_email_1=payload["recovery_email_1"],
                recovery_email_2=payload["recovery_email_2"],
                recovery_code_1=payload["recovery_code_1"],
                recovery_code_2=payload["recovery_code_2"],
                extra_1=payload["extra_1"],
                extra_2=payload["extra_2"],
                extra_3=payload["extra_3"],
                status=default_status,
                provider=payload["provider"],
                assigned_at=assigned_at,
                used_at=used_at,
            )
        )

    created = len(new_rows)
    skipped_existing = len(parsed_by_email) - created

    try:
        if new_rows:
            db.session.add_all(new_rows)
            db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Se detectaron duplicados durante la insercion."}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "status": "ok",
            "default_status": default_status,
            "total_lines": len(lines),
            "valid_unique_items": len(parsed_by_email),
            "created": created,
            "skipped_existing": skipped_existing,
            "invalid_lines": invalid_lines,
            "duplicated_in_payload": duplicated_in_payload,
        }
    ), 200


@app.route("/api/email_pool", methods=["GET"])
def api_email_pool():
    try:
        limit = request.args.get("limit", default=50, type=int)
        limit = max(1, min(limit, 200))
        only_status = _normalize_email_pool_status(request.args.get("status"), keep_unknown=False)

        base_query = EmailPoolAccount.query
        if only_status:
            base_query = base_query.filter(EmailPoolAccount.status == only_status)

        rows = (
            base_query
            .order_by(EmailPoolAccount.id.desc())
            .limit(limit)
            .all()
        )

        total = EmailPoolAccount.query.count()
        sin_uso = EmailPoolAccount.query.filter_by(status="sin_uso").count()
        pendiente = EmailPoolAccount.query.filter_by(status="pendiente").count()
        usado = EmailPoolAccount.query.filter_by(status="usado").count()
        fallo = EmailPoolAccount.query.filter_by(status="fallo").count()

        items = [_email_pool_account_to_dict(row) for row in rows]
        return jsonify(
            {
                "status": "ok",
                "counts": {
                    "total": total,
                    "sin_uso": sin_uso,
                    "pendiente": pendiente,
                    "usado": usado,
                    "fallo": fallo,
                },
                "items": items,
            }
        ), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/email_pool/by_email", methods=["GET"])
def api_email_pool_by_email():
    email_raw = request.args.get("email", type=str)
    if email_raw is None:
        return jsonify({"error": "email requerido"}), 400

    email_norm = _normalize_pool_email_value(email_raw)
    if not email_norm:
        return jsonify({"error": "email invalido"}), 400

    try:
        row = EmailPoolAccount.query.filter_by(email_lower=email_norm.lower()).first()
        if not row:
            return jsonify({"error": "not_found"}), 404

        return jsonify(
            {
                "status": "ok",
                "item": _email_pool_account_to_dict(row),
            }
        ), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/email_pool/status", methods=["POST"])
def api_email_pool_status():
    data = request.get_json(silent=True) or {}
    account_id_raw = data.get("id", data.get("email_pool_id"))
    status = _normalize_email_pool_status(data.get("status"))
    result_id = data.get("result_id")
    username = (data.get("username") or "").strip() or None

    if account_id_raw is None:
        return jsonify({"error": "id requerido"}), 400
    try:
        account_id = int(account_id_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "id invalido"}), 400

    if status not in {"sin_uso", "pendiente", "usado", "fallo"}:
        return jsonify({"error": "status invalido. Usa: sin_uso|pendiente|usado|fallo"}), 400

    try:
        result_id_int = int(result_id) if result_id is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "result_id invalido"}), 400

    try:
        updated = _set_email_pool_status_by_id(
            account_id,
            status,
            result_id=result_id_int,
            username=username,
        )
        if not updated:
            return jsonify({"error": "not_found"}), 404

        row = db.session.get(EmailPoolAccount, account_id)
        db.session.commit()
        item = _email_pool_account_to_dict(row)
        item["pool_status"] = row.status
        return jsonify(
            {
                "status": "ok",
                "item": item,
            }
        ), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/email_pool/update", methods=["POST"])
def api_email_pool_update():
    """
    Actualiza parcialmente una cuenta del pool de correos.
    Requiere id/email_pool_id o lookup_email para localizar la cuenta.
    Campos opcionales editables:
      email, password, status, recovery_email_1, recovery_email_2, recovery_code_1, recovery_code_2, extra_1, extra_2, extra_3
    """
    data = request.get_json(silent=True) or {}
    account_id_raw = data.get("id", data.get("email_pool_id"))
    lookup_email_raw = data.get("lookup_email")

    try:
        row = None
        if account_id_raw is not None:
            account_id = int(account_id_raw)
            row = db.session.get(EmailPoolAccount, account_id)
        elif lookup_email_raw is not None:
            lookup_email = _normalize_pool_email_value(lookup_email_raw)
            if not lookup_email:
                return jsonify({"error": "lookup_email invalido"}), 400
            row = EmailPoolAccount.query.filter_by(email_lower=lookup_email.lower()).first()
        else:
            return jsonify({"error": "id/email_pool_id o lookup_email requerido"}), 400

        if not row:
            return jsonify({"error": "not_found"}), 404

        touched = False

        if "email" in data:
            email_norm = _normalize_pool_email_value(data.get("email"))
            if not email_norm:
                return jsonify({"error": "email invalido"}), 400
            row.email = email_norm
            row.email_lower = email_norm.lower()
            row.provider = _email_pool_provider_from_email(email_norm)
            touched = True

        if "password" in data:
            password_text = str(data.get("password") or "").strip()
            if not password_text:
                return jsonify({"error": "password invalido"}), 400
            row.password = password_text
            touched = True

        for key in ("recovery_email_1", "recovery_email_2"):
            if key in data:
                value = _normalize_optional_email_or_none(data.get(key))
                if value == "":
                    return jsonify({"error": f"{key} invalido"}), 400
                setattr(row, key, value)
                touched = True

        for key in ("recovery_code_1", "recovery_code_2", "extra_1", "extra_2", "extra_3"):
            if key in data:
                setattr(row, key, _string_or_none(data.get(key)))
                touched = True

        status_present = "status" in data
        status = _normalize_email_pool_status(data.get("status")) if status_present else ""
        if status_present and status not in {"sin_uso", "pendiente", "usado", "fallo"}:
            return jsonify({"error": "status invalido. Usa: sin_uso|pendiente|usado|fallo"}), 400

        result_id = data.get("result_id")
        if result_id is not None:
            try:
                result_id = int(result_id)
            except (TypeError, ValueError):
                return jsonify({"error": "result_id invalido"}), 400
        username = _string_or_none(data.get("username"))

        if not touched and not status_present:
            return jsonify({"error": "sin cambios: envia al menos un campo para actualizar"}), 400

        if touched:
            db.session.flush()

        if status_present:
            changed = _set_email_pool_status_by_id(
                row.id,
                status,
                result_id=result_id,
                username=username,
            )
            if not changed:
                return jsonify({"error": "not_found"}), 404
            db.session.flush()
            row = db.session.get(EmailPoolAccount, row.id)

        db.session.commit()
        item = _email_pool_account_to_dict(row)
        item["pool_status"] = row.status
        return jsonify({"status": "ok", "item": item}), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Se detectaron duplicados durante la actualizacion."}), 409
    except ValueError:
        return jsonify({"error": "id invalido"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/email_pool/delete", methods=["POST"])
def api_email_pool_delete():
    """
    Elimina una cuenta del pool de correos por id/email.
    Body:
      - { "id": 22 }
      - { "email_pool_id": 22 }
      - { "email": "correo@dominio.com" }
    """
    data = request.get_json(silent=True) or {}
    account_id_raw = data.get("id", data.get("email_pool_id"))
    email_raw = data.get("email")

    account = None
    try:
        if account_id_raw is not None:
            account_id = int(account_id_raw)
            account = db.session.get(EmailPoolAccount, account_id)
        elif email_raw is not None:
            email_norm = _normalize_pool_email_value(email_raw)
            if not email_norm:
                return jsonify({"error": "email invalido"}), 400
            account = EmailPoolAccount.query.filter_by(email_lower=email_norm.lower()).first()
        else:
            return jsonify({"error": "id o email requerido"}), 400

        if not account:
            return jsonify({"error": "not_found"}), 404

        payload = _email_pool_account_to_dict(account)
        db.session.delete(account)
        db.session.commit()
        return jsonify({"status": "deleted", "item": payload}), 200
    except ValueError:
        return jsonify({"error": "id invalido"}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route("/api/ext_log", methods=["POST"])
def ext_log():
    """Puente de logs: recibe logs de la extensión y los imprime en consola del servidor."""
    data = request.json or {}
    level = data.get("level", "INFO")
    source = data.get("source", "EXT")
    msg = data.get("message", "")
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{source}] [{level}] {msg}")
    return jsonify({"status": "ok"})


@app.route("/api/report_error", methods=["POST"])
def report_error():
    data = request.json or {}
    error_type = data.get("type", "Error")
    msg = data.get("message", "Sin detalles")

    timestamp = datetime.now().strftime("%H:%M:%S")
    alerta = {"time": timestamp, "type": error_type, "msg": msg}

    global LAST_ERRORS
    LAST_ERRORS.insert(0, alerta)
    if len(LAST_ERRORS) > 10:
        LAST_ERRORS.pop()

    emit_dashboard_update()
    return jsonify({"status": "ok"})


@app.route("/api/delete_result/<int:id>", methods=["DELETE"])
def delete_result(id):
    try:
        res = db.session.get(Resultado, id)
        if not res:
            return jsonify({"error": "Result not found"}), 404

        db.session.delete(res)
        db.session.commit()

        emit_dashboard_update()
        return jsonify({"status": "deleted"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/delete_all_results", methods=["DELETE"])
def delete_all_results():
    try:
        filas_borradas = Resultado.query.delete()
        db.session.commit()
        
        print(f"[ADMIN] Se eliminaron {filas_borradas} resultados.")
        emit_dashboard_update()
        return jsonify({"message": "Todos los éxitos eliminados"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/pending_success", methods=["GET"])
def api_pending_success():
    """
    Devuelve resultados success/used/lucas con reviewed==0 (sin revisar) o reviewed==3 (disponible/liberado).
    Parámetros opcionales:
      - limit: cuántos registros traer (1-50, default 1)
      - after_id: paginado simple (id > after_id)
      - claim=1: si se envía, cualquier registro con reviewed==3 se marca a 0 (sin revisar) al entregarse.
      - only_unreviewed=1: si se envía, solo entrega reviewed==0
    La ruta no altera el estado; la extensión debe confirmar con /api/results/ack.
    """
    try:
        limit = request.args.get("limit", default=1, type=int)
        limit = max(1, min(limit, 50))
        after_id = request.args.get("after_id", default=0, type=int)
        claim = request.args.get("claim", default=0, type=int) == 1
        only_unreviewed = request.args.get("only_unreviewed", default=0, type=int) == 1

        base_query = Resultado.query.filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
        if only_unreviewed:
            base_query = base_query.filter(Resultado.reviewed == 0)
        else:
            base_query = base_query.filter(Resultado.reviewed.in_([0, 3]))

        rows = (
            base_query.filter(Resultado.id > after_id)
            .order_by(Resultado.id.asc())
            .limit(limit)
            .all()
        )

        items = []
        if claim and rows:
            for r in rows:
                if r.reviewed == 3:
                    r.reviewed = 0                                                     
            db.session.commit()

        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "original_id": r.original_id,
                    "username": r.username,
                    "password": r.password,
                    "status": r.result_status,
                    "reviewed": r.reviewed or 0,
                    "extra_fields": r.extra_fields or {},
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                }
            )

        next_after = after_id if not rows else rows[-1].id
        return jsonify({"items": items, "next_after": next_after}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/available_success", methods=["GET"])
def api_available_success():
    """
    Devuelve solo resultados success/used/lucas marcados como disponibles (reviewed == 3).
    Parámetros opcionales:
      - limit: cuántos registros traer (1-200, default 50)
      - after_id: paginado simple (id > after_id)
    """
    try:
        _normalize_debt_status_for_results()
        limit = request.args.get("limit", default=50, type=int)
        limit = max(1, min(limit, 200))
        after_id = request.args.get("after_id", default=0, type=int)

        base_query = (
            Resultado.query
            .filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
            .filter(Resultado.reviewed == 3)
            .filter(Resultado.id > after_id)
        )

                                                                                      
        debt_cutoff, avail_min = _get_cutoff_dates()
        candidate_rows = (
            base_query
            .order_by(Resultado.id.asc())
            .limit(max(limit * 5, 50))
            .all()
        )

        filtered = []
        for r in candidate_rows:
            due = _extract_due_date_from_extras(r.extra_fields or {})
            if due and due > avail_min:
                filtered.append(r)
            elif due is None:
                                                                          
                continue
            if len(filtered) >= limit:
                break

        items = [
            {
                "id": r.id,
                "original_id": r.original_id,
                "username": r.username,
                "password": r.password,
                "status": r.result_status,
                "reviewed": r.reviewed or 0,
                "extra_fields": r.extra_fields or {},
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in filtered
        ]

        last_row = filtered[-1] if filtered else None
        next_after = after_id if not last_row else last_row.id
        return jsonify({"items": items, "next_after": next_after}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/ready_success_numeric", methods=["GET"])
def api_ready_success_numeric():
    """
    Devuelve success/used/lucas listos/disponibles para UI manual:
      - reviewed (default 4): 3 o 4
      - limit: 1..50 (default 1)
      - after_id: paginado simple
      - username: filtro exacto opcional
      - include_used=1: incluye status "used"
      - generate=1: asegura v4_generated_email y v4_generated_password en extra_fields
      - email_source=generated|pool (alias: hotmail|outlook|gmail|pool_hotmail)
      - al entregar un item, lo reclama como reviewed=7 con TTL temporal
    Regla principal: solo entrega items con usernames numéricos válidos (descarta "no nro", etc).
    """
    try:
        _release_expired_processing()
        _normalize_debt_status_for_results()

        reviewed = request.args.get("reviewed", default=4, type=int)
        if reviewed == 2:
            reviewed = 3
        if reviewed not in (3, 4):
            return jsonify({"error": "reviewed debe ser 3 o 4"}), 400

        limit = request.args.get("limit", default=1, type=int)
        limit = max(1, min(limit, 50))
        after_id = request.args.get("after_id", default=0, type=int)
        username_filter = (request.args.get("username") or "").strip()
        processing_owner = (
            request.args.get("processing_owner")
            or request.args.get("owner")
            or request.args.get("claimed_by")
        )
        include_used = request.args.get("include_used", default=1, type=int) == 1
        should_generate = request.args.get("generate", default=1, type=int) == 1
        email_source = _normalize_email_source_input(
            request.args.get("email_source", default="generated", type=str)
        )
        if email_source not in {"generated", "pool"}:
            return jsonify({"error": "email_source invalido. Usa: generated|pool"}), 400

        statuses = ["success", "lucas"]
        if include_used:
            statuses.append("used")

        items = []
        touched = False
        scan_cursor = after_id
        last_scanned_id = after_id
        batch_size = max(limit * 25, 100)
        pool_exhausted = False

        while len(items) < limit:
            batch_query = (
                Resultado.query
                .filter(Resultado.result_status.in_(statuses))
                .filter(Resultado.reviewed == reviewed)
                .filter(Resultado.id > scan_cursor)
                .order_by(Resultado.id.asc())
            )
            if username_filter:
                batch_query = batch_query.filter(Resultado.username == username_filter)

            candidate_rows = batch_query.limit(batch_size).all()
            if not candidate_rows:
                break

            for row in candidate_rows:
                last_scanned_id = row.id
                extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
                usernames_numeric = _extract_numeric_usernames(extras)
                if not usernames_numeric:
                    continue

                row = _claim_processing_result(row, reviewed, owner=processing_owner)
                if not row:
                    continue
                touched = True
                extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)

                hydrated = _hydrate_ready_fields(row, extras, usernames_numeric, max_fields=0)
                extras = hydrated["extras"]
                changed = hydrated["changed"]

                if should_generate:
                    extras, generated_email, generated_password, changed_creds = _ensure_v4_credentials_for_result(
                        row,
                        extras,
                        max_fields=0,
                    )
                    changed = changed or changed_creds
                else:
                    generated_email = str(extras.get("v4_generated_email") or "").strip()
                    generated_password = str(extras.get("v4_generated_password") or "").strip()

                pool_email_id = None
                pool_email_status = None
                pool_email_password = None
                pool_recovery_email_1 = None
                pool_recovery_email_2 = None
                pool_recovery_code_1 = None
                pool_recovery_code_2 = None
                pool_extra_1 = None
                pool_extra_2 = None
                pool_extra_3 = None
                if email_source == "pool":
                    pool_row = _reusable_email_pool_account_for_result(row)
                    if not pool_row:
                        pool_row = _claim_next_email_pool_account(result_id=row.id, username=row.username)
                    if not pool_row:
                        _undo_processing_claim(row)
                        touched = True
                        pool_exhausted = True
                        break
                    generated_email = pool_row.email
                    pool_email_id = pool_row.id
                    pool_email_status = pool_row.status
                    pool_email_password = pool_row.password
                    pool_recovery_email_1 = pool_row.recovery_email_1
                    pool_recovery_email_2 = pool_row.recovery_email_2
                    pool_recovery_code_1 = pool_row.recovery_code_1
                    pool_recovery_code_2 = pool_row.recovery_code_2
                    pool_extra_1 = pool_row.extra_1
                    pool_extra_2 = pool_row.extra_2
                    pool_extra_3 = pool_row.extra_3
                    if extras.get("v4_generated_email") != generated_email:
                        extras["v4_generated_email"] = generated_email
                        changed = True
                    if extras.get("v4_email_source") != "pool":
                        extras["v4_email_source"] = "pool"
                        changed = True
                    if extras.get("v4_pool_email_id") != pool_email_id:
                        extras["v4_pool_email_id"] = pool_email_id
                        changed = True
                    if extras.get("v4_pool_email_status") != pool_email_status:
                        extras["v4_pool_email_status"] = pool_email_status
                        changed = True
                else:
                    desired_generated_email = _mail_from_username(row.username, nombre=row.nombre)
                    if generated_email != desired_generated_email:
                        generated_email = desired_generated_email
                        extras["v4_generated_email"] = generated_email
                        changed = True
                    if extras.get("v4_email_source") != "generated":
                        extras["v4_email_source"] = "generated"
                        changed = True
                    if "v4_pool_email_id" in extras:
                        extras.pop("v4_pool_email_id", None)
                        changed = True
                    if "v4_pool_email_status" in extras:
                        extras.pop("v4_pool_email_status", None)
                        changed = True

                if changed:
                    row.extra_fields = extras
                    touched = True

                item = {
                    "id": row.id,
                    "original_id": row.original_id,
                    "username": row.username,
                    "password": row.password,
                    "status": row.result_status,
                    "reviewed": row.reviewed or 0,
                    "nombre": row.nombre,
                    "usernames_numeric": usernames_numeric,
                    "doc": hydrated["doc"],
                    "sex": hydrated["sex"],
                    "altura": hydrated["altura"],
                    "phone4": hydrated["phone4"],
                    "address": hydrated["address"],
                    "generated_email": generated_email,
                    "generated_password": generated_password,
                    "email_source": email_source,
                    "pool_email_id": pool_email_id,
                    "pool_email_status": pool_email_status,
                    "pool_email_password": pool_email_password,
                    "pool_recovery_email_1": pool_recovery_email_1,
                    "pool_recovery_email_2": pool_recovery_email_2,
                    "pool_recovery_code_1": pool_recovery_code_1,
                    "pool_recovery_code_2": pool_recovery_code_2,
                    "pool_extra_1": pool_extra_1,
                    "pool_extra_2": pool_extra_2,
                    "pool_extra_3": pool_extra_3,
                    "extra_fields": extras,
                    "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                }
                item.update(_processing_payload(row))
                items.append(item)
                if len(items) >= limit:
                    break

            scan_cursor = last_scanned_id
            if pool_exhausted:
                break
            if len(candidate_rows) < batch_size:
                break

        if touched:
            db.session.commit()

        if items:
            next_after = items[-1]["id"]
        elif last_scanned_id > after_id:
            next_after = last_scanned_id
        else:
            next_after = after_id
        if pool_exhausted and not items:
            return jsonify({"status": "empty_email_pool", "items": [], "next_after": next_after}), 200
        if pool_exhausted and items:
            return jsonify({"status": "partial_email_pool", "items": items, "next_after": next_after}), 200
        return jsonify({"items": items, "next_after": next_after}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/available_debt", methods=["GET"])
def api_available_debt():
    """
    Devuelve resultados success/used/lucas marcados como deuda (reviewed == 5).
    Filtro adicional: due_date <= debt_cutoff.
    Parámetros opcionales:
      - limit: cuántos registros traer (1-200, default 50)
      - after_id: paginado simple (id > after_id)
    """
    try:
        _normalize_debt_status_for_results()
        limit = request.args.get("limit", default=50, type=int)
        limit = max(1, min(limit, 200))
        after_id = request.args.get("after_id", default=0, type=int)

        debt_cutoff, _ = _get_cutoff_dates()

        base_query = (
            Resultado.query
            .filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
            .filter(Resultado.reviewed == 5)
            .filter(Resultado.id > after_id)
        )

        candidate_rows = (
            base_query
            .order_by(Resultado.id.asc())
            .limit(max(limit * 5, 50))
            .all()
        )

        filtered = []
        for r in candidate_rows:
            due = _extract_due_date_from_extras(r.extra_fields or {})
            if due and due <= debt_cutoff:
                filtered.append(r)
            elif due is None:
                                                              
                continue
            if len(filtered) >= limit:
                break

        items = [
            {
                "id": r.id,
                "original_id": r.original_id,
                "username": r.username,
                "password": r.password,
                "status": r.result_status,
                "reviewed": r.reviewed or 0,
                "extra_fields": r.extra_fields or {},
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in filtered
        ]

        last_row = filtered[-1] if filtered else None
        next_after = after_id if not last_row else last_row.id
        return jsonify({"items": items, "next_after": next_after}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/available_no_date", methods=["GET"])
def api_available_no_date():
    """
    Devuelve resultados success/used/lucas marcados como sin fecha (reviewed == NO_DATE_REVIEWED).
    Solo incluye los que realmente no tienen fecha de vencimiento en extra_fields.
    Parámetros opcionales:
      - limit: cuántos registros traer (1-200, default 50)
      - after_id: paginado simple (id > after_id)
    """
    try:
        _normalize_debt_status_for_results()
        limit = request.args.get("limit", default=50, type=int)
        limit = max(1, min(limit, 200))
        after_id = request.args.get("after_id", default=0, type=int)

        base_query = (
            Resultado.query
            .filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
            .filter(Resultado.reviewed == NO_DATE_REVIEWED)
            .filter(Resultado.id > after_id)
        )

        candidate_rows = (
            base_query
            .order_by(Resultado.id.asc())
            .limit(max(limit * 5, 50))
            .all()
        )

        filtered = []
        for r in candidate_rows:
            due = _extract_due_date_from_extras(r.extra_fields or {})
            if due is None:
                filtered.append(r)
            if len(filtered) >= limit:
                break

        items = [
            {
                "id": r.id,
                "original_id": r.original_id,
                "username": r.username,
                "password": r.password,
                "status": r.result_status,
                "reviewed": r.reviewed or 0,
                "extra_fields": r.extra_fields or {},
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in filtered
        ]

        last_row = filtered[-1] if filtered else None
        next_after = after_id if not last_row else last_row.id
        return jsonify({"items": items, "next_after": next_after}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/search", methods=["GET"])
def api_results_search():
    """
    Busca resultados por id o username (DNI).
    Parámetros (uno de los dos es requerido):
      - id: entero (id de resultado)
      - username: string (DNI/username exacto)
    Si se busca por username devuelve el más reciente (limit 1 fijo).
    """
    try:
        rid = request.args.get("id", type=int)
        username = request.args.get("username", type=str)
        touched = False

        if rid is None and (not username):
            return jsonify({"error": "id o username requerido"}), 400

        if rid is not None:
            row = db.session.get(Resultado, rid)
            if not row:
                return jsonify({"error": "not_found"}), 404
            payload, changed, extras = _project_result_for_ui(row, hydrate_ready=True, max_fields=0)
            if changed:
                row.extra_fields = extras
                db.session.commit()
            return jsonify(payload), 200
                                       
                                                                                                                   
                                                          
        row = (
            Resultado.query
            .filter(Resultado.username == username)
            .filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
            .order_by(Resultado.id.desc())
            .limit(1)
            .first()
        )
        if not row:
            row = (
                Resultado.query
                .filter(Resultado.username == username)
                .order_by(Resultado.id.desc())
                .limit(1)
                .first()
            )
        if not row:
            return jsonify({"items": []}), 200
        item, changed, extras = _project_result_for_ui(row, hydrate_ready=True, max_fields=0)
        if changed:
            row.extra_fields = extras
            touched = True
        if touched:
            db.session.commit()
        return jsonify({"items": [item]}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/job", methods=["GET"])
def api_results_job():
    """
    Entrega un único resultado success/used/lucas sin revisar (reviewed==0) o disponible (3),
    no modifica su estado (la extensión decidirá luego con /api/results/ack).
    Parámetros opcionales:
      - include_available=1 -> incluye reviewed==3 además de reviewed==0. Por defecto solo reviewed==0.
    Respuestas:
      - 200 {"status":"ok","item":{...}}
      - 204 sin contenido si no hay disponibles
    """
    try:
        include_available = request.args.get("include_available", default=0, type=int) == 1

        base_query = Resultado.query.filter(Resultado.result_status.in_(_POSITIVE_RESULT_STATUSES))
        if include_available:
            base_query = base_query.filter(Resultado.reviewed.in_([0, 3]))
        else:
            base_query = base_query.filter(Resultado.reviewed == 0)

        row = base_query.order_by(Resultado.id.asc()).first()
        if not row:
            return "", 204

        item = {
            "id": row.id,
            "original_id": row.original_id,
            "username": row.username,
            "password": row.password,
            "status": row.result_status,
            "reviewed": row.reviewed or 0,
        "nombre": row.nombre,
            "extra_fields": row.extra_fields or {},
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        }
        return jsonify({"status": "ok", "item": item}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/ack", methods=["POST"])
def api_results_ack():
    """
    Marca un resultado como revisado, disponible o listo.
    Body JSON: { "id": <int>, "reviewed": int, "email_pool_id"?: int, "email_pool_status"?: str }
    """
    data = request.get_json(silent=True) or {}
    rid = data.get("id")
    reviewed_raw = data.get("reviewed", 1)
    reviewed = _normalize_review_state_value(reviewed_raw, allow_none=True, bool_legacy=True)
    if reviewed is None:
        return jsonify({"error": "reviewed invalido"}), 400
    extra_fields = data.get("extra_fields")
    processing_owner = data.get("processing_owner") or data.get("owner") or data.get("claimed_by")
    processing_return_state = data.get("processing_return_state")
    explicit_pool_id_raw = data.get("email_pool_id", data.get("pool_email_id"))
    explicit_pool_status = _normalize_email_pool_status(data.get("email_pool_status"))
    if data.get("email_pool_status") is not None and not explicit_pool_status:
        return jsonify({"error": "email_pool_status invalido. Usa: sin_uso|pendiente|usado|fallo"}), 400

    if rid is None:
        return jsonify({"error": "id requerido"}), 400
    if reviewed not in _VALID_REVIEW_STATES:
        return jsonify({"error": "reviewed invalido"}), 400

    explicit_pool_id = None
    if explicit_pool_id_raw is not None:
        try:
            explicit_pool_id = int(explicit_pool_id_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "email_pool_id invalido"}), 400

    try:
        row = db.session.get(Resultado, rid)
        if not row:
            return jsonify({"error": "not_found"}), 404

        _apply_review_state(
            row,
            reviewed,
            owner=processing_owner,
            return_state=processing_return_state,
        )
        if extra_fields and isinstance(extra_fields, dict):
                                                                  
            usernames_only = {}
            if "usernames" in extra_fields:
                usernames_only["usernames"] = extra_fields.get("usernames")
            row.extra_fields = _merge_extra_fields(usernames_only, row.extra_fields, extra_fields)
        if explicit_pool_id is not None:
            row_extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
            row_extras["v4_pool_email_id"] = explicit_pool_id
            row.extra_fields = _merge_extra_fields(row.extra_fields, row_extras)

        pool_sync = _sync_email_pool_for_result(
            row,
            reviewed=reviewed,
            explicit_status=explicit_pool_status if data.get("email_pool_status") is not None else None,
        )
        if explicit_pool_id is not None and not pool_sync.get("updated"):
            return jsonify({"error": "email_pool_not_found"}), 404

        db.session.commit()
        _normalize_debt_status_for_results()
        emit_dashboard_update()
        payload = {
            "status": "ok",
            "id": rid,
            "reviewed": row.reviewed,
            "email_pool_id": pool_sync.get("pool_id"),
            "email_pool_status": pool_sync.get("pool_status"),
        }
        payload.update(_processing_payload(row))
        return jsonify(payload), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def _is_local_request() -> bool:
    host_only = (request.host or "").split(":", 1)[0].strip().lower().strip("[]")
    return host_only in {"127.0.0.1", "localhost", "::1"}


def _admin_authorized() -> bool:
                                                               
    return True


@app.route("/api/pool/<int:cid>", methods=["DELETE"])
def api_delete_pool_item(cid: int):
    """
    Elimina una entrada específica del pool (tabla Credencial) por ID.
    Nota: no borra History (así se mantiene el control de duplicados).
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    try:
        cred = db.session.get(Credencial, cid)
        if not cred:
            return jsonify({"error": "not_found", "id": cid}), 404

        username = cred.username
        db.session.delete(cred)
        db.session.commit()
        emit_dashboard_update()

        return jsonify({"status": "deleted", "id": cid, "username": username}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/pool", methods=["DELETE"])
def api_delete_pool_all():
    """
    Elimina TODO el pool (tabla Credencial).
    Nota: no borra History ni Resultados.
    """
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    try:
        deleted = db.session.query(Credencial).delete(synchronize_session=False)
        db.session.commit()
        emit_dashboard_update()
        return jsonify({"status": "deleted_all", "deleted": deleted}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard")
def api_dashboard():
    """
    Snapshot del dashboard (stats + resultados).
    Params opcionales:
      - status: success|lucas|fail|none|error|all (default: success)
      - limit_results: int, 0 o negativo = sin límite (default: 0)
      - limit_numbers: int, 0 deshabilita cola (default: 0)
      - page, page_size: paginación de resultados (default page_size=40)
      - review: 0|1|3|4|5|6|7|8|9|10|11|12|13|14|15 para filtrar por estado de revisión
    """
    limit_results = request.args.get("limit_results", default=0, type=int)
    limit_numbers = request.args.get("limit_numbers", default=0, type=int)
    status_filter = request.args.get("status", default="success", type=str)
    page = request.args.get("page", default=1, type=int)
    page_size = request.args.get("page_size", default=40, type=int)
    review_filter = request.args.get("review", default=None, type=int)

    data = get_stats_data(
        limit_results=limit_results,
        limit_numbers=limit_numbers,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
        review_filter=review_filter,
    )
    if data:
        return jsonify(data)
    return jsonify({"error": "Error interno"}), 500


@app.route("/api/new")
@app.route("/api/new/reviews")
def api_new_reviews():
    """
    Lightweight data source for /static/new.html.
    Returns only reviewed 7/8/9 rows plus exact counters.
    """
    page = request.args.get("page", default=1, type=int)
    page_size = request.args.get("page_size", default=600, type=int)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 600), 5000))
    offset = (page - 1) * page_size
    states = (PROCESSING_REVIEWED, VALIDATED_REVIEWED, REGISTER_REVIEWED)
    exposed_extra_keys = {
        "add_fail",
        "comment",
        "comentario",
        "copied",
        "doc",
        "documento",
        "email",
        "generated_email",
        "gender",
        "nombre",
        "nombre_completo",
        "phone_last4",
        "processing_expires_at",
        "sex",
        "altura",
        "usernames",
        "usernames_no_used",
        "v4_altura",
        "v4_generated_email",
        "v4_generated_password",
        "v4_phone_last4",
        "v4_review_comment",
        "v4_sex",
        "v4_step5_failure_kind",
        "v4_step5_result",
        "v4_usernames",
    }

    try:
        try:
            _release_expired_processing()
        except Exception:
            pass

        counts = {str(state): 0 for state in states}
        count_rows = (
            db.session.query(Resultado.reviewed, db.func.count(Resultado.id))
            .filter(Resultado.reviewed.in_(states))
            .group_by(Resultado.reviewed)
            .all()
        )
        for state, total in count_rows:
            try:
                state_int = int(state)
            except Exception:
                continue
            if state_int in states:
                counts[str(state_int)] = int(total or 0)

        total_items = sum(counts.values())
        rows = (
            Resultado.query
            .options(
                load_only(
                    Resultado.id,
                    Resultado.original_id,
                    Resultado.username,
                    Resultado.result_status,
                    Resultado.reviewed,
                    Resultado.timestamp,
                    Resultado.nombre,
                    Resultado.extra_fields,
                    Resultado.processing_started_at,
                    Resultado.processing_expires_at,
                    Resultado.processing_owner,
                    Resultado.processing_attempts,
                    Resultado.processing_return_state,
                )
            )
            .filter(Resultado.reviewed.in_(states))
            .order_by(Resultado.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

        results = []
        changed_any = False
        for row in rows:
            extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
            usernames_numeric = _extract_numeric_usernames(extras)
            if not usernames_numeric and extras.get("usernames_no_used"):
                usernames_numeric = _extract_numeric_usernames(
                    {"usernames": extras.get("usernames_no_used")}
                )
            doc = _extract_doc_from_result(row, extras)
            sex = _extract_sex_from_result(extras)
            altura = _extract_altura_from_extras(extras)
            phone4 = _extract_phone4_from_result(extras, usernames_numeric)
            extras, generated_email, generated_password, changed_creds = _ensure_v4_credentials_for_result(
                row,
                extras,
                max_fields=0,
            )
            if changed_creds:
                row.extra_fields = extras
                changed_any = True
            exposed_extras = {
                key: extras[key]
                for key in exposed_extra_keys
                if key in extras
            }
            ts = row.timestamp
            item = {
                "id": row.id,
                "original_id": row.original_id,
                "username": row.username,
                "status": row.result_status,
                "reviewed": row.reviewed if row.reviewed is not None else 0,
                "nombre": row.nombre,
                "doc": doc,
                "sex": sex,
                "altura": altura,
                "phone4": phone4,
                "usernames_numeric": usernames_numeric,
                "generated_email": generated_email,
                "generated_password": generated_password,
                "extra_fields": exposed_extras,
                "timestamp": ts.strftime("%H:%M:%S") if ts else "--:--",
            }
            item.update(_processing_payload(row))
            results.append(item)

        if changed_any:
            db.session.commit()

        return jsonify(
            {
                "status": "ok",
                "counts": counts,
                "results": results,
                "pagination": {
                    "total_items": total_items,
                    "page": page,
                    "page_size": page_size,
                    "total_pages": max(1, math.ceil(total_items / page_size)),
                },
            }
        ), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


def _bool_from_payload(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "si", "on", "x"}


@app.route("/api/new/reviews/copied", methods=["POST"])
def api_new_reviews_copied_bulk():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    copied = _bool_from_payload(data.get("copied", data.get("value", False)))
    raw_ids = data.get("ids")
    if not isinstance(raw_ids, list):
        return jsonify({"error": "ids debe ser lista"}), 400

    ids = []
    seen = set()
    for raw_id in raw_ids:
        try:
            rid = int(raw_id)
        except (TypeError, ValueError):
            continue
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        ids.append(rid)
        if len(ids) >= 5000:
            break

    if not ids:
        return jsonify({"error": "ids vacio"}), 400

    try:
        rows = (
            Resultado.query
            .filter(Resultado.id.in_(ids))
            .filter(Resultado.reviewed.in_((PROCESSING_REVIEWED, VALIDATED_REVIEWED, REGISTER_REVIEWED)))
            .all()
        )
        updated_ids = []
        for row in rows:
            extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
            extras["copied"] = copied
            row.extra_fields = extras
            updated_ids.append(row.id)
        db.session.commit()
        return jsonify({"status": "ok", "copied": copied, "ids": updated_ids, "count": len(updated_ids)}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/new/reviews/<int:rid>/copied", methods=["POST"])
def api_new_review_copied(rid):
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403

    data = request.get_json(silent=True) or {}
    copied = _bool_from_payload(data.get("copied", data.get("value", False)))

    try:
        row = db.session.get(Resultado, rid)
        if not row:
            return jsonify({"error": "not_found", "id": rid}), 404
        if row.reviewed not in (PROCESSING_REVIEWED, VALIDATED_REVIEWED, REGISTER_REVIEWED):
            return jsonify({"error": "review_state_not_allowed", "id": rid, "reviewed": row.reviewed}), 400

        extras = _normalize_extra_fields(row.extra_fields or {}, max_fields=0)
        extras["copied"] = copied
        row.extra_fields = extras
        db.session.commit()
        return jsonify({"status": "ok", "id": rid, "copied": copied, "extra_fields": {"copied": copied}}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/json-doc/<string:namespace>/<string:doc_key>", methods=["GET", "POST", "DELETE"])
def api_json_doc(namespace, doc_key):
    namespace = (namespace or "default").strip()
    doc_key = (doc_key or "").strip()
    if not doc_key:
        return jsonify({"error": "doc_key requerido"}), 400

    try:
        doc = JsonDocument.query.filter_by(namespace=namespace, doc_key=doc_key).first()

        if request.method == "GET":
            if not doc:
                return jsonify({"error": "not_found"}), 404
            return jsonify(
                {
                    "namespace": doc.namespace,
                    "doc_key": doc.doc_key,
                    "data": doc.data or {},
                    "created_at": doc.created_at.isoformat() if doc.created_at else None,
                    "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
                }
            ), 200

        if request.method == "DELETE":
            if not doc:
                return jsonify({"error": "not_found"}), 404
            db.session.delete(doc)
            db.session.commit()
            return jsonify({"status": "deleted", "namespace": namespace, "doc_key": doc_key}), 200

        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "body JSON requerido"}), 400

                                                                         
        if isinstance(payload, dict):
            payload = _normalize_extra_fields(payload)
        else:
            payload = {"value": _json_safe(payload)}

        if doc is None:
            doc = JsonDocument(namespace=namespace, doc_key=doc_key, data=payload)
            db.session.add(doc)
        else:
            doc.data = payload

        db.session.commit()
        return jsonify({"status": "saved", "namespace": namespace, "doc_key": doc_key, "data": payload}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@app.route("/api/json-doc/<string:namespace>", methods=["GET"])
def api_json_doc_list(namespace):
    namespace = (namespace or "default").strip()
    try:
        limit = request.args.get("limit", default=50, type=int)
        limit = max(1, min(limit, 200))
        rows = (
            JsonDocument.query.filter_by(namespace=namespace)
            .order_by(JsonDocument.updated_at.desc(), JsonDocument.id.desc())
            .limit(limit)
            .all()
        )
        return jsonify(
            {
                "namespace": namespace,
                "count": len(rows),
                "documents": [
                    {
                        "doc_key": row.doc_key,
                        "data": row.data or {},
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    }
                    for row in rows
                ],
            }
        ), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/dash")
def dash():
    return redirect("/static/dash.html")


@app.route("/new")
def new_dashboard():
    return redirect("/static/new.html")


@app.route("/static/test", strict_slashes=False)
def static_test_index():
                                                                       
                                                                                    
    qs = (request.query_string or b"").decode("utf-8", errors="ignore")
    target = "/static/test/index.html"
    if qs:
        target = f"{target}?{qs}"
    return redirect(target)


@app.route("/debug-login", methods=["POST"])
def debug_login():
    raw_body = request.get_data(as_text=True) or ""
    print(raw_body)
    return raw_body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/bootstrap", methods=["GET"])
def bootstrap_info():
    """Retorna la URL actual de Cloudflare (mantenido por compatibilidad)."""
    if not CLOUDFLARE_URL:
        return jsonify({"status": "pending", "cloudflare_url": None}), 503
    return jsonify({"status": "ok", "cloudflare_url": CLOUDFLARE_URL}), 200


@app.route("/api/admin/update_url", methods=["POST"])
def api_admin_update_url():
    """Fuerza el reenvío del correo FLOW con la URL actual de Cloudflare."""
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    if not CLOUDFLARE_URL:
        return jsonify({"error": "No hay URL de Cloudflare disponible"}), 404
    threading.Thread(target=update_cloudflare_url_via_email, args=(CLOUDFLARE_URL,), daemon=True).start()
    return jsonify({"status": "sent", "url": CLOUDFLARE_URL}), 200


                           
                   
                           
@socketio.on("connect")
def on_connect(auth=None):
    emit_dashboard_update()

TRYCF_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

def start_cloudflared_tunnel(port: int, local_host: str = "localhost", wait_seconds: int = 20):
    """
    Levanta un Cloudflare Quick Tunnel (cloudflared) y guarda la URL pública
    en memoria para que /bootstrap la exponga.
    """
    global CLOUDFLARE_URL, CF_PROC
                                                     
    cmd = ["cloudflared", "tunnel", "--url", f"http://{local_host}:{port}"]
    
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags
        )
    except FileNotFoundError:
        print(" ! cloudflared no está en PATH. Instálalo para habilitar el túnel.")
        CF_PROC = None
        CLOUDFLARE_URL = None
        return None, None
    except Exception as e:
        print(f" ! No se pudo iniciar cloudflared: {e}")
        CF_PROC = None
        CLOUDFLARE_URL = None
        return None, None

    CF_PROC = proc
    url_holder = {"url": None}

    def _reader():
        global CLOUDFLARE_URL
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                
                                                                
                                       
                m = TRYCF_RE.search(line)
                if not m:
                    continue
                new_url = m.group(0)
                if new_url != CLOUDFLARE_URL:
                    CLOUDFLARE_URL = new_url
                    if not url_holder["url"]:
                        url_holder["url"] = new_url
                    print(f"*SERVER FLOW V1 URL: {new_url}*")
                                                            
                    threading.Thread(target=update_cloudflare_url_via_email, args=(new_url,), daemon=True).start()
        except Exception:
                                                                  
            pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

                                                                              
    t0 = time.time()
    while time.time() - t0 < wait_seconds:
        if proc.poll() is not None:
                                              
            break
        if url_holder["url"]:
            break
        time.sleep(0.5)

                                  
    def _cleanup():
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass

    atexit.register(_cleanup)

    return proc, url_holder.get("url")


                           
                                      
                           

_OTP_SUBJECT_HINTS = {
    "registro de usuario",
    "recupero de usuario",
}

_OTP_BODY_HINTS = (
    "codigo para crear tu cuenta",
    "código para crear tu cuenta",
    "validar tu identidad",
    "validez de 3 minutos",
    "recupero de usuario",
    "registro de usuario",
)


def _email_password_normalized() -> str:
    return (EMAIL_APP_PASSWORD or "").strip().strip('"').replace(" ", "")


def _decode_mime_value(raw_value) -> str:
    if not raw_value:
        return ""
    parts = []
    for value, charset in decode_header(raw_value):
        if isinstance(value, bytes):
            enc = charset or "utf-8"
            try:
                parts.append(value.decode(enc, errors="replace"))
            except Exception:
                parts.append(value.decode("utf-8", errors="replace"))
        else:
            parts.append(str(value))
    return "".join(parts).strip()


def _strip_html_to_text(html_text: str) -> str:
    if not html_text:
        return ""
    text = unescape(html_text)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>|</div>|</tr>|</td>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _message_text_content(msg) -> str:
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = (part.get_content_type() or "").lower()
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if content_type == "text/plain":
                parts.append(text)
            elif content_type == "text/html":
                parts.append(_strip_html_to_text(text))
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            text = ""
        if (msg.get_content_type() or "").lower() == "text/html":
            parts.append(_strip_html_to_text(text))
        else:
            parts.append(text)
    text = "\n".join(p for p in parts if p)
    return re.sub(r"\n{2,}", "\n", text).strip()


def _normalize_target_email(email_value=None, local_part=None) -> str:
    email_value = (email_value or "").strip().lower()
    local_part = (local_part or "").strip().lower()
    if email_value:
        return email_value
    if local_part:
        return f"{local_part}@{OTP_EMAIL_DOMAIN}"
    return ""


def _message_targets_email(msg, target_email: str) -> bool:
    target = (target_email or "").strip().lower()
    if not target:
        return False
    headers_to_check = (
        _decode_mime_value(msg.get("To")),
        _decode_mime_value(msg.get("Delivered-To")),
        _decode_mime_value(msg.get("X-Original-To")),
        _decode_mime_value(msg.get("Cc")),
    )
    for header_val in headers_to_check:
        if target in header_val.lower():
            return True
    return False


def _otp_code_from_text(text: str, subject: str = "") -> str | None:
    normalized = (text or "").lower()
    subject_norm = (subject or "").lower()
    candidates = []
    for match in re.finditer(r"(?<!\d)(\d{6})(?!\d)", text or ""):
        code = match.group(1)
        start = max(0, match.start() - 220)
        end = min(len(normalized), match.end() + 220)
        window = normalized[start:end]
        score = 0
        if any(hint in subject_norm for hint in _OTP_SUBJECT_HINTS):
            score += 40
        for hint in _OTP_BODY_HINTS:
            if hint in window:
                score += 25
        if "minut" in window:
            score += 10
        if "cuenta" in window:
            score += 8
        if "telefono" in window or "teléfono" in window:
            score -= 5
        candidates.append((score, match.start(), code))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _fetch_latest_otp_email(target_email: str):
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise RuntimeError("No hay credenciales de email configuradas")

    password = _email_password_normalized()
    if not password:
        raise RuntimeError("EMAIL_APP_PASSWORD vacío")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(EMAIL_USER, password)
        mail.select("inbox")
        status, data = mail.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("No se pudo listar inbox")

        uids = data[0].split()
        if not uids:
            return None

        for uid in reversed(uids[-OTP_IMAP_MAX_SCAN:]):
            status, raw_data = mail.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not raw_data or raw_data[0] is None:
                continue
            raw_bytes = raw_data[0][1]
            msg = email_lib.message_from_bytes(raw_bytes)
            if not _message_targets_email(msg, target_email):
                continue

            subject = _decode_mime_value(msg.get("Subject"))
            body_text = _message_text_content(msg)
            code = _otp_code_from_text(body_text, subject=subject)
            if not code:
                continue

            return {
                "imap_uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                "email": target_email,
                "subject": subject,
                "from": _decode_mime_value(msg.get("From")),
                "to": _decode_mime_value(msg.get("To")),
                "date": _decode_mime_value(msg.get("Date")),
                "code": code,
            }
        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _delete_email_by_uid(imap_uid: str) -> bool:
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise RuntimeError("No hay credenciales de email configuradas")

    password = _email_password_normalized()
    if not password:
        raise RuntimeError("EMAIL_APP_PASSWORD vacío")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(EMAIL_USER, password)
        mail.select("inbox")
        status, _ = mail.uid("store", str(imap_uid), "+FLAGS", r"(\Deleted)")
        if status != "OK":
            return False
        mail.expunge()
        return True
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _delete_all_inbox_emails() -> int:
    """
    Borra todos los correos de INBOX en la cuenta IMAP configurada.
    Devuelve cuántos correos fueron marcados y expurgados.
    """
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        raise RuntimeError("No hay credenciales de email configuradas")

    password = _email_password_normalized()
    if not password:
        raise RuntimeError("EMAIL_APP_PASSWORD vacío")

    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        mail.login(EMAIL_USER, password)
        mail.select("inbox")
        status, data = mail.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("No se pudo listar inbox para purge")

        uids = data[0].split() if data and data[0] else []
        if not uids:
            return 0

        deleted = 0
        for uid in uids:
            st, _ = mail.uid("store", uid, "+FLAGS", r"(\Deleted)")
            if st == "OK":
                deleted += 1
        mail.expunge()
        return deleted
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _schedule_inbox_purge_async():
    """
    Lanza el purge total del inbox en segundo plano para no bloquear la
    respuesta del OTP.
    """
    def _job():
        try:
            deleted = _delete_all_inbox_emails()
            print(f"[OTP] Purge IMAP completado. Correos eliminados: {deleted}")
        except Exception as e:
            print(f"[OTP] Purge IMAP fallo: {e}")

    threading.Thread(target=_job, daemon=True).start()

def cleanup_flow_emails():
    """Conecta al Gmail por IMAP, busca todos los correos con asunto FLOW y los elimina."""
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER, _email_password_normalized())
        mail.select("inbox")
        _, data = mail.search(None, f'(SUBJECT "{EMAIL_SUBJECT}")')
        ids = data[0].split()
        for eid in ids:
            mail.store(eid, "+FLAGS", "\\Deleted")
        mail.expunge()
        mail.logout()
    except Exception as e:
        print(f"[EMAIL ERROR] cleanup: {e}")


def send_url_email(url):
    """Envía un correo con asunto FLOW y la URL en el cuerpo."""
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        return False
    try:
        payload = {
            "status": "ok",
            "cloudflare_url": url,
            "dashboard_url": f"{url}/dash",
            "bootstrap_url": f"{url}/bootstrap",
            "sent_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        msg = MIMEText(
            json.dumps(payload, ensure_ascii=False, indent=2),
            _subtype="plain",
            _charset="utf-8",
        )
        msg["Subject"] = EMAIL_SUBJECT
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_USER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_USER, _email_password_normalized())
            server.sendmail(EMAIL_USER, EMAIL_USER, msg.as_string())
        print("[EMAIL] URL enviada OK.")
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] send: {e}")
        return False


def update_cloudflare_url_via_email(url):
    """Limpia correos FLOW anteriores y envía uno nuevo con la URL."""
    cleanup_flow_emails()
    send_url_email(url)


if __name__ == "__main__":
    _ensure_unified_schema_columns()
    restored = _restore_lucas_statuses_from_extras()
    if restored["credencial"] or restored["resultado"]:
        print(
            " * [LUCAS] Restaurados desde extra_fields -> "
            f"credencial={restored['credencial']} resultado={restored['resultado']}"
        )
    
                                              
    start_cloudflared_tunnel(PORT)

    socketio.start_background_task(background_maintenance_tasks)

    socketio.run(
        app,
        port=PORT,
        debug=False,
        use_reloader=False,
        log_output=False,
        allow_unsafe_werkzeug=True,
    )
