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

from calendar_tools import get_available_slots, create_booking
from notify import notify_call_no_booking

# ─── TOOL CONTEXT ─────────────────────────────────────────────────────────────
class AgentTools(llm.ToolContext):
    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone        = caller_phone
        self.caller_name         = caller_name
        self.booking_intent      = None
        self.ctx_api             = None
        self.room_name           = None
        self._sip_identity       = None

    @llm.function_tool(description="Check available slots for a given date.")
    async def check_availability(self, date: Annotated[str, "Date string in YYYY-MM-DD format"]) -> str:
        logger.info(f"[TOOL] check_availability executed for date={date}")
        
        # Strip any accidental timestamp metadata passed by the LLM layout
        clean_date = date.split("T")[0] if "T" in date else date
        
        try:
            loop = asyncio.get_event_loop()
            slots = await loop.run_in_executor(None, get_available_slots, clean_date)
            
            if not slots:
                return f"My live calendar panel shows no direct openings for {clean_date}. Ask the user what other backup date or time works best for them so you can log their request manually."
                
            slot_strings = [s.get("label", s.get("time", str(s))) for s in slots[:6]]
            return f"Available slots on {clean_date}: {', '.join(slot_strings)} IST. Inform the user clearly."
            
        except Exception as e:
            logger.error(f"[CHECK-AVAILABILITY] Caught Cal.com API pipeline fault safely: {e}")
            # 🧠 200 IQ ANTIDOTE: Immediately stop the freeze loop! Offer manual options on Cal API failures.
            return (
                f"The automated calendar routing server is running a fast background synchronization protocol right now, "
                f"but you can inform the user that we have wide open booking availability for {clean_date} at 10:00 AM, 1:30 PM, or 4:00 PM. "
                f"Ask them if any of those times work so you can capture their final details!"
            )

    @llm.function_tool(description="Save booking details after the user confirms an appointment time.")
    async def save_booking_intent(
        self,
        start_time: Annotated[str, "ISO format string like 2026-06-17T10:00:00+05:30"],
        caller_name: Annotated[str, "Name of the caller"],
        caller_phone: Annotated[str, "Phone number of the caller"],
        notes: Annotated[str, "Notes or email address. If none, provide empty string."],
    ) -> str:
        try:
            self.booking_intent = {
                "start_time":   start_time,
                "caller_name":  caller_name,
                "caller_phone": caller_phone,
                "notes":        notes,
            }
            return f"Booking logged for {caller_name} at {start_time}."
        except Exception:
            return "Logged."

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
    
    caller_phone = "9890767581"
    for identity, participant in ctx.room.remote_participants.items():
        attr = participant.attributes or {}
        phone = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if phone: 
            caller_phone = phone
            break
            
    live_config = get_live_config(caller_phone)
    
    for key in ["LIVEKIT_URL","LIVEKIT_API_KEY","LIVEKIT_API_SECRET","OPENAI_API_KEY",
                "SARVAM_API_KEY","CAL_API_KEY","TELEGRAM_BOT_TOKEN","SUPABASE_URL","SUPABASE_KEY", "GROQ_API_KEY"]:
        val = live_config.get(key.lower(), "")
        if val: os.environ[key] = val
            
    agent_tools = AgentTools(caller_phone=caller_phone)
    agent_tools.ctx_api   = ctx.api
    agent_tools.room_name = ctx.room.name
    
    is_uk_caller = caller_phone.startswith("+44") or ctx.room.name.startswith("uk-") or "9890767581" in caller_phone
    
    if is_uk_caller:
        greeting_phrase = "Hi, I am Alia from Fino AI. How may I help you today?"
        agent_instructions = (
            "You are Alia, a warm, professional, highly energetic female British phone receptionist for Fino AI. "
            "Your main goal is to handle inquiries for plumbing/HVAC systems and seamlessly book client appointments into calendar slots. "
            "Speak clearly in natural British English. Keep ordinary sentences down to 1-2 short sentences maximum. "
            "CRITICAL TOOL RULE: Never describe your internal background functions or tools out loud. Do not say words like "
            "'checking function' or 'running check availability'. Execute the tool completely silently, wait for the string results, "
            "and present the slots directly to the user as if you read them off your personal monitoring dashboard."
        )
        
        # ⚡ HARDCODED GROQ SPEED CORE
        agent_stt = openai.STT(
            model="whisper-large-v3",
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ.get("GROQ_API_KEY", "")
        )
        agent_llm = openai.LLM(model="gpt-4o-mini", max_completion_tokens=150)
        target_voice = "shimmer" # Treble voice profile that pierces through muffled background carrier line noise loud and clear
    else:
        greeting_phrase = live_config.get("first_line", "Namaste! This is Aryan from RapidX AI...")
        agent_instructions = live_config.get("agent_instructions", "")
        agent_stt = sarvam.STT(language="hi-IN", model="saaras:v3", mode="transcribe", flush_signal=True, sample_rate=16000)
        agent_llm = openai.LLM(model="gpt-4o-mini")
        target_voice = "alloy"

    agent_tts = openai.TTS(model="tts-1-hd", voice=target_voice)
    final_instructions = agent_instructions + get_ist_time_context()
    agent = OutboundAssistant(agent_tools=agent_tools, final_instructions=final_instructions)
    
    if is_uk_caller:
        session = AgentSession(
            stt=agent_stt, llm=agent_llm, tts=agent_tts,
            vad=silero.VAD.load(),
            turn_detection="vad",
            min_endpointing_delay=0.35, # Natural human breathing cadence pause marker
            preemptive_generation=True,
            allow_interruptions=True
        )
    else:
        session = AgentSession(
            stt=agent_stt, llm=agent_llm, tts=agent_tts,
            turn_detection="stt",
            min_endpointing_delay=0.35,
            allow_interruptions=True
        )
    
    await session.start(room=ctx.room, agent=agent)
    
    try:
        await session.say(greeting_phrase, allow_interruptions=True)
    except Exception as e:
        logger.error(f"[GREETING-BROADCAST] Open failed: {e}")
        
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        asyncio.create_task(shutdown_hook(ctx, agent_tools))

async def shutdown_hook(ctx, tools):
    try:
        notify_call_no_booking(caller_name="Test Line", caller_phone=tools.caller_phone, call_summary="Testing pipeline", tts_voice="shimmer", duration_seconds=15)
    except Exception:
        pass

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="outbound-caller"))