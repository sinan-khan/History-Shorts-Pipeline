"""Generate accurate history scripts and YouTube metadata."""
from __future__ import annotations
import json
import os
import re
import requests
from utils import log

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
SHORT_PROMPT = """Write an accurate, engaging history YouTube Short.
Output ONLY JSON: {"title":"...","description":"...","tags":[...],"beats":[{"line":"...","foot