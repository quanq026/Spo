from fastapi import FastAPI, HTTPException
import requests
import os, json, time, base64

app = FastAPI(title="Spotify IoT API (Gist Enhanced)")
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hoặc ["http://localhost:5500", "http://127.0.0.1:5500"] cho chặt chẽ
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ======================
# CONFIG
# ======================
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
GITHUB_GIST_ID = os.getenv("GITHUB_GIST_ID", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GIST_FILENAME = os.getenv("GIST_FILENAME", "spotify_tokens.json")

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
# TOKEN HANDLING
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

    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token in Gist. Call /init first")
    
    if not refresh_token:
        raise HTTPException(status_code=400, detail="No refresh_token in Gist")

    # Renew if expired or near expiry (< 5 min)
    if time.time() >= expires_at - 300:
        print(f"[DEBUG] Token expires in {int(expires_at - time.time())}s, renewing...")
        token_data = renew_access_token(refresh_token)
        if token_data:
            return token_data["access_token"]
        else:
            raise HTTPException(status_code=500, detail="Failed to renew token")

    return access_token

# ======================
# SPOTIFY API HELPERS
# ======================
def spotify_request(method, endpoint, access_token, **kwargs):
    """Helper gửi request Spotify với retry logic"""
    url = f"https://api.spotify.com/v1{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    res = requests.request(method, url, headers=headers, timeout=10, **kwargs)
    
    # Retry nếu 401 (token expired)
    if res.status_code == 401:
        print("[DEBUG] Got 401, retrying with fresh token...")
        cached = load_token_from_gist()
        token_data = renew_access_token(cached.get("refresh_token", ""))
        if token_data:
            headers["Authorization"] = f"Bearer {token_data['access_token']}"
            res = requests.request(method, url, headers=headers, timeout=10, **kwargs)
    
    return res

def parse_time(ms: int):
    """Format ms -> mm:ss"""
    minutes = int(ms / 60000)
    seconds = int((ms % 60000) / 1000)
    return f"{minutes:02}:{seconds:02}"

def parse_track_data(data: dict):
    """Parse playback state thành JSON response"""
    if not data or not data.get("item"):
        return {"is_playing": False, "message": "No active playback"}

    item = data["item"]
    album = item.get("album", {})
    images = album.get("images", [])
    thumbnail = images[1]["url"] if len(images) > 1 else (images[0]["url"] if images else "")
    
    progress_ms = data.get("progress_ms", 0)
    duration_ms = item.get("duration_ms", 0)
    progress_percent = (progress_ms / duration_ms * 100) if duration_ms else 0

    return {
        "is_playing": data.get("is_playing", False),
        "track": item.get("name", ""),
        "artist": ", ".join(a["name"] for a in item.get("artists", [])),
        "album": album.get("name", ""),
        "thumbnail": thumbnail,
        "duration_ms": duration_ms,
        "progress_ms": progress_ms,
        "progress_percent": round(progress_percent, 2),
        "progress": f"{parse_time(progress_ms)} / {parse_time(duration_ms)}",
        "device": data.get("device", {}).get("name", ""),
        "volume_percent": data.get("device", {}).get("volume_percent"),
        "shuffle_state": data.get("shuffle_state", False),
        "repeat_state": data.get("repeat_state", "off"),
        "track_id": item.get("id"),
    }

# ======================
# ROUTES
# ======================
@app.get("/")
def root():
    cached = load_token_from_gist()
    has_tokens = bool(cached.get("access_token") and cached.get("refresh_token"))
    
    return {
        "status": "✅ Ready" if has_tokens else "⚠️ Not initialized",
        "storage": "GitHub Gist",
        "gist_id": GITHUB_GIST_ID[:10] + "..." if GITHUB_GIST_ID else None,
        "endpoints": {
            "/current": "Get detailed playback state",
            "/play": "Resume playback",
            "/pause": "Pause playback",
            "/next": "Skip to next track",
            "/prev": "Skip to previous track",
            "/like": "Save current track to library",
            "/dislike": "Remove current track from library",
            "/force-renew": "Force token renewal",
            "/debug": "Debug token status",
            "/init": "Initialize Gist (POST with tokens)",
        },
        "setup_required": not has_tokens
    }

@app.get("/current")
def current():
    """Lấy trạng thái playback hiện tại, kèm thông tin liked"""
    access_token = get_valid_token()
    res = spotify_request("GET", "/me/player", access_token)

    if res.status_code == 200:
        data = res.json()
        parsed = parse_track_data(data)

        # ✅ Kiểm tra bài này có nằm trong "Liked Songs" không
        track_id = parsed.get("track_id")
        if track_id:
            like_res = spotify_request("GET", f"/me/tracks/contains?ids={track_id}", access_token)
            if like_res.status_code == 200:
                liked_status = like_res.json()[0]
                parsed["is_liked"] = liked_status
            else:
                parsed["is_liked"] = None

        return parsed

    elif res.status_code == 204:
        return {"is_playing": False, "message": "Nothing playing"}
    else:
        raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/play")
def play():
    """Resume playback"""
    access_token = get_valid_token()
    res = spotify_request("PUT", "/me/player/play", access_token)
    
    if res.status_code in [204, 200]:
        return {"success": True, "action": "play"}
    raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/pause")
def pause():
    """Pause playback"""
    access_token = get_valid_token()
    res = spotify_request("PUT", "/me/player/pause", access_token)
    
    if res.status_code in [204, 200]:
        return {"success": True, "action": "pause"}
    raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/next")
def next_track():
    """Skip to next track"""
    access_token = get_valid_token()
    res = spotify_request("POST", "/me/player/next", access_token)
    
    if res.status_code in [204, 200]:
        return {"success": True, "action": "next"}
    raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/prev")
def prev_track():
    """Skip to previous track"""
    access_token = get_valid_token()
    res = spotify_request("POST", "/me/player/previous", access_token)
    
    if res.status_code in [204, 200]:
        return {"success": True, "action": "previous"}
    raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/like")
def like_track():
    """Save current track to library"""
    access_token = get_valid_token()
    
    # Get current track
    current = spotify_request("GET", "/me/player", access_token)
    if current.status_code == 200:
        data = current.json()
        track_id = data.get("item", {}).get("id")
        if track_id:
            res = spotify_request("PUT", f"/me/tracks?ids={track_id}", access_token)
            if res.status_code in [200, 204]:
                return {"success": True, "action": "liked", "track_id": track_id}
            raise HTTPException(status_code=res.status_code, detail=res.text)
    
    raise HTTPException(status_code=400, detail="No track playing")

@app.get("/dislike")
def dislike_track():
    """Remove current track from library"""
    access_token = get_valid_token()
    
    # Get current track
    current = spotify_request("GET", "/me/player", access_token)
    if current.status_code == 200:
        data = current.json()
        track_id = data.get("item", {}).get("id")
        if track_id:
            res = spotify_request("DELETE", f"/me/tracks?ids={track_id}", access_token)
            if res.status_code in [200, 204]:
                return {"success": True, "action": "disliked", "track_id": track_id}
            raise HTTPException(status_code=res.status_code, detail=res.text)
    
    raise HTTPException(status_code=400, detail="No track playing")

@app.get("/queue")
def get_queue():
    """Lấy danh sách bài hát trong hàng chờ (tối đa 30 bài)"""
    access_token = get_valid_token()
    res = spotify_request("GET", "/me/player/queue", access_token)

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=res.text)

    data = res.json()
    queue_items = data.get("queue", [])

    queue_list = []
    for i, item in enumerate(queue_items[:20]):  # Giới hạn 30 bài
        album = item.get("album", {})
        images = album.get("images", [])
        thumbnail = images[1]["url"] if len(images) > 1 else (images[0]["url"] if images else "")
        artists = ", ".join(a["name"] for a in item.get("artists", []))

        queue_list.append({
            "index": i + 1,
            "track": item.get("name", ""),
            "artist": artists,
            "album": album.get("name", ""),
            "thumbnail": thumbnail,
            "id": item.get("id", "")
        })

    # Bài hiện đang phát (nếu có)
    current = data.get("currently_playing")
    current_info = None
    if current:
        c_album = current.get("album", {})
        c_images = c_album.get("images", [])
        c_thumb = c_images[1]["url"] if len(c_images) > 1 else (c_images[0]["url"] if c_images else "")
        current_info = {
            "track": current.get("name", ""),
            "artist": ", ".join(a["name"] for a in current.get("artists", [])),
            "album": c_album.get("name", ""),
            "thumbnail": c_thumb,
            "id": current.get("id", "")
        }

    return {
        "success": True,
        "currently_playing": current_info,
        "up_next": queue_list,
        "total": len(queue_list)
    }

@app.get("/shuffle/{state}")
def toggle_shuffle(state: str):
    """Bật hoặc tắt chế độ trộn bài"""
    access_token = get_valid_token()
    
    if state.lower() not in ["true", "false"]:
        raise HTTPException(status_code=400, detail="State must be 'true' or 'false'")
    
    res = spotify_request("PUT", f"/me/player/shuffle?state={state.lower()}", access_token)
    
    if res.status_code in [204, 200]:
        return {
            "success": True,
            "shuffle_state": state.lower() == "true"
        }
    else:
        raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/queue/{index}")
def play_from_queue(index: int):
    """Phát bài trong context hiện tại thay vì reset queue"""
    access_token = get_valid_token()

    # Lấy thông tin player (để biết context_uri)
    player_res = spotify_request("GET", "/me/player", access_token)
    if player_res.status_code != 200:
        raise HTTPException(status_code=player_res.status_code, detail="Cannot get player info")
    
    player_data = player_res.json()
    context_uri = player_data.get("context", {}).get("uri", None)

    # Lấy queue
    queue_res = spotify_request("GET", "/me/player/queue", access_token)
    if queue_res.status_code != 200:
        raise HTTPException(status_code=queue_res.status_code, detail="Failed to get queue")

    queue_data = queue_res.json()
    queue_list = queue_data.get("queue", [])
    current = queue_data.get("currently_playing", {})
    full_list = [current] + queue_list

    if index < 0 or index >= len(full_list):
        raise HTTPException(status_code=400, detail=f"Index {index} out of range")

    target_track = full_list[index]
    track_id = target_track.get("id")

    # Nếu có context (playlist/album), phát theo context
    if context_uri:
        body = {"context_uri": context_uri, "offset": {"uri": f"spotify:track:{track_id}"}}
    else:
        # Nếu không có context (ví dụ đang phát single lẻ)
        body = {"uris": [f"spotify:track:{track_id}"]}

    res = spotify_request("PUT", "/me/player/play", access_token, json=body)

    if res.status_code in [204, 200]:
        return {
            "success": True,
            "message": f"Now playing {target_track.get('name')} by {', '.join(a['name'] for a in target_track.get('artists', []))}",
            "track_id": track_id,
            "used_context": bool(context_uri)
        }
    else:
        raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/volume/{level}")
def set_volume(level: int):
    """Điều chỉnh âm lượng (0–100%)"""
    if not (0 <= level <= 100):
        raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")

    access_token = get_valid_token()
    res = spotify_request("PUT", f"/me/player/volume?volume_percent={level}", access_token)

    if res.status_code in [204, 200]:
        return {
            "success": True,
            "volume_percent": level
        }
    else:
        raise HTTPException(status_code=res.status_code, detail=res.text)

@app.get("/force-renew")
def force_renew():
    """Force renew token"""
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
    """Debug token status"""
    cached = load_token_from_gist()
    expires_at = cached.get("expires_at", 0)
    expires_in = int(expires_at - time.time())
    
    return {
        "gist_id": GITHUB_GIST_ID[:10] + "..." if GITHUB_GIST_ID else None,
        "access_token_preview": cached.get("access_token", "")[:20] + "..." if cached.get("access_token") else None,
        "has_refresh_token": bool(cached.get("refresh_token")),
        "expires_at": expires_at,
        "expires_in_seconds": expires_in,
        "is_expired": expires_in <= 0,
        "timestamp": time.time()
    }

@app.post("/init")
async def init_tokens(request: dict):
    """Initialize Gist with tokens"""
    access_token = request.get("access_token", "")
    refresh_token = request.get("refresh_token", "")
    
    if not access_token or not refresh_token:
        return {"error": "Both access_token and refresh_token required"}

    expires_at = time.time() + 3600
    success = save_token_to_gist(access_token, refresh_token, expires_at)
    
    return (
        {"success": True, "message": "Tokens saved to Gist", "expires_in": 3600}
        if success
        else {"success": False, "message": "Failed to save to Gist"}
    )

app = app
