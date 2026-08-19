"""Upload finished videos to YouTube Data API v3 with audience-aware scheduling."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from utils import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Publish in the late-afternoon/evening overlap for the US and UK. The UTC value
# is intentionally fixed; US/UK daylight-saving shifts change the local clock by
# an hour, but this remains a strong cross-market window.
SHORT_UTC_HOUR = 17   # 17:00 UTC: ~12pm ET / 1pm BST
LONG_UTC_HOUR = 19    # 19:00 UTC: ~2pm ET / 8pm BST


def _get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def _next_publish_time(hour_utc: int) -> str:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    # Always schedule at least 10 minutes ahead so YouTube receives a valid future timestamp.
    if target <= now + timedelta(minutes=10):
        target += timedelta(days=1)
    return target.isoformat().replace("+00:00", "Z")


def upload_video(video_path: Path, title: str, description: str, tags: list[str] | None = None,
                 thumbnail_path: Path | None = None, privacy_status: str = "public",
                 publish_at: str | None = None) -> str:
    service = _get_service()
    status = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": "27",
        },
        "status": status,
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True, chunksize=4 * 1024 * 1024)
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    log.info("Uploading %s to YouTube...", video_path)
    response = None
    while response is None:
        upload_status, response = request.next_chunk()
        if upload_status:
            log.info("Upload progress: %d%%", int(upload_status.progress() * 100))
    video_id = response["id"]

    if thumbnail_path and thumbnail_path.exists():
        log.info("Uploading custom thumbnail...")
        service.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
        ).execute()
    log.info("Upload complete: https://youtube.com/watch?v=%s (publish_at=%s)", video_id, publish_at or "now")
    return video_id


def upload_short(video_path: Path, title: str, description: str, tags: list[str] | None = None,
                 privacy_status: str = "public") -> str:
    return upload_video(video_path, title, description, tags, None, privacy_status, _next_publish_time(SHORT_UTC_HOUR))


def upload_long(video_path: Path, title: str, description: str, tags: list[str] | None = None,
                thumbnail_path: Path | None = None, privacy_status: str = "public") -> str:
    return upload_video(video_path, title, description, tags, thumbnail_path, privacy_status, _next_publish_time(LONG_UTC_HOUR))
