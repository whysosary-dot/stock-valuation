#!/usr/bin/env python3
"""
Telegram 채널 메시지를 가져와 telegram.json으로 GitHub에 저장.
- Bot API getUpdates 로 새 메시지 수신
- 기존 telegram.json 과 병합 (최대 200개 보관)
- GitHub Contents API로 커밋
"""
import os, sys, json, base64, datetime, urllib.request, urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
TOKEN_FILE = BASE_DIR / ".github_token"
BOT_TOKEN  = "8574870425:AAEXmdC3SnzuQb6VPOl8LOUGpJjpqlhrvf8"
CHAT_ID    = "-1003980062440"
REPO       = "whysosary-dot/stock-valuation"
TG_FILE    = "telegram.json"
OFFSET_FILE = BASE_DIR / ".tg_offset"
BRANCH     = "main"
MAX_MSGS   = 200


def get_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok: return tok.strip()
    if TOKEN_FILE.exists(): return TOKEN_FILE.read_text().strip()
    raise SystemExit("GitHub 토큰 없음")


def tg_api(method, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}?{qs}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read().decode())


def get_file_url(file_id):
    try:
        res = tg_api("getFile", file_id=file_id)
        if res.get("ok"):
            path = res["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    except Exception:
        pass
    return None


def gh_get_file(token, path):
    api = f"https://api.github.com/repos/{REPO}/contents/{path}?ref={BRANCH}"
    req = urllib.request.Request(api, headers={
        "Authorization": f"token {token}", "Accept": "application/vnd.github+json", "User-Agent": "sv"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            meta = json.loads(r.read().decode())
        return json.loads(base64.b64decode(meta["content"]).decode()), meta["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], None
        raise


def gh_put_file(token, path, content_bytes, sha, message):
    api = f"https://api.github.com/repos/{REPO}/contents/{path}"
    body = {"message": message, "content": base64.b64encode(content_bytes).decode(), "branch": BRANCH}
    if sha:
        body["sha"] = sha
    put = urllib.request.Request(api, method="PUT", data=json.dumps(body).encode(),
        headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": "sv", "Content-Type": "application/json"})
    with urllib.request.urlopen(put, timeout=30) as r:
        return json.loads(r.read().decode())


def main():
    token = get_token()

    # 현재 offset 로드
    offset = 0
    if OFFSET_FILE.exists():
        try: offset = int(OFFSET_FILE.read_text().strip())
        except: offset = 0

    # Telegram getUpdates
    params = dict(limit=100, timeout=0, allowed_updates="channel_post")
    if offset > 0:
        params["offset"] = offset + 1

    try:
        res = tg_api("getUpdates", **params)
    except Exception as e:
        print(f"  getUpdates 실패: {e}")
        return 0

    updates = res.get("result", [])
    new_posts = [u["channel_post"] for u in updates if "channel_post" in u]
    print(f"  새 메시지: {len(new_posts)}개")

    if updates:
        new_offset = updates[-1]["update_id"]
        OFFSET_FILE.write_text(str(new_offset))

    # 사진 URL 확인
    for post in new_posts:
        if post.get("photo"):
            best = max(post["photo"], key=lambda x: x.get("file_size", 0))
            post["_photo_url"] = get_file_url(best["file_id"])
        elif post.get("document"):
            post["_doc_url"] = get_file_url(post["document"]["file_id"])

    if not new_posts:
        print("  새 메시지 없음")
        return 0

    # GitHub 기존 telegram.json 로드 후 병합
    saved, sha = gh_get_file(token, TG_FILE)
    by_id = {m["message_id"]: m for m in saved if isinstance(m, dict)}
    for p in new_posts:
        if p.get("message_id"):
            by_id[p["message_id"]] = p
    merged = sorted(by_id.values(), key=lambda m: m.get("date", 0), reverse=True)[:MAX_MSGS]

    content = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode()
    msg = f"✈️ Telegram 메시지 업데이트: {datetime.date.today().isoformat()}"
    result = gh_put_file(token, TG_FILE, content, sha, msg)
    commit = result.get("commit", {})
    print(f"  ✓ 커밋: {commit.get('sha','?')[:7]} ({len(merged)}개 메시지 저장)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
