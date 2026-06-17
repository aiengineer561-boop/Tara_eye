"""
Robot Eyes Cloud Relay  —  FastAPI on Render.com

Architecture:
    User (anywhere) → This server (public URL) ← ESP32 (polls every ~2s)

Deploy: push to GitHub → connect repo on render.com → auto-deploy.
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from collections import deque
import time, os, datetime

app = FastAPI(title="Robot Eyes Cloud Relay")

API_KEY = os.getenv("EYES_API_KEY", "roboteyes2025")

# ---- in-memory state ----
pending_emotion: Optional[str] = None
pending_blink: bool = False
pending_ota_url: Optional[str] = None

device_status = {
    "online": False,
    "ip": "unknown",
    "emotion": "neutral",
    "uptime": 0,
    "heap": 0,
    "last_seen": 0,
    "firmware": "unknown",
}

# ---- activity log (last 50 events) ----
activity_log: deque = deque(maxlen=50)

def log_event(source: str, action: str, detail: str = ""):
    ts = datetime.datetime.utcnow().strftime("%H:%M:%S")
    entry = {"time": ts, "source": source, "action": action, "detail": detail}
    activity_log.appendleft(entry)

EMOTIONS = [
    "neutral", "neutral_static", "happy", "sad",
    "angry", "curious", "suspicious"
]

class EmotionReq(BaseModel):
    emotion: str

class OtaReq(BaseModel):
    url: str

class Heartbeat(BaseModel):
    ip: str = "unknown"
    emotion: str = "neutral"
    uptime: int = 0
    heap: int = 0
    firmware: str = "unknown"

def check_key(request: Request) -> bool:
    key = request.query_params.get("key") or request.headers.get("X-API-Key")
    return key == API_KEY

# ============================================================================
#  DEVICE-FACING ENDPOINTS
# ============================================================================

@app.get("/api/device/poll")
async def device_poll(request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_emotion, pending_blink, pending_ota_url
    resp = {
        "emotion": pending_emotion,
        "blink": pending_blink,
        "ota_url": pending_ota_url,
    }
    if pending_emotion or pending_blink or pending_ota_url:
        parts = []
        if pending_emotion: parts.append(f"emotion={pending_emotion}")
        if pending_blink: parts.append("blink")
        if pending_ota_url: parts.append("ota")
        log_event("ESP32", "poll (picked up)", ", ".join(parts))
    pending_emotion = None
    pending_blink = False
    pending_ota_url = None
    return resp

@app.post("/api/device/heartbeat")
async def device_heartbeat(hb: Heartbeat, request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    was_offline = not device_status["online"] or (time.time() - device_status["last_seen"] > 15)
    device_status.update({
        "online": True, "ip": hb.ip, "emotion": hb.emotion,
        "uptime": hb.uptime, "heap": hb.heap,
        "firmware": hb.firmware, "last_seen": time.time(),
    })
    if was_offline:
        log_event("ESP32", "came online", f"IP {hb.ip}, fw {hb.firmware}")
    return {"ok": True}

# ============================================================================
#  USER-FACING API
# ============================================================================

@app.post("/api/emotion")
async def set_emotion(req: EmotionReq, request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_emotion
    pending_emotion = req.emotion
    log_event("USER", "set emotion", req.emotion)
    return {"ok": True, "queued": req.emotion}

@app.post("/api/blink")
async def do_blink(request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_blink
    pending_blink = True
    log_event("USER", "blink", "")
    return {"ok": True}

@app.post("/api/ota")
async def queue_ota(req: OtaReq, request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_ota_url
    pending_ota_url = req.url
    log_event("USER", "OTA queued", req.url[:60])
    return {"ok": True, "ota_url": req.url}

@app.get("/api/status")
async def get_status():
    age = time.time() - device_status["last_seen"] if device_status["last_seen"] else 999
    return {**device_status, "online": age < 15}

@app.get("/api/logs")
async def get_logs():
    return {"logs": list(activity_log)}

# ============================================================================
#  WEB UI
# ============================================================================

PAGE = r"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robot Eyes — Cloud Control</title>
<style>
:root{
  --bg:#080c12;--card:#0f1520;--card2:#131b28;--cyan:#23d5e8;--cyanDim:#0e4f56;
  --red:#e85d5d;--redDim:#3a1c1c;--grn:#3fb950;--grnDim:#162d1f;--amber:#e8a823;
  --txt:#e6edf3;--txt2:#b0bac5;--mut:#5a6370;--bdr:#1a2233;--bdr2:#243040;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--txt);
  min-height:100vh}
.wrap{max-width:860px;margin:0 auto;padding:16px}
h1{font-size:24px;font-weight:700;margin-bottom:2px}
.sub{color:var(--mut);font-size:13px;margin-bottom:20px}

/* status bar */
.status-bar{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--bdr);border-radius:12px;padding:12px 16px;
  flex:1;min-width:120px}
.stat .label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--mut);margin-bottom:4px}
.stat .val{font-size:18px;font-weight:600}
.stat .val.online{color:var(--grn)}.stat .val.offline{color:var(--red)}

/* cards */
.card{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:18px;margin-bottom:16px}
.card-title{font-size:15px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.card-title .icon{font-size:18px}

/* emotion grid */
.emo-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}
.emo-btn{background:var(--card2);color:var(--txt2);border:1px solid var(--bdr2);border-radius:10px;
  padding:12px 8px;font-size:14px;cursor:pointer;transition:all .15s;text-align:center}
.emo-btn:hover{border-color:var(--cyan);color:var(--txt);background:#162030}
.emo-btn.active{border-color:var(--cyan);background:#0d2535;color:var(--cyan);
  box-shadow:0 0 16px #23d5e818}
.emo-btn .emoji{font-size:22px;display:block;margin-bottom:4px}
.blink-btn{background:var(--cyanDim);border-color:var(--cyan);color:var(--cyan)}
.blink-btn:hover{background:#1a5a62}

/* OTA */
.ota-row{display:flex;gap:8px;margin-top:8px}
.ota-input{flex:1;background:#0a1018;border:1px solid var(--bdr2);border-radius:8px;padding:10px 12px;
  color:var(--txt);font-size:13px;font-family:monospace}
.ota-input::placeholder{color:var(--mut)}
.ota-btn{background:var(--cyan);color:#04222a;border:none;border-radius:8px;padding:10px 18px;
  font-weight:600;font-size:13px;cursor:pointer;white-space:nowrap}
.ota-btn:hover{filter:brightness(1.15)}

/* log */
.log-box{background:#060a10;border:1px solid var(--bdr);border-radius:10px;
  max-height:280px;overflow-y:auto;font-family:'Cascadia Code','Fira Code',monospace;font-size:12px}
.log-entry{padding:6px 12px;border-bottom:1px solid #0d1320;display:flex;gap:10px;align-items:baseline}
.log-entry:last-child{border-bottom:none}
.log-time{color:var(--mut);min-width:60px;font-size:11px}
.log-src{min-width:52px;font-size:11px;font-weight:600;border-radius:4px;padding:1px 6px;text-align:center}
.log-src.esp{background:var(--grnDim);color:var(--grn)}
.log-src.usr{background:var(--cyanDim);color:var(--cyan)}
.log-action{color:var(--txt2)}
.log-detail{color:var(--amber);margin-left:auto}
.empty-log{padding:16px;text-align:center;color:var(--mut)}

/* API ref */
.api-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--bdr);
  font-size:13px}
.api-row:last-child{border-bottom:none}
.method{font-weight:700;font-size:11px;border-radius:4px;padding:2px 8px;min-width:44px;text-align:center}
.method.post{background:var(--cyanDim);color:var(--cyan)}
.method.get{background:var(--grnDim);color:var(--grn)}
.api-path{font-family:monospace;color:var(--txt)}
.api-note{color:var(--mut);margin-left:auto;font-size:12px}

/* toast */
.toast{position:fixed;bottom:20px;right:20px;background:var(--card2);border:1px solid var(--cyan);
  border-radius:10px;padding:10px 20px;font-size:14px;color:var(--cyan);
  opacity:0;transform:translateY(10px);transition:.25s;pointer-events:none;z-index:99}
.toast.show{opacity:1;transform:translateY(0)}

/* test panel */
.test-row{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap}
.test-input{background:#0a1018;border:1px solid var(--bdr2);border-radius:8px;padding:8px 12px;
  color:var(--txt);font-size:13px;font-family:monospace;width:240px}
.test-btn{background:var(--card2);border:1px solid var(--bdr2);border-radius:8px;padding:8px 14px;
  color:var(--txt2);font-size:13px;cursor:pointer}
.test-btn:hover{border-color:var(--cyan);color:var(--cyan)}
.test-result{background:#060a10;border-radius:8px;padding:10px 12px;margin-top:8px;
  font-family:monospace;font-size:12px;color:var(--txt2);display:none;white-space:pre-wrap;word-break:break-all}
</style></head><body>
<div class="wrap">
  <h1>&#127758; Robot Eyes — Cloud Control</h1>
  <div class="sub">Control your robot from anywhere in the world</div>

  <!-- status bar -->
  <div class="status-bar">
    <div class="stat"><div class="label">Status</div><div class="val" id="s_on">—</div></div>
    <div class="stat"><div class="label">Local IP</div><div class="val" id="s_ip">—</div></div>
    <div class="stat"><div class="label">Emotion</div><div class="val" id="s_emo">—</div></div>
    <div class="stat"><div class="label">Uptime</div><div class="val" id="s_up">—</div></div>
    <div class="stat"><div class="label">Heap</div><div class="val" id="s_hp">—</div></div>
    <div class="stat"><div class="label">Firmware</div><div class="val" id="s_fw">—</div></div>
  </div>

  <!-- expressions -->
  <div class="card">
    <div class="card-title"><span class="icon">&#128065;</span> Expressions</div>
    <div class="emo-grid" id="btns">
      <button class="emo-btn" onclick="em('neutral')"><span class="emoji">&#128528;</span>Neutral</button>
      <button class="emo-btn" onclick="em('neutral_static')"><span class="emoji">&#128566;</span>Static</button>
      <button class="emo-btn" onclick="em('happy')"><span class="emoji">&#128522;</span>Happy</button>
      <button class="emo-btn" onclick="em('sad')"><span class="emoji">&#128546;</span>Sad</button>
      <button class="emo-btn" onclick="em('angry')"><span class="emoji">&#128544;</span>Angry</button>
      <button class="emo-btn" onclick="em('curious')"><span class="emoji">&#129300;</span>Curious</button>
      <button class="emo-btn" onclick="em('suspicious')"><span class="emoji">&#128530;</span>Suspicious</button>
      <button class="emo-btn blink-btn" onclick="bl()"><span class="emoji">&#128065;</span>Blink</button>
    </div>
  </div>

  <!-- OTA -->
  <div class="card">
    <div class="card-title"><span class="icon">&#128640;</span> Remote OTA Firmware Update</div>
    <div style="color:var(--mut);font-size:13px;margin-bottom:8px">
      Paste a public URL to a compiled <code style="color:var(--cyan)">.bin</code> file.
      The ESP32 downloads and flashes it on its next poll (~2s).
    </div>
    <div class="ota-row">
      <input class="ota-input" id="ota_url" placeholder="https://github.com/you/repo/releases/download/v1/firmware.bin">
      <button class="ota-btn" onclick="ota()">&#9889; Flash</button>
    </div>
  </div>

  <!-- live log -->
  <div class="card">
    <div class="card-title"><span class="icon">&#128203;</span> Live Activity Log</div>
    <div class="log-box" id="logbox"><div class="empty-log">Waiting for activity...</div></div>
  </div>

  <!-- API test panel -->
  <div class="card">
    <div class="card-title"><span class="icon">&#128295;</span> API Test Panel</div>
    <div style="color:var(--mut);font-size:13px;margin-bottom:8px">
      Test any endpoint right here. Responses appear below each call.
    </div>
    <div class="test-row">
      <button class="test-btn" onclick="testApi('GET','/api/status')">GET /api/status</button>
      <button class="test-btn" onclick="testApi('POST','/api/blink')">POST /api/blink</button>
    </div>
    <div class="test-row">
      <input class="test-input" id="test_emo" placeholder="emotion name" value="happy">
      <button class="test-btn" onclick="testEmo()">POST /api/emotion</button>
    </div>
    <div class="test-result" id="test_out"></div>
  </div>

  <!-- API reference -->
  <div class="card">
    <div class="card-title"><span class="icon">&#128218;</span> API Reference</div>
    <div class="api-row"><span class="method post">POST</span><span class="api-path">/api/emotion</span><span class="api-note">{"emotion":"happy"}</span></div>
    <div class="api-row"><span class="method post">POST</span><span class="api-path">/api/blink</span><span class="api-note">(no body)</span></div>
    <div class="api-row"><span class="method post">POST</span><span class="api-path">/api/ota</span><span class="api-note">{"url":"https://..."}</span></div>
    <div class="api-row"><span class="method get">GET</span><span class="api-path">/api/status</span><span class="api-note">device info</span></div>
    <div class="api-row"><span class="method get">GET</span><span class="api-path">/api/logs</span><span class="api-note">activity log</span></div>
    <div style="color:var(--mut);font-size:12px;margin-top:10px">
      All POST endpoints need <code style="color:var(--cyan)">?key=YOUR_KEY</code> or header <code style="color:var(--cyan)">X-API-Key</code>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const KEY='""" + API_KEY + r"""';
const $=id=>document.getElementById(id);
const H={'Content-Type':'application/json','X-API-Key':KEY};

function toast(t){let e=$('toast');e.textContent=t;e.classList.add('show');
  setTimeout(()=>e.classList.remove('show'),2200);}

async function api(method,path,body){
  let o={method,headers:H};
  if(body)o.body=JSON.stringify(body);
  return await(await fetch(path+'?key='+KEY,o)).json();
}
async function em(n){await api('POST','/api/emotion',{emotion:n});toast('→ '+n);}
async function bl(){await api('POST','/api/blink');toast('→ blink');}
async function ota(){
  let u=$('ota_url').value.trim();
  if(!u){alert('Paste a .bin URL first');return;}
  if(!confirm('Flash firmware from:\n'+u+'\n\nDevice will reboot.')){return;}
  await api('POST','/api/ota',{url:u});toast('OTA queued');
}

/* status polling */
async function poll(){try{
  let j=await(await fetch('/api/status')).json();
  let on=j.online;
  let el=$('s_on');el.textContent=on?'Online':'Offline';el.className='val '+(on?'online':'offline');
  $('s_ip').textContent=j.ip;
  $('s_emo').textContent=j.emotion;
  $('s_fw').textContent=j.firmware;
  $('s_hp').textContent=((j.heap/1024)|0)+' KB';
  let s=(j.uptime/1000)|0;$('s_up').textContent=((s/3600)|0)+'h '+((s%3600/60)|0)+'m '+(s%60)+'s';
  /* highlight active button */
  document.querySelectorAll('.emo-btn').forEach(b=>{
    let n=b.textContent.replace(/[^\w\s]/g,'').trim().toLowerCase().replace(/\s+/g,'_');
    b.classList.toggle('active',n===j.emotion);
  });
}catch(x){}}

/* log polling */
async function pollLog(){try{
  let j=await(await fetch('/api/logs')).json();
  let box=$('logbox');
  if(!j.logs.length){box.innerHTML='<div class="empty-log">Waiting for activity...</div>';return;}
  box.innerHTML=j.logs.map(e=>`<div class="log-entry">
    <span class="log-time">${e.time}</span>
    <span class="log-src ${e.source==='ESP32'?'esp':'usr'}">${e.source}</span>
    <span class="log-action">${e.action}</span>
    ${e.detail?`<span class="log-detail">${e.detail}</span>`:''}
  </div>`).join('');
}catch(x){}}

/* API test panel */
async function testApi(method,path){
  let out=$('test_out');out.style.display='block';
  out.textContent='Loading...';
  try{
    let o={method,headers:H};
    let r=await fetch(path+'?key='+KEY,o);
    let j=await r.json();
    out.textContent=method+' '+path+'\nHTTP '+r.status+'\n\n'+JSON.stringify(j,null,2);
  }catch(e){out.textContent='Error: '+e;}
}
async function testEmo(){
  let name=$('test_emo').value.trim();
  let out=$('test_out');out.style.display='block';
  out.textContent='Loading...';
  try{
    let r=await fetch('/api/emotion?key='+KEY,{method:'POST',headers:H,body:JSON.stringify({emotion:name})});
    let j=await r.json();
    out.textContent='POST /api/emotion\nBody: {"emotion":"'+name+'"}\nHTTP '+r.status+'\n\n'+JSON.stringify(j,null,2);
  }catch(e){out.textContent='Error: '+e;}
}

setInterval(poll,2000);poll();
setInterval(pollLog,3000);pollLog();
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE
