from fastapi import FastAPI, Query
import requests
import base64
from typing import Optional
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Spotify Local API")

# Biến cố định - cần thay đổi trực tiếp trong code
CLIENT_ID = "8b3fc1403b66432ebb25bc9faf2e3de0"
CLIENT_SECRET = "8fcf7a30219644378e89a34bb4f71b77"

# Biến động - sẽ được cập nhật qua API
current_tokens: dict[str, Optional[str]] = {
    "access_token": None,
    "refresh_token": None
}

def renew_access_token():
    url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": current_tokens["refresh_token"]
    }
    
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10)
        if res.status_code == 200:
            j = res.json()
            current_tokens["access_token"] = j.get("access_token")
            if "refresh_token" in j:
                current_tokens["refresh_token"] = j["refresh_token"]
            return current_tokens["access_token"]
        else:
            raise Exception(f"Renew failed: {res.status_code} {res.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error renewing token: {e}")
        raise

def get_currently_playing(access_token):
    url = "https://api.spotify.com/v1/me/player/currently-playing"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return res
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting currently playing: {e}")
        raise

@app.get("/set-tokens")
def set_tokens(access_token: str = Query(...), refresh_token: str = Query(...)):
    """Cập nhật tokens qua URL"""
    current_tokens["access_token"] = access_token
    current_tokens["refresh_token"] = refresh_token
    return {"message": "Tokens updated successfully"}

@app.get("/current")
def get_current():
    if not current_tokens["access_token"] or not current_tokens["refresh_token"]:
        return {"error": "Tokens not set. Please call /set-tokens first"}
    
    try:
        res = get_currently_playing(current_tokens["access_token"])
        
        # Nếu token hết hạn, làm mới và thử lại
        if res.status_code == 401:
            try:
                renew_access_token()
                res = get_currently_playing(current_tokens["access_token"])
            except Exception as e:
                logger.error(f"Error refreshing token: {e}")
                return {"error": f"Token refresh failed: {str(e)}"}

        if res.status_code == 200:
            data = res.json()
            if data.get("is_playing"):
                item = data["item"]
                track = item["name"]
                artist = ", ".join(a["name"] for a in item["artists"])
                album = item["album"]["name"]
                return {
                    "track": track,
                    "artist": artist,
                    "album": album,
                    "is_playing": True
                }
            else:
                return {"is_playing": False}
        elif res.status_code == 204:
            return {"is_playing": False, "message": "Nothing playing"}
        else:
            return {"error": f"{res.status_code} - {res.text}"}
            
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error: {e}")
        return {
            "error": "Network connection failed",
            "is_playing": False,
            "detail": "Temporary network issue, please try again"
        }
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout error: {e}")
        return {
            "error": "Request timeout",
            "is_playing": False,
            "detail": "Request took too long"
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            "error": "Unexpected error occurred",
            "is_playing": False,
            "detail": str(e)
        }

@app.get("/")
def root():
    return {
        "message": "Spotify API",
        "endpoints": {
            "/set-tokens": "Set access_token and refresh_token via query params",
            "/current": "Get currently playing track"
        }
    }
