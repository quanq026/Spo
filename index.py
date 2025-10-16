from fastapi import FastAPI, HTTPException
import requests
import os, json, time, base64

app = FastAPI(title="Spotify IoT API (Gist Storage)")

# ======================
# CONFIG
# ======================
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
GITHUB_GIST_ID = os.getenv("GITHUB_GIST_ID", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GIST_FILENAME = os.getenv("GIST_FILENAME", "gistfile1.txt")


# ======================
# GIST STORAGE
# ======================
def load_token_from_gist() -> dict:
    """Đọc token từ GitHub Gist"""
    if not GITHUB_GIST_ID or not GITHUB_TOKEN:
        return {"access_token": "", "refresh_token": "", "expires_at": 0}

    url = f"https://api.github.com/gists/{GITHUB_GIST_ID}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            content = data["files"][GIST_FILENAME]["content"]
            return json.loads(content)
    except Exception as e:
        print(f"[ERROR] Load Gist failed: {e}")
    return {"access_token": "", "refresh_token": "", "expires_at": 0}


def save_token_to_gist(access_token: str, refresh_token: str, expires_at: float):
    """Lưu token vào GitHub Gist"""
    if not GITHUB_GIST_ID or not GITHUB_TOKEN:
        print("[WARN] Gist not configured")
        return False

    url = f"https://api.github.com/gists/{GITHUB_GIST_ID}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github+json",
    }
    data = {
        "files": {
            GIST_FILENAME: {
                "content": json.dumps(
                    {
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "expires_at": expires_at,
                    },
                    indent=2,
                )
            }
        }
    }

    try:
        res = requests.patch(url, headers=headers, json=data, timeout=10)
        print(f"[DEBUG] Gist save status: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print(f"[ERROR] Save Gist failed: {e}")
        return False


# ======================
# SPOTIFY TOKEN LOGIC
# ======================
def renew_access_token(refresh_token: str):
    """Làm mới access token"""
    url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}

    res = requests.post(url, headers=headers, data=data, timeout=10)
    print(f"[DEBUG] Renew status: {res.status_code}")

    if res.status_code == 200:
        token_data = res.json()
        access_token = token_data["access_token"]
        new_refresh_token = token_data.get("refresh_token", refresh_token)
        expires_at = time.time() + token_data.get("expires_in", 3600)
        save_token_to_gist(access_token, new_refresh_token, expires_at)
        return token_data
    else:
        print(f"[ERROR] Renew failed: {res.text[:200]}")
        return None


def get_valid_token() -> str:
    """Đọc token từ Gist, tự renew nếu gần hết hạn"""
    cached = load_token_from_gist()
    access_token = cached.get("access_token", "")
    refresh_token = cached.get("refresh_token", "")
    expires_at = cached.get("expires_at", 0)

    if not refresh_token:
        raise HTTPException(status_code=400, detail="No refresh_token found in Gist")

    if time.time() >= expires_at - 300:
        print("[DEBUG] Token expired or near expiry → renewing...")
        token_data = renew_access_token(refresh_token)
        if token_data:
            return token_data["access_token"]

    return access_token


# ======================
# SPOTIFY API CALL
# ======================
def get_currently_playing(access_token: str):
    url = "https://api.spotify.com/v1/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}
    return requests.get(url, headers=headers, timeout=10)


def parse_track_data(data: dict):
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
    return {
        "status": "✅ Gist storage active",
        "gist_id": GITHUB_GIST_ID[:10] + "..." if GITHUB_GIST_ID else None,
        "endpoints": {
            "/current": "Get currently playing track",
            "/force-renew": "Force renew token",
            "/debug": "Inspect token",
            "/init": "Initialize Gist with tokens",
        },
    }


@app.get("/current")
def current():
    access_token = get_valid_token()
    res = get_currently_playing(access_token)

    if res.status_code == 401:
        print("[DEBUG] 401 → retry renew")
        cached = load_token_from_gist()
        token_data = renew_access_token(cached.get("refresh_token", ""))
        if token_data:
            access_token = token_data["access_token"]
            res = get_currently_playing(access_token)

    if res.status_code == 200:
        return parse_track_data(res.json())
    elif res.status_code == 204:
        return {"is_playing": False, "message": "Nothing playing"}
    else:
        raise HTTPException(status_code=res.status_code, detail=res.text)


@app.get("/force-renew")
def force_renew():
    cached = load_token_from_gist()
    refresh_token = cached.get("refresh_token", "")
    if not refresh_token:
        return {"error": "No refresh_token found in Gist"}

    token_data = renew_access_token(refresh_token)
    return (
        {"success": True, "message": "Token renewed", "expires_in": token_data.get("expires_in", 3600)}
        if token_data
        else {"success": False, "message": "Failed to renew token"}
    )


@app.get("/debug")
def debug():
    cached = load_token_from_gist()
    return {
        "gist_id": GITHUB_GIST_ID[:10] + "..." if GITHUB_GIST_ID else None,
        "access_token_preview": cached.get("access_token", "")[:20] + "...",
        "has_refresh_token": bool(cached.get("refresh_token")),
        "expires_at": cached.get("expires_at"),
        "expires_in_seconds": int(cached.get("expires_at", 0) - time.time()),
    }


@app.post("/init")
def init_tokens(request: dict):
    access_token = request.get("access_token", "")
    refresh_token = request.get("refresh_token", "")
    if not access_token or not refresh_token:
        return {"error": "Both tokens required"}

    expires_at = time.time() + 3600
    success = save_token_to_gist(access_token, refresh_token, expires_at)
    return (
        {"success": True, "message": "Saved to Gist", "expires_in": 3600}
        if success
        else {"success": False, "message": "Failed to save"}
    )


app = app
