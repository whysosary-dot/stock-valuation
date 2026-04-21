#!/usr/bin/env bash
# 로컬 서버 시작 스크립트
set -e
cd "$(dirname "$0")"

# 가상환경 체크 (선택)
if [ ! -d ".venv" ]; then
  echo "[info] .venv 없음 → 시스템 pip 사용"
fi

# 의존성 설치
python3 -m pip install -q --user -r requirements.txt 2>/dev/null || \
  python3 -m pip install -q --break-system-packages -r requirements.txt

# 서버 시작
exec python3 server.py
