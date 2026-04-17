import sys
import os

# Vercel _vendor 패키지보다 requirements.txt 설치 패키지를 우선 사용
sys.path = [p for p in sys.path if "_vendor" not in str(p)]

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

from app import app

handler = app
