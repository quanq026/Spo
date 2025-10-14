from fastapi import FastAPI, HTTPException
import requests
import base64
import os
import time

app = FastAPI(title="Spotify IoT API")

CLIENT_ID = "8b3fc1403b66432ebb25bc9faf2e3de0"
CLIENT_SECRET = "8fcf7a30219644378e89a34bb4f71b77"

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
    Endpoint đơn giản cho ESP32/IoT
    Mỗi lần gọi sẽ renew token mới (đảm bảo luôn hoạt động)
    """
    refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN", "")
    
    if not refresh_token:
        return {
            "error": "SPOTIFY_REFRESH_TOKEN not configured",
            "message": "Please set SPOTIFY_REFRESH_TOKEN in Vercel Environment Variables"
        }
    
    try:
        # Luôn renew token mới mỗi lần gọi
        token_data = renew_access_token(refresh_token)
        
        if not token_data or "access_token" not in token_data:
            raise HTTPException(status_code=401, detail="Failed to refresh token")
        
        access_token = token_data["access_token"]
        
        # Gọi Spotify API
        res = get_currently_playing(access_token)
        
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
    
    return {
        "message": "Spotify API for IoT/ESP32 - Always Fresh Token",
        "status": "✅ Ready" if refresh_token else "❌ Not configured",
        "endpoints": {
            "/current": "Get currently playing track (auto-renews every call)",
            "/ping": "Keep function warm"
        },
        "setup": {
            "1": "Get REFRESH_TOKEN from Spotify",
            "2": "Add to Vercel Environment Variables: SPOTIFY_REFRESH_TOKEN",
            "3": "Deploy - done!"
        },
        "note": "This version renews token on every request - simple and reliable"
    }

@app.get("/ping")
def ping():
    """Keep function warm"""
    return {"status": "alive", "timestamp": time.time()}

# For Vercel
app = app
