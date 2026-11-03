#!/usr/bin/env python3
"""
research_note.py — 리서치 노트(옛 🔬 Research 탭 = investment-notes 카드)를 📁 폴더탭에 저장·추가하는 공용 스크립트.

폴더 규칙
  기업 자료  → 주식 / 관심종목 / <기업명>   (폴더 없으면 생성)
  산업 자료  → 주식 / 산업 / <산업명>       (폴더 없으면 생성, --industry)
  자료(카드) 1건 = 폴더탭 웹문서 1개 (이름 = 「🔬 자료 제목」). 원본은 sv/research/<folder_id>.json

데이터 (invest-private, 비공개)
  sv/research/<folder_id>.json      원본 구조화 데이터 {name, ticker, sector, tags, cards:[...]}  ← 진실의 원천
  sv/sheets/data/<sheet_id>.json    렌더된 웹문서 {"type":"webdoc","html":...}
  sv/sheets/index.json              폴더탭 인덱스 (folders / sheets)

사용법 (Mac 의 stock-valuation 폴더에서; 토큰은 옆의 .github_token — 절대 출력 금지)
  python3 research_note.py add --name 삼성전자 --card card.json [--ticker 005930 --sector 반도체]
  python3 research_note.py add --name 반도체 --industry --card -      # stdin 으로 카드 JSON
  python3 research_note.py add --name 한라IMS --cards cards.json      # 여러 카드(JSON 배열) 한 번에
  python3 research_note.py list                                       # 리서치 노트가 있는 폴더 목록
  python3 research_note.py show --name 삼성전자                        # 카드 텍스트 출력(최신순)
  python3 research_note.py render --name 삼성전자 > x.html            # 렌더 HTML 미리보기
  python3 research_note.py migrate --src <companies.json> --out <dir> # investment-notes 일괄 이관 → 로컬 파일 생성 후
      python3 gh_push.py --repo whysosary-dot/invest-private -m "research: 이관" --dir <dir>=    로 한 커밋에 푸시

카드 JSON 형식 (investment-notes 와 동일)
  {"title": "...", "date": "YYYY-MM-DD", "summary": ["...", "..."], "tags": ["..."],
   "chart": {"type":"bar|bar-h|donut|line|bar-stack", "title":"", "unit":"", "labels":[], "data":[], "consensus":null, "surprise":null, "surprise_pct":null, "colors":null},
   "charts": [...], "images": ["https://..."], "color": "red|amber|green|blue|purple|pink"}
  date = 자료 원본 날짜, added_at 은 자동(오늘). 수치가 있으면 반드시 chart 로.
"""
import argparse, base64, datetime, json, os, random, re, string, sys, time, urllib.error, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = "whysosary-dot/invest-private"; BR = "main"
WATCHLIST_FOLDER = "tlqgd8o6"   # 주식 / 관심종목
INDUSTRY_FOLDER = "y0d4bgen"    # 주식 / 산업
NOTE_PREFIX = "🔬 리서치 노트"
IMG_BASE = "https://whysosary-dot.github.io/investment-notes/"
CHUNK = 4 * 1024 * 1024
TODAY = datetime.date.today().isoformat()

# ───────────────────────── GitHub ─────────────────────────
def tok(): return (BASE / ".github_token").read_text().strip()
def H(raw=False):
    return {"Authorization": "token " + tok(), "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "User-Agent": "sv-research-note", "Content-Type": "application/json"}
def api(path): return f"https://api.github.com/repos/{REPO}/contents/{path}"
def _open(req, timeout=60):
    for attempt in range(5):   # 네트워크 일시 오류(Connection refused 등) 재시도
        try: return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.URLError as e:
            if isinstance(e, urllib.error.HTTPError) or attempt == 4: raise
            time.sleep(3 * (attempt + 1))
def gh_get(path, raw=False):
    try:
        r = _open(urllib.request.Request(api(path) + f"?ref={BR}&t={int(time.time())}", headers=H(raw)))
        d = r.read()
        return d.decode() if raw else json.loads(d)
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise
def gh_put(path, text, sha, msg):
    body = {"message": msg, "content": base64.b64encode(text.encode()).decode(), "branch": BR}
    if sha: body["sha"] = sha
    r = _open(urllib.request.Request(api(path), method="PUT", data=json.dumps(body).encode(), headers=H()), timeout=120)
    return json.loads(r.read())
def put_text(path, text, msg):
    ex = gh_get(path); return gh_put(path, text, ex and ex["sha"], msg)
def put_data(sid, text, msg):
    base = f"sv/sheets/data/{sid}"
    if len(text) <= CHUNK: return put_text(base + ".json", text, msg)
    n = (len(text) + CHUNK - 1) // CHUNK
    for i in range(n): put_text(f"{base}.c{i}.json", text[i*CHUNK:(i+1)*CHUNK], f"{msg} ({i+1}/{n})")
    return put_text(base + ".json", json.dumps({"_chunked": True, "parts": n, "chars": len(text)}), f"{msg} (manifest)")
def load_index():
    m = gh_get("sv/sheets/index.json"); return json.loads(base64.b64decode(m["content"])), m["sha"]
def new_id(idx, prefix=""):
    used = {f["id"] for f in idx["folders"]} | {s["id"] for s in idx["sheets"]}
    while True:
        i = prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=8 - len(prefix)))
        if i not in used: return i

# ───────────────────────── 이름 매칭 ─────────────────────────
def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s))           # 괄호(영문명 등) 제거
    s = re.sub(r"[\s\-_·.,/&]+", "", s).lower()
    return s
def path_of(byid, f):
    out = []; cur = f
    while cur: out.append(cur["name"]); cur = byid.get(cur.get("parent"))
    return " / ".join(reversed(out))
def find_folder(idx, name, industry=False):
    parent = INDUSTRY_FOLDER if industry else WATCHLIST_FOLDER
    hits = [f for f in idx["folders"] if f.get("parent") == parent and (f["id"] == name or norm(f["name"]) == norm(name))]
    if not hits: return None
    # 같은 이름 폴더가 여럿이면 리서치 노트가 있는 폴더 → 카드 많은 폴더 순
    def score(f):
        has = any(s.get("folder") == f["id"] and s.get("name", "").startswith(NOTE_PREFIX) for s in idx["sheets"])
        return (has, sum(1 for s in idx["sheets"] if s.get("folder") == f["id"]))
    return sorted(hits, key=score, reverse=True)[0]
def find_note(idx, fid):
    return next((s for s in idx["sheets"] if s.get("folder") == fid and (s.get("research") or s.get("name", "").startswith(NOTE_PREFIX))), None)

# ───────────────────────── 렌더 ─────────────────────────
CSS = r"""
:root{--bg:#f7f8fb;--panel:#fff;--panel-2:#f1f3f7;--line:#e3e7ee;--text:#11151a;--muted:#5b6471;--accent:#1f6feb;--tag:#e8eef9;--shadow:0 2px 10px rgba(20,30,55,.07);--radius:14px}
*{box-sizing:border-box}html,body{background:var(--bg);color:var(--text);margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Apple SD Gothic Neo","Pretendard","Noto Sans KR",Roboto,"Helvetica Neue",Arial,sans-serif;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:0 18px}
.topbar{padding:18px 0 10px;border-bottom:1px solid var(--line);background:var(--panel);margin-bottom:14px}
.topbar h1{margin:0 0 4px;font-size:22px;letter-spacing:-.01em;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.topbar h1 a{color:inherit;text-decoration:none;border-bottom:1px dashed var(--line)}.topbar h1 a:hover{color:var(--accent);border-color:var(--accent)}
.topbar .subtitle{margin:0;color:var(--muted);font-size:13px}
.kind{font-size:11px;font-weight:700;border-radius:999px;padding:2px 9px;background:var(--tag);color:var(--muted)}
.toolbar{display:flex;gap:10px;align-items:center;margin:0 0 12px;flex-wrap:wrap}
.toolbar input[type=search]{flex:1;min-width:200px;padding:9px 12px;border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--text);font-size:14px;outline:none}
.toolbar input[type=search]:focus{border-color:var(--accent)}
.counter{color:var(--muted);font-size:13px;white-space:nowrap;margin-left:auto}
.tagbar{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}
.tagbar .tchip{font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--muted);cursor:pointer}
.tagbar .tchip.active{border-color:var(--accent);color:var(--accent);background:#eef4ff}
.color-filter{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
.cf-chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--panel);color:var(--muted);cursor:pointer}
.cf-chip.active{border-color:var(--accent);color:var(--accent)}.cf-dot{width:9px;height:9px;border-radius:50%;background:#999}
.cf-chip.sw-red .cf-dot{background:#E24B4A}.cf-chip.sw-amber .cf-dot{background:#EF9F27}.cf-chip.sw-green .cf-dot{background:#639922}.cf-chip.sw-blue .cf-dot{background:#378ADD}.cf-chip.sw-purple .cf-dot{background:#7F77DD}.cf-chip.sw-pink .cf-dot{background:#D4537E}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;padding-bottom:50px}
body.single .cards{grid-template-columns:minmax(0,760px)}body.single .card.collapsed .card-body{max-height:none;overflow:visible}body.single .card.collapsed .card-body::after,body.single .card-toggle{display:none}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:16px 16px 14px;box-shadow:var(--shadow);display:flex;flex-direction:column}
.card h3{margin:0 0 4px;font-size:15px;line-height:1.35}
.card .date{color:var(--muted);font-size:12px;margin:0 0 10px}
.card ul{margin:0 0 12px;padding-left:18px}.card li{font-size:13.5px;margin:3px 0}
.card .tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto}
.card .tag{background:var(--tag);color:var(--text);font-size:11px;padding:3px 8px;border-radius:999px;cursor:pointer}
.card-body{position:relative}.card.collapsed .card-body{max-height:260px;overflow:hidden}
.card.collapsed .card-body::after{content:"";position:absolute;left:0;right:0;bottom:0;height:56px;background:linear-gradient(to bottom,rgba(0,0,0,0),var(--panel));pointer-events:none}
.card-toggle{margin-top:6px;width:100%;display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel-2);color:var(--muted);font-size:12.5px;cursor:pointer}
.card-chart{margin:0 0 14px}.chart-hd{display:flex;align-items:center;gap:8px;margin-bottom:6px}.chart-lbl{font-size:12px;color:var(--muted)}
.chart-badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px}.chart-badge.surp{background:rgba(15,110,86,.18);color:#0F6E56}.chart-badge.shock{background:rgba(216,90,48,.18);color:#D85A30}
.chart-wrap{position:relative}
.card-imgs{display:flex;flex-direction:column;gap:10px;margin:0 0 12px}.card-imgs .img{display:block;border-radius:10px;overflow:hidden;border:1px solid var(--line);background:var(--panel-2)}.card-imgs .img img{display:block;width:100%;height:auto}
.card-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.card-dot.sw-red{background:#E24B4A}.card-dot.sw-amber{background:#EF9F27}.card-dot.sw-green{background:#639922}.card-dot.sw-blue{background:#378ADD}.card-dot.sw-purple{background:#7F77DD}.card-dot.sw-pink{background:#D4537E}
.empty{color:var(--muted);padding:30px 0;text-align:center}
.inv-lightbox{position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:99;cursor:zoom-out}.inv-lightbox img{max-width:96vw;max-height:96vh}
.foot{color:var(--muted);font-size:11.5px;padding:0 0 30px}
"""

JS = r"""
(function(){
  const D = window.__RN; const cardsEl=document.getElementById('cards'), empty=document.getElementById('empty'), counter=document.getElementById('counter'), q=document.getElementById('q');
  const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cards=(D.cards||[]).map((c,i)=>({c,i})).sort((a,b)=>String(b.c.date||'').localeCompare(String(a.c.date||''))||String(b.c.added_at||'').localeCompare(String(a.c.added_at||''))||(b.i-a.i)).map(x=>x.c);
  function drawChart(canvas,cfg){
    if(!canvas||typeof Chart==='undefined')return; const type=cfg.type||'bar';
    const COLORS=["#378ADD","#0F6E56","#534AB7","#EF9F27","#D85A30","#85B7EB","#639922","#D4537E"];
    const fnt={font:{size:10}};
    if(type==='donut'){new Chart(canvas,{type:'doughnut',data:{labels:cfg.labels,datasets:[{data:cfg.data,backgroundColor:(cfg.colors||COLORS).slice(0,cfg.data.length),borderWidth:1,borderColor:'rgba(128,128,128,0.15)'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'right',labels:{font:{size:10},boxWidth:10,padding:6}},tooltip:{callbacks:{label:c=>' '+c.label+': '+c.parsed.toLocaleString()+(cfg.unit||'')}}}}});return;}
    if(type==='line'){new Chart(canvas,{type:'line',data:{labels:cfg.labels,datasets:[{data:cfg.data,borderColor:cfg.color||'#0F6E56',backgroundColor:cfg.fill?(cfg.fill_color||'rgba(15,110,86,0.12)'):'transparent',fill:!!cfg.fill,tension:.3,borderWidth:2,pointRadius:3,pointBackgroundColor:cfg.color||'#0F6E56'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y.toLocaleString()+(cfg.unit?' '+cfg.unit:'')}}},scales:{x:{ticks:{font:{size:10},autoSkip:true,maxTicksLimit:8,maxRotation:45},grid:{display:false}},y:{ticks:{font:{size:10},callback:v=>v.toLocaleString()},grid:{color:'rgba(130,130,130,0.12)'},beginAtZero:true}}}});return;}
    if(type==='bar-h'){new Chart(canvas,{type:'bar',data:{labels:cfg.labels,datasets:[{data:cfg.data,backgroundColor:cfg.colors||cfg.data.map(()=>'#85B7EB'),borderRadius:3,borderSkipped:false}]},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>' '+c.parsed.x.toLocaleString()+(cfg.unit?' '+cfg.unit:'')}}},scales:{y:{ticks:fnt,grid:{display:false}},x:{ticks:{font:{size:10},callback:v=>v.toLocaleString()},grid:{color:'rgba(130,130,130,0.12)'}}}}});return;}
    if(type==='bar-stack'&&cfg.series){new Chart(canvas,{type:'bar',data:{labels:cfg.labels,datasets:cfg.series.map((s,i)=>({label:s.label||s.name||('계열'+(i+1)),data:s.data,backgroundColor:s.color||COLORS[i%COLORS.length],borderRadius:2,borderSkipped:false}))},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:true,position:'bottom',labels:{font:{size:10},boxWidth:10,padding:6}},tooltip:{callbacks:{label:c=>' '+c.dataset.label+': '+c.parsed.y.toLocaleString()+(cfg.unit?' '+cfg.unit:'')}}},scales:{x:{stacked:true,ticks:{font:{size:10},autoSkip:false},grid:{display:false}},y:{stacked:true,ticks:{font:{size:10},callback:v=>v.toLocaleString()},grid:{color:'rgba(130,130,130,0.12)'}}}}});return;}
    if(!Array.isArray(cfg.data))return;
    const n=cfg.data.length; const latest=cfg.surprise===true?'#0F6E56':cfg.surprise===false?'#D85A30':'#534AB7';
    const bg=cfg.colors?cfg.colors:cfg.data.map((_,i)=>i<n-1?'#85B7EB':latest);
    const datasets=[{data:cfg.data,backgroundColor:bg,borderRadius:3,borderSkipped:false}];
    if(cfg.consensus!=null)datasets.push({type:'line',data:cfg.data.map(()=>cfg.consensus),borderColor:'#EF9F27',borderDash:[5,3],borderWidth:1.5,pointRadius:0,tension:0,fill:false});
    new Chart(canvas,{type:'bar',data:{labels:cfg.labels,datasets},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.parsed.y.toLocaleString()+(cfg.unit?' '+cfg.unit:'')}}},scales:{x:{ticks:{font:{size:10},autoSkip:false},grid:{display:false}},y:{ticks:{font:{size:10},callback:v=>v.toLocaleString()},grid:{color:'rgba(130,130,130,0.12)'}}}}});
  }
  const hgt=cfg=>{const t=cfg.type||'bar'; if(t==='donut')return 160; if(t==='bar-h')return Math.max((cfg.labels||[]).length*38+44,110); if(t==='bar-stack')return 190; return 150;};
  const CL={red:'빨강',amber:'노랑',green:'초록',blue:'파랑',purple:'보라',pink:'분홍'};
  function render(list){
    cardsEl.innerHTML=''; if(!list.length){empty.hidden=false;counter.textContent='0 / '+cards.length;return;} empty.hidden=true; const queue=[];
    for(const k of list){
      const div=document.createElement('article'); div.className='card';
      const dot=k.color?'<span class="card-dot sw-'+esc(k.color)+'" title="'+esc(CL[k.color]||k.color)+'"></span>':'';
      let imgs=[]; if(k.source_image)imgs.push(k.source_image); if(Array.isArray(k.images))imgs=imgs.concat(k.images);
      const src=s=>/^(https?:|data:)/.test(s)?s:(D.img_base+s);
      const imgBlock=imgs.length?'<div class="card-imgs">'+imgs.map(s=>'<a class="img" href="'+esc(src(s))+'" target="_blank" rel="noopener"><img loading="lazy" src="'+esc(src(s))+'" alt="첨부 이미지"></a>').join('')+'</div>':'';
      const chs=k.charts?k.charts:(k.chart?[k.chart]:[]); let chartBlock='';
      chs.forEach((cfg,ci)=>{ if(!cfg)return; let badge=''; if(cfg.consensus!=null&&cfg.surprise_pct!=null){badge='<span class="chart-badge '+(cfg.surprise===false?'shock':'surp')+'">'+(cfg.surprise_pct>=0?'+':'')+cfg.surprise_pct+'%</span>';}
        const id='ch-'+esc(k.id)+'-'+ci; chartBlock+='<div class="card-chart"><div class="chart-hd"><span class="chart-lbl">'+esc(cfg.title||'차트')+(cfg.unit?' ('+esc(cfg.unit)+')':'')+'</span>'+badge+'</div><div class="chart-wrap" style="height:'+hgt(cfg)+'px"><canvas id="'+id+'"></canvas></div></div>'; queue.push({id,cfg}); });
      const bullets=(k.summary||[]).map(s=>'<li>'+esc(s)+'</li>').join(''); const tags=(k.tags||[]).map(t=>'<span class="tag" data-tag="'+esc(t)+'">'+esc(t)+'</span>').join('');
      div.innerHTML='<h3>'+dot+esc(k.title||'(제목 없음)')+'</h3><p class="date">'+esc(k.date||'')+(k.added_at&&k.added_at!==k.date?' · 추가 '+esc(k.added_at):'')+'</p><div class="card-body">'+imgBlock+chartBlock+(bullets?'<ul>'+bullets+'</ul>':'')+(tags?'<div class="tags">'+tags+'</div>':'')+'</div>';
      cardsEl.appendChild(div);
    }
    requestAnimationFrame(()=>{queue.forEach(({id,cfg})=>drawChart(document.getElementById(id),cfg)); requestAnimationFrame(collapsibles);});
    counter.textContent=list.length+' / '+cards.length;
  }
  function collapsibles(){cardsEl.querySelectorAll('.card').forEach(card=>{const body=card.querySelector('.card-body'); if(!body||card.querySelector('.card-toggle'))return; if(body.scrollHeight<=800)return; card.classList.add('collapsed'); const b=document.createElement('button'); b.type='button'; b.className='card-toggle'; b.innerHTML='⌄ 더보기'; b.onclick=()=>{const c=card.classList.toggle('collapsed'); b.innerHTML=c?'⌄ 더보기':'⌃ 접기';}; body.insertAdjacentElement('afterend',b);});}
  const CHO=["ㄱ","ㄲ","ㄴ","ㄷ","ㄸ","ㄹ","ㅁ","ㅂ","ㅃ","ㅅ","ㅆ","ㅇ","ㅈ","ㅉ","ㅊ","ㅋ","ㅌ","ㅍ","ㅎ"];
  const toCho=s=>{let o='';for(const ch of String(s)){const c=ch.charCodeAt(0);o+=(c>=0xAC00&&c<=0xD7A3)?CHO[Math.floor((c-0xAC00)/588)]:ch;}return o;};
  let colorFilter='', tagFilter='';
  function filter(){const t=(q.value||'').trim().toLowerCase(); let base=cards;
    if(t){const cho=/^[ㄱ-ㅎ]+$/.test(t.replace(/\s+/g,'')); base=base.filter(k=>{const hay=[k.title,k.date,...(k.summary||[]),...(k.tags||[])].filter(Boolean).join(' ').toLowerCase(); return hay.includes(t)||(cho&&toCho(hay).replace(/\s+/g,'').includes(t.replace(/\s+/g,'')));});}
    if(colorFilter)base=base.filter(k=>(k.color||'')===colorFilter); if(tagFilter)base=base.filter(k=>(k.tags||[]).includes(tagFilter)); render(base);}
  q.addEventListener('input',filter);
  // 색상 필터
  const used=new Set(cards.map(c=>c.color).filter(Boolean)); const cf=document.getElementById('cf');
  if(used.size){cf.innerHTML='<button class="cf-chip active" data-color="">전체</button>'+[["red","빨강"],["amber","노랑"],["green","초록"],["blue","파랑"],["purple","보라"],["pink","분홍"]].filter(c=>used.has(c[0])).map(c=>'<button class="cf-chip sw-'+c[0]+'" data-color="'+c[0]+'"><span class="cf-dot"></span>'+c[1]+'</button>').join('');
    cf.onclick=e=>{const b=e.target.closest('.cf-chip'); if(!b)return; colorFilter=b.dataset.color||''; cf.querySelectorAll('.cf-chip').forEach(x=>x.classList.remove('active')); b.classList.add('active'); filter();};} else cf.remove();
  // 태그 필터 (상위 18개)
  const tc={}; cards.forEach(k=>(k.tags||[]).forEach(t=>tc[t]=(tc[t]||0)+1)); const top=Object.entries(tc).sort((a,b)=>b[1]-a[1]).slice(0,18); const tb=document.getElementById('tb');
  if(top.length>1){tb.innerHTML=top.map(([t,n])=>'<span class="tchip" data-tag="'+esc(t)+'">'+esc(t)+' <b>'+n+'</b></span>').join('');
    const setTag=t=>{tagFilter=tagFilter===t?'':t; tb.querySelectorAll('.tchip').forEach(x=>x.classList.toggle('active',x.dataset.tag===tagFilter)); filter();};
    tb.onclick=e=>{const c=e.target.closest('.tchip'); if(c)setTag(c.dataset.tag);}; cardsEl.addEventListener('click',e=>{const t=e.target.closest('.tag'); if(t)setTag(t.dataset.tag);});} else tb.remove();
  cardsEl.addEventListener('click',e=>{const a=e.target.closest('a.img'); if(!a)return; e.preventDefault(); const ov=document.createElement('div'); ov.className='inv-lightbox'; const im=document.createElement('img'); im.src=a.querySelector('img').src; ov.appendChild(im); ov.onclick=()=>ov.remove(); document.body.appendChild(ov);});
  render(cards);
})();
"""

def esc(s): return str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def render_html(note, single=False):
    cards = note.get("cards", [])
    dates = sorted([c.get("date", "") for c in cards if c.get("date")])
    link = f'<a href="https://m.stock.naver.com/domestic/stock/{esc(note["ticker"])}/total" target="_blank" rel="noopener">{esc(note["name"])} ↗</a>' if note.get("ticker") and re.fullmatch(r"\d{6}", str(note["ticker"])) else esc(note["name"])
    kind = "산업" if note.get("kind") == "industry" else "기업"
    meta = " · ".join(x for x in [note.get("ticker") if note.get("ticker") else None, note.get("sector"), None if single else f"{len(cards)}건", None if single else (f"{dates[0]} ~ {dates[-1]}" if dates else None)] if x)
    tags = " ".join(f'<span class="kind">{esc(t)}</span>' for t in (note.get("tags") or [])[:8])
    data = {"cards": cards, "img_base": IMG_BASE}
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(cards[0].get("title")) if single and cards else NOTE_PREFIX + " — " + esc(note["name"])}</title><style>{CSS}</style></head><body>
<header class="topbar"><div class="wrap"><h1><span class="kind">{kind}</span>{link}</h1><p class="subtitle">{esc(meta)} {tags}</p></div></header>
<main class="wrap">
<section class="toolbar"{' style="display:none"' if single else ''}><input id="q" type="search" placeholder="이 노트 안에서 검색 (초성 가능)…" autocomplete="off"><div id="counter" class="counter"></div></section>
<div id="cf" class="color-filter"{' style="display:none"' if single else ''}></div><div id="tb" class="tagbar"{' style="display:none"' if single else ''}></div>
<section id="cards" class="cards"></section><section id="empty" class="empty" hidden>카드가 없습니다.</section>
<p class="foot">옛 Research 탭(investment-notes)에서 이관된 리서치 카드 · 추가는 Claude 에게 "○○ 리서치 노트에 추가" (research_note.py) · 렌더 {TODAY}</p>
</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>window.__RN={payload};{'document.body.classList.add("single");' if single else ''}</script><script>{JS}</script></body></html>"""

def card_name(c):
    t = re.sub(r"\s+", " ", str(c.get("title") or "")).strip() or "(제목 없음)"
    return "🔬 " + (t if len(t) <= 70 else t[:69].rstrip() + "…")

def card_sheet(fid, c, idx, ord_):
    """리서치 카드 1장 = 폴더탭 웹문서 1개"""
    return {"id": new_id(idx, "rc"), "name": card_name(c), "folder": fid, "updated": int(time.time() * 1000), "ord": ord_,
            "type": "webdoc", "research": fid, "rcard": c["id"], "rdate": c.get("date", "")}

def card_html(note, c):
    return render_html({**note, "cards": [c]}, single=True)

def note_name(note):
    """폴더탭에 보이는 카드 이름 = 가장 최근 자료의 제목 (+ 나머지 건수). 예: 「🔬 3Q26 IR — 시설투자 29조 외 20건」"""
    cards = note.get("cards", [])
    if not cards: return NOTE_PREFIX
    latest = sorted(cards, key=lambda c: (c.get("date", ""), c.get("added_at", "")))[-1]
    t = re.sub(r"\s+", " ", str(latest.get("title") or "")).strip() or "리서치 노트"
    if len(t) > 42: t = t[:41].rstrip() + "…"
    return f"🔬 {t}" + (f" 외 {len(cards) - 1}건" if len(cards) > 1 else "")

def next_card_id(cards):
    mx = 0
    for c in cards:
        m = re.match(r"card-(\d+)$", str(c.get("id", "")))
        if m: mx = max(mx, int(m.group(1)))
    return f"card-{mx + 1:03d}"

def normalize_card(c):
    c = dict(c)
    c.setdefault("date", TODAY); c.setdefault("added_at", TODAY)
    c.setdefault("summary", []); c.setdefault("tags", [])
    if "source_image" not in c: c["source_image"] = ""
    return c

# ───────────────────────── 명령 ─────────────────────────
def cmd_add(a):
    raw = sys.stdin.read() if (a.card == "-" or a.cards == "-") else Path(a.card or a.cards).read_text(encoding="utf-8")
    obj = json.loads(raw); new_cards = obj if isinstance(obj, list) else [obj]
    idx, sha = load_index(); byid = {f["id"]: f for f in idx["folders"]}
    fld = find_folder(idx, a.name, a.industry)
    created = False
    if not fld:
        fld = {"id": new_id(idx), "name": a.name, "parent": INDUSTRY_FOLDER if a.industry else WATCHLIST_FOLDER}
        idx["folders"].append(fld); byid[fld["id"]] = fld; created = True
    note = json.loads(gh_get(f"sv/research/{fld['id']}.json", raw=True) or "null") or {
        "folder": fld["id"], "name": fld["name"], "kind": "industry" if a.industry else "company", "ticker": "", "sector": "", "tags": [], "cards": []}
    if a.ticker: note["ticker"] = a.ticker
    if a.sector: note["sector"] = a.sector
    for c in new_cards:
        c = normalize_card(c); c["id"] = next_card_id(note["cards"]); note["cards"].append(c)
        for t in c.get("tags", []):
            if t not in note["tags"] and len(note["tags"]) < 12: note["tags"].append(t)
    note["updated_at"] = TODAY
    added = note["cards"][-len(new_cards):]
    msg = f"research: {fld['name']} +{len(new_cards)} — {added[-1].get('title','')[:40]}"
    put_text(f"sv/research/{fld['id']}.json", json.dumps(note, ensure_ascii=False, indent=1), msg)
    ords = [x.get("ord", 0) for x in idx["sheets"] if x.get("folder") == fld["id"]]
    base = (min(ords) - 10) if ords else 0
    new_sheets = []
    for k, c in enumerate(sorted(added, key=lambda c: (c.get("date", ""), c.get("added_at", "")))):   # 오래된 것부터 → 최신이 맨 위(ord 최소)
        sh = card_sheet(fld["id"], c, idx, base - k); idx["sheets"].append(sh); new_sheets.append(sh)
        put_data(sh["id"], json.dumps({"type": "webdoc", "html": card_html(note, c), "research": fld["id"]}, ensure_ascii=False), msg)
    try: gh_put("sv/sheets/index.json", json.dumps(idx, ensure_ascii=False, indent=2), sha, msg)
    except urllib.error.HTTPError as e:
        if e.code not in (409, 422): raise
        idx2, sha2 = load_index()
        if created and fld["id"] not in {f["id"] for f in idx2["folders"]}: idx2["folders"].append(fld)
        idx2["sheets"] += new_sheets
        gh_put("sv/sheets/index.json", json.dumps(idx2, ensure_ascii=False, indent=2), sha2, msg)
    for sh in new_sheets: print(f"✅ [{path_of(byid, fld)}] {sh['name']}  (sheet {sh['id']}){' — 폴더 생성' if created else ''}")
    print("   대시보드 폴더탭에서 새로고침 후 확인.")

def cmd_list(a):
    idx, _ = load_index(); byid = {f["id"]: f for f in idx["folders"]}
    cnt = {}
    for x in idx["sheets"]:
        if x.get("research"): cnt[x["folder"]] = cnt.get(x["folder"], 0) + 1
    for fid, n in sorted(cnt.items(), key=lambda kv: path_of(byid, byid.get(kv[0], {"name": "?"}))):
        print(f"{fid}  {path_of(byid, byid.get(fid, {'name': '?'}))}  —  리서치 카드 {n}건")

def _load_note(a):
    idx, _ = load_index(); fld = find_folder(idx, a.name, a.industry)
    if not fld: sys.exit(f"폴더 '{a.name}' 없음")
    raw = gh_get(f"sv/research/{fld['id']}.json", raw=True)
    if not raw: sys.exit(f"[{fld['name']}] 리서치 노트 없음")
    return json.loads(raw)

def cmd_show(a):
    note = _load_note(a)
    print(f"== {note['name']} ({note.get('ticker') or ''} {note.get('sector') or ''}) · {len(note['cards'])}건")
    for c in sorted(note["cards"], key=lambda c: (c.get("date", ""), c.get("added_at", "")), reverse=True):
        print(f"\n[{c.get('date')}] {c.get('title')}  #{' #'.join(c.get('tags', [])[:6])}")
        for s in c.get("summary", []): print("  - " + s)
        for ch in ([c["chart"]] if c.get("chart") else []) + (c.get("charts") or []):
            if ch and ch.get("labels") is not None: print(f"  📊 {ch.get('title')}({ch.get('unit') or ''}): " + ", ".join(f"{l} {v}" for l, v in zip(ch.get("labels", []), ch.get("data") or [])))

def cmd_render(a):
    print(render_html(_load_note(a)))

def cmd_migrate(a):
    src = json.load(open(a.src, encoding="utf-8")); out = Path(a.out)
    idx, _ = load_index(); byid = {f["id"]: f for f in idx["folders"]}
    (out / "sv/research").mkdir(parents=True, exist_ok=True); (out / "sv/sheets/data").mkdir(parents=True, exist_ok=True)
    notes = {}   # folder_id → note
    for ent in src.get("companies", []):
        if not ent.get("cards"): continue
        industry = ent.get("type") == "industry"
        name = re.sub(r"\s*\(.*?\)\s*$", "", ent["name"]).strip() or ent["name"]   # "엔비디아 (NVIDIA)" → "엔비디아"
        fld = find_folder(idx, name, industry)
        if not fld:
            fld = {"id": new_id(idx), "name": name, "parent": INDUSTRY_FOLDER if industry else WATCHLIST_FOLDER}
            idx["folders"].append(fld); byid[fld["id"]] = fld; print("  폴더 생성:", path_of(byid, fld))
        note = notes.get(fld["id"])
        if not note:
            raw = gh_get(f"sv/research/{fld['id']}.json", raw=True)
            note = json.loads(raw) if raw else {"folder": fld["id"], "name": fld["name"], "kind": "industry" if industry else "company", "ticker": "", "sector": "", "tags": [], "cards": []}
            notes[fld["id"]] = note
        if ent.get("ticker") and not note.get("ticker"): note["ticker"] = str(ent["ticker"])
        if ent.get("sector") and not note.get("sector"): note["sector"] = ent["sector"]
        for t in ent.get("tags") or []:
            if t not in note["tags"] and len(note["tags"]) < 12: note["tags"].append(t)
        note.setdefault("src_ids", [])
        if ent["id"] not in note["src_ids"]: note["src_ids"].append(ent["id"])
        have = {(c.get("src"), c.get("src_id")) for c in note["cards"]}
        for c in ent["cards"]:
            if (ent["id"], c.get("id")) in have: continue
            c2 = normalize_card(c); c2["src"] = ent["id"]; c2["src_id"] = c.get("id"); c2["id"] = next_card_id(note["cards"]); note["cards"].append(c2)
    n_cards = 0
    for fid, note in notes.items():
        note["updated_at"] = TODAY; n_cards += len(note["cards"])
        (out / f"sv/research/{fid}.json").write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
        sh = find_note(idx, fid)
        if not sh:
            ords = [s.get("ord", 0) for s in idx["sheets"] if s.get("folder") == fid]
            sh = {"id": new_id(idx, "rn"), "name": note_name(note), "folder": fid, "updated": int(time.time() * 1000), "ord": (min(ords) - 10) if ords else 0, "type": "webdoc", "research": True}
            idx["sheets"].append(sh)
        else: sh["name"] = note_name(note); sh["updated"] = int(time.time() * 1000); sh["research"] = True
        html = render_html(note); txt = json.dumps({"type": "webdoc", "html": html, "research": fid}, ensure_ascii=False)
        if len(txt) > CHUNK: print("  ⚠ 4M자 초과 (분할 필요):", note["name"], len(txt))
        (out / f"sv/sheets/data/{sh['id']}.json").write_text(txt, encoding="utf-8")
    (out / "sv/sheets/index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 노트 {len(notes)}개 · 카드 {n_cards}건 → {out}  (sv/research, sv/sheets/data, sv/sheets/index.json)")
    print(f"   푸시: python3 gh_push.py --repo {REPO} -m \"research: investment-notes 이관\" --dir {out}=")

def main():
    ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("add"); p.add_argument("--name", required=True); p.add_argument("--industry", action="store_true"); p.add_argument("--card"); p.add_argument("--cards"); p.add_argument("--ticker"); p.add_argument("--sector")
    sp.add_parser("list")
    for k in ("show", "render"):
        p = sp.add_parser(k); p.add_argument("--name", required=True); p.add_argument("--industry", action="store_true")
    p = sp.add_parser("migrate"); p.add_argument("--src", required=True); p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "add" and not (a.card or a.cards): sys.exit("--card 또는 --cards 필요")
    {"add": cmd_add, "list": cmd_list, "show": cmd_show, "render": cmd_render, "migrate": cmd_migrate}[a.cmd](a)

if __name__ == "__main__":
    main()
