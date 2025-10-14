from fastapi import FastAPI, HTTPException
import requests
import base64
import os
import json
from pathlib import Path
import time

app = FastAPI(title="Spotify IoT API")

CLIENT_ID = "8b3fc1403b66432ebb25bc9faf2e3de0"
CLIENT_SECRET = "8fcf7a30219644378e89a34bb4f71b77"

# File cache cho token (Vercel cho phép write vào /tmp)
TOKEN_CACHE_FILE = "/tmp/spotify_token.json"

def get_cached_token():
    """Lấy token từ cache hoặc env"""
    try:
        if os.path.exists(TOKEN_CACHE_FILE):
            with open(TOKEN_CACHE_FILE, 'r') as f:
                cache = json.load(f)
                # Kiểm tra xem token còn hạn không (buffer 5 phút)
                if cache.get('expires_at', 0) > time.time() + 300:
                    return cache.get('access_token')
    except Exception:
        pass
    
    # Fallback về env variable
    return os.getenv("SPOTIFY_ACCESS_TOKEN", "")

def save_token_to_cache(access_token: str, expires_in: int = 3600):
    """Lưu token vào cache"""
    try:
        cache_data = {
            'access_token': access_token,
            'expires_at': time.time() + expires_in,
            'updated_at': time.time()
        }
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump(cache_data, f)
    except Exception as e:
        print(f"Failed to cache token: {e}")

def renew_access_token(refresh_token: str):
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
        if res.status_code == 200:
            return res.json()
        else:
            return None
    except Exception:
        return None

def get_currently_playing(access_token: str):
    """Lấy bài hát đang phát"""
    url = "https://api.spotify.com/v1/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        return requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        raise

@app.get("/current")
def get_current():
    """
    Endpoint đơn giản cho ESP32/IoT - chỉ cần gọi /current
    Token được cache tự động
    """
    refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", "")
    
    if not refresh_token:
        return {
            "error": "Tokens not configured",
            "message": "Please set SPOTIFY_REFRESH_TOKEN in Vercel Environment Variables"
        }
    
    try:
        # Lấy token từ cache hoặc env
        access_token = get_cached_token()
        
        if not access_token:
            # Nếu không có token, renew ngay
            token_data = renew_access_token(refresh_token)
            if token_data and "access_token" in token_data:
                access_token = token_data["access_token"]
                save_token_to_cache(access_token, token_data.get('expires_in', 3600))
            else:
                raise HTTPException(status_code=401, detail="Failed to get initial token")
        
        # Thử gọi API
        res = get_currently_playing(access_token)
        
        # Nếu token hết hạn (401), làm mới
        if res.status_code == 401:
            token_data = renew_access_token(refresh_token)
            if token_data and "access_token" in token_data:
                new_access_token = token_data["access_token"]
                save_token_to_cache(new_access_token, token_data.get('expires_in', 3600))
                
                # Thử lại với token mới
                res = get_currently_playing(new_access_token)
            else:
                raise HTTPException(status_code=401, detail="Failed to refresh token")

        # Xử lý response
        if res.status_code == 200:
            return parse_track_data(res.json())
        elif res.status_code == 204:
            return {"is_playing": False, "message": "Nothing playing"}
        else:
            raise HTTPException(status_code=res.status_code, detail=res.text)
            
    except requests.exceptions.ConnectionError:
        return {
            "error": "Network connection failed",
            "is_playing": False
        }
    except requests.exceptions.Timeout:
        return {
            "error": "Request timeout",
            "is_playing": False
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "error": "Unexpected error",
            "is_playing": False,
            "detail": str(e)
        }

def parse_track_data(data: dict):
    """Parse dữ liệu track từ Spotify API"""
    if data.get("is_playing"):
        item = data["item"]
        return {
            "track": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "is_playing": True
        }
    else:
        return {"is_playing": False}

@app.get("/")
def root():
    refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", "")
    has_cached = os.path.exists(TOKEN_CACHE_FILE)
    
    return {
        "message": "Spotify API for IoT/ESP32",
        "status": "✅ Ready" if refresh_token else "❌ Not configured",
        "cache_status": "✅ Token cached" if has_cached else "⚠️ No cache yet",
        "endpoints": {
            "/current": "Get currently playing track (auto-renews token)",
            "/ping": "Keep function warm"
        },
        "setup": {
            "1": "Get REFRESH_TOKEN from Spotify (only this is needed!)",
            "2": "Add to Vercel Environment Variables: SPOTIFY_REFRESH_TOKEN",
            "3": "Deploy - token will auto-renew and cache"
        }
    }

@app.get("/ping")
def ping():
    """Keep function warm"""
    return {"status": "alive", "timestamp": time.time()}

# For Vercel
app = app
