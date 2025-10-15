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

# ✅ In-memory cache cho token (tồn tại trong lifecycle của container)
token_cache = {
    "access_token": os.getenv("SPOTIFY_ACCESS_TOKEN", ""),
    "expires_at": 0  # timestamp
}

def renew_access_token(refresh_token: str) -> Optional[dict]:
    """Làm mới access token và cache lại"""
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
        if res.status_code == 200:
            token_data = res.json()
            
            # ✅ LƯU VÀO CACHE IN-MEMORY
            token_cache["access_token"] = token_data["access_token"]
            token_cache["expires_at"] = time.time() + token_data.get("expires_in", 3600)
            
            return token_data
        return None
    except Exception:
        return None

def get_valid_token() -> str:
    """Lấy token hợp lệ (từ cache hoặc renew)"""
    # Nếu token sắp hết hạn (còn < 5 phút), renew ngay
    if time.time() >= token_cache["expires_at"] - 300:
        if SPOTIFY_REFRESH_TOKEN:
            token_data = renew_access_token(SPOTIFY_REFRESH_TOKEN)
            if token_data:
                return token_cache["access_token"]
    
    return token_cache["access_token"]

def get_currently_playing(access_token: str):
    """Lấy bài hát đang phát"""
    url = "https://api.spotify.com/v1/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}
    return requests.get(url, headers=headers, timeout=10)

@app.get("/current")
def get_current():
    """
    Endpoint cho ESP32/IoT với auto token refresh
    """
    if not SPOTIFY_REFRESH_TOKEN:
        return {
            "error": "SPOTIFY_REFRESH_TOKEN not configured",
            "message": "Set it in Vercel Environment Variables"
        }
    
    try:
        # ✅ Lấy token hợp lệ (tự động renew nếu cần)
        access_token = get_valid_token()
        
        if not access_token:
            raise HTTPException(status_code=401, detail="Cannot get valid token")
        
        # Gọi Spotify API
        res = get_currently_playing(access_token)
        
        # Nếu vẫn 401 (token cache sai), force renew
        if res.status_code == 401:
            token_data = renew_access_token(SPOTIFY_REFRESH_TOKEN)
            if token_data:
                access_token = token_data["access_token"]
                res = get_currently_playing(access_token)
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
    configured = bool(token_cache["access_token"] and SPOTIFY_REFRESH_TOKEN)
    return {
        "message": "Spotify API for IoT/ESP32",
        "status": "✅ Ready" if configured else "❌ Not configured",
        "token_status": {
            "cached": bool(token_cache["access_token"]),
            "expires_in": max(0, int(token_cache["expires_at"] - time.time())) if token_cache["expires_at"] else 0
        },
        "endpoints": {
            "/current": "Get currently playing track",
            "/ping": "Health check"
        }
    }
# For Vercel
app = app
