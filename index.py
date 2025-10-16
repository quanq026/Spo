from fastapi import FastAPI, HTTPException
import requests
import base64
import os
import time
from typing import Optional

app = FastAPI(title="Spotify IoT API")

CLIENT_ID = "8b3fc1403b66432ebb25bc9faf2e3de0"
CLIENT_SECRET = "8fcf7a30219644378e89a34bb4f71b77"

# Environment Variables
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN", "")

# JSONBin.io config
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY", "")
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID", "")

def load_token_from_jsonbin() -> dict:
    """Đọc token từ JSONBin"""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        return {"access_token": "", "refresh_token": "", "expires_at": 0}
    
    try:
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}/latest"
        headers = {"X-Master-Key": JSONBIN_API_KEY}
        res = requests.get(url, headers=headers, timeout=5)
        
        if res.status_code == 200:
            data = res.json()
            return data.get("record", {"access_token": "", "refresh_token": "", "expires_at": 0})
    except:
        pass
    
    return {"access_token": "", "refresh_token": "", "expires_at": 0}

def save_token_to_jsonbin(access_token: str, refresh_token: str, expires_at: float):
    """Lưu token vào JSONBin"""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        print("[WARN] JSONBin not configured, skipping save")
        return False
    
    try:
        url = f"https://api.jsonbin.io/v3/b/{JSONBIN_BIN_ID}"
        headers = {
            "X-Master-Key": JSONBIN_API_KEY,
            "Content-Type": "application/json"
        }
        data = {
            "access_token": access_token,
            "refresh_token": refresh_token,  # ✅ LƯU CẢ REFRESH TOKEN
            "expires_at": expires_at
        }
        
        res = requests.put(url, headers=headers, json=data, timeout=5)
        print(f"[DEBUG] JSONBin save status: {res.status_code}")
        return res.status_code == 200
    except Exception as e:
        print(f"[ERROR] JSONBin save failed: {e}")
        return False

def renew_access_token(refresh_token: str) -> Optional[dict]:
    """Làm mới access token"""
    url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10)
        print(f"[DEBUG] Renew token status: {res.status_code}")
        
        if res.status_code == 200:
            token_data = res.json()
            access_token = token_data["access_token"]
            # ⚠️ Spotify có thể trả về refresh_token mới, nếu không có thì giữ cái cũ
            new_refresh_token = token_data.get("refresh_token", refresh_token)
            expires_at = time.time() + token_data.get("expires_in", 3600)
            
            # ✅ Lưu CẢ HAI tokens vào JSONBin
            save_token_to_jsonbin(access_token, new_refresh_token, expires_at)
            
            print(f"[DEBUG] Token renewed, expires in {token_data.get('expires_in', 3600)}s")
            return token_data
        else:
            print(f"[DEBUG] Renew failed: {res.text[:200]}")
            return None
    except Exception as e:
        print(f"[DEBUG] Renew exception: {str(e)}")
        return None

def get_valid_token() -> str:
    """Lấy token hợp lệ (từ JSONBin hoặc renew)"""
    # Đọc token từ JSONBin
    cached_data = load_token_from_jsonbin()
    access_token = cached_data.get("access_token", "")
    refresh_token = cached_data.get("refresh_token", "")
    expires_at = cached_data.get("expires_at", 0)
    
    print(f"[DEBUG] Loaded from JSONBin - expires in {int(expires_at - time.time())}s")
    
    # Nếu không có refresh_token trong JSONBin, dùng từ ENV
    if not refresh_token:
        refresh_token = SPOTIFY_REFRESH_TOKEN
        print("[DEBUG] Using refresh_token from ENV (not in JSONBin)")
    
    # Nếu token sắp hết hạn (còn < 5 phút), renew ngay
    if time.time() >= expires_at - 300:
        if refresh_token:
            print("[DEBUG] Token expired or near expiry, renewing...")
            token_data = renew_access_token(refresh_token)
            if token_data:
                return token_data["access_token"]
    
    return access_token

def get_currently_playing(access_token: str):
    """Lấy bài hát đang phát"""
    url = "https://api.spotify.com/v1/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}
    return requests.get(url, headers=headers, timeout=10)

@app.get("/current")
def get_current():
    """Endpoint cho ESP32/IoT với auto token refresh"""
    if not SPOTIFY_REFRESH_TOKEN:
        return {
            "error": "SPOTIFY_REFRESH_TOKEN not configured",
            "message": "Set it in Vercel Environment Variables"
        }
    
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        return {
            "error": "JSONBin not configured",
            "message": "Set JSONBIN_API_KEY and JSONBIN_BIN_ID in Vercel Environment Variables"
        }
    
    try:
        # Lấy token hợp lệ (tự động renew nếu cần)
        access_token = get_valid_token()
        
        if not access_token:
            raise HTTPException(status_code=401, detail="Cannot get valid token")
        
        # Gọi Spotify API
        res = get_currently_playing(access_token)
        
        print(f"[DEBUG] Spotify API response: {res.status_code}")
        
        # Nếu vẫn 401 (token cache sai), force renew
        if res.status_code == 401:
            print("[DEBUG] Got 401, forcing token renewal...")
            token_data = renew_access_token(SPOTIFY_REFRESH_TOKEN)
            if token_data:
                access_token = token_data["access_token"]
                res = get_currently_playing(access_token)
                print(f"[DEBUG] Retry with new token: {res.status_code}")
            else:
                raise HTTPException(status_code=401, detail="Failed to refresh token")
        
        # Parse response
        if res.status_code == 200:
            return parse_track_data(res.json())
        elif res.status_code == 204:
            return {"is_playing": False, "message": "Nothing playing"}
        else:
            raise HTTPException(status_code=res.status_code, detail=res.text)
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"[DEBUG] Exception in get_current: {str(e)}")
        return {
            "error": "Unexpected error",
            "is_playing": False,
            "detail": str(e)
        }

def parse_track_data(data: dict):
    """Parse dữ liệu track"""
    if data.get("is_playing"):
        item = data["item"]
        return {
            "track": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "is_playing": True
        }
    return {"is_playing": False}

@app.get("/")
def root():
    jsonbin_configured = bool(JSONBIN_API_KEY and JSONBIN_BIN_ID)
    refresh_configured = bool(SPOTIFY_REFRESH_TOKEN)
    
    return {
        "message": "Spotify API for IoT/ESP32 with JSONBin Storage",
        "status": "✅ Ready" if (jsonbin_configured and refresh_configured) else "❌ Not fully configured",
        "storage": {
            "type": "JSONBin.io",
            "configured": jsonbin_configured,
            "bin_id": JSONBIN_BIN_ID[:10] + "..." if JSONBIN_BIN_ID else None
        },
        "token_status": {
            "has_refresh_token": refresh_configured
        },
        "endpoints": {
            "/current": "Get currently playing track",
            "/debug": "Debug token and storage status",
            "/force-renew": "Force token renewal"
        },
        "setup": [
            "1. Create account at https://jsonbin.io",
            "2. Get API Key from dashboard",
            "3. Create a bin with: {\"access_token\":\"\",\"refresh_token\":\"\",\"expires_at\":0}",
            "4. Set env vars: JSONBIN_API_KEY, JSONBIN_BIN_ID, SPOTIFY_REFRESH_TOKEN",
            "5. Redeploy",
            "6. Call /init to populate JSONBin with initial tokens"
        ]
    }

@app.get("/debug")
def debug():
    """Debug endpoint"""
    cached_data = load_token_from_jsonbin()
    
    return {
        "jsonbin": {
            "configured": bool(JSONBIN_API_KEY and JSONBIN_BIN_ID),
            "bin_id": JSONBIN_BIN_ID[:10] + "..." if JSONBIN_BIN_ID else None
        },
        "cached_token": {
            "has_token": bool(cached_data.get("access_token")),
            "token_preview": cached_data.get("access_token", "")[:20] + "..." if cached_data.get("access_token") else None,
            "expires_at": cached_data.get("expires_at", 0),
            "expires_in_seconds": max(0, int(cached_data.get("expires_at", 0) - time.time())),
            "is_expired": time.time() >= cached_data.get("expires_at", 0)
        },
        "refresh_token": {
            "configured": bool(SPOTIFY_REFRESH_TOKEN),
            "preview": SPOTIFY_REFRESH_TOKEN[:20] + "..." if SPOTIFY_REFRESH_TOKEN else None
        },
        "timestamp": time.time()
    }

@app.get("/force-renew")
def force_renew():
    """Force renew token ngay lập tức"""
    if not SPOTIFY_REFRESH_TOKEN:
        return {"error": "SPOTIFY_REFRESH_TOKEN not configured"}
    
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        return {"error": "JSONBin not configured"}
    
    print("[FORCE] Renewing token...")
    token_data = renew_access_token(SPOTIFY_REFRESH_TOKEN)
    
    if token_data:
        return {
            "success": True,
            "message": "Token renewed and saved to JSONBin",
            "expires_in": token_data.get("expires_in", 3600)
        }
    else:
        return {
            "success": False,
            "message": "Failed to renew token"
        }

@app.get("/init")
def init_tokens():
    """Initialize JSONBin với tokens từ spotify_tokens.json của bạn"""
    return {
        "message": "Manual initialization endpoint",
        "instructions": [
            "Paste your tokens here:",
            "POST /init",
            "Body: {",
            '  "access_token": "BQDdoRQa...",',
            '  "refresh_token": "AQDh726y..."',
            "}"
        ],
        "note": "Or just set SPOTIFY_REFRESH_TOKEN in ENV and call /force-renew"
    }

@app.post("/init")
def init_tokens_post(access_token: str = "", refresh_token: str = ""):
    """Save initial tokens to JSONBin"""
    if not JSONBIN_API_KEY or not JSONBIN_BIN_ID:
        return {"error": "JSONBin not configured"}
    
    if not access_token or not refresh_token:
        return {"error": "Both access_token and refresh_token required"}
    
    expires_at = time.time() + 3600  # 1 hour from now
    success = save_token_to_jsonbin(access_token, refresh_token, expires_at)
    
    if success:
        return {
            "success": True,
            "message": "Tokens saved to JSONBin successfully",
            "expires_in": 3600
        }
    else:
        return {
            "success": False,
            "message": "Failed to save tokens to JSONBin"
        }

# For Vercel
app = app
