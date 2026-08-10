"""Regenerate index.html from the live Supabase table. Run after pipeline.py;
the weekly Action commits the result so the public GitHub Pages chart stays fresh.

Reads SUPABASE_URL / SUPABASE_SERVICE_KEY from the environment.
"""
import os, json

def load_rows():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not (url and key):
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    from supabase import create_client
    sb = create_client(url, key)
    rows, page = [], 0
    while True:                                   # page past the 1000-row cap
        res = sb.table("rb_player_mentions").select("player,mentions,career_fwar") \
                .range(page * 1000, page * 1000 + 999).execute()
        rows += res.data
        if len(res.data) < 1000:
            break
        page += 1
    return rows

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Rates &amp; Barrels — Mentions vs. Career WAR</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&family=Inter:wght@400;500;600&display=swap" rel="stylesheet"/>
<style>
  :root{--bg:#0e1a22;--panel:#15242f;--panel2:#1b2f3c;--grid:#24404e;--chalk:#eaf2f1;
    --muted:#8ba6b2;--amber:#e8a33d;--teal:#3fb7a6;--neutral:#5f7d8a;--dirt:#c8703c;--line:#40606f;--hi:#ff5d6c;}
  *{box-sizing:border-box;}
  html,body{margin:0;background:var(--bg);color:var(--chalk);}
  body{font-family:'Inter',system-ui,sans-serif;-webkit-font-smoothing:antialiased;
    padding:clamp(16px,3vw,34px);max-width:1200px;margin:0 auto;}
  .eyebrow{font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.22em;
    text-transform:uppercase;color:var(--amber);margin:0 0 10px;}
  h1{font-family:'Oswald',sans-serif;font-weight:600;font-size:clamp(28px,5vw,46px);
    line-height:1.02;margin:0 0 12px;text-transform:uppercase;}
  h1 .thin{color:var(--muted);font-weight:400;}
  .sub{color:var(--muted);font-size:15px;max-width:70ch;line-height:1.55;margin:0 0 6px;}
  .note{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--dirt);margin:10px 0 0;}
  .search{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:22px 2px 0;}
  .search input{background:var(--panel);border:1px solid var(--line);border-radius:9px;color:var(--chalk);
    font-family:'IBM Plex Mono',monospace;font-size:15px;padding:11px 14px;width:min(340px,100%);outline:none;}
  .search input:focus{border-color:var(--amber);}
  .search button{background:transparent;border:1px solid var(--line);border-radius:9px;color:var(--muted);
    font-family:'IBM Plex Mono',monospace;font-size:13px;padding:11px 13px;cursor:pointer;}
  .search button:hover{border-color:var(--amber);color:var(--chalk);}
  #readout{margin:14px 2px 0;min-height:26px;font-family:'IBM Plex Mono',monospace;font-size:14px;color:var(--chalk);}
  #readout .big{font-family:'Oswald',sans-serif;font-size:19px;text-transform:uppercase;color:var(--hi);}
  #readout .dim{color:var(--muted);}
  #readout.miss .big{color:var(--muted);}
  .board{position:relative;margin-top:16px;background:linear-gradient(180deg,var(--panel2),var(--panel));
    border:1px solid var(--grid);border-radius:14px;padding:8px 8px 4px;box-shadow:0 24px 60px -30px #000;}
  svg{display:block;width:100%;height:auto;font-family:'IBM Plex Mono',monospace;}
  .axis-label{fill:var(--muted);font-size:12px;letter-spacing:.06em;text-transform:uppercase;}
  .tick{fill:var(--muted);font-size:11px;}
  .gridline{stroke:var(--grid);stroke-width:1;} .gridline.zero{stroke:var(--line);stroke-width:1.4;}
  .fitline{stroke:var(--line);stroke-width:1.6;stroke-dasharray:6 5;opacity:.75;}
  .zone{fill:var(--amber);opacity:.06;}
  .zone-label{fill:var(--amber);opacity:.85;font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:500;}
  .dot{cursor:pointer;transition:opacity .12s,stroke-width .12s;}
  .dot:hover{stroke-width:2.4px;}
  .dot-label{fill:var(--chalk);font-size:10.5px;pointer-events:none;opacity:.92;}
  #hiRing{opacity:0;} #hiRing.on{opacity:1;}
  #hiDot{opacity:0;} #hiDot.on{opacity:1;}
  #hiLabel{fill:var(--hi);font-family:'Oswald',sans-serif;font-size:14px;text-transform:uppercase;opacity:0;}
  #hiLabel.on{opacity:1;}
  .pulse{animation:p 1.6s ease-out infinite;} @keyframes p{0%{r:8;opacity:.9;}100%{r:26;opacity:0;}}
  .legend{display:flex;flex-wrap:wrap;gap:14px 22px;margin:16px 4px 4px;}
  .legend span{display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);}
  .sw{width:12px;height:12px;border-radius:50%;display:inline-block;}
  .flags-head{font-family:'Oswald',sans-serif;text-transform:uppercase;font-size:19px;letter-spacing:.03em;margin:30px 4px 4px;}
  .flags-head small{display:block;font-family:'Inter',sans-serif;text-transform:none;letter-spacing:normal;font-size:13px;color:var(--muted);font-weight:400;margin-top:4px;}
  .flags{margin-top:14px;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;}
  .flag{background:var(--panel);border:1px solid var(--grid);border-left:3px solid var(--amber);border-radius:8px;padding:11px 13px;}
  .flag .name{font-family:'Oswald',sans-serif;font-size:16px;text-transform:uppercase;}
  .flag .stat{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted);margin-top:3px;}
  .flag .stat b{color:var(--amber);font-weight:500;}
  #tip{position:fixed;pointer-events:none;z-index:20;background:#0a141b;border:1px solid var(--line);border-radius:8px;
    padding:9px 11px;font-size:12.5px;color:var(--chalk);box-shadow:0 10px 30px -10px #000;opacity:0;transition:opacity .1s;max-width:230px;}
  #tip .t-name{font-family:'Oswald',sans-serif;font-size:15px;text-transform:uppercase;margin-bottom:4px;}
  #tip .t-row{font-family:'IBM Plex Mono',monospace;color:var(--muted);} #tip .t-row b{color:var(--chalk);font-weight:500;}
  footer{color:var(--muted);font-size:12px;margin-top:30px;line-height:1.6;}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;}}
</style>
</head>
<body>
  <p class="eyebrow">Rates &amp; Barrels · full archive · __NCOMMA__ players</p>
  <h1>Who gets the airtime <span class="thin">vs.</span><br/>who earned the WAR</h1>
  <p class="sub">Every dot is a player named on the podcast. Height and bubble size scale with total mentions across the full archive; horizontal position is career WAR. The dashed line is the expected mention rate for a given WAR — dots above it get discussed more than their production alone predicts. The 130 most-mentioned are drawn; <b>search any of the __NCOMMA__ players</b> to pin them on the plot.</p>

  <div class="search">
    <input id="q" list="players" placeholder="Type a player… e.g. Billy Cook" autocomplete="off"/>
    <datalist id="players"></datalist>
    <button id="clear">clear</button>
  </div>
  <div id="readout"></div>

  <div class="board">
    <svg id="chart" viewBox="0 0 980 620" role="img" aria-label="Mentions versus career WAR"></svg>
  </div>

  <div class="legend">
    <span><i class="sw" style="background:var(--amber)"></i>Over-discussed underproducer (WAR &le; 8, 120+ mentions)</span>
    <span><i class="sw" style="background:var(--teal)"></i>Established producer (WAR &ge; 25)</span>
    <span><i class="sw" style="background:var(--neutral)"></i>Everyone else</span>
    <span><i class="sw" style="background:var(--hi)"></i>Your search</span>
  </div>

  <p class="flags-head">The over-discussed corner
    <small>Most-mentioned players with little career production. Young prospects still cooking sit here alongside genuine flops; ordered by mentions.</small>
  </p>
  <div class="flags" id="flags"></div>

  <footer>Career WAR is Baseball-Reference's version (fWAR isn't reachable from an automated job). Same-name players (e.g. "Luis García") are merged. Prospects with few MLB games read as low-WAR because the number is career-to-date, not talent.</footer>
  <div id="tip"></div>

<script>
const ALL = __ALL__;
const TOTAL = __N__;
const byNorm = {}; ALL.forEach(d=>byNorm[d.n.toLowerCase()]=d);
const PLOT = [...ALL].sort((a,b)=>b.m-a.m).slice(0,130);

const WAR_CEIL=8, MENTION_FLOOR=120;
function cat(d){ if(d.w<=WAR_CEIL&&d.m>=MENTION_FLOOR)return"amber"; if(d.w>=25)return"teal"; return"neutral"; }
const C={amber:"#e8a33d",teal:"#3fb7a6",neutral:"#5f7d8a"};

const W=980,H=620,M={t:44,r:30,b:66,l:70},iw=W-M.l-M.r,ih=H-M.t-M.b;
const xMin=-5,xMax=95,yMax=920;
const x=v=>M.l+((v-xMin)/(xMax-xMin))*iw, y=v=>M.t+ih-(v/yMax)*ih, r=m=>4+Math.sqrt(m)*1.15;
const svg=document.getElementById("chart"),NS="http://www.w3.org/2000/svg";
function el(t,a,txt,parent){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);
  if(txt!=null)e.textContent=txt;(parent||svg).appendChild(e);return e;}

el("rect",{class:"zone",x:x(xMin),y:M.t,width:x(WAR_CEIL)-x(xMin),height:y(MENTION_FLOOR)-M.t});
el("text",{class:"zone-label",x:x(xMin)+10,y:M.t+20},"Over-discussed");
for(let v=0;v<=xMax;v+=10){el("line",{class:"gridline"+(v===0?" zero":""),x1:x(v),y1:M.t,x2:x(v),y2:M.t+ih});
  el("text",{class:"tick","text-anchor":"middle",x:x(v),y:M.t+ih+20},v);}
for(let v=0;v<=yMax;v+=100){el("line",{class:"gridline"+(v===0?" zero":""),x1:x(xMin),y1:y(v),x2:x(xMax),y2:y(v)});
  el("text",{class:"tick","text-anchor":"end",x:M.l-10,y:y(v)+4},v);}
el("text",{class:"axis-label","text-anchor":"middle",x:M.l+iw/2,y:H-18},"Career WAR (Baseball-Reference)");
el("text",{class:"axis-label","text-anchor":"middle",transform:`rotate(-90 20 ${M.t+ih/2})`,x:20,y:M.t+ih/2},"Podcast mentions");
(function(){const xs=PLOT.map(d=>d.w),ys=PLOT.map(d=>d.m),n=PLOT.length;
  const mx=xs.reduce((a,b)=>a+b)/n,my=ys.reduce((a,b)=>a+b)/n;let nu=0,dn=0;
  for(let i=0;i<n;i++){nu+=(xs[i]-mx)*(ys[i]-my);dn+=(xs[i]-mx)**2;}const b=nu/dn,a=my-b*mx;
  el("line",{class:"fitline",x1:x(xMin),y1:y(a+b*xMin),x2:x(xMax),y2:y(a+b*xMax)});})();

const tip=document.getElementById("tip");
[...PLOT].sort((p,q)=>q.m-p.m).forEach(d=>{
  const c=C[cat(d)],cx=x(d.w),cy=y(d.m),rr=r(d.m);
  const dot=el("circle",{class:"dot",cx,cy,r:rr,fill:c,"fill-opacity":.6,stroke:c,"stroke-width":1.3});
  if(d.m>=300||(cat(d)==="amber"&&d.m>=200)){const anc=cx>W-170?"end":"start",dx=anc==="end"?-(rr+5):(rr+5);
    el("text",{class:"dot-label","text-anchor":anc,x:cx+dx,y:cy+3.5},d.n);}
  const tag=cat(d)==="amber"?"over-discussed":cat(d)==="teal"?"established":"—";
  dot.addEventListener("mousemove",e=>{tip.style.opacity=1;tip.style.left=Math.min(e.clientX+14,innerWidth-242)+"px";
    tip.style.top=(e.clientY+14)+"px";tip.innerHTML=`<div class="t-name">${d.n}</div><div class="t-row">mentions <b>${d.m}</b></div><div class="t-row">career WAR <b>${d.w}</b></div><div class="t-tag" style="color:${c}">${tag}</div>`;});
  dot.addEventListener("mouseleave",()=>tip.style.opacity=0);
});

// highlight layer (drawn last, on top)
const hiRing=el("circle",{id:"hiRing",class:"pulse",fill:"none",stroke:"var(--hi)","stroke-width":2,cx:0,cy:0,r:8});
const hiDot=el("circle",{id:"hiDot",fill:"var(--hi)","fill-opacity":.85,stroke:"#fff","stroke-width":1.5,cx:0,cy:0,r:6});
const hiLabel=el("text",{id:"hiLabel","text-anchor":"middle",x:0,y:0},"");

// datalist
const dl=document.getElementById("players");
ALL.forEach(d=>{const o=document.createElement("option");o.value=d.n;dl.appendChild(o);});

const readout=document.getElementById("readout"),q=document.getElementById("q");
function pct(rank){return Math.round((1-(rank-1)/TOTAL)*100);}
function show(name){
  const d=byNorm[(name||"").trim().toLowerCase()];
  if(!d){readout.className="miss";
    readout.innerHTML=name?`<span class="big">${name}</span> <span class="dim">— not named on the show (or spelled differently)</span>`:"";
    hiRing.classList.remove("on");hiDot.classList.remove("on");hiLabel.classList.remove("on");return;}
  const cx=x(d.w),cy=y(d.m);
  hiRing.setAttribute("cx",cx);hiRing.setAttribute("cy",cy);
  hiDot.setAttribute("cx",cx);hiDot.setAttribute("cy",cy);
  const above=cy>70; hiLabel.setAttribute("x",cx);hiLabel.setAttribute("y",above?cy-14:cy+22);hiLabel.textContent=d.n;
  hiRing.classList.add("on");hiDot.classList.add("on");hiLabel.classList.add("on");
  readout.className="";
  readout.innerHTML=`<span class="big">${d.n}</span> &nbsp;<b>${d.m}</b> mentions `+
    `<span class="dim">(rank ${d.r} of ${TOTAL.toLocaleString()} — top ${pct(d.r)}%)</span> · `+
    `<b>${d.w}</b> career WAR`;
}
q.addEventListener("input",()=>show(q.value));
q.addEventListener("change",()=>show(q.value));
document.getElementById("clear").addEventListener("click",()=>{q.value="";show("");});

// flags cards
const flags=PLOT.filter(d=>cat(d)==="amber").sort((a,b)=>b.m-a.m).slice(0,18);
const fw=document.getElementById("flags");
flags.forEach(d=>{const e=document.createElement("div");e.className="flag";
  e.innerHTML=`<div class="name">${d.n}</div><div class="stat"><b>${d.m}</b> mentions &middot; <b>${d.w}</b> career WAR</div>`;fw.appendChild(e);});

// preload Billy Cook — the reason for all this
q.value="Billy Cook"; show("Billy Cook");
</script>
</body>
</html>"""

def main():
    rows = [r for r in load_rows() if r["player"] != "Trevor May"]
    rows.sort(key=lambda r: -(r["mentions"] or 0))
    N = len(rows)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    allw = [{"n": r["player"], "m": int(r["mentions"]),
             "w": round(float(r["career_fwar"]), 1), "r": r["rank"]}
            for r in rows if r["career_fwar"] is not None]
    html = TEMPLATE.replace("__ALL__", json.dumps(allw, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__NCOMMA__", f"{N:,}").replace("__N__", str(N))
    with open("index.html", "w") as f:
        f.write(html)
    print(f"built index.html: {N} players, {len(allw)} with WAR")

if __name__ == "__main__":
    main()
