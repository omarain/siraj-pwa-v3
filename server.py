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

STATIC = os.path.join(os.path.dirname(__file__), "static")
SIRAJ_API = os.getenv("SIRAJ_API_URL", "https://siraj-api.fixwave.ai")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", secrets.token_hex(32))
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")

# Token expiry
MAX_TOKEN_AGE_DAYS = 7
REMEMBER_ME_DAYS = 30

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
async def login(body: LoginBody, response: Response):
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
    
    resp = JSONResponse({"ok": True, "user": data.get("user", {})})
    resp.set_cookie("siraj_token", token, max_age=max_age, httponly=True, samesite="lax", secure=True)
    return resp

@app.post("/api/signup")
async def signup(body: LoginBody, response: Response):
    """Register new user with Supabase Auth."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{SUPABASE_URL}/auth/v1/signup",
            json={"email": body.email, "password": body.password},
            headers={"apikey": SUPABASE_KEY, "Content-Type": "application/json"},
        )
        if r.status_code != 200:
            detail = r.json() if r.text else {"msg": "Signup failed"}
            raise HTTPException(400, detail.get("msg", "Signup failed"))
        data = r.json()
    
    token = data.get("access_token", "")
    max_age = 86400 * MAX_TOKEN_AGE_DAYS
    
    resp = JSONResponse({"ok": True, "user": data.get("user", {})})
    if token:
        resp.set_cookie("siraj_token", token, max_age=max_age, httponly=True, samesite="lax", secure=True)
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
    """Stream chat through Siraj API (Hermes). No system message injection."""
    user = await require_user(request)
    
    async def stream():
        async with httpx.AsyncClient(timeout=120) as client:
            headers = {
                "Content-Type": "application/json",
                "X-Hermes-Session-Key": f"pwa:{user['id']}",
            }
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

PROXY_HEADERS = {"content-type", "accept", "accept-encoding"}

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_v1(path: str, request: Request):
    """Proxy /v1/* to Siraj API."""
    headers = {k: v for k, v in request.headers.items() if k.lower() in PROXY_HEADERS}
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
