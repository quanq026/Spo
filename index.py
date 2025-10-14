from fastapi import FastAPI, Query, HTTPException
import requests
import base64
import os

app = FastAPI(title="Spotify IoT API")

CLIENT_ID = "8b3fc1403b66432ebb25bc9faf2e3de0"
CLIENT_SECRET = "8fcf7a30219644378e89a34bb4f71b77"

# Lấy tokens từ Environment Variables của Vercel
SPOTIFY_ACCESS_TOKEN = os.getenv("SPOTIFY_ACCESS_TOKEN", "")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN", "")

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
    Tokens được lấy từ Environment Variables
    """
    if not SPOTIFY_ACCESS_TOKEN or not SPOTIFY_REFRESH_TOKEN:
        return {
            "error": "Tokens not configured",
            "message": "Please set SPOTIFY_ACCESS_TOKEN and SPOTIFY_REFRESH_TOKEN in Vercel Environment Variables"
        }
    
    try:
        # Thử với access token từ env
        res = get_currently_playing(SPOTIFY_ACCESS_TOKEN)
        
        # Nếu token hết hạn (401), làm mới
        if res.status_code == 401:
            token_data = renew_access_token(SPOTIFY_REFRESH_TOKEN)
            if token_data and "access_token" in token_data:
                new_access_token = token_data["access_token"]
                
                # Thử lại với token mới
                res = get_currently_playing(new_access_token)
                
                # Log token mới (cần update lại env vars trên Vercel)
                if res.status_code == 200:
                    data = res.json()
                    result = parse_track_data(data)
                    result["note"] = f"⚠️ Token refreshed! Update SPOTIFY_ACCESS_TOKEN in Vercel to: {new_access_token[:20]}..."
                    return result
            else:
                raise HTTPException(status_code=401, detail="Failed to refresh token")

        # Xử lý response bình thường
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

@app.get("/keep-alive")
def keep_alive():
    """Endpoint for Vercel Cron to keep function warm"""
    import time
    return {
        "status": "alive",
        "timestamp": int(time.time()),
        "message": "Function is warm"
    }

@app.get("/")
def root():
    configured = bool(SPOTIFY_ACCESS_TOKEN and SPOTIFY_REFRESH_TOKEN)
    return {
        "message": "Spotify API for IoT/ESP32",
        "status": "✅ Ready" if configured else "❌ Not configured",
        "endpoints": {
            "/current": "Get currently playing track (simple URL for IoT)",
            "/keep-alive": "Keep function warm (auto-called by Vercel Cron)"
        },
        "setup": {
            "1": "Get tokens from Spotify",
            "2": "Add to Vercel Environment Variables: SPOTIFY_ACCESS_TOKEN, SPOTIFY_REFRESH_TOKEN",
            "3": "Redeploy",
            "4": "Call /current from ESP32"
        }
    }

# For Vercel
app = app
