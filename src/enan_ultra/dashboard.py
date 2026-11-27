"""Loopback-only, authenticated admin command center."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from enan_ultra.config import Settings
from enan_ultra.embedded_server import build_embedded_server
from enan_ultra.services.health import health_snapshot

logger = logging.getLogger(__name__)
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Enan Ultra · Mission Control</title>
<style>
:root{color-scheme:dark;--bg:#07111f;--panel:#101f32;--edge:#23415b;--cyan:#52e4df;--violet:#ab8bff;--muted:#8da8c1;--text:#edf7ff;--green:#70e4a4;--amber:#ffd17c}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% -20%,#253d72 0,transparent 45%),var(--bg);font:15px/1.5 system-ui,Segoe UI,sans-serif;color:var(--text);min-height:100vh}
main{max-width:1240px;margin:auto;padding:28px 22px 64px}.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:26px}.brand{display:flex;gap:14px;align-items:center}.orb{width:52px;height:52px;border-radius:50%;border:1px solid #7dddf1;box-shadow:0 0 25px #4bcede66,inset 0 0 18px #7e6dff55;display:grid;place-items:center;color:var(--cyan);font-size:24px;animation:float 4s ease-in-out infinite}@keyframes float{50%{transform:translateY(-4px)}}h1{font-size:22px;margin:0;letter-spacing:.03em}.sub{color:var(--muted);font-size:13px}.pill{padding:6px 12px;border:1px solid var(--edge);border-radius:99px;color:var(--cyan);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card{background:linear-gradient(150deg,#11233aee,#0b1828ee);border:1px solid var(--edge);border-radius:16px;padding:18px;box-shadow:0 10px 30px #0003}.card h2{font-size:14px;text-transform:uppercase;letter-spacing:.11em;color:var(--muted);margin:0 0 12px}.metric{font-size:27px;font-weight:650}.small{font-size:12px;color:var(--muted)}.wide{grid-column:span 2}.full{grid-column:1/-1}.rows{display:grid;gap:9px}.row{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-bottom:1px solid #20364c}.row:last-child{border:0}.status{color:var(--green)}.down{color:#ff9c9c}.list{max-height:360px;overflow:auto}.empty{color:var(--muted);padding:8px 0}.button{border:1px solid #3c6684;background:#17334b;color:var(--text);padding:8px 12px;border-radius:9px;cursor:pointer}.button:hover{border-color:var(--cyan)}.button.danger{border-color:#9c5d68;color:#ffb3bc}.login{max-width:420px;margin:13vh auto}.login input{width:100%;padding:12px;border-radius:8px;border:1px solid var(--edge);background:#07111f;color:var(--text);margin:12px 0}.login form{display:grid}.hide{display:none}.voice{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.voice output{color:var(--muted)}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.wide{grid-column:span 2}}
@media(max-width:520px){main{padding:18px 12px}.grid{grid-template-columns:1fr}.wide,.full{grid-column:span 1}.top{align-items:flex-start}}
</style></head><body><main>
<section id="login" class="card login"><div class="brand"><div class="orb">✦</div><div><h1>Enan Ultra</h1><div class="sub">Private Mission Control</div></div></div><form id="login-form"><label for="secret">Dashboard access key</label><input id="secret" type="password" autocomplete="current-password" required minlength="32"><button class="button">Unlock command center</button><output id="login-error" class="small"></output></form></section>
<section id="app" class="hide"><header class="top"><div class="brand"><div class="orb">✦</div><div><h1>Enan Ultra</h1><div class="sub">Mission Control · loopback admin session</div></div></div><div><span class="pill" id="live">SYNCING</span> <button class="button" id="logout">Lock</button></div></header>
<div class="grid"><article class="card"><h2>Service</h2><div class="metric" id="service">—</div><div class="small" id="uptime">Waiting for telemetry</div></article><article class="card"><h2>Requests today</h2><div class="metric" id="requests">—</div><div class="small">Across all chats</div></article><article class="card"><h2>Reserved / reported cost</h2><div class="metric" id="cost">—</div><div class="small">USD today</div></article><article class="card"><h2>Review queue</h2><div class="metric" id="queue">—</div><div class="small" id="agents">Running agent jobs</div></article>
<article class="card wide"><h2>Provider constellation</h2><div class="rows list" id="providers"></div></article><article class="card wide"><h2>Feedback signal &amp; improvement proposals</h2><div class="metric"><span id="positive">0</span> <span class="small">positive</span> · <span id="negative">0</span> <span class="small">needs review</span></div><div class="rows list" id="feedback"></div><div class="small">Repeated low ratings suggest categories for manual review; prompts/models are never changed automatically.</div></article>
<article class="card wide"><h2>Pending approvals</h2><div class="rows list" id="approvals"></div></article><article class="card wide"><h2>Recent specialist jobs</h2><div class="rows list" id="jobs"></div></article>
<article class="card full"><h2>Active users · conversation memory</h2><div class="rows list" id="users"></div><div class="small">Only private-chat user IDs and activity counts are shown; group history is not listed or cleared.</div></article>
<article class="card wide"><h2>Service controls</h2><div class="rows" id="vm"></div><pre id="vm-logs" class="small"></pre><button class="button danger" id="restart">Request graceful restart</button><div class="small">Restart requires a separate approval. No shell access.</div></article><article class="card wide"><h2>Home Assistant</h2><div class="rows list" id="ha"></div><div class="small">Only configured entity IDs are shown. State-changing calls require admin approval.</div></article>
<article class="card full"><h2>Push-to-talk · transcription only</h2><div class="voice"><button class="button" id="record">Start recording</button><button class="button" id="transcribe" disabled>Transcribe</button><output id="voice-state">Microphone access starts only after you press record. Audio is sent to the configured transcription provider and not stored.</output></div><pre id="transcript" class="small"></pre></article>
<article class="card full"><h2>Recent audit events</h2><div class="rows list" id="audit"></div></article></div></section></main>
<script>
const $=id=>document.getElementById(id), login=$('login'), app=$('app');
async function api(path,options={}){const r=await fetch(path,{credentials:'same-origin',...options});if(r.status===401){showLogin();throw Error('session expired')}if(!r.ok)throw Error('request failed');return r.json()}
function showLogin(){login.classList.remove('hide');app.classList.add('hide')}
function item(container,left,right,kind=''){const row=document.createElement('div');row.className='row';const a=document.createElement('span');a.textContent=left;const b=document.createElement('span');b.textContent=right;b.className=kind;row.append(a,b);container.append(row)}
async function refresh(){try{const [s,p,u,j,a,v,h,au,f,users]=await Promise.all(['/api/v1/status','/api/v1/providers','/api/v1/usage','/api/v1/jobs','/api/v1/approvals','/api/v1/vm','/api/v1/home-assistant','/api/v1/audit','/api/v1/feedback','/api/v1/users'].map(x=>api(x)));app.classList.remove('hide');login.classList.add('hide');$('live').textContent='LIVE';$('service').textContent=s.state;const r=s.resources||{};$('uptime').textContent='Uptime '+s.uptime_seconds+' seconds · RAM '+(r.memory_used_percent===null?'n/a':r.memory_used_percent+'%')+' · Disk '+r.disk_used_percent+'% used · '+Math.round(r.disk_free_bytes/1073741824)+' GB free';$('requests').textContent=u.requests_today;$('cost').textContent='$'+Number(u.cost_today_usd).toFixed(4);$('queue').textContent=u.pending_approvals;$('agents').textContent=u.running_agents+' running agent jobs';$('positive').textContent=u.feedback_positive_today;$('negative').textContent=u.feedback_negative_today;
const providers=$('providers');providers.replaceChildren();for(const x of p){item(providers,x.provider+' · '+(x.model||'model not configured'),x.status==='healthy'?'ONLINE':x.status.toUpperCase(),x.status==='healthy'?'status':'down')}
const approvals=$('approvals');approvals.replaceChildren();for(const x of a){const row=document.createElement('div');row.className='row';const label=document.createElement('span');label.textContent='#'+x.id+' · '+x.kind+' · '+x.summary+' · expires '+x.expires_at.slice(11,16)+' UTC';const controls=document.createElement('span');if(x.required_approver==='admin'){for(const [verb,style] of [['approve',''],['deny',' danger']]){const b=document.createElement('button');b.className='button'+style;b.textContent=verb==='approve'?'Approve':'Deny';b.onclick=async()=>{try{await api('/api/v1/approvals/'+x.id+'/'+verb,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});refresh()}catch(e){$('live').textContent='ACTION FAILED'}};controls.append(b)}}else controls.textContent='Requester confirmation required';row.append(label,controls);approvals.append(row)}if(!a.length)approvals.innerHTML='<div class="empty">No pending approvals.</div>';
const jobs=$('jobs');jobs.replaceChildren();for(const x of j){item(jobs,'#'+x.id+' · '+x.task_type+' · '+x.status,x.step_count+' steps')}if(!j.length)jobs.innerHTML='<div class="empty">No agent jobs yet.</div>';
const usersList=$('users');usersList.replaceChildren();for(const x of users){const row=document.createElement('div');row.className='row';const label=document.createElement('span');label.textContent='User '+x.user_id+' · '+x.message_count+' messages · '+(x.last_active||'memory only');const clear=document.createElement('button');clear.className='button danger';clear.textContent='Clear Memory';clear.onclick=async()=>{if(!confirm('Clear private conversation history and long-term memory for user '+x.user_id+'?'))return;try{await api('/api/v1/users/'+x.user_id+'/clear-memory',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});refresh()}catch(e){$('live').textContent='CLEAR FAILED'}};row.append(label,clear);usersList.append(row)}if(!users.length)usersList.innerHTML='<div class="empty">No private chat users found.</div>';
const vm=$('vm');vm.replaceChildren();item(vm,v.service,v.state,v.state==='active'?'status':'down');$('vm-logs').textContent=(v.logs||[]).join('\n')||'No sanitized app logs yet.';
const feedback=$('feedback');feedback.replaceChildren();for(const x of f)item(feedback,x.category,x.negative_count+' ratings · '+x.suggestion);if(!f.length)feedback.innerHTML='<div class="empty">No repeated negative category yet.</div>';
const ha=$('ha');ha.replaceChildren();for(const x of h){item(ha,x.entity_id,x.state)}if(!h.length)ha.innerHTML='<div class="empty">No configured/available allowlisted entities.</div>';
const audit=$('audit');audit.replaceChildren();for(const x of au)item(audit,x.action+' · '+x.target,x.outcome+' · '+x.created_at.slice(0,16));if(!au.length)audit.innerHTML='<div class="empty">No audit events.</div>';
startEvents();
}catch(e){$('live').textContent='OFFLINE'}}
function startEvents(){if(window.enanEvents)return;window.enanEvents=new EventSource('/api/v1/events');window.enanEvents.addEventListener('telemetry',e=>{try{const d=JSON.parse(e.data),u=d.metrics;$('requests').textContent=u.requests_today;$('cost').textContent='$'+Number(u.cost_today_usd).toFixed(4);$('queue').textContent=u.pending_approvals;$('agents').textContent=u.running_agents+' running agent jobs';$('positive').textContent=u.feedback_positive_today;$('negative').textContent=u.feedback_negative_today;const p=$('providers');p.replaceChildren();for(const x of d.providers)item(p,x.provider+' · '+(x.model||'model not configured'),x.status==='healthy'?'ONLINE':x.status.toUpperCase(),x.status==='healthy'?'status':'down');$('live').textContent='LIVE'}catch(e){}});window.enanEvents.onerror=()=>{$('live').textContent='RECONNECTING'}}
$('login-form').addEventListener('submit',async e=>{e.preventDefault();const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:$('secret').value})});if(r.ok){$('secret').value='';refresh()}else $('login-error').textContent='Access key was rejected.'});
$('logout').onclick=async()=>{await fetch('/auth/logout',{method:'POST'});if(window.enanEvents){window.enanEvents.close();window.enanEvents=null}showLogin()};
$('restart').onclick=async()=>{if(!confirm('Create an approval request for a graceful bot restart?'))return;try{const r=await api('/api/v1/vm/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});$('live').textContent='RESTART APPROVAL #'+r.approval_id;refresh()}catch(e){$('live').textContent='REQUEST FAILED'}};
let recorder,recorded=[];$('record').onclick=async()=>{try{if(!recorder||recorder.state==='inactive'){const stream=await navigator.mediaDevices.getUserMedia({audio:true});recorder=new MediaRecorder(stream);recorded=[];recorder.ondataavailable=e=>{if(e.data.size)recorded.push(e.data)};recorder.onstop=()=>{stream.getTracks().forEach(t=>t.stop());$('transcribe').disabled=!recorded.length};recorder.start();$('record').textContent='Stop recording';$('voice-state').textContent='Recording locally in this page…'}else{recorder.stop();$('record').textContent='Start recording';$('voice-state').textContent='Recording stopped; press Transcribe to send it for speech recognition.'}}catch(e){$('voice-state').textContent='Microphone permission or browser recording is unavailable.'}};
$('transcribe').onclick=async()=>{const blob=new Blob(recorded,{type:recorded[0]?.type||'audio/webm'});if(blob.size>8*1024*1024){$('voice-state').textContent='Recording exceeds 8 MB.';return}try{const r=await fetch('/api/v1/voice/transcribe',{method:'POST',headers:{'Content-Type':blob.type},body:blob});if(!r.ok)throw Error();const data=await r.json();$('transcript').textContent=data.text;$('voice-state').textContent='Transcription completed. Nothing was executed or saved.'}catch(e){$('voice-state').textContent='Transcription failed or voice input is disabled.'}};
refresh();setInterval(refresh,8000);
</script></body></html>"""


class AdminDashboard:
    def __init__(
        self, settings: Settings, db: Any, router: Any, actions: Any, vm_control: Any,
    ) -> None:
        self.settings, self.db, self.router, self.actions = settings, db, router, actions
        self.vm_control = vm_control
        self.started_at = time.monotonic()
        self.login_attempts: dict[str, deque[float]] = {}
        self.app = self._build_app()
        self.server = build_embedded_server(
            self.app, host=settings.dashboard_host, port=settings.dashboard_port,
        )

    def _session(self, expires: int) -> str:
        message = f"admin:{expires}".encode()
        signature = hmac.new(self.settings.dashboard_auth_token.encode(), message, hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def _authorized(self, request: Request) -> bool:
        cookie = request.cookies.get("enan_admin", "")
        try:
            expires_raw, signature = cookie.split(".", 1)
            expires = int(expires_raw)
        except (ValueError, TypeError):
            return False
        return expires > int(time.time()) and hmac.compare_digest(signature, self._session(expires).split(".", 1)[1])

    async def _require_admin(self, request: Request) -> None:
        if not self._authorized(request):
            raise HTTPException(status_code=401, detail="Authentication required")

    async def _same_origin(self, request: Request) -> None:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        if not origin:
            raise HTTPException(status_code=403, detail="Origin header required")
        from urllib.parse import urlsplit
        parsed = urlsplit(origin)
        if parsed.netloc != host or parsed.scheme != request.url.scheme:
            raise HTTPException(status_code=403, detail="Cross-origin request rejected")

    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def harden(request: Request, call_next: Callable[..., Any]):
            host = request.url.hostname
            if host not in {"127.0.0.1", "localhost", "::1"}:
                return JSONResponse({"detail": "Loopback access only"}, status_code=403)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:"
            return response

        @app.post("/auth/login")
        async def login(request: Request):
            await self._same_origin(request)
            peer = request.client.host if request.client else "unknown"
            now = time.monotonic()
            attempts = self.login_attempts.setdefault(peer, deque())
            while attempts and now - attempts[0] > 300:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(status_code=429, detail="Too many login attempts")
            payload = await request.json()
            supplied = str(payload.get("token", ""))[:500]
            if not hmac.compare_digest(supplied, self.settings.dashboard_auth_token):
                attempts.append(now)
                raise HTTPException(status_code=401, detail="Invalid access key")
            attempts.clear()
            expires = int(time.time()) + 12 * 3600
            response = JSONResponse({"ok": True})
            response.set_cookie("enan_admin", self._session(expires), max_age=12 * 3600,
                                httponly=True, secure=False, samesite="strict", path="/")
            return response

        @app.post("/auth/logout")
        async def logout(request: Request):
            await self._same_origin(request)
            response = JSONResponse({"ok": True})
            response.delete_cookie("enan_admin", path="/")
            return response

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return HTMLResponse(_PAGE)

        @app.get("/api/v1/status")
        async def status(request: Request):
            await self._require_admin(request)
            return {"state": "active", "uptime_seconds": int(time.monotonic() - self.started_at),
                    "dashboard": "loopback", "active_live_sessions": await self.db.active_live_count(),
                    "resources": health_snapshot(self.settings.database_path)["resources"]}

        @app.get("/api/v1/health")
        async def health(request: Request):
            await self._require_admin(request)
            return health_snapshot(self.settings.database_path)

        @app.get("/api/v1/providers")
        async def providers(request: Request):
            await self._require_admin(request)
            return await self.router.provider_status()

        @app.get("/api/v1/usage")
        async def usage(request: Request):
            await self._require_admin(request)
            return await self.db.dashboard_metrics()

        @app.get("/api/v1/users")
        async def users(request: Request):
            await self._require_admin(request)
            return await self.db.active_users()

        @app.post("/api/v1/users/{user_id}/clear-memory")
        async def clear_user_memory(user_id: int, request: Request):
            await self._require_admin(request)
            await self._same_origin(request)
            if user_id <= 0:
                raise HTTPException(status_code=400, detail="A positive private-chat user ID is required")
            await self.db.clear_user_conversation(user_id)
            admin_id = min(self.settings.effective_admin_user_ids)
            await self.db.audit(
                admin_id, "memory.clear", str(user_id),
                "private history and long-term memory cleared",
            )
            return {"ok": True}

        @app.get("/api/v1/feedback")
        async def feedback(request: Request):
            await self._require_admin(request)
            return await self.db.feedback_suggestions()

        @app.get("/api/v1/jobs")
        async def jobs(request: Request):
            await self._require_admin(request)
            return await self.db.recent_agent_jobs(60)

        @app.get("/api/v1/approvals")
        async def approvals(request: Request):
            await self._require_admin(request)
            return await self.db.pending_approvals(100)

        @app.post("/api/v1/approvals/{approval_id}/approve")
        async def approve(approval_id: int, request: Request):
            await self._require_admin(request)
            await self._same_origin(request)
            admin_id = min(self.settings.effective_admin_user_ids)
            result = await self.actions.approve(approval_id, admin_id, _ExecutionContext(self.settings, self.db))
            if not result.ok:
                raise HTTPException(status_code=409, detail=result.message)
            return {"ok": True, "message": result.message}

        @app.post("/api/v1/approvals/{approval_id}/deny")
        async def deny(approval_id: int, request: Request):
            await self._require_admin(request)
            await self._same_origin(request)
            admin_id = min(self.settings.effective_admin_user_ids)
            result = await self.actions.deny(approval_id, admin_id, _ExecutionContext(self.settings, self.db))
            if not result.ok:
                raise HTTPException(status_code=409, detail=result.message)
            return {"ok": True}

        @app.get("/api/v1/vm")
        async def vm(request: Request):
            await self._require_admin(request)
            return {**await self.vm_control.status(), "logs": await self.vm_control.sanitized_logs(20)}

        @app.post("/api/v1/vm/restart")
        async def request_vm_restart(request: Request):
            await self._require_admin(request)
            await self._same_origin(request)
            admin_id = min(self.settings.effective_admin_user_ids)
            approval_id = await self.db.create_approval(
                admin_id, "vm_restart", "Restart the Enan Ultra Telegram service", {}, "admin",
            )
            return {"ok": True, "approval_id": approval_id}

        @app.get("/api/v1/home-assistant")
        async def home_assistant(request: Request):
            await self._require_admin(request)
            if not self.actions.home_assistant:
                return []
            try:
                return await self.actions.home_assistant.states()
            except Exception as exc:
                logger.info("Home Assistant dashboard request unavailable (%s)", type(exc).__name__)
                return []

        @app.post("/api/v1/voice/transcribe")
        async def transcribe(request: Request):
            await self._require_admin(request)
            await self._same_origin(request)
            if not self.settings.voice_input_enabled:
                raise HTTPException(status_code=404, detail="Voice input is disabled")
            declared = int(request.headers.get("content-length", "0") or 0)
            if declared > min(self.settings.max_upload_bytes, 8 * 1024 * 1024):
                raise HTTPException(status_code=413, detail="Voice clip exceeds upload limit")
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > min(self.settings.max_upload_bytes, 8 * 1024 * 1024):
                    raise HTTPException(status_code=413, detail="Voice clip exceeds upload limit")
            if not body:
                raise HTTPException(status_code=400, detail="Empty voice clip")
            admin_id = min(self.settings.effective_admin_user_ids)
            estimate = await self.router.estimate_transcription_cost(len(body), max_tokens=1000)
            event_id, limit = await self.db.reserve_usage(
                admin_id, "dashboard-voice", estimate, self.settings.user_daily_request_limit,
                self.settings.max_daily_cost_usd,
                self.settings.user_monthly_request_limit, self.settings.user_monthly_cost_limit_usd,
            )
            if event_id is None:
                raise HTTPException(status_code=429, detail=f"{limit or 'usage'} limit reached")
            try:
                text = await self.router.transcribe(bytes(body), "dashboard-voice.webm", request.headers.get("content-type", "audio/webm"))
                await self.db.finish_usage(event_id, "dashboard-voice", {})
            except Exception as exc:
                logger.info("Dashboard voice transcription failed (%s)", type(exc).__name__)
                raise HTTPException(status_code=503, detail="Transcription provider unavailable") from exc
            return {"text": text[:4000]}

        @app.get("/api/v1/audit")
        async def audit(request: Request):
            await self._require_admin(request)
            return await self.db.recent_audit_events(100)

        @app.get("/api/v1/events")
        async def events(request: Request):
            await self._require_admin(request)

            async def generate():
                while self._authorized(request) and not await request.is_disconnected():
                    payload = {"metrics": await self.db.dashboard_metrics(),
                               "providers": await self.router.provider_status()}
                    yield "event: telemetry\ndata: " + json.dumps(payload, separators=(",", ":")) + "\n\n"
                    await asyncio.sleep(8)

            return StreamingResponse(generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

        return app


class _ExecutionContext:
    def __init__(self, settings: Settings, db: Any) -> None:
        self.settings = settings
        self.db = db
