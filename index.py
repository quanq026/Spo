from fastapi import FastAPI, Query, HTTPException
import requests
import base64

app = FastAPI(title="Spotify Vercel API")

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
def get_current(
    access_token: str = Query(..., description="Spotify access token"),
    refresh_token: str = Query(..., description="Spotify refresh token")
):
    """
    Lấy bài hát đang phát. 
    Tokens phải được truyền qua query params vì Vercel không lưu state.
    """
    try:
        # Thử với access token hiện tại
        res = get_currently_playing(access_token)
        
        # Nếu token hết hạn (401), làm mới và thử lại
        if res.status_code == 401:
            token_data = renew_access_token(refresh_token)
            if token_data and "access_token" in token_data:
                new_access_token = token_data["access_token"]
                new_refresh_token = token_data.get("refresh_token", refresh_token)
                
                # Thử lại với token mới
                res = get_currently_playing(new_access_token)
                
                # Trả về token mới để client cập nhật
                if res.status_code == 200:
                    data = res.json()
                    result = parse_track_data(data)
                    result["new_tokens"] = {
                        "access_token": new_access_token,
                        "refresh_token": new_refresh_token
                    }
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

@app.get("/")
def root():
    return {
        "message": "Spotify API for Vercel",
        "note": "Tokens must be passed as query params (stateless)",
        "endpoints": {
            "/current": "Get currently playing track (requires access_token & refresh_token as query params)"
        },
        "example": "/current?access_token=YOUR_TOKEN&refresh_token=YOUR_REFRESH_TOKEN"
    }

# For Vercel
app = app
