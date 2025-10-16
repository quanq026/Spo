from fastapi import FastAPI, HTTPException
import requests
import base64
import os
import time

app = FastAPI(title="Spotify IoT API (Self-Managed)")

# ======================
# CONFIG
# ======================
CLIENT_ID = "8b3fc1403b66432ebb25bc9faf2e3de0"
CLIENT_SECRET = "8fcf7a30219644378e89a34bb4f71b77"

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY", "")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID", "")


# ======================
# JSONBin FUNCTIONS
# ======================
def load_token_from_jsonbin() -> dict:
    """Đọc token (access, refresh, expires_at) từ JSONBin"""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        return {"access_token": "", "refresh_token": "", "expires_at": 0}

    try:
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}/latest"
        headers = {"X-Master-Key": JSONBIN_API_KEY}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            return data.get("record", {"access_token": "", "refresh_token": "", "expires_at": 0})
    except Exception as e:
        print(f"[ERROR] Load token JSONBin failed: {e}")
    return {"access_token": "", "refresh_token": "", "expires_at": 0}


def save_token_to_jsonbin(access_token: str, refresh_token: str, expires_at: float):
    """Lưu token mới vào JSONBin"""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        print("[WARN] JSONBin not configured, skipping save")
        return False

    try:
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
        headers = {
            "X-Master-Key": JSONBIN_API_KEY,
            "Content-Type": "application/json",
        }
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        }
        res = requests.put(url, headers=headers, json=data, timeout=5)
        print(f"[DEBUG] JSONBin save status: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print(f"[ERROR] JSONBin save failed: {e}")
        return False


# ======================
# SPOTIFY TOKEN LOGIC
# ======================
def renew_access_token(refresh_token: str):
    """Làm mới access_token bằng refresh_token từ JSONBin"""
    url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}

    try:
        res = requests.post(url, headers=headers, data=data, timeout=10)
        print(f"[DEBUG] Renew token status: {res.status_code}")
        if res.status_code == 200:
            token_data = res.json()
            access_token = token_data["access_token"]
            new_refresh_token = token_data.get("refresh_token", refresh_token)
            expires_at = time.time() + token_data.get("expires_in", 3600)

            save_token_to_jsonbin(access_token, new_refresh_token, expires_at)
            print("[INFO] Token renewed successfully.")
            return token_data
        else:
            print(f"[DEBUG] Renew failed: {res.text[:200]}")
            return None
    except Exception as e:
        print(f"[ERROR] Renew exception: {e}")
        return None


def get_valid_token() -> str:
    """Đọc token từ JSONBin, tự động renew nếu sắp hết hạn"""
    cached = load_token_from_jsonbin()
    access_token = cached.get("access_token", "")
    refresh_token = cached.get("refresh_token", "")
    expires_at = cached.get("expires_at", 0)

    print(f"[DEBUG] Token expires in {int(expires_at - time.time())}s")

    if not refresh_token:
        raise HTTPException(status_code=400, detail="No refresh_token found in JSONBin")

    # Nếu token sắp hết hạn (dưới 5 phút)
    if time.time() >= expires_at - 300:
        print("[DEBUG] Token near expiry, renewing...")
        token_data = renew_access_token(refresh_token)
        if token_data:
            return token_data["access_token"]

    return access_token


def get_currently_playing(access_token: str):
    """Lấy bài hát đang phát"""
    url = "https://api.spotify.com/v1/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}
    return requests.get(url, headers=headers, timeout=10)


def parse_track_data(data: dict):
    """Parse dữ liệu bài hát"""
    if data.get("is_playing"):
        item = data.get("item", {})
        return {
            "track": item.get("name", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name", ""),
            "is_playing": True,
        }
    return {"is_playing": False}


# ======================
# ROUTES
# ======================
@app.get("/")
def root():
    jsonbin_configured = bool(JSONBIN_API_KEY and JSONBIN_BIN_ID)
    return {
        "message": "Spotify API for IoT/ESP32 with JSONBin Storage",
        "status": "✅ Ready" if jsonbin_configured else "❌ JSONBin not configured",
        "storage": {
            "type": "JSONBin.io",
            "configured": jsonbin_configured,
            "bin_id": JSONBIN_BIN_ID[:10] + "..." if JSONBIN_BIN_ID else None,
        },
        "endpoints": {
            "/current": "Get currently playing track",
            "/force-renew": "Force renew token",
            "/debug": "Inspect token status",
            "/init": "Initialize JSONBin with tokens",
        },
    }


@app.get("/current")
def get_current():
    """Endpoint cho ESP32/IoT - tự động renew token"""
    if not (JSONBIN_API_KEY and JSONBIN_BIN_ID):
        raise HTTPException(status_code=500, detail="JSONBin not configured")

    try:
        access_token = get_valid_token()
        res = get_currently_playing(access_token)

        if res.status_code == 401:
            print("[DEBUG] 401 - forcing renew...")
            cached = load_token_from_jsonbin()
            token_data = renew_access_token(cached.get("refresh_token", ""))
            if token_data:
                access_token = token_data["access_token"]
                res = get_currently_playing(access_token)
            else:
                raise HTTPException(status_code=401, detail="Token refresh failed")

        if res.status_code == 200:
            return parse_track_data(res.json())
        elif res.status_code == 204:
            return {"is_playing": False, "message": "Nothing playing"}
        else:
            raise HTTPException(status_code=res.status_code, detail=res.text)

    except Exception as e:
        return {"error": "Unexpected error", "detail": str(e)}


@app.get("/force-renew")
def force_renew():
    """Làm mới token thủ công - chỉ đọc refresh_token từ JSONBin"""
    cached = load_token_from_jsonbin()
    refresh_token = cached.get("refresh_token", "")

    if not refresh_token:
        return {"error": "No refresh_token found in JSONBin"}

    token_data = renew_access_token(refresh_token)
    if token_data:
        return {
            "success": True,
            "message": "Token renewed and saved to JSONBin",
            "expires_in": token_data.get("expires_in", 3600),
        }
    else:
        return {"success": False, "message": "Failed to renew token"}


@app.get("/debug")
def debug():
    """Kiểm tra trạng thái token từ JSONBin"""
    cached = load_token_from_jsonbin()
    return {
        "jsonbin": {
            "configured": bool(JSONBIN_API_KEY and JSONBIN_BIN_ID),
            "bin_id": JSONBIN_BIN_ID[:10] + "..." if JSONBIN_BIN_ID else None,
        },
        "cached_token": {
            "has_token": bool(cached.get("access_token")),
            "token_preview": cached.get("access_token", "")[:20] + "..."
            if cached.get("access_token")
            else None,
            "expires_at": cached.get("expires_at", 0),
            "expires_in_seconds": max(0, int(cached.get("expires_at", 0) - time.time())),
            "is_expired": time.time() >= cached.get("expires_at", 0),
        },
        "refresh_token_stored": bool(cached.get("refresh_token")),
        "timestamp": time.time(),
    }


@app.get("/init")
def init_tokens():
    """Hướng dẫn khởi tạo token ban đầu"""
    return {
        "message": "Manual initialization endpoint",
        "instructions": [
            "POST /init",
            "Body: {",
            '  "access_token": "BQDxxx...",',
            '  "refresh_token": "AQDyyy..."',
            "}",
        ],
        "note": "Once saved, system will auto-manage tokens using JSONBin",
    }


@app.post("/init")
def init_tokens_post(request: dict):
    """Lưu token ban đầu vào JSONBin"""
    if not (JSONBIN_API_KEY and JSONBIN_BIN_ID):
        return {"error": "JSONBin not configured"}

    access_token = request.get("access_token", "")
    refresh_token = request.get("refresh_token", "")
    if not access_token or not refresh_token:
        return {"error": "Both access_token and refresh_token required"}

    expires_at = time.time() + 3600
    success = save_token_to_jsonbin(access_token, refresh_token, expires_at)
    return (
        {"success": True, "message": "Tokens saved to JSONBin", "expires_in": 3600}
        if success
        else {"success": False, "message": "Failed to save tokens"}
    )


# Vercel entrypoint
app = app
