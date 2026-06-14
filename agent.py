import os
import json
import logging
import certifi
import pytz
import re
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from dotenv import load_dotenv
from typing import Annotated

# Fix for macOS SSL certificate verification
os.environ["SSL_CERT_FILE"] = certifi.where()

# ── Sentry error tracking ─────────────────────────────────────────────────────
import sentry_sdk
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        integrations=[AsyncioIntegration()],
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
load_dotenv()
logger = logging.getLogger("outbound-agent")
logging.basicConfig(level=logging.INFO)

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
from livekit.plugins import openai, sarvam, silero

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# ── Rate limiting ─────────────────────────────────────────────────────────────
_call_timestamps: dict = defaultdict(list)
RATE_LIMIT_CALLS  = 5
RATE_LIMIT_WINDOW = 3600  # 1 hour

def is_rate_limited(phone: str) -> bool:
    if phone in ("unknown", "demo"):
        return False
    now = time.time()
    _call_timestamps[phone] = [t for t in _call_timestamps[phone] if now - t < RATE_LIMIT_WINDOW]
    if len(_call_timestamps[phone]) >= RATE_LIMIT_CALLS:
        return True
    _call_timestamps[phone].append(now)
    return False

# ── Config Loader ─────────────────────────────────────────────────────────────
def get_live_config(phone_number: str | None = None):
    config = {}
    paths = []
    AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
    if phone_number and phone_number != "unknown":
        clean = phone_number.replace("+", "").replace(" ", "")
        paths.append(os.path.join(AGENT_DIR, "configs", f"{clean}.json"))
    paths += [os.path.join(AGENT_DIR, "configs", "default.json"), CONFIG_FILE]
    
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                logger.info(f"[CONFIG] Loaded: {path}")
                break
            except Exception as e:
                logger.error(f"[CONFIG] Failed to read {path}: {e}")
                
    if not config:
        logger.warning("[CONFIG] No config.json found — using environment variables only.")
        
    def env(key, default=""):
        return os.environ.get(key, default)
        
    return {
        "agent_instructions":        config.get("agent_instructions",
                                    env("AGENT_INSTRUCTIONS",
                                    "You are Aryan, a sharp multilingual AI sales consultant at RapidX AI. "
                                    "Book discovery calls and demos. Keep responses to 1-2 short sentences. "
                                    "Sound human, natural. Detect caller's language and respond in it.")),
        "first_line":                config.get("first_line",
                                    env("FIRST_LINE",
                                    "Namaste! This is Aryan from RapidX AI — we help businesses automate with AI. "
                                    "May I ask what kind of business you run?")),
        "stt_min_endpointing_delay": config.get("stt_min_endpointing_delay",
                                    float(env("STT_ENDPOINTING_DELAY", "0.35"))),
        "llm_model":                 config.get("llm_model",          env("LLM_MODEL", "llama-3.3-70b-versatile")),
        "llm_provider":              config.get("llm_provider",       env("LLM_PROVIDER", "groq")),
        "tts_voice":                 config.get("tts_voice",          env("TTS_VOICE", "alloy")),
        "tts_language":              config.get("tts_language",       env("TTS_LANGUAGE", "hi-IN")),
        "tts_provider":              config.get("tts_provider",       env("TTS_PROVIDER", "openai")),
        "stt_provider":              config.get("stt_provider",       env("STT_PROVIDER", "sarvam")),
        "stt_language":              config.get("stt_language",       env("STT_LANGUAGE", "unknown")),
        "lang_preset":               config.get("lang_preset",        env("LANG_PRESET", "multilingual")),
        "max_turns":                 config.get("max_turns",          int(env("MAX_TURNS", "25"))),
        "livekit_url":               config.get("livekit_url",        env("LIVEKIT_URL")),
        "livekit_api_key":           config.get("livekit_api_key",    env("LIVEKIT_API_KEY")),
        "livekit_api_secret":        config.get("livekit_api_secret", env("LIVEKIT_API_SECRET")),
        "openai_api_key":            config.get("openai_api_key",     env("OPENAI_API_KEY")),
        "groq_api_key":              config.get("groq_api_key",       env("GROQ_API_KEY")),
        "sarvam_api_key":            config.get("sarvam_api_key",     env("SARVAM_API_KEY")),
        "cal_api_key":               config.get("cal_api_key",        env("CAL_API_KEY")),
        "cal_event_type_id":         config.get("cal_event_type_id",  env("CAL_EVENT_TYPE_ID")),
        "telegram_bot_token":        config.get("telegram_bot_token", env("TELEGRAM_BOT_TOKEN")),
        "telegram_chat_id":          config.get("telegram_chat_id",   env("TELEGRAM_CHAT_ID")),
        "supabase_url":              config.get("supabase_url",       env("SUPABASE_URL")),
        "supabase_key":              config.get("supabase_key",       env("SUPABASE_KEY")),
        **config,
    }

def get_ist_time_context() -> str:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    today_str = now.strftime("%A, %B %d, %Y")
    time_str  = now.strftime("%I:%M %p")
    days_lines = []
    for i in range(7):
        day   = now + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime("%A"))
        days_lines.append(f"  {label}: {day.strftime('%A %d %B %Y')} → ISO {day.strftime('%Y-%m-%d')}")
    days_block = "\n".join(days_lines)
    return (
        f"\n\n[SYSTEM CONTEXT]\n"
        f"Current date & time: {today_str} at {time_str} IST\n"
        f"Resolve ALL relative day references using this table:\n{days_block}\n"
        f"Always use ISO dates when calling save_booking_intent. Appointments in IST (+05:30).]"
    )

from calendar_tools import get_available_slots, create_booking, cancel_booking
from notify import (
    notify_booking_confirmed,
    notify_booking_cancelled,
    notify_call_no_booking,
    notify_agent_error,
)

# ─── TOOL CONTEXT ─────────────────────────────────────────────────────────────
class AgentTools(llm.ToolContext):
    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone        = caller_phone
        self.caller_name         = caller_name
        self.booking_intent: dict | None = None
        self.sip_domain          = os.getenv("VOBIZ_SIP_DOMAIN")
        self.ctx_api              = None
        self.room_name           = None
        self._sip_identity       = None

    @llm.function_tool(description="Transfer this call to a human agent. Use if caller asks for a human.")
    async def transfer_call(self, reason: Annotated[str, "Brief reason for transfer"] = "requested") -> str:
        logger.info(f"[TOOL] transfer_call triggered — reason: {reason}")
        destination = os.getenv("DEFAULT_TRANSFER_NUMBER")
        if destination and self.sip_domain and "@" not in destination:
            clean_dest  = destination.replace("tel:", "").replace("sip:", "")
            destination = f"sip:{clean_dest}@{self.sip_domain}"
        if destination and not destination.startswith("sip:"):
            destination = f"sip:{destination}"
        try:
            if self.ctx_api and self.room_name and destination and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to=destination,
                        play_dialtone=False,
                    )
                )
                return "Transfer initiated successfully."
            return "Unable to transfer right now."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return "Unable to transfer right now."

    @llm.function_tool(description="End the call. Use when caller says goodbye or after confirming a booking.")
    async def end_call(self, reason: Annotated[str, "Brief reason for ending the call"] = "goodbye") -> str:
        logger.info(f"[TOOL] end_call triggered — reason: {reason}")
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to="tel:+00000000",
                        play_dialtone=False,
                    )
                )
        except Exception as e:
            logger.warning(f"[END-CALL] SIP hangup failed: {e}")
        return "Call ended."

    @llm.function_tool(description="Save booking details after the user confirms an appointment time.")
    async def save_booking_intent(
        self,
        start_time: Annotated[str, "ISO format string like 2026-03-01T10:00:00+05:30"],
        caller_name: Annotated[str, "Name of the caller"],
        caller_phone: Annotated[str, "Phone number of the caller"],
        notes: Annotated[str, "Notes or email address. If none, provide an empty string."],
    ) -> str:
        logger.info(f"[TOOL] save_booking_intent: {caller_name} at {start_time}")
        try:
            self.booking_intent = {
                "start_time":   start_time,
                "caller_name":  caller_name,
                "caller_phone": caller_phone,
                "notes":        notes,
            }
            self.caller_name = caller_name
            return f"Booking intent saved for {caller_name} at {start_time}. It will confirm on hangup."
        except Exception as e:
            logger.error(f"[SAVE-INTENT] failed: {e}")
            return "I had trouble saving the booking. Please try again."

    @llm.function_tool(description="Check available slots for a given date.")
    async def check_availability(self, date: Annotated[str, "Date string in YYYY-MM-DD format"]) -> str:
        logger.info(f"[TOOL] check_availability: date={date}")
        try:
            loop = asyncio.get_event_loop()
            slots = await loop.run_in_executor(None, get_available_slots, date)
            if not slots:
                return f"No available slots on {date}. Would you like to check another date?"
            slot_strings = [s.get("label", s.get("time", str(s))) for s in slots[:6]]
            return f"Available slots on {date}: {', '.join(slot_strings)} IST."
        except Exception as e:
            logger.error(f"[CHECK-AVAILABILITY] failed: {e}")
            return "I can take your appointment directly. What time works best for you?"

# ─── AGENT CLASS ──────────────────────────────────────────────────────────────
class OutboundAssistant(Agent):
    def __init__(self, agent_tools: AgentTools, final_instructions: str):
        tools = llm.find_function_tools(agent_tools)
        super().__init__(instructions=final_instructions, tools=tools)

# ─── MAIN RUNNER ──────────────────────────────────────────────────────────────
agent_is_speaking = False

async def entrypoint(ctx: JobContext):
    global agent_is_speaking
    await ctx.connect()
    logger.info(f"[ROOM] Connected: {ctx.room.name}")
    
    phone_number = None
    caller_name  = ""
    caller_phone = "unknown"
    metadata = ctx.job.metadata or ""
    if metadata:
        try:
            meta = json.loads(metadata)
            phone_number = meta.get("phone_number")
        except Exception:
            pass
            
    for identity, participant in ctx.room.remote_participants.items():
        if participant.name and participant.name not in ("", "Caller", "Unknown"):
            caller_name = participant.name
        if not phone_number:
            attr = participant.attributes or {}
            phone_number = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if not phone_number and "+" in identity:
            m = re.search(r"\+\d{7,15}", identity)
            if m: phone_number = m.group()
    caller_phone = phone_number or "unknown"
    
    if is_rate_limited(caller_phone):
        logger.warning(f"[RATE-LIMIT] Blocked {caller_phone}")
        return
        
    live_config = get_live_config(caller_phone)
    
    for key in ["LIVEKIT_URL","LIVEKIT_API_KEY","LIVEKIT_API_SECRET","OPENAI_API_KEY",
                "SARVAM_API_KEY","CAL_API_KEY","TELEGRAM_BOT_TOKEN","SUPABASE_URL","SUPABASE_KEY", "GROQ_API_KEY"]:
        val = live_config.get(key.lower(), "")
        if val: os.environ[key] = val
            
    # Instantiate tools cleanly
    agent_tools = AgentTools(caller_phone=caller_phone, caller_name=caller_name)
    agent_tools._sip_identity = f"sip_{caller_phone.replace('+','')}" if phone_number else "inbound_caller"
    agent_tools.ctx_api   = ctx.api
    agent_tools.room_name = ctx.room.name
    
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.35)
    llm_model     = live_config.get("llm_model", "")
    llm_provider  = live_config.get("llm_provider", "groq")
    tts_voice     = live_config.get("tts_voice", "alloy")
    max_turns     = live_config.get("max_turns", 25)
    
    # ── 🚀 GEOGRAPHIC ROUTING INTERCEPT ──────────────────────────────────────
    is_uk_caller = caller_phone.startswith("+44") or ctx.room.name.startswith("uk-") or "9890767581" in caller_phone
    
    if is_uk_caller:
        logger.info(f"[GEO-ROUTING] UK Mode Engaged for Caller: {caller_phone}")
        greeting_phrase = "Hi, I am Alia from Fino AI. How may I help you today?"
        agent_instructions = (
            "You are Alia, a warm, professional, and charming female British receptionist for Fino AI. "
            "Your main goal is to handle incoming plumbing/HVAC business inquiries and book appointments into open slots. "
            "Speak in warm, conversational British English. Keep responses short and snappy (1-2 sentences maximum). "
            "CRITICAL TOOL RULE: Do not speak about your internal tools or functions out loud. Never say phrases like "
            "'checking function' or 'running check availability'. Execute tools completely silently in the background, "
            "and only speak when you have the final results ready for the user."
        )
        
        # ⚡ SPEED UP 1: Route STT through Groq's high-performance Whisper cluster
        agent_stt = openai.STT(
            model="whisper-large-v3",
            base_url="https://api.groq.com/openai/v1",
            api_key=live_config.get("groq_api_key") or os.environ.get("GROQ_API_KEY", "")
        )
        
        # 🛡️ FIX LEAKAGE: Force native OpenAI gpt-4o-mini for clean, leak-free tool calling pipelines
        agent_llm = openai.LLM(model="gpt-4o-mini", max_completion_tokens=150)
        
        # 🔊 ATTRACTIVE VOICE: Force 'shimmer', a clear, bright female corporate model
        target_voice = "shimmer"
        logger.info("[UK-STACK] Configured Groq Whisper STT + GPT-4o-Mini + Shimmer TTS for Alia.")
    else:
        logger.info(f"[GEO-ROUTING] Incoming Domestic Dial Detected: {caller_phone}")
        greeting_phrase = live_config.get("first_line", "Namaste! This is Aryan from RapidX AI...")
        agent_instructions = live_config.get("agent_instructions", "")
        target_voice = str(tts_voice).lower().strip() if tts_voice else "alloy"
        
        agent_stt = sarvam.STT(language="hi-IN", model="saaras:v3", mode="transcribe", flush_signal=True, sample_rate=16000)
        
        if llm_provider == "groq":
            agent_llm = openai.LLM(
                model=llm_model or "llama-3.3-70b-versatile",
                base_url="https://api.groq.com/openai/v1",
                api_key=os.environ.get("GROQ_API_KEY", ""),
                max_completion_tokens=150,
            )
            try:
                from livekit.agents.llm._provider_format import openai as _oai_fmt
                _orig_to_fnc_ctx = _oai_fmt.to_fnc_ctx
                def _groq_safe_to_fnc_ctx(tool_ctx, *, strict=True):
                    schemas = _orig_to_fnc_ctx(tool_ctx, strict=False)
                    for schema in schemas:
                        params = schema.get("function", {}).get("parameters", {})
                        if isinstance(params, dict):
                            params.pop("title", None)
                            if "required" in params and not params["required"]:
                                params.pop("required", None)
                            for prop in params.get("properties", {}).values():
                                if isinstance(prop, dict): prop.pop("title", None)
                    return schemas
                _oai_fmt.to_fnc_ctx = _groq_safe_to_fnc_ctx
            except Exception:
                pass
        else:
            agent_llm = openai.LLM(model=llm_model or "gpt-4o-mini", max_completion_tokens=150)

    # ── Text to Speech Synthesizer Setup ──────────────────────────────────────
    OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer", "ash", "sage", "coral"}
    if target_voice not in OPENAI_VOICES:
        target_voice = "alloy"
        
    agent_tts = openai.TTS(model="tts-1", voice=target_voice)

    final_instructions = agent_instructions + get_ist_time_context()
    agent = OutboundAssistant(agent_tools=agent_tools, final_instructions=final_instructions)
    
    # ── 🎛️ ADAPTIVE TURN DETECTION ENGINE ─────────────────────────────────────
    if is_uk_caller:
        # Natural 450ms human pause endpointing timer. Stops conversational cut-off looping completely.
        session = AgentSession(
            stt=agent_stt, llm=agent_llm, tts=agent_tts,
            vad=silero.VAD.load(),
            turn_detection="vad",
            min_endpointing_delay=0.45, 
            allow_interruptions=True
        )
        logger.info("[SESSION] Initialized Smooth-Flow VAD Session for Alia.")
    else:
        session = AgentSession(
            stt=agent_stt, llm=agent_llm, tts=agent_tts,
            turn_detection="stt",
            min_endpointing_delay=float(delay_setting),
            allow_interruptions=True
        )
        logger.info("[SESSION] Initialized Sarvam Session.")
    
    await session.start(room=ctx.room, agent=agent)
    
    try:
        await session.say(greeting_phrase, allow_interruptions=True)
    except Exception as e:
        logger.error(f"[GREETING-BROADCAST] Open failed: {e}")
        
    call_start_time = datetime.now()
    egress_id = None
    try:
        rec_api = api.LiveKitAPI(url=os.environ["LIVEKIT_URL"], api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"])
        egress_resp = await rec_api.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=ctx.room.name, audio_only=True,
                file_outputs=[api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG, filepath=f"recordings/{ctx.room.name}.ogg",
                    s3=api.S3Upload(
                        access_key=os.environ["SUPABASE_S3_ACCESS_KEY"], secret=os.environ["SUPABASE_S3_SECRET_KEY"],
                        bucket="call-recordings", region=os.environ.get("SUPABASE_S3_REGION", "ap-south-1"),
                        endpoint=os.environ["SUPABASE_S3_ENDPOINT"], force_path_style=True,
                    )
                )]
            )
        )
        egress_id = egress_resp.egress_id
        await rec_api.aclose()
    except Exception as e:
        logger.warning(f"[RECORDING] Idle: {e}")
        
    async def upsert_active_call(status: str):
        try:
            from db import get_supabase
            sb = get_supabase()
            if sb:
                sb.table("active_calls").upsert({
                    "room_id": ctx.room.name, "phone": caller_phone,
                    "caller_name": caller_name, "status": status,
                    "last_updated": datetime.utcnow().isoformat(),
                }).execute()
        except Exception:
            pass
            
    await upsert_active_call("active")
    
    async def _log_transcript(role: str, content: str):
        try:
            from db import get_supabase
            sb = get_supabase()
            if sb:
                sb.table("call_transcripts").insert({"call_room_id": ctx.room.name, "phone": caller_phone, "role": role, "content": content}).execute()
        except Exception:
            pass
            
    @session.on("agent_speech_started")
    def _agent_speech_started(ev):
        global agent_is_speaking
        agent_is_speaking = True
        
    @session.on("agent_speech_finished")
    def _agent_speech_finished(ev):
        global agent_is_speaking
        agent_is_speaking = False
        
    turn_count = 0
    interrupt_count = 0
    FILLER_WORDS = {"okay.", "okay", "ok", "uh", "hmm", "hm", "yeah", "yes", "no", "um", "ah", "oh", "right", "sure", "fine", "good"}
    
    @session.on("user_speech_committed")
    def on_user_speech_committed(ev):
        nonlocal turn_count
        global agent_is_speaking
        transcript = ev.user_transcript.strip()
        transcript_lower = transcript.lower().rstrip(".")
        if agent_is_speaking or not transcript or len(transcript) < 3 or transcript_lower in FILLER_WORDS:
            return
        asyncio.create_task(_log_transcript("user", transcript))
        turn_count += 1
        if turn_count >= max_turns:
            asyncio.create_task(session.generate_reply(instructions="Politely wrap up: thank the caller and say goodbye."))
            
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        global agent_is_speaking
        agent_is_speaking = False
        asyncio.create_task(unified_shutdown_hook(ctx))

    async def unified_shutdown_hook(shutdown_ctx: JobContext):
        logger.info("[SHUTDOWN] Sequence started.")
        duration = int((datetime.now() - call_start_time).total_seconds())
        booking_status_msg = "No booking"
        
        if agent_tools.booking_intent:
            from calendar_tools import async_create_booking
            intent = agent_tools.booking_intent
            result = await async_create_booking(
                start_time=intent["start_time"], caller_name=intent["caller_name"] or "Unknown Caller",
                caller_phone=intent["caller_phone"], notes=intent["notes"],
            )
            if result.get("success"):
                notify_booking_confirmed(
                    caller_name=intent["caller_name"], caller_phone=intent["caller_phone"],
                    booking_time_iso=intent["start_time"], booking_id=result.get("booking_id"),
                    notes=intent["notes"], tts_voice=target_voice, ai_summary="",
                )
                booking_status_msg = f"Booking Confirmed: {result.get('booking_id')}"
            else:
                booking_status_msg = f"Booking Failed: {result.get('message')}"
        else:
            notify_call_no_booking(
                caller_name=agent_tools.caller_name, caller_phone=agent_tools.caller_phone,
                call_summary="Caller did not schedule during this call.", tts_voice=target_voice, duration_seconds=duration,
            )
            
        transcript_text = ""
        try:
            messages = agent.chat_ctx.messages
            if callable(messages): messages = messages()
            lines = []
            for msg in messages:
                if getattr(msg, "role", None) in ("user", "assistant"):
                    content = getattr(msg, "content", "")
                    if isinstance(content, list):
                        content = " ".join(str(c) for c in content if isinstance(c, str))
                    lines.append(f"[{msg.role.upper()}] {content}")
            transcript_text = "\n".join(lines)
        except Exception:
            transcript_text = "unavailable"
            
        sentiment = "unknown"
        _openai_key = os.environ.get("OPENAI_API_KEY", "")
        if transcript_text and transcript_text != "unavailable" and _openai_key:
            try:
                import openai as _oai
                _client = _oai.AsyncOpenAI(api_key=_openai_key)
                resp = await _client.chat.completions.create(
                    model="gpt-4o-mini", max_tokens=5,
                    messages=[{"role":"user","content": f"Classify this call as one word: positive, neutral, negative, or frustrated.\n\n{transcript_text[:800]}"}]
                )
                sentiment = resp.choices[0].message.content.strip().lower()
            except Exception:
                pass
                
        await upsert_active_call("completed")
        
        _n8n_url = os.getenv("N8N_WEBHOOK_URL")
        if _n8n_url:
            try:
                import httpx
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: httpx.post(_n8n_url, json={
                        "event": "call_completed", "phone": caller_phone, "caller_name": agent_tools.caller_name,
                        "duration": duration, "booked": bool(agent_tools.booking_intent), "sentiment": sentiment,
                        "summary": booking_status_msg, "recording_url": f"{os.environ.get('SUPABASE_URL','')}/storage/v1/object/public/call-recordings/recordings/{ctx.room.name}.ogg" if egress_id else "",
                        "interrupt_count": interrupt_count,
                    }, timeout=5.0)
                )
            except Exception:
                pass
                
        from db import save_call_log
        try:
            save_call_log(
                phone=caller_phone, duration=duration, transcript=transcript_text, summary=booking_status_msg,
                recording_url=f"{os.environ.get('SUPABASE_URL','')}/storage/v1/object/public/call-recordings/recordings/{ctx.room.name}.ogg" if egress_id else "",
                caller_name=agent_tools.caller_name or "", sentiment=sentiment, estimated_cost_usd=round((duration/60)*0.008, 4),
                call_date=datetime.now(pytz.timezone("Asia/Kolkata")).date().isoformat(),
                call_hour=datetime.now(pytz.timezone("Asia/Kolkata")).hour,
                call_day_of_week=datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%A"),
                was_booked=bool(agent_tools.booking_intent), interrupt_count=interrupt_count,
            )
        except Exception:
            pass

    ctx.add_shutdown_callback(unified_shutdown_hook)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="outbound-caller"))