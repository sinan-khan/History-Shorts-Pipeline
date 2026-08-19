"""Upload finished videos to YouTube Data API v3."""
from __future__ import annotations
import os
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from utils import log

SCOPES=["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI="https://oauth2.googleapis.com/token"

def _get_service():
    creds=Credentials(token=None,refresh_token=os.environ["YT_REFRESH_TOKEN"],client_id=os.environ["YT_CLIENT_ID"],client_secret=os.environ["YT_CLIENT_SECRET"],token_uri=TOKEN_URI,scopes=SCOPES)
    return build("youtube","v3",credentials=creds)

def upload_video(video_path: Path,title: str,description: str,tags: list[str]|None=None,thumbnail_path: Path|None=None,privacy_status: str="public") -> str:
    service=_get_service()
    body={"snippet":{"title":title[:100],"description":description,"tags":tags or [],"categoryId":"27"},"status":{"privacyStatus":privacy_status,"selfDeclaredMadeForKids":False}}
    media=MediaFileUpload(str(video_path),mimetype="video/mp4",resumable=True,chunksize=4*1024*1024)
    request=service.videos().insert(part="snippet,status",body=body,media_body=media)
    log.info("Uploading %s to YouTube...",video_path)
    response=None
    while response is None:
        status,response=request.next_chunk()
        if status: log.info("Upload progress: %d%%",int(status.progress()*100))
    video_id=response["id"]
    if thumbnail_path and thumbnail_path.exists():
        log.info("Uploading custom thumbnail...")
        service.thumbnails().set(videoId=video_id,media_body=MediaFileUpload(str(thumbnail_path),mimetype="image/jpeg")).execute()
    log.info("Upload complete: https://youtube.com/watch?v=%s",video_id)
    return video_id

def upload_short(video_path: Path,title: str,description: str,tags: list[str]|None=None,privacy_status: str="public") -> str:
    return upload_video(video_path,title,description,tags,None,privacy_status)
