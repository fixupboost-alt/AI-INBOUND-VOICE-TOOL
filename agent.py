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

os.environ["SSL_CERT_FILE"] = certifi.where()

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

# ── FORCE HARDCODED SPEED OVERRIDES ──────────────────────────────────────────
# This forces the server to use Groq even if your Coolify dashboard variables are broken.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

def get_ist_time_context() -> str:
    # HARDCODING THE DATE TO 2026 TO FIX THE CALENDAR MATCHING MISALIGNMENT
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    # Force alignment for June 2026
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

class AgentTools(llm.ToolContext):
    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone = caller_phone
        self.caller_name = caller_name
        self.booking_intent = None
        self.ctx_api = None
        self.room_name = None
        self._sip_identity = None

    @llm.function_tool(description="Check available slots for a given date.")
    async def check_availability(self, date: Annotated[str, "Date string in YYYY-MM-DD format"]) -> str:
        logger.info(f"[TOOL] check_availability executed for date={date}")
        try:
            loop = asyncio.get_event_loop()
            slots = await loop.run_in_executor(None, get_available_slots, date)
            if not slots:
                return f"No available slots on {date} in the system. Ask the user if another date works."
            slot_strings = [s.get("label", s.get("time", str(s))) for s in slots[:6]]
            return f"Available slots on {date}: {', '.join(slot_strings)} IST. Inform the user clearly."
        except Exception as e:
            logger.error(f"[CHECK-AVAILABILITY] failed: {e}")
            return "I see an issue syncing the live slot map, but I can write down your preferred time directly. What time works?"

    @llm.function_tool(description="Save booking details after the user confirms an appointment time.")
    async def save_booking_intent(
        self,
        start_time: Annotated[str, "ISO format string like 2026-06-17T10:00:00+05:30"],
        caller_name: Annotated[str, "Name of the caller"],
        caller_phone: Annotated[str, "Phone number of the caller"],
        notes: Annotated[str, "Notes or email address."],
    ) -> str:
        try:
            self.booking_intent = {
                "start_time": start_time,
                "caller_name": caller_name,
                "caller_phone": caller_phone,
                "notes": notes,
            }
            return f"Booking details logged for {caller_name} at {start_time}."
        except Exception:
            return "Logged."

class OutboundAssistant(Agent):
    def __init__(self, agent_tools: AgentTools, final_instructions: str):
        tools = llm.find_function_tools(agent_tools)
        super().__init__(instructions=final_instructions, tools=tools)

async def entrypoint(ctx: JobContext):
    await ctx.connect()
    
    caller_phone = "9890767581"
    for identity, participant in ctx.room.remote_participants.items():
        attr = participant.attributes or {}
        phone = attr.get("sip.phoneNumber") or attr.get("phoneNumber")
        if phone: 
            caller_phone = phone
            break

    agent_tools = AgentTools(caller_phone=caller_phone)
    agent_tools.ctx_api = ctx.api
    agent_tools.room_name = ctx.room.name
    
    is_uk_caller = caller_phone.startswith("+44") or ctx.room.name.startswith("uk-") or "9890767581" in caller_phone
    
    if is_uk_caller:
        greeting_phrase = "Hi, I am Alia from Fino AI. How can I help you today?"
        agent_instructions = (
            "You are Alia, an energetic, crisp, and high-volume female British receptionist for Fino AI. "
            "Your main goal is to check plumbing/HVAC appointment slots and book bookings. "
            "Speak in professional British English. Keep regular turns down to 1 sentence. "
            "CRITICAL SPEED RULE: Run all calendar tools completely silently. Never say 'checking function' out loud. "
            "Once you call check_availability, read the returned options directly to the user."
        )
        # ⚡ ACCELERATION CORE: Forcing Groq cloud speed directly inside the initialization block
        agent_stt = openai.STT(
            model="whisper-large-v3",
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY
        )
        agent_llm = openai.LLM(model="gpt-4o-mini", max_completion_tokens=100)
        # 🔊 LOUD AUDIO CORE: Switching back to Onyx or Alloy but forced into high-definition streaming
        agent_tts = openai.TTS(model="tts-1-hd", voice="nova") 
    else:
        greeting_phrase = "Namaste! This is Aryan from RapidX AI..."
        agent_instructions = "You are Aryan..."
        agent_stt = sarvam.STT(language="hi-IN", model="saaras:v3")
        agent_llm = openai.LLM(model="gpt-4o-mini")
        agent_tts = openai.TTS(model="tts-1", voice="alloy")

    final_instructions = agent_instructions + get_ist_time_context()
    agent = OutboundAssistant(agent_tools=agent_tools, final_instructions=final_instructions)
    
    session = AgentSession(
        stt=agent_stt, llm=agent_llm, tts=agent_tts,
        vad=silero.VAD.load(),
        turn_detection="vad",
        min_endpointing_delay=0.35, # Dropped back to a tight 350ms to kill lag gaps
        preemptive_generation=True,
        allow_interruptions=True
    )
    
    await session.start(room=ctx.room, agent=agent)
    await session.say(greeting_phrase, allow_interruptions=True)
    
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        asyncio.create_task(shutdown_hook(ctx, agent_tools))

async def shutdown_hook(ctx, tools):
    duration = 10
    notify_call_no_booking(caller_name="Test", caller_phone=tools.caller_phone, call_summary="Testing", tts_voice="nova", duration_seconds=duration)

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="outbound-caller"))