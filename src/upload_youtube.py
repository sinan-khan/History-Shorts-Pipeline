"""Upload finished videos to YouTube Data API v3 with audience-aware scheduling."""
from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from utils import log

SCOPES=["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_URI="https://oauth2.googleapis.com/token"
# A practical