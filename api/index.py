import sys
import os

# 프로젝트 루트를 Python 경로에 추가
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

from app import app

# Vercel 서버리스 핸들러
handler = app
