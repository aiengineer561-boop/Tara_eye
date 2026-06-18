"""
main.py — the CLOUD half (deploy this on Render).

Render is on the public internet and CANNOT reach your ESP32 (your router blocks
inbound). So this app never talks to the robot. Instead:

    browser clicks "happy"  ->  this app puts {emotion: happy} on a QUEUE
    listener.py (at home)   ->  long-polls GET /pull, gets the command,
                                fires it at the ESP on your LAN,
                                and reports the device's status/log back up.

That's why the listener can reach the robot and the cloud can't: the listener
makes an OUTBOUND connection, which NAT allows.

DEPLOY ON RENDER
----------------
  • New > Web Service > point at your repo (or upload these files).
  • Build command:  pip install -r requirements.txt
  • Start command:  uvicorn main:app --host 0.0.0.0 --port $PORT

Notes
  • Free Render services sleep when idle. The listener's long-poll keeps hitting
    /pull, which keeps this awake — so that mostly takes care of itself.
  • Queue/status are in memory: fine for one instance. For multiple instances
    you'd move them to Redis, but you almost certainly don't need that.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Config + shared state (in memory)                                           #
# --------------------------------------------------------------------------- #
EMOTIONS = [
    "neutral", "happy", "joy", "love", "starstruck", "surprised",
    "curious", "suspicious", "sad", "angry", "sleepy", "dizzy",
]

QUEUE: "deque[dict]" = deque(maxlen=200)          # commands waiting for the listener
LOG: "deque[dict]" = deque(maxlen=400)            # device log mirror: {seq, line}
mirror = {"last_report": 0.0, "status": None, "error": None}
_log_seq = 0


# --------------------------------------------------------------------------- #
# App                                                                         #
# --------------------------------------------------------------------------- #
app = FastAPI(title="Robot Eyes Cloud", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class CmdIn(BaseModel):
    c: str


class ReportIn(BaseModel):
    status: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    lines: list[str] = []


def _push(item: dict) -> dict:
    QUEUE.append(item)
    return {"queued": True, "pending": len(QUEUE)}


# ----- endpoints the BROWSER calls (enqueue) ------------------------------- #
@app.post("/emotion/{name}")
async def enqueue_emotion(name: str):
    return _push({"type": "emotion", "name": name})


@app.post("/blink")
async def enqueue_blink():
    return _push({"type": "blink"})


@app.post("/cmd")
async def enqueue_cmd(body: CmdIn):
    return _push({"type": "cmd", "c": body.c})


@app.post("/media/{action}")
async def enqueue_media(action: str, target: str = "both"):
    return _push({"type": "media", "action": action, "target": target})


# ----- endpoints the LISTENER calls ---------------------------------------- #
@app.get("/pull")
async def pull():
    """Long-poll: hold up to ~25s waiting for a command, then return what's queued."""
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if QUEUE:
            items = []
            while QUEUE:
                items.append(QUEUE.popleft())
            return {"commands": items}
        await asyncio.sleep(0.25)
    return {"commands": []}


@app.post("/report")
async def report(rep: ReportIn):
    """Listener pushes the device's current status + new log lines up to us."""
    global _log_seq
    mirror["last_report"] = time.time()
    mirror["status"] = rep.status
    mirror["error"] = rep.error
    for line in rep.lines:
        _log_seq += 1
        LOG.append({"seq": _log_seq, "line": line})
    return {"ok": True}


# ----- endpoint the BROWSER polls for live state --------------------------- #
@app.get("/state")
async def state(after: int = 0):
    fresh = (time.time() - mirror["last_report"]) < 12
    new = [x["line"] for x in LOG if x["seq"] > after]
    seq = LOG[-1]["seq"] if LOG else after
    return {
        "listener_online": fresh,
        "device_ok": fresh and mirror["status"] is not None,
        "status": mirror["status"],
        "error": mirror["error"],
        "seq": seq,
        "lines": new,
    }


@app.get("/healthz")
async def healthz():
    return {"ok": True, "pending": len(QUEUE)}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PAGE


# --------------------------------------------------------------------------- #
# Control page                                                                #
# --------------------------------------------------------------------------- #
HTML_PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot Eyes · Cloud</title>
<style>
  :root{
    --bg:#0e1117; --bg2:#11151d; --panel:#161b26; --panel2:#1b2130;
    --line:#222a39; --line2:#2c3650;
    --text:#e9edf6; --muted:#8b93a7; --faint:#5c6478;
    --amber:#ffc24b; --amber-soft:#ffd980; --mint:#74e6d2; --danger:#ff7a7a;
    --eye-fill:#ffce5a; --eye-iris:#bf6f0d; --eye-glow:#ffb838;
    --r:14px;
    --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{
    background:var(--bg); color:var(--text); font-family:var(--sans);
    line-height:1.45; -webkit-font-smoothing:antialiased;
    padding:22px 18px 56px; max-width:1040px; margin:0 auto;
  }
  .eyebrow{font-family:var(--mono); font-size:11px; letter-spacing:.22em;
    text-transform:uppercase; color:var(--faint)}
  a{color:var(--mint)}

  .banner{display:none; margin:0 0 14px; padding:11px 14px; border-radius:12px;
    background:rgba(255,194,75,.12); border:1px solid var(--amber); color:var(--amber-soft);
    font-family:var(--mono); font-size:12px}
  .banner.show{display:block}

  header{display:flex; align-items:center; justify-content:space-between;
    gap:16px; flex-wrap:wrap; margin-bottom:10px}
  .brand h1{font-size:19px; font-weight:800; letter-spacing:.02em; margin:2px 0 0}
  .pill{display:inline-flex; align-items:center; gap:8px; font-family:var(--mono);
    font-size:12px; padding:7px 12px; border:1px solid var(--line);
    border-radius:999px; background:var(--panel); color:var(--muted)}
  .dot{width:8px; height:8px; border-radius:50%; background:var(--faint)}
  .pill.up .dot{background:var(--mint); box-shadow:0 0 10px var(--mint)}
  .pill.warn .dot{background:var(--amber); box-shadow:0 0 10px var(--amber)}
  .pill.down .dot{background:var(--danger); box-shadow:0 0 10px var(--danger)}

  .stage{display:flex; flex-direction:column; align-items:center; padding:26px 0 14px}
  .eyes{display:flex; gap:30px}
  .eye{width:128px; height:160px; filter:drop-shadow(0 0 22px var(--eye-glow))}
  .lid, .gaze{transition:transform .18s cubic-bezier(.2,.8,.25,1);
    transform-box:view-box; transform-origin:0 0}
  .eye.dim{filter:drop-shadow(0 0 8px var(--eye-glow)); opacity:.78}
  .eye.spin .gaze{animation:spin 1.1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .now{margin-top:16px; font-family:var(--mono); font-size:12px; color:var(--muted)}
  .now b{color:var(--amber-soft); font-weight:600}

  .grid{display:grid; grid-template-columns:1.1fr .9fr; gap:14px; margin-top:8px}
  .card{background:var(--panel); border:1px solid var(--line); border-radius:var(--r); padding:16px}
  .card h2{font-size:12px; font-family:var(--mono); letter-spacing:.14em;
    text-transform:uppercase; color:var(--muted); margin:0 0 12px; font-weight:600}

  .btns{display:grid; grid-template-columns:repeat(3,1fr); gap:8px}
  .btn{appearance:none; cursor:pointer; font-family:var(--mono); font-size:12px;
    letter-spacing:.06em; text-transform:lowercase; color:var(--text);
    background:var(--panel2); border:1px solid var(--line); border-radius:10px;
    padding:11px 8px; transition:transform .08s, border-color .15s, color .15s, background .15s}
  .btn:hover{border-color:var(--line2); color:var(--amber-soft); transform:translateY(-1px)}
  .btn:active{transform:translateY(0)}
  .btn[data-active="1"]{background:rgba(255,194,75,.14); border-color:var(--amber); color:var(--amber-soft)}
  .btn:focus-visible{outline:2px solid var(--amber); outline-offset:2px}
  .row{display:flex; gap:8px; margin-top:10px; flex-wrap:wrap}
  .btn.ghost{background:transparent}
  .btn.warn:hover{border-color:var(--danger); color:var(--danger)}

  .field{display:flex; gap:8px}
  input[type=text], input[type=password], select{font-family:var(--mono); font-size:13px;
    color:var(--text); background:var(--bg2); border:1px solid var(--line);
    border-radius:10px; padding:10px 12px; width:100%}
  input:focus, select:focus{outline:none; border-color:var(--amber)}
  .send{flex:0 0 auto; cursor:pointer; font-family:var(--mono); font-size:12px;
    background:var(--amber); color:#1a1206; border:0; border-radius:10px;
    padding:0 16px; font-weight:700; letter-spacing:.04em}
  .send:hover{background:var(--amber-soft)}
  .log{margin-top:12px; height:188px; overflow:auto; background:#0a0d13;
    border:1px solid var(--line); border-radius:10px; padding:10px 12px;
    font-family:var(--mono); font-size:12px; line-height:1.6; color:#aeb7cc}
  .log .l{white-space:pre-wrap; word-break:break-word}
  .log .l.sys{color:var(--mint)} .log .l.err{color:var(--danger)}
  .log::-webkit-scrollbar{width:8px}
  .log::-webkit-scrollbar-thumb{background:var(--line2); border-radius:8px}

  .strip{display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px}
  .label{font-family:var(--mono); font-size:11px; color:var(--faint);
    letter-spacing:.12em; text-transform:uppercase; margin-bottom:8px; display:block}
  .meta{font-family:var(--mono); font-size:11px; color:var(--muted); margin-top:10px}
  footer{margin-top:22px; color:var(--faint); font-size:12px; line-height:1.6}
  footer code{font-family:var(--mono); color:var(--muted); background:var(--bg2);
    padding:1px 6px; border-radius:6px; border:1px solid var(--line)}

  @media (max-width:760px){
    .grid, .strip{grid-template-columns:1fr}
    .eyes{gap:22px} .eye{width:104px; height:130px}
  }
  @media (prefers-reduced-motion:reduce){
    .lid,.gaze{transition:none} .eye.spin .gaze{animation:none}
  }
</style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="eyebrow">ESP32 · cloud + listener</div>
      <h1>Robot Eyes</h1>
    </div>
    <div class="pill" id="pill"><span class="dot"></span><span id="pillTxt">connecting…</span></div>
  </header>

  <section class="stage">
    <div class="eyes">
      <svg class="eye" id="eyeL" viewBox="0 0 120 150" aria-hidden="true">
        <defs><clipPath id="clipL"><rect x="6" y="6" width="108" height="138" rx="48" ry="54"/></clipPath></defs>
        <g clip-path="url(#clipL)">
          <rect class="body" x="6" y="6" width="108" height="138" rx="48" ry="54" fill="var(--eye-fill)"/>
          <g class="gaze">
            <circle class="iris" cx="60" cy="75" r="30" fill="var(--eye-iris)"/>
            <ellipse class="spec" cx="50" cy="62" rx="9" ry="6" fill="rgba(255,255,255,.85)"/>
          </g>
          <rect class="lid lid-top" x="-12" y="-150" width="144" height="150" fill="var(--bg)"/>
          <rect class="lid lid-bot" x="-12" y="0" width="144" height="300" fill="var(--bg)"/>
        </g>
      </svg>
      <svg class="eye" id="eyeR" viewBox="0 0 120 150" aria-hidden="true">
        <defs><clipPath id="clipR"><rect x="6" y="6" width="108" height="138" rx="48" ry="54"/></clipPath></defs>
        <g clip-path="url(#clipR)">
          <rect class="body" x="6" y="6" width="108" height="138" rx="48" ry="54" fill="var(--eye-fill)"/>
          <g class="gaze">
            <circle class="iris" cx="60" cy="75" r="30" fill="var(--eye-iris)"/>
            <ellipse class="spec" cx="50" cy="62" rx="9" ry="6" fill="rgba(255,255,255,.85)"/>
          </g>
          <rect class="lid lid-top" x="-12" y="-150" width="144" height="150" fill="var(--bg)"/>
          <rect class="lid lid-bot" x="-12" y="0" width="144" height="300" fill="var(--bg)"/>
        </g>
      </svg>
    </div>
    <div class="now">showing <b id="nowEmotion">neutral</b></div>
  </section>

  <div class="grid">
    <div class="card">
      <h2>Expressions</h2>
      <div class="btns" id="emotionBtns"></div>
      <div class="row">
        <button class="btn ghost" id="blinkBtn">blink</button>
        <button class="btn ghost" onclick="sendCmd('idle')">idle</button>
      </div>
    </div>

    <div class="card">
      <h2>Console</h2>
      <div class="field">
        <input type="text" id="cmdInput" placeholder="happy · blink · look left · status · help"
               autocomplete="off" spellcheck="false">
        <button class="send" id="cmdSend">send</button>
      </div>
      <div class="log" id="log"></div>
    </div>
  </div>

  <div class="strip">
    <div class="card">
      <h2>GIF</h2>
      <label class="label">Play a GIF already stored on the device</label>
      <div class="field">
        <select id="gifTarget" style="width:auto">
          <option value="both">both</option><option value="left">left</option><option value="right">right</option>
        </select>
        <button class="btn ghost" onclick="media('show')">show</button>
        <button class="btn ghost" onclick="media('stop')">stop</button>
        <button class="btn ghost warn" onclick="media('clear')">clear</button>
      </div>
      <div class="meta">Uploading a new GIF over the cloud isn't wired up yet — say the word and I'll add it.</div>
    </div>

    <div class="card">
      <h2>Connection</h2>
      <label class="label">Where the listener and device stand right now</label>
      <div class="meta" id="meta">—</div>
    </div>
  </div>

  <footer>
    Clicks here drop commands on a queue. <code>listener.py</code> running next to the robot
    pulls them and drives the eyes, then reports status back. If the pill says
    <b>listener offline</b>, start the listener at home.
  </footer>

<script>
// ---- eye animation (same as the local relay) ----------------------------- //
const SX=6, SW=108, SH=138, TOP=6, BOT=144;
const GAZE_X=12, GAZE_Y=14;
const STATES = {
  neutral:   {lt:.06, lb:.06, lx:0,   ly:0},
  happy:     {lt:.05, lb:.42, lx:0,   ly:.10},
  joy:       {lt:.03, lb:.56, lx:0,   ly:.06},
  love:      {lt:.05, lb:.42, lx:0,   ly:.06, fill:'#ffd0e2', iris:'#d83a78', glow:'#ff7eb0'},
  starstruck:{lt:0,   lb:0,   lx:0,   ly:0,  r:36},
  surprised: {lt:0,   lb:0,   lx:0,   ly:0,  r:36},
  curious:   {lt:.10, lb:.02, lx:.55, ly:-.45},
  suspicious:{lt:.44, lb:.30, lx:.55, ly:0},
  sad:       {lt:.30, lb:.02, lx:0,   ly:.55, fill:'#d6ecff', iris:'#2f7fc2', glow:'#8fc6ff'},
  angry:     {lt:.42, lb:.10, lx:0,   ly:-.25, fill:'#ffcf9e', iris:'#c2410c', glow:'#ff9a4b'},
  sleepy:    {lt:.62, lb:.05, lx:0,   ly:.20, dim:true},
  dizzy:     {lt:.12, lb:.12, lx:0,   ly:0,  spin:true},
};
let current='neutral';
function paint(name){
  const p=STATES[name]||STATES.neutral;
  const covTop=TOP+p.lt*SH, covBot=BOT-p.lb*SH;
  const gx=(p.lx||0)*GAZE_X, gy=(p.ly||0)*GAZE_Y;
  for(const id of ['eyeL','eyeR']){
    const e=document.getElementById(id);
    e.querySelector('.lid-top').style.transform=`translateY(${covTop}px)`;
    e.querySelector('.lid-bot').style.transform=`translateY(${covBot}px)`;
    e.querySelector('.gaze').style.transform=`translate(${gx}px,${gy}px)`;
    e.querySelector('.iris').setAttribute('r',p.r||30);
    e.style.setProperty('--eye-fill',p.fill||'#ffce5a');
    e.style.setProperty('--eye-iris',p.iris||'#bf6f0d');
    e.style.setProperty('--eye-glow',p.glow||'#ffb838');
    e.classList.toggle('dim',!!p.dim);
    e.classList.toggle('spin',!!p.spin);
  }
}
function setEyes(name){
  if(!(name in STATES)) name='neutral';
  current=name; paint(name);
  document.getElementById('nowEmotion').textContent=name;
  document.querySelectorAll('#emotionBtns .btn').forEach(b=>b.dataset.active=(b.dataset.emotion===name)?'1':'0');
}
function blinkEyes(){
  for(const id of ['eyeL','eyeR']){
    const e=document.getElementById(id);
    e.querySelector('.lid-top').style.transform='translateY(75px)';
    e.querySelector('.lid-bot').style.transform='translateY(75px)';
  }
  setTimeout(()=>paint(current),140);
}
function gazeTo(dir){
  const map={up:[0,-1],down:[0,1],left:[-1,0],right:[1,0],center:[0,0]};
  const v=map[dir]; if(!v) return;
  document.querySelectorAll('.gaze').forEach(g=>g.style.transform=`translate(${v[0]*GAZE_X}px,${v[1]*GAZE_Y}px)`);
}

// ---- networking (talks to the CLOUD queue, not the device) ---------------- //
function logLine(text, cls){
  const box=document.getElementById('log');
  const div=document.createElement('div');
  div.className='l'+(cls?' '+cls:''); div.textContent=text;
  box.appendChild(div); box.scrollTop=box.scrollHeight;
  while(box.childNodes.length>300) box.removeChild(box.firstChild);
}
async function call(method, path, body){
  const opt={method, headers:{}};
  if(body!==undefined){ opt.headers['Content-Type']='application/json'; opt.body=JSON.stringify(body); }
  const r=await fetch(path, opt);
  let data; try{ data=await r.json(); }catch{ data={ok:r.ok}; }
  if(!r.ok) throw new Error(data.detail||('HTTP '+r.status));
  return data;
}

async function setEmotion(name){
  setEyes(name);                                   // optimistic; listener confirms via log
  try{ await call('POST','/emotion/'+encodeURIComponent(name)); }
  catch(e){ logLine('! '+e.message,'err'); }
}
async function sendCmd(text){
  if(!text) return;
  const t=text.trim().toLowerCase();
  if(t in STATES) setEyes(t);
  else if(t==='blink') blinkEyes();
  else if(t.startsWith('look ')) gazeTo(t.slice(5).trim());
  logLine('> '+text);
  try{ await call('POST','/cmd',{c:text}); }
  catch(e){ logLine('! '+e.message,'err'); }
}
async function media(action){
  const target=document.getElementById('gifTarget').value;
  const path = action==='show' ? '/media/show?target='+target : '/media/'+action;
  try{ await call('POST',path); logLine('# media '+action,'sys'); }
  catch(e){ logLine('! '+e.message,'err'); }
}

// ---- live state poll ------------------------------------------------------ //
let seqCursor=0;
async function pollState(){
  const pill=document.getElementById('pill'), txt=document.getElementById('pillTxt');
  try{
    const s=await call('GET','/state?after='+seqCursor);
    if(typeof s.seq==='number') seqCursor=s.seq;
    (s.lines||[]).forEach(l=>logLine(l));
    if(!s.listener_online){
      pill.className='pill down'; txt.textContent='listener offline';
      document.getElementById('meta').textContent='start listener.py next to the robot';
    } else if(!s.device_ok){
      pill.className='pill warn'; txt.textContent='no device';
      document.getElementById('meta').textContent=s.error||'listener up, ESP not answering';
    } else {
      pill.className='pill up'; txt.textContent='linked';
      const st=s.status||{};
      if(st.emotion && st.emotion!==current) setEyes(st.emotion);
      const where=st.sta_connected?(st.sta_ssid+' · '+st.sta_ip):('AP · '+(st.ap_ip||''));
      const up=st.uptime?Math.floor(st.uptime/1000)+'s up':'';
      document.getElementById('meta').textContent=where+(up?'  ·  '+up:'')+(st.heap?'  ·  '+Math.round(st.heap/1024)+'k free':'');
    }
  }catch(e){
    pill.className='pill down'; txt.textContent='cloud error';
  }
}

// ---- idle life ------------------------------------------------------------ //
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
function idleLoop(){ if(reduce) return; blinkEyes(); setTimeout(idleLoop, 4000+Math.random()*5000); }

// ---- wire up -------------------------------------------------------------- //
function build(){
  const wrap=document.getElementById('emotionBtns');
  for(const name of {{EMOTIONS}}){
    const b=document.createElement('button');
    b.className='btn'; b.textContent=name; b.dataset.emotion=name; b.dataset.active='0';
    b.onclick=()=>setEmotion(name);
    wrap.appendChild(b);
  }
  document.getElementById('blinkBtn').onclick=()=>{ blinkEyes(); call('POST','/blink').catch(e=>logLine('! '+e.message,'err')); };

  const ci=document.getElementById('cmdInput');
  document.getElementById('cmdSend').onclick=()=>{ sendCmd(ci.value); ci.value=''; };
  ci.addEventListener('keydown',e=>{ if(e.key==='Enter'){ sendCmd(ci.value); ci.value=''; } });

  setEyes('neutral');
  pollState(); setInterval(pollState, 1600);
  setTimeout(idleLoop, 6000);
}
build();
</script>
</body>
</html>'''

HTML_PAGE = HTML_PAGE.replace("{{EMOTIONS}}", json.dumps(EMOTIONS))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
