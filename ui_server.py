import json
import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(title="RapidX AI Dashboard")

CONFIG_FILE = "config.json"

def read_config():
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

    def get_val(key, env_key, default=""):
        return config.get(key) if config.get(key) else os.getenv(env_key, default)

    return {
        "first_line": get_val("first_line", "FIRST_LINE", "Namaste! This is Aryan from RapidX AI — we help businesses automate with AI. Hmm, may I ask what kind of business you run?"),
        "agent_instructions": get_val("agent_instructions", "AGENT_INSTRUCTIONS", ""),
        "stt_min_endpointing_delay": float(get_val("stt_min_endpointing_delay", "STT_MIN_ENDPOINTING_DELAY", 0.6)),
        "llm_model": get_val("llm_model", "LLM_MODEL", "gpt-4o-mini"),
        "tts_voice": get_val("tts_voice", "TTS_VOICE", "kavya"),
        "tts_language": get_val("tts_language", "TTS_LANGUAGE", "hi-IN"),
        "livekit_url": get_val("livekit_url", "LIVEKIT_URL", ""),
        "sip_trunk_id": get_val("sip_trunk_id", "SIP_TRUNK_ID", ""),
        "livekit_api_key": get_val("livekit_api_key", "LIVEKIT_API_KEY", ""),
        "livekit_api_secret": get_val("livekit_api_secret", "LIVEKIT_API_SECRET", ""),
        "openai_api_key": get_val("openai_api_key", "OPENAI_API_KEY", ""),
        "sarvam_api_key": get_val("sarvam_api_key", "SARVAM_API_KEY", ""),
        "cal_api_key": get_val("cal_api_key", "CAL_API_KEY", ""),
        "cal_event_type_id": get_val("cal_event_type_id", "CAL_EVENT_TYPE_ID", ""),
        "telegram_bot_token": get_val("telegram_bot_token", "TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": get_val("telegram_chat_id", "TELEGRAM_CHAT_ID", ""),
        "supabase_url": get_val("supabase_url", "SUPABASE_URL", ""),
        "supabase_key": get_val("supabase_key", "SUPABASE_KEY", ""),
        **config
    }

def write_config(data):
    config = read_config()
    config.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/config")
async def api_get_config():
    return read_config()

@app.post("/api/config")
async def api_post_config(request: Request):
    data = await request.json()
    write_config(data)
    logger.info("Configuration updated via UI.")
    return {"status": "success"}

@app.get("/api/logs")
async def api_get_logs():
    config = read_config()
    os.environ["SUPABASE_URL"] = config.get("supabase_url", "")
    os.environ["SUPABASE_KEY"] = config.get("supabase_key", "")
    import db
    try:
        logs = db.fetch_call_logs(limit=50)
        return logs
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return []

@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    config = read_config()
    os.environ["SUPABASE_URL"] = config.get("supabase_url", "")
    os.environ["SUPABASE_KEY"] = config.get("supabase_key", "")
    try:
        from supabase import create_client
        supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        res = supabase.table("call_logs").select("*").eq("id", log_id).single().execute()
        data = res.data
        text = f"Call Log — {data.get('created_at', '')}\n"
        text += f"Phone: {data.get('phone_number', 'Unknown')}\n"
        text += f"Duration: {data.get('duration_seconds', 0)}s\n"
        text += f"Summary: {data.get('summary', '')}\n\n"
        text += "--- TRANSCRIPT ---\n"
        text += data.get("transcript", "No transcript available.")
        return PlainTextResponse(content=text, media_type="text/plain",
                                 headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"})
    except Exception as e:
        return PlainTextResponse(content=f"Error: {e}", status_code=500)

@app.get("/api/bookings")
async def api_get_bookings():
    config = read_config()
    os.environ["SUPABASE_URL"] = config.get("supabase_url", "")
    os.environ["SUPABASE_KEY"] = config.get("supabase_key", "")
    import db
    try:
        return db.fetch_bookings()
    except Exception as e:
        logger.error(f"Error fetching bookings: {e}")
        return []

@app.get("/api/stats")
async def api_get_stats():
    config = read_config()
    os.environ["SUPABASE_URL"] = config.get("supabase_url", "")
    os.environ["SUPABASE_KEY"] = config.get("supabase_key", "")
    import db
    try:
        return db.fetch_stats()
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}

@app.get("/api/contacts")
async def api_get_contacts():
    config = read_config()
    os.environ["SUPABASE_URL"] = config.get("supabase_url", "")
    os.environ["SUPABASE_KEY"] = config.get("supabase_key", "")
    try:
        from supabase import create_client
        supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        res = supabase.table("call_logs") \
            .select("phone_number, caller_name, summary, created_at") \
            .order("created_at", desc=True) \
            .limit(500) \
            .execute()
        rows = res.data or []

        contacts = {}
        for r in rows:
            phone = r.get("phone_number") or "unknown"
            if phone not in contacts:
                contacts[phone] = {
                    "phone_number": phone,
                    "caller_name": r.get("caller_name") or "",
                    "total_calls": 0,
                    "last_seen": r.get("created_at"),
                    "is_booked": False,
                }
            c = contacts[phone]
            c["total_calls"] += 1
            if not c["caller_name"] and r.get("caller_name"):
                c["caller_name"] = r["caller_name"]
            if r.get("summary") and "Confirmed" in r.get("summary", ""):
                c["is_booked"] = True

        return sorted(contacts.values(), key=lambda x: x["last_seen"] or "", reverse=True)
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")
        return []

DEMO_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Voice Demo — RapidX AI</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Inter',sans-serif;background:#0f1117;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;flex-direction:column;gap:24px;padding:24px}
    .card{background:#1c2333;border:1px solid #2a3448;border-radius:20px;padding:40px;max-width:440px;width:100%;text-align:center;box-shadow:0 24px 60px rgba(0,0,0,0.4)}
    h1{font-size:22px;font-weight:700;margin-bottom:6px}
    .sub{color:#8892a4;font-size:13px;margin-bottom:28px}
    .avatar{width:80px;height:80px;border-radius:50%;background:linear-gradient(135deg,#6c63ff,#a855f7);display:flex;align-items:center;justify-content:center;font-size:36px;margin:0 auto 24px}
    .btn{width:100%;padding:14px;border-radius:12px;font-size:15px;font-weight:600;cursor:pointer;border:none;transition:all 0.2s}
    .btn-start{background:#6c63ff;color:#fff}
    .btn-start:hover{background:#5a52e0;box-shadow:0 0 24px rgba(108,99,255,0.4)}
    .btn-end{background:#ef4444;color:#fff;display:none}
    .btn-end:hover{background:#dc2626}
    #status{font-size:13px;color:#8892a4;margin-top:16px;min-height:20px}
    .pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:6px;animation:pulse 1.5s infinite}
    @keyframes pulse{0%,100%{box-shadow:0 0 4px #22c55e}50%{box-shadow:0 0 12px #22c55e}}
    .vol-bar{display:flex;gap:3px;align-items:flex-end;justify-content:center;height:32px;margin-top:12px;display:none}
    .vol-bar span{width:4px;background:#6c63ff;border-radius:2px;transition:height 0.1s}
  </style>
</head>
<body>
  <div class="card">
    <div class="avatar">🎙</div>
    <h1>Talk to Aryan</h1>
    <div class="sub">AI-powered multilingual consultant · RapidX AI</div>
    <button class="btn btn-start" id="startBtn" onclick="startCall()">📞 Start Demo Call</button>
    <button class="btn btn-end" id="endBtn" onclick="endCall()">📵 End Call</button>
    <div id="status">Click to start a live voice demo</div>
    <div class="vol-bar" id="volBar">
      <span id="b1" style="height:8px"></span><span id="b2" style="height:14px"></span>
      <span id="b3" style="height:20px"></span><span id="b4" style="height:14px"></span>
      <span id="b5" style="height:8px"></span>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js"></script>
  <script>
    let room;
    async function startCall() {
      document.getElementById('status').textContent = 'Connecting...';
      document.getElementById('startBtn').disabled = true;
      try {
        const res = await fetch('/api/demo-token').then(r => r.json());
        if (res.error) throw new Error(res.error);
        room = new LivekitClient.Room();
        await room.connect(res.url, res.token, {autoSubscribe: true});
        await room.localParticipant.setMicrophoneEnabled(true);
        document.getElementById('startBtn').style.display = 'none';
        document.getElementById('endBtn').style.display = 'block';
        document.getElementById('volBar').style.display = 'flex';
        setStatus('<span class="pulse"></span>Connected — speak now!');
        animateBars();
      } catch(e) {
        setStatus('❌ ' + e.message);
        document.getElementById('startBtn').disabled = false;
      }
    }
    async function endCall() {
      if (room) { await room.disconnect(); room = null; }
      document.getElementById('startBtn').style.display = 'block';
      document.getElementById('startBtn').disabled = false;
      document.getElementById('endBtn').style.display = 'none';
      document.getElementById('volBar').style.display = 'none';
      setStatus('Call ended. Click to start again.');
    }
    function setStatus(html) { document.getElementById('status').innerHTML = html; }
    function animateBars() {
      if (!room) return;
      ['b1','b2','b3','b4','b5'].forEach(id => {
        document.getElementById(id).style.height = (4 + Math.random()*24) + 'px';
      });
      setTimeout(animateBars, 150);
    }
  </script>
</body>
</html>"""

@app.post("/api/call/single")
async def api_call_single(request: Request):
    data = await request.json()
    phone = (data.get("phone") or "").strip()
    if not phone.startswith("+"):
        return {"status": "error", "message": "Phone number must start with + and country code"}
    config = read_config()
    try:
        import random, json as _json
        from livekit import api as lkapi
        lk = lkapi.LiveKitAPI(
            url=config.get("livekit_url") or os.environ.get("LIVEKIT_URL",""),
            api_key=config.get("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY",""),
            api_secret=config.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET",""),
        )
        room_name = f"call-{phone.replace('+','')}-{random.randint(1000,9999)}"
        dispatch = await lk.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room_name,
                metadata=_json.dumps({"phone_number": phone}),
            )
        )
        await lk.aclose()
        logger.info(f"Outbound call dispatched to {phone}: {dispatch.id}")
        return {"status": "ok", "dispatch_id": dispatch.id, "room": room_name, "phone": phone}
    except Exception as e:
        logger.error(f"Call dispatch error: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/call/bulk")
async def api_call_bulk(request: Request):
    import random, json as _json
    from livekit import api as lkapi
    data = await request.json()
    numbers = [n.strip() for n in (data.get("numbers") or "").splitlines() if n.strip()]
    results = []
    cfg = read_config()
    lk_url    = cfg.get("livekit_url")    or os.environ.get("LIVEKIT_URL","")
    lk_key    = cfg.get("livekit_api_key")    or os.environ.get("LIVEKIT_API_KEY","")
    lk_secret = cfg.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET","")
    for phone in numbers:
        if not phone.startswith("+"):
            results.append({"phone": phone, "status": "error", "message": "Must start with +"})
            continue
        try:
            lk = lkapi.LiveKitAPI(url=lk_url, api_key=lk_key, api_secret=lk_secret)
            room_name = f"call-{phone.replace('+','')}-{random.randint(1000,9999)}"
            dispatch = await lk.agent_dispatch.create_dispatch(
                lkapi.CreateAgentDispatchRequest(
                    agent_name="outbound-caller",
                    room=room_name,
                    metadata=_json.dumps({"phone_number": phone}),
                )
            )
            await lk.aclose()
            results.append({"phone": phone, "status": "ok", "dispatch_id": dispatch.id})
            logger.info(f"Bulk outbound dispatched to {phone}: {dispatch.id}")
        except Exception as e:
            results.append({"phone": phone, "status": "error", "message": str(e)})
    return {"results": results, "total": len(results)}

@app.get("/api/demo-token")
async def api_demo_token():
    config = read_config()
    try:
        from livekit.api import AccessToken, VideoGrants
        import time, random
        room_name = f"demo-{random.randint(10000,99999)}"
        api_key    = config.get("livekit_api_key") or os.environ.get("LIVEKIT_API_KEY","")
        api_secret = config.get("livekit_api_secret") or os.environ.get("LIVEKIT_API_SECRET","")
        livekit_url = config.get("livekit_url") or os.environ.get("LIVEKIT_URL","")

        token = AccessToken(api_key, api_secret) \
            .with_identity("demo-user") \
            .with_name("Demo Caller") \
            .with_grants(VideoGrants(room_join=True, room=room_name)) \
            .with_ttl(3600) \
            .to_jwt()

        import json as _json
        from livekit import api as lkapi
        lk = lkapi.LiveKitAPI(url=livekit_url, api_key=api_key, api_secret=api_secret)
        await lk.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(
                agent_name="outbound-caller",
                room=room_name,
                metadata=_json.dumps({"phone_number": "demo", "is_demo": True}),
            )
        )
        await lk.aclose()
        return {"token": token, "room": room_name, "url": livekit_url}
    except Exception as e:
        logger.error(f"Demo token error: {e}")
        return {"error": str(e)}

@app.get("/demo", response_class=HTMLResponse)
async def get_demo_page():
    return HTMLResponse(content=DEMO_PAGE_HTML)

try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
    from fastapi.responses import Response as _Resp

    def _get_or_create_metric(metric_class, name, description, **kwargs):
        try:
            return metric_class(name, description, **kwargs)
        except ValueError:
            return REGISTRY._names_to_collectors.get(name) or metric_class(name, description, **kwargs)

    _voice_calls_total   = _get_or_create_metric(Counter,   "voice_calls_total",          "Total calls handled by the agent")
    _voice_calls_booked  = _get_or_create_metric(Counter,   "voice_calls_booked_total",   "Calls that resulted in a booking")
    _voice_call_duration = _get_or_create_metric(Histogram, "voice_call_duration_seconds", "Call duration in seconds",
                                                 buckets=[10, 30, 60, 120, 300, 600, 1200])
    _voice_calls_active  = _get_or_create_metric(Gauge,      "voice_calls_active",          "Currently active calls")

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return _Resp(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/internal/record-call", include_in_schema=False)
    async def record_call_metric(request: Request):
        data = await request.json()
        _voice_calls_total.inc()
        if data.get("booked"):
            _voice_calls_booked.inc()
        if data.get("duration"):
            _voice_call_duration.observe(data["duration"])
        return {"ok": True}

    logger.info("[METRICS] Prometheus metrics enabled at /metrics")

except ImportError:
    logger.warning("[METRICS] prometheus_client not installed — /metrics disabled")

@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        "service": "rapidx-ai-voice-agent",
    }

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rendered_dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard components compiling... Please try again.</h1>", status_code=503)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=False)