"""Generate history scripts and YouTube metadata for Shorts or long-form videos."""
from __future__ import annotations

import json
import os
import re

import requests

from utils import log

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

SHORT_PROMPT = """You write accurate, engaging history YouTube Shorts scripts.
Output ONLY valid JSON with exactly: