from fastapi import FastAPI, HTTPException
import requests
import base64  # ✅ THÊM DÒNG NÀY
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
        print(f"[DEBUG] Renew token status: {res.status_code}")
        
        if res.status_code == 200:
            token_data = res.json()
            
            # ✅ LƯU VÀO CACHE IN-MEMORY
            token_cache["access_token"] = token_data["access_token"]
            token_cache["expires_at"] = time.time() + token_data.get("expires_in", 3600)
            
            print(f"[DEBUG] Token renewed successfully, expires in {token_data.get('expires_in', 3600)}s")
            return token_data
        else:
            print(f"[DEBUG] Renew failed: {res.text[:200]}")
            return None
    except Exception as e:
        print(f"[DEBUG] Renew exception: {str(e)}")
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
        
        print(f"[DEBUG] Using token: {access_token[:20]}... (expires in {int(token_cache['expires_at'] - time.time())}s)")
        
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
    configured = bool(token_cache["access_token"] and SPOTIFY_REFRESH_TOKEN)
    return {
        "message": "Spotify API for IoT/ESP32",
        "status": "✅ Ready" if configured else "❌ Not configured",
        "token_status": {
            "cached": bool(token_cache["access_token"]),
            "expires_in": max(0, int(token_cache["expires_at"] - time.time())) if token_cache["expires_at"] else 0,
            "has_refresh_token": bool(SPOTIFY_REFRESH_TOKEN)
        },
        "endpoints": {
            "/current": "Get currently playing track (use this for keep-warm)",
            "/debug": "Debug token status",
            "/test-renew": "Test token renewal",
            "/verify-credentials": "Verify CLIENT_ID/SECRET/REFRESH_TOKEN match"
        },
        "note": "Use /current with UptimeRobot/cron to prevent cold starts"
    }

@app.get("/test-cache")
def test_cache():
    """Test xem cache có tồn tại giữa các request không"""
    import random
    
    # Tạo một số ngẫu nhiên và lưu vào cache
    if "test_number" not in token_cache:
        token_cache["test_number"] = random.randint(1000, 9999)
        cache_status = "NEW - Cache mới tạo"
    else:
        cache_status = "PERSISTED - Cache vẫn còn từ request trước"
    
    return {
        "test_number": token_cache.get("test_number"),
        "cache_status": cache_status,
        "instructions": "Gọi endpoint này 2-3 lần liên tiếp. Nếu test_number GIỐNG NHAU → cache OK. Nếu KHÁC NHAU mỗi lần → cache bị mất (cold start)",
        "full_cache": {
            "has_access_token": bool(token_cache.get("access_token")),
            "expires_at": token_cache.get("expires_at", 0),
            "test_number": token_cache.get("test_number")
        }
    }
def verify_credentials():
    """Verify if CLIENT_ID, CLIENT_SECRET, and REFRESH_TOKEN match"""
    
    # Test refresh token với credentials hiện tại
    url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": SPOTIFY_REFRESH_TOKEN
    }
    
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10)
        
        return {
            "client_id": CLIENT_ID,
            "client_id_length": len(CLIENT_ID) if CLIENT_ID else 0,
            "client_secret_length": len(CLIENT_SECRET) if CLIENT_SECRET else 0,
            "refresh_token_length": len(SPOTIFY_REFRESH_TOKEN) if SPOTIFY_REFRESH_TOKEN else 0,
            "refresh_token_preview": SPOTIFY_REFRESH_TOKEN[:30] + "..." if SPOTIFY_REFRESH_TOKEN else None,
            "test_result": {
                "status_code": res.status_code,
                "success": res.status_code == 200,
                "response": res.json() if res.text else None
            },
            "diagnosis": {
                "400_invalid_grant": "Refresh token không thuộc về app này, hoặc đã bị revoke",
                "400_invalid_client": "CLIENT_ID hoặc CLIENT_SECRET sai",
                "200": "Mọi thứ OK!"
            }[str(res.status_code)] if res.status_code in [200, 400] else "Unknown error"
        }
    except Exception as e:
        return {
            "error": str(e),
            "client_id": CLIENT_ID,
            "has_all_credentials": bool(CLIENT_ID and CLIENT_SECRET and SPOTIFY_REFRESH_TOKEN)
        }
def debug():
    """Debug endpoint để kiểm tra token status"""
    return {
        "client_id": CLIENT_ID[:10] + "..." if CLIENT_ID else None,
        "has_refresh_token": bool(SPOTIFY_REFRESH_TOKEN),
        "refresh_token_preview": SPOTIFY_REFRESH_TOKEN[:20] + "..." if SPOTIFY_REFRESH_TOKEN else None,
        "cache": {
            "has_access_token": bool(token_cache["access_token"]),
            "access_token_preview": token_cache["access_token"][:20] + "..." if token_cache["access_token"] else None,
            "expires_at": token_cache["expires_at"],
            "expires_in_seconds": max(0, int(token_cache["expires_at"] - time.time())) if token_cache["expires_at"] else 0,
            "is_expired": time.time() >= token_cache["expires_at"] if token_cache["expires_at"] else True
        },
        "timestamp": time.time()
    }

@app.get("/test-renew")
def test_renew():
    """Test token renewal mechanism"""
    if not SPOTIFY_REFRESH_TOKEN:
        return {"error": "No refresh token configured"}
    
    # Lưu token cũ
    old_token = token_cache["access_token"][:20] + "..." if token_cache["access_token"] else "none"
    
    print("[TEST] Force renewing token...")
    
    # Test renew với error details
    url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": SPOTIFY_REFRESH_TOKEN
    }
    
    try:
        res = requests.post(url, headers=headers, data=data, timeout=10)
        print(f"[TEST] Renew response status: {res.status_code}")
        print(f"[TEST] Renew response: {res.text[:500]}")
        
        if res.status_code == 200:
            token_data = res.json()
            
            # Update cache
            token_cache["access_token"] = token_data["access_token"]
            token_cache["expires_at"] = time.time() + token_data.get("expires_in", 3600)
            
            new_token = token_cache["access_token"][:20] + "..." if token_cache["access_token"] else "none"
            return {
                "success": True,
                "message": "Token renewed successfully",
                "old_token_preview": old_token,
                "new_token_preview": new_token,
                "expires_in": token_data.get("expires_in", 3600),
                "token_changed": old_token != new_token
            }
        else:
            return {
                "success": False,
                "message": "Failed to renew token",
                "status_code": res.status_code,
                "error": res.json() if res.text else "No response body",
                "hint": "Check if refresh token is valid or revoked"
            }
    except Exception as e:
        return {
            "success": False,
            "message": "Exception during renewal",
            "error": str(e)
        }

# For Vercel
app = app
