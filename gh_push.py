#!/usr/bin/env python3
"""
gh_push.py — Mac 의 .github_token 으로 어떤 whysosary-dot 레포에든 파일을 커밋(push)한다.
다른 Claude 세션이 "세션 소스에 레포가 없어 403" 일 때 이 스크립트를 Mac(device_bash)에서 실행하면 된다.
토큰은 파일에서만 읽고 절대 출력하지 않는다.

사용 예 (stock-valuation 폴더에서):
  python3 gh_push.py --repo whysosary-dot/us-market-briefing -m "US market briefing 2026-10-31" \
      --file /path/local/2026-10-31.md=briefings/2026-10-31.md \
      --file /path/local/index.html=index.html
  python3 gh_push.py --repo whysosary-dot/us-market-briefing -m "msg" --dir /path/local/site=   # 폴더 전체를 레포 루트에
  python3 gh_push.py --repo whysosary-dot/us-market-briefing --get index.html > /tmp/index.html   # 원격 파일 받기
  python3 gh_push.py --repo whysosary-dot/us-market-briefing --ls briefings                       # 원격 목록

여러 --file 은 Git Data API 로 '하나의 커밋'에 담는다. 같은 내용이면 커밋하지 않는다.
"""
import argparse, base64, json, os, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = open(os.path.join(HERE, ".github_token")).read().strip()

def api(method, path, body=None, raw=False):
    req = urllib.request.Request("https://api.github.com/" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "token " + TOKEN, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "gh_push"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = r.read(); return r.status, (d if raw else json.loads(d or b"{}"))
    except urllib.error.HTTPError as e:
        d = e.read()
        try: j = json.loads(d or b"{}")
        except Exception: j = {"message": d[:200].decode(errors="replace")}
        return e.code, j

def die(msg): print("ERR:", msg.replace(TOKEN, "[REDACTED]"), file=sys.stderr); sys.exit(1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--branch", default=None)
    ap.add_argument("-m", "--message", default="update")
    ap.add_argument("--file", action="append", default=[], help="LOCAL=REMOTE_PATH (여러 번)")
    ap.add_argument("--dir", action="append", default=[], help="LOCAL_DIR=REMOTE_PREFIX (여러 번)")
    ap.add_argument("--get", help="원격 파일 내용을 stdout 으로")
    ap.add_argument("--ls", help="원격 디렉터리 목록")
    a = ap.parse_args()

    s, repo = api("GET", f"repos/{a.repo}")
    if s != 200: die(f"repo 조회 실패 {s} {repo.get('message')}")
    branch = a.branch or repo["default_branch"]

    if a.get is not None:
        s, j = api("GET", f"repos/{a.repo}/contents/{a.get}?ref={branch}")
        if s != 200: die(f"get 실패 {s} {j.get('message')}")
        sys.stdout.buffer.write(base64.b64decode(j["content"])); return
    if a.ls is not None:
        s, j = api("GET", f"repos/{a.repo}/contents/{a.ls}?ref={branch}")
        if s != 200: die(f"ls 실패 {s} {j.get('message')}")
        for e in j: print(e["type"][:4], e["size"], e["path"])
        return

    files = []
    for f in a.file:
        loc, rem = f.split("=", 1); files.append((loc, rem.strip("/")))
    for d in a.dir:
        loc, pre = d.split("=", 1); pre = pre.strip("/")
        for root, _, fs in os.walk(loc):
            for fn in fs:
                if fn.startswith(".") : continue
                lp = os.path.join(root, fn); rel = os.path.relpath(lp, loc)
                files.append((lp, (pre + "/" + rel) if pre else rel))
    if not files: die("--file 또는 --dir 필요")

    # head commit & tree
    s, ref = api("GET", f"repos/{a.repo}/git/ref/heads/{branch}")
    if s != 200: die(f"branch 조회 실패 {s}")
    head_sha = ref["object"]["sha"]
    s, commit = api("GET", f"repos/{a.repo}/git/commits/{head_sha}")
    base_tree = commit["tree"]["sha"]

    # blobs
    tree = []
    for lp, rp in files:
        data = open(lp, "rb").read()
        # 같은 내용이면 건너뜀
        s, cur = api("GET", f"repos/{a.repo}/contents/{rp}?ref={branch}")
        if s == 200 and cur.get("encoding") == "base64" and base64.b64decode(cur["content"]) == data:
            print("SAME", rp); continue
        s, blob = api("POST", f"repos/{a.repo}/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        if s != 201: die(f"blob 실패 {rp} {s} {blob.get('message')}")
        tree.append({"path": rp, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("ADD", rp, len(data))
    if not tree: print("변동 없음 — 커밋하지 않음"); return

    s, newtree = api("POST", f"repos/{a.repo}/git/trees", {"base_tree": base_tree, "tree": tree})
    if s != 201: die(f"tree 실패 {s} {newtree.get('message')}")
    s, newc = api("POST", f"repos/{a.repo}/git/commits",
                  {"message": a.message, "tree": newtree["sha"], "parents": [head_sha],
                   "author": {"name": "리송", "email": "whysosary@naver.com"}})
    if s != 201: die(f"commit 실패 {s} {newc.get('message')}")
    for i in range(3):
        s, upd = api("PATCH", f"repos/{a.repo}/git/refs/heads/{branch}", {"sha": newc["sha"], "force": False})
        if s == 200: break
        time.sleep(3)
    if s != 200: die(f"ref 갱신 실패 {s} {upd.get('message')} (다른 커밋이 먼저 들어왔으면 다시 실행)")
    print("PUSHED", a.repo, branch, newc["sha"][:7], "|", len(tree), "files |", a.message)
    if repo.get("has_pages"): print("Pages 반영까지 보통 1~3분(캐시 최대 10분)")

if __name__ == "__main__":
    main()
