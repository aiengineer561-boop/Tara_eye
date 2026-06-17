"""
Robot Eyes Cloud Relay  —  FastAPI on Render.com

Architecture:
    User (anywhere) → This server (public URL) ← ESP32 (polls every ~2s)

The ESP32 polls /api/device/poll for pending commands.
The user (or the web UI) pushes commands to /api/emotion, /api/blink, /api/ota.
This server is the meeting point.

Deploy:  push to GitHub → connect repo on render.com → auto-deploy.
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import time, os

app = FastAPI(title="Robot Eyes Cloud Relay")

# ---- simple shared secret so random people can't control your robot ----
API_KEY = os.getenv("EYES_API_KEY", "roboteyes2025")

# ---- in-memory state (fine for a single-device relay) ----
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

EMOTIONS = [
    "neutral", "neutral_static", "happy", "sad",
    "angry", "curious", "suspicious"
]

# ---- models ----
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

# ---- auth helper ----
def check_key(request: Request) -> bool:
    key = request.query_params.get("key") or request.headers.get("X-API-Key")
    return key == API_KEY

# ============================================================================
#  DEVICE-FACING ENDPOINTS  (the ESP32 calls these)
# ============================================================================

@app.get("/api/device/poll")
async def device_poll(request: Request):
    """ESP32 polls this every ~2s. Returns any pending commands, then clears them."""
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)

    global pending_emotion, pending_blink, pending_ota_url
    resp = {
        "emotion": pending_emotion,
        "blink": pending_blink,
        "ota_url": pending_ota_url,
    }
    # clear after read
    pending_emotion = None
    pending_blink = False
    pending_ota_url = None
    return resp

@app.post("/api/device/heartbeat")
async def device_heartbeat(hb: Heartbeat, request: Request):
    """ESP32 reports its status every few seconds."""
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)

    device_status.update({
        "online": True,
        "ip": hb.ip,
        "emotion": hb.emotion,
        "uptime": hb.uptime,
        "heap": hb.heap,
        "firmware": hb.firmware,
        "last_seen": time.time(),
    })
    return {"ok": True}

# ============================================================================
#  USER-FACING API  (web UI / Python / curl call these)
# ============================================================================

@app.post("/api/emotion")
async def set_emotion(req: EmotionReq, request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_emotion
    pending_emotion = req.emotion
    return {"ok": True, "queued": req.emotion}

@app.post("/api/blink")
async def do_blink(request: Request):
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_blink
    pending_blink = True
    return {"ok": True}

@app.post("/api/ota")
async def queue_ota(req: OtaReq, request: Request):
    """Queue a firmware URL. ESP32 will download and flash it on next poll."""
    if not check_key(request):
        return JSONResponse({"error": "unauthorized"}, 401)
    global pending_ota_url
    pending_ota_url = req.url
    return {"ok": True, "ota_url": req.url}

@app.get("/api/status")
async def get_status():
    """Public status (no key needed — it's read-only device info)."""
    age = time.time() - device_status["last_seen"] if device_status["last_seen"] else 999
    return {**device_status, "online": age < 15}

# ============================================================================
#  WEB UI
# ============================================================================

PAGE = r"""<!DOCTYPE html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robot Eyes — Cloud Control</title><style>
:root{--bg:#0a0e14;--card:#121821;--cyan:#23d5e8;--red:#e85d5d;--grn:#3fb950;
      --txt:#e6edf3;--mut:#7d8590;--bdr:#1e2630}
*{box-sizing:border-box;font-family:system-ui,Segoe UI,Roboto,sans-serif}
body{margin:0;background:var(--bg);color:var(--txt);padding:16px;max-width:720px;margin:auto}
h1{font-size:22px;margin-bottom:4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--bdr);border-radius:14px;padding:16px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;margin-top:10px}
button{background:#1b2531;color:var(--txt);border:1px solid #2b3848;border-radius:10px;
       padding:12px;font-size:15px;cursor:pointer;transition:.15s}
button:hover{border-color:var(--cyan)}
button.active{border-color:var(--cyan);background:#162030;box-shadow:0 0 12px #23d5e820}
.go{background:var(--cyan);color:#04222a;border-color:var(--cyan);font-weight:600}
.go:hover{filter:brightness(1.1)}
.row{display:flex;justify-content:space-between;padding:5px 0;color:var(--mut)}
.row b{color:var(--txt)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.on{background:var(--grn)}.off{background:var(--red)}
input[type=text]{background:#0c1118;border:1px solid #2b3848;border-radius:8px;
                 padding:10px;color:var(--txt);width:100%;margin:8px 0;font-size:14px}
code{background:#0c1118;padding:2px 7px;border-radius:6px;color:var(--cyan);font-size:13px}
small{color:var(--mut)}
.toast{position:fixed;bottom:20px;right:20px;background:#1b2531;border:1px solid var(--cyan);
       border-radius:10px;padding:10px 18px;font-size:14px;opacity:0;transition:.3s;pointer-events:none}
.toast.show{opacity:1}
</style></head><body>
<h1>&#127758; Robot Eyes — Cloud Control</h1>
<div class="sub">Control your robot from anywhere in the world</div>

<div class="card">
  <div class="row"><span><span class="dot" id="dot"></span>Device status</span><b id="onl">—</b></div>
  <div class="row"><span>Local IP</span><b id="ip">—</b></div>
  <div class="row"><span>Current emotion</span><b id="emo">—</b></div>
  <div class="row"><span>Uptime</span><b id="upt">—</b></div>
  <div class="row"><span>Free heap</span><b id="heap">—</b></div>
  <div class="row"><span>Firmware</span><b id="fw">—</b></div>
  <div class="row"><span>Last seen</span><b id="seen">—</b></div>
</div>

<div class="card"><b>Expressions</b>
  <div class="grid" id="btns">
    <button onclick="e('neutral')">Neutral</button>
    <button onclick="e('neutral_static')">Neutral static</button>
    <button onclick="e('happy')">&#128522; Happy</button>
    <button onclick="e('sad')">&#128546; Sad</button>
    <button onclick="e('angry')">&#128544; Angry</button>
    <button onclick="e('curious')">&#129300; Curious</button>
    <button onclick="e('suspicious')">&#128528; Suspicious</button>
    <button onclick="bk()">&#128065; Blink</button>
  </div>
</div>

<div class="card"><b>Remote OTA Firmware Update</b><br>
  <small>Paste a public URL to a compiled <code>.bin</code> file
  (e.g. GitHub release, file host).
  The ESP32 will download and flash it on its next poll (~2s).</small>
  <input type="text" id="ota_url" placeholder="https://github.com/you/repo/releases/download/v1/eyes_ota.ino.bin">
  <button class="go" onclick="ota()">&#128640; Flash firmware</button>
</div>

<div class="card"><b>API reference</b>
  <div class="row"><code>POST /api/emotion</code><small>{"emotion":"happy"}</small></div>
  <div class="row"><code>POST /api/blink</code></div>
  <div class="row"><code>POST /api/ota</code><small>{"url":"https://..."}</small></div>
  <div class="row"><code>GET&nbsp; /api/status</code></div>
  <small>All POST endpoints require <code>?key=YOUR_KEY</code> or header <code>X-API-Key</code></small>
</div>

<div class="toast" id="toast"></div>

<script>
const KEY='"""+API_KEY+r"""';
const $=id=>document.getElementById(id);
function toast(t){let e=$('toast');e.textContent=t;e.classList.add('show');setTimeout(()=>e.classList.remove('show'),2000)}
async function api(method,path,body){
  let o={method,headers:{'Content-Type':'application/json','X-API-Key':KEY}};
  if(body)o.body=JSON.stringify(body);
  let r=await fetch(path+'?key='+KEY,o);return r.json();
}
async function e(n){await api('POST','/api/emotion',{emotion:n});toast(n);}
async function bk(){await api('POST','/api/blink');toast('blink');}
async function ota(){let u=$('ota_url').value.trim();
  if(!u){alert('Paste a .bin URL first');return;}
  if(!confirm('Flash firmware from:\n'+u+'\n\nThe device will reboot.')){return;}
  await api('POST','/api/ota',{url:u});toast('OTA queued');}
async function r(){try{
  let j=await(await fetch('/api/status')).json();
  let on=j.online;
  $('dot').className='dot '+(on?'on':'off');
  $('onl').textContent=on?'Online':'Offline';
  $('ip').textContent=j.ip;$('emo').textContent=j.emotion;
  $('heap').textContent=((j.heap/1024)|0)+' KB';
  $('fw').textContent=j.firmware;
  let s=(j.uptime/1000)|0;$('upt').textContent=((s/60)|0)+'m '+(s%60)+'s';
  if(j.last_seen){let a=Math.round(Date.now()/1000-j.last_seen);
    $('seen').textContent=a<5?'just now':a+'s ago';}
  /* highlight active emotion button */
  document.querySelectorAll('#btns button').forEach(b=>{
    let n=b.textContent.replace(/[^\w\s]/g,'').trim().toLowerCase().replace(/\s+/,'_');
    b.classList.toggle('active',n===j.emotion);
  });
}catch(x){}}
setInterval(r,2000);r();
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE

# ---- run with: uvicorn main:app --host 0.0.0.0 --port $PORT ----
