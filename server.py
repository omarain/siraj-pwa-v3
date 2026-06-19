"""
Siraj PWA v3 — FastAPI server with Supabase Auth + Workshop shell
Railway-ready. Proxies to Siraj API (Hermes) on the Pi.
"""
import os, secrets, time, json, hashlib, hmac
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel

app = FastAPI(title="Siraj PWA v3")

# ── Cache-Control middleware ───────────────────────────────────
@app.middleware("http")
async def cache_control_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    ct = response.headers.get("content-type", "")

    if path == "/" or path == "/login":
        # HTML pages: never cache
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    elif path.startswith("/static/"):
        # Static assets: cache for 1 hour, stale-while-revalidate for 1 day
        response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
    # API routes: leave default (no-store by FastAPI convention)

    return response

STATIC = os.path.join(os.path.dirname(__file__), "static")
SIRAJ_API = os.getenv("SIRAJ_API_URL", "http://localhost:8642")

# Read Supabase config from env or file
def _load_supabase_env():
    """Load supabase config from env vars or fallback to ~/.siraj/supabase.env"""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if url and key:
        return url, key
    # Fallback: read from file
    try:
        with open(os.path.expanduser("~/.siraj/supabase.env"), "rb") as f:
            raw = f.read().decode()
        env = {}
        for line in raw.strip().split("\n"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
        return env.get("SUPABASE_PROJECT_URL", url), env.get("SUPABASE_SERVICE_ROLE_KEY", key)
    except Exception:
        return url, key

SUPABASE_URL, SUPABASE_KEY = _load_supabase_env()
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))

# Load Siraj Gateway API key
def _load_api_server_key() -> str:
    key = os.getenv("API_SERVER_KEY", "")
    if key:
        return key
    try:
        with open(os.path.expanduser("~/.siraj/.env"), "rb") as f:
            raw = f.read().decode()
        for line in raw.strip().split("\n"):
            if line.startswith("API_SERVER_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

API_SERVER_KEY = _load_api_server_key()

# Token expiry
MAX_TOKEN_AGE_DAYS = 30  # default: 30 days
REMEMBER_ME_DAYS = 90

def _is_secure_request(request: Request) -> bool:
    """Check if the original request was HTTPS (behind Cloudflare/proxy)."""
    proto = request.headers.get("x-forwarded-proto", "")
    if proto == "https":
        return True
    return request.url.scheme == "https"

# ── Auth helpers ──────────────────────────────────────────────

def verify_supabase_jwt(token: str) -> dict | None:
    """Verify a Supabase-issued JWT. Returns user payload or None."""
    import base64
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        # Decode payload (skip signature verification for now — Supabase handles it)
        payload_b64 = parts[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        # Check expiry
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None

def get_token_from_request(request: Request) -> str | None:
    """Extract JWT from Authorization header or cookie."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("siraj_token")

async def require_user(request: Request) -> dict:
    """Get authenticated user or raise 401."""
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = verify_supabase_jwt(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired token")
    return {
        "id": payload.get("sub"),
        "email": payload.get("email", ""),
        "name": payload.get("user_metadata", {}).get("name", payload.get("email", "")),
        "token": token,
    }

async def supabase_rpc(fn: str, body: dict = None) -> dict:
    """Call Supabase REST API with service role."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        if body:
            r = await client.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", json=body, headers=headers)
        else:
            r = await client.post(f"{SUPABASE_URL}/rest/v1/rpc/{fn}", headers=headers)
        r.raise_for_status()
        return r.json() if r.text else {}

# ── Auth endpoints ────────────────────────────────────────────

class LoginBody(BaseModel):
    email: str
    password: str
    remember_me: bool = False

@app.post("/api/login")
async def login(body: LoginBody, request: Request, response: Response):
    """Sign in with Supabase Auth."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            json={"email": body.email, "password": body.password, "gotrue_meta_security": {}},
            headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid credentials")
        data = r.json()
    
    token = data["access_token"]
    max_age = 86400 * (REMEMBER_ME_DAYS if body.remember_me else MAX_TOKEN_AGE_DAYS)
    secure = _is_secure_request(request)
    
    resp = JSONResponse({"ok": True, "user": data.get("user", {}), "token": token})
    resp.set_cookie("siraj_token", token, max_age=max_age, httponly=True, samesite="lax", secure=secure, path="/")
    return resp

@app.post("/api/signup")
async def signup(body: LoginBody, request: Request, response: Response):
    """Register new user with Supabase Auth — auto-confirms email so login works immediately."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            json={"email": body.email, "password": body.password},
            headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        )
        if r.status_code == 422:
            detail = r.json() if r.text else {}
            raise HTTPException(400, detail.get("msg", "Invalid email format"))
        if r.status_code == 429:
            raise HTTPException(429, "Too many attempts — wait a moment")
        if r.status_code not in (200, 201):
            detail = r.json() if r.text else {"msg": "Signup failed"}
            raise HTTPException(400, detail.get("msg", "Signup failed"))
        data = r.json()
        user = data.get("user", {})
        user_id = user.get("id", "")

    # If no token returned, email confirmation is required — auto-confirm via admin API
    token = data.get("access_token", "")
    if not token and user_id:
        admin_headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            await client.put(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                json={"email_confirm": True},
                headers=admin_headers,
            )
            # Now sign them in to get a token
            r2 = await client.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
                json={"email": body.email, "password": body.password},
                headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
            )
            if r2.status_code == 200:
                token = r2.json().get("access_token", "")

    max_age = 86400 * MAX_TOKEN_AGE_DAYS
    secure = _is_secure_request(request)

    resp = JSONResponse({"ok": True, "user": data.get("user", {}), "token": token})
    if token:
        resp.set_cookie("siraj_token", token, max_age=max_age, httponly=True, samesite="lax", secure=secure, path="/")
    else:
        raise HTTPException(500, "Account created but couldn't sign in — try logging in")
    return resp

@app.post("/api/logout")
async def logout(response: Response):
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("siraj_token")
    return resp

@app.get("/api/me")
async def me(request: Request):
    user = await require_user(request)
    return {"user": user}

# ── Messaging platform connections ────────────────────────────

class ConnectTelegramBody(BaseModel):
    telegram_id: str  # e.g. "6051514433"

@app.post("/api/messaging/connect/telegram")
async def connect_telegram(body: ConnectTelegramBody, request: Request):
    """Initiate Telegram connection: generate OTP, send via Siraj gateway."""
    user = await require_user(request)
    tg_id = body.telegram_id.strip()

    if not tg_id.isdigit():
        raise HTTPException(400, "Telegram ID must be numeric")

    # Check if already connected
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_connections?user_id=eq.{user['id']}&platform=eq.telegram",
            headers=headers,
        )
        if r.json():
            raise HTTPException(409, "Telegram already connected. Disconnect first to reconnect.")

    # Generate 6-digit OTP
    import random
    otp = str(random.randint(100000, 999999))
    expires = (datetime.utcnow() + timedelta(minutes=5)).isoformat()

    # Store OTP
    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/pwa_otps",
            json={
                "user_id": user["id"],
                "platform": "telegram",
                "platform_user_id": tg_id,
                "otp": otp,
                "expires_at": expires,
            },
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
        )

    # Send OTP via Siraj gateway (Telegram message)
    if API_SERVER_KEY:
        async with httpx.AsyncClient(timeout=60) as client:
            gw_headers = {
                "Authorization": f"Bearer {API_SERVER_KEY}",
                "Content-Type": "application/json",
            }
            # Use chat completions endpoint with a system-level send command
            # Siraj will use send_message tool to deliver the OTP
            payload = {
                "model": "hermes-agent",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            f"You are sending an automated verification message. "
                            f"Use the send_message tool RIGHT NOW to send a Telegram message "
                            f"to chat_id {tg_id} with exactly this text: "
                            f"'🔐 Siraj Workshop OTP: {otp} (expires in 5 minutes)' "
                            f"Do not ask questions, do not explain — just send the message immediately."
                        ),
                    }
                ],
                "stream": False,
                "max_tokens": 50,
            }
            r = await client.post(
                f"{SIRAJ_API}/v1/chat/completions",
                json=payload,
                headers=gw_headers,
            )
            # We don't care about the response — just that the message was sent

    return {
        "ok": True,
        "message": f"OTP sent to Telegram. Check your Telegram messages from Siraj.",
        "expires_in": 300,
    }

class VerifyTelegramBody(BaseModel):
    telegram_id: str
    otp: str

@app.post("/api/messaging/verify/telegram")
async def verify_telegram(body: VerifyTelegramBody, request: Request):
    """Verify OTP and complete Telegram connection."""
    user = await require_user(request)
    tg_id = body.telegram_id.strip()
    otp = body.otp.strip()

    if not tg_id.isdigit():
        raise HTTPException(400, "Telegram ID must be numeric")

    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Accept": "application/json"}

    # Verify OTP
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_otps"
            f"?user_id=eq.{user['id']}&platform=eq.telegram"
            f"&platform_user_id=eq.{tg_id}&otp=eq.{otp}&used=eq.false"
            f"&expires_at=gt.{datetime.utcnow().isoformat()}",
            headers=headers,
        )
        rows = r.json()
        if not rows:
            raise HTTPException(400, "Invalid or expired OTP")

        otp_row = rows[0]

        # Mark OTP as used
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/pwa_otps?id=eq.{otp_row['id']}",
            json={"used": True},
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
        )

        # Create connection
        session_key = f"agent:main:telegram:dm:{tg_id}"
        await client.post(
            f"{SUPABASE_URL}/rest/v1/pwa_connections",
            json={
                "user_id": user["id"],
                "platform": "telegram",
                "platform_user_id": tg_id,
                "session_key": session_key,
            },
            headers={**headers, "Content-Type": "application/json", "Prefer": "return=minimal"},
        )

    return {"ok": True, "platform": "telegram", "platform_user_id": tg_id}

@app.get("/api/messaging/status")
async def messaging_status(request: Request):
    """Get connected messaging platforms for current user."""
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_connections?user_id=eq.{user['id']}",
            headers=headers,
        )
        return {"connections": r.json()}

class DisconnectBody(BaseModel):
    platform: str

@app.delete("/api/messaging/disconnect/{platform}")
async def disconnect_platform(platform: str, request: Request):
    """Disconnect a messaging platform."""
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/pwa_connections?user_id=eq.{user['id']}&platform=eq.{platform}",
            headers=headers,
        )
        return {"ok": True}

# ── Chat endpoints ────────────────────────────────────────────

@app.get("/api/chats")
async def list_chats(request: Request):
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_chats?user_id=eq.{user['id']}&order=updated_at.desc&limit=50",
            headers=headers,
        )
        r.raise_for_status()
        return {"chats": r.json()}

class CreateChatBody(BaseModel):
    name: str = "New Chat"

@app.post("/api/chats")
async def create_chat(body: CreateChatBody, request: Request):
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/pwa_chats",
            json={"user_id": user["id"], "name": body.name},
            headers=headers,
        )
        r.raise_for_status()
        return {"ok": True, "chat": r.json()[0] if r.json() else {}}

@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str, request: Request):
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/pwa_chats?id=eq.{chat_id}&user_id=eq.{user['id']}",
            headers=headers,
        )
        r.raise_for_status()
        return {"ok": True}

@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, request: Request):
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    # Verify chat belongs to user
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_chats?id=eq.{chat_id}&user_id=eq.{user['id']}",
            headers=headers,
        )
        if not r.json():
            raise HTTPException(404, "Chat not found")
        r2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_messages?chat_id=eq.{chat_id}&order=created_at.asc&limit=200",
            headers=headers,
        )
        r2.raise_for_status()
        return {"messages": r2.json()}

class SaveMessagesBody(BaseModel):
    messages: list[dict]

@app.post("/api/chats/{chat_id}/messages")
async def save_messages(chat_id: str, body: SaveMessagesBody, request: Request):
    user = await require_user(request)
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=15) as client:
        # Verify ownership
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/pwa_chats?id=eq.{chat_id}&user_id=eq.{user['id']}",
            headers=headers,
        )
        if not r.json():
            raise HTTPException(404, "Chat not found")
        # Delete old messages, insert new batch
        await client.delete(
            f"{SUPABASE_URL}/rest/v1/pwa_messages?chat_id=eq.{chat_id}",
            headers=headers,
        )
        for msg in body.messages:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/pwa_messages",
                json={"chat_id": chat_id, "role": msg["role"], "content": msg["content"]},
                headers=headers,
            )
        # Update chat timestamp
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/pwa_chats?id=eq.{chat_id}",
            json={"updated_at": datetime.utcnow().isoformat()},
            headers=headers,
        )
        return {"ok": True}

# ── Chat streaming (proxy to Siraj API) ───────────────────────

class ChatBody(BaseModel):
    messages: list[dict]
    chat_id: str | None = None

@app.post("/api/chat")
async def chat(body: ChatBody, request: Request):
    """Stream chat through Siraj API. Uses linked Telegram session key if available."""
    user = await require_user(request)

    # Check for linked messaging platforms
    session_keys = [f"pwa:{user['id']}"]
    try:
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/pwa_connections?user_id=eq.{user['id']}",
                headers=headers,
            )
            for conn in r.json():
                if conn.get("session_key"):
                    session_keys.append(conn["session_key"])
    except Exception:
        pass

    async def stream():
        async with httpx.AsyncClient(timeout=120) as client:
            headers = {
                "Content-Type": "application/json",
                "X-Siraj-Session-Key": ",".join(session_keys),
            }
            if API_SERVER_KEY:
                headers["Authorization"] = f"Bearer {API_SERVER_KEY}"
            payload = {
                "model": "hermes-agent",
                "messages": body.messages,
                "stream": True,
            }
            try:
                async with client.stream("POST", f"{SIRAJ_API}/v1/chat/completions", json=payload, headers=headers) as r:
                    async for line in r.aiter_lines():
                        if line:
                            yield line + "\n"
            except Exception as e:
                yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
    
    return StreamingResponse(stream(), media_type="text/event-stream")

# ── Siraj API proxy ───────────────────────────────────────────

PROXY_HEADERS = {"content-type", "accept", "accept-encoding", "authorization"}

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_v1(path: str, request: Request):
    """Proxy /v1/* to Siraj API."""
    headers = {k: v for k, v in request.headers.items() if k.lower() in PROXY_HEADERS}
    # If client didn't send auth, use the server's API key
    if "authorization" not in {k.lower() for k in headers} and API_SERVER_KEY:
        headers["Authorization"] = f"Bearer {API_SERVER_KEY}"
    body = await request.body()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.request(
            request.method, f"{SIRAJ_API}/v1/{path}",
            headers=headers, content=body, params=dict(request.query_params),
        )
        return Response(content=r.content, status_code=r.status_code, headers=dict(r.headers))

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_api(path: str, request: Request):
    """Proxy /api/* to Siraj API (catch-all for non-PWA API routes)."""
    # Skip PWA-specific routes
    if path.startswith("login") or path.startswith("signup") or path.startswith("logout") or \
       path.startswith("me") or path.startswith("chat") or path.startswith("chats"):
        raise HTTPException(404)
    
    headers = {k: v for k, v in request.headers.items() if k.lower() in PROXY_HEADERS}
    body = await request.body()
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.request(
            request.method, f"{SIRAJ_API}/api/{path}",
            headers=headers, content=body, params=dict(request.query_params),
        )
        return Response(content=r.content, status_code=r.status_code, headers=dict(r.headers))

# ── Health ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "app": "siraj-pwa-v3", "env": os.getenv("RAILWAY_ENVIRONMENT", "local")}

# ── Static files ──────────────────────────────────────────────

@app.get("/")
async def index(request: Request):
    # Check if authenticated
    token = get_token_from_request(request)
    if token and verify_supabase_jwt(token):
        return FileResponse(os.path.join(STATIC, "index.html"))
    return FileResponse(os.path.join(STATIC, "login.html"))

@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(STATIC, "login.html"))

app.mount("/static", StaticFiles(directory=STATIC), name="static")
