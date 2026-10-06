#!/usr/bin/env python3
"""
Daily auto-update — 3-phase pipeline:
  Phase 1 (_phase1_mc.py):       FX + 전 종목 시총 → _phase1_result.json
  Phase 2A (_phase2_hist.py 0):  전반부 가격이력   → _phase2a_result.json
  Phase 2B (_phase2_hist.py 1):  후반부 가격이력 + GitHub 커밋

각 단계를 subprocess로 분리해 45s 제한에 걸리지 않게 순차 실행.
종목이 늘어나도 2A/2B 절반씩 나누므로 자동 대응.

수동 실행: python3 daily_update.py
"""
import subprocess
import sys
import datetime
from pathlib import Path

BASE = Path(__file__).parent.resolve()
PYLIB = [str(BASE / "pylib"), str(BASE / "pylibs")]
PYTHON = sys.executable

def run_phase(script: str, *args, label: str = ""):
    env_path = ":".join(PYLIB)
    cmd = [PYTHON, str(BASE / script)] + list(args)
    print(f"\n{'='*50}")
    print(f"  [{datetime.datetime.now():%H:%M:%S}] {label or script}")
    print(f"{'='*50}")
    import os, sys as _sys
    env = os.environ.copy()
    env["PYTHONPATH"] = env_path + (":" + env.get("PYTHONPATH","") if env.get("PYTHONPATH") else "")
    result = subprocess.run(cmd, env=env, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=_sys.stderr)
    if result.returncode != 0:
        print(f"  [ERROR] {script} 실패 (exit={result.returncode})")
        return False
    return True

def main():
    t0 = datetime.datetime.now()
    print(f"[{t0:%Y-%m-%d %H:%M:%S}] 일일 전체 업데이트 시작")

    if not run_phase("_phase1_mc.py", label="Phase 1: FX + 시총"):
        print("Phase 1 실패 — 중단")
        return 1

    if not run_phase("_phase2_hist.py", "0", label="Phase 2A: 가격이력 전반부"):
        print("Phase 2A 실패 — 중단")
        return 1

    if not run_phase("_phase2_hist.py", "1", label="Phase 2B: 가격이력 후반부 + 커밋"):
        print("Phase 2B 실패 — 중단")
        return 1

    # Phase 3: 이름-코드 무결성 검증 (비차단 — 불일치 시 ⚠️ 로그만, 커밋은 이미 완료)
    run_phase("validate_stocks.py", label="Phase 3: 무결성 검증 (비차단)")

    total = (datetime.datetime.now() - t0).total_seconds()
    print(f"\n✅ 전체 완료 | 총 {total:.1f}초")
    return 0

if __name__ == "__main__":
    sys.exit(main())
