"""
AgentroxAI — Inbound Voice Agent
All settings are baked in as defaults. config.json overrides when present (local/dashboard use).
On Coolify: reads from environment variables + hardcoded defaults below.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Annotated

from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import deepgram, openai, silero, cartesia

load_dotenv()
logger = logging.getLogger("voice-agent")

# ─────────────────────────────────────────────────────────────────────────────
# HARDCODED DEFAULTS — these run on Coolify where config.json doesn't exist.
# Change them here OR override via config.json (dashboard) OR env vars.
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SYSTEM_PROMPT = """You are an AI Sales Representative for AgentroxAI, an AI Automation Agency that helps businesses save time, reduce costs, and increase revenue through AI Agents and Business Automation.

Your role is to professionally answer inbound calls, understand the caller's needs, explain our services, qualify the lead, and encourage them to book a Zoom consultation with our founder, Kshitij.

## Personality
- Friendly and professional
- Confident but not pushy
- Conversational and natural
- Focused on understanding the prospect's business challenges
- Keep responses short and suitable for voice conversations

## Company Information
Company Name: AgentroxAI

Services:
- AI Voice Agents (like this one!)
- AI Chat Agents
- Customer Support Automation
- Lead Qualification Automation
- Appointment Booking Systems
- CRM Automation
- Business Process Automation
- Custom AI Solutions

Target Customers:
- Local Businesses, Agencies, Service Businesses, Healthcare Clinics
- Real Estate Companies, Educational Institutions, E-commerce Businesses

Value Proposition: We help businesses automate repetitive tasks, improve customer response times, generate more leads, reduce operational costs, and scale efficiently using AI.

## Primary Objective
Book a FREE Zoom consultation between the prospect and Kshitij. Do NOT close a sale on this call.

## Call Flow
Step 1 — Discovery: Ask what business they run, how many employees, what challenge they face, what tools they use.
Step 2 — Qualify: Check if they own/influence decisions and have genuine AI/automation needs.
Step 3 — Offer: "Based on what you shared, I think Kshitij can definitely help. Want to book a free Zoom call with him?"
Step 4 — Book: When they say yes, IMMEDIATELY call get_next_available_slot, tell them the time, confirm it.
Step 5 — Collect: Ask for full name, then email address. Confirm: "Just to confirm — your name is [name] and email is [email]. Correct?"
Step 6 — Confirm: Call book_consultation with all details. Then say: "Done! Kshitij will send the Zoom link to your email shortly."

## Objection Handling
- "Too expensive" → "Most clients recover the cost in month one from saved labor. Kshitij can show exact numbers — want a quick call?"
- "Not sure it works" → "That's exactly why we do a free demo — no commitment at all."
- "Already have someone" → "We work alongside your existing team — we just handle the repetitive stuff."

## Rules
- NEVER pressure the caller
- NEVER promise specific results  
- Max 2 sentences per response — this is a phone call
- Always guide qualified leads toward the Zoom consultation
- If caller declines, politely thank them and end the call
- When booking, ALWAYS call get_next_available_slot first, then book_consultation — NEVER pretend to book"""

DEFAULT_FIRST_LINE = "Namaste! AgentroxAI mein aapka swagat hai. Aap kaise hain, aur main aapki kaise madad kar sakta hoon?"

DEFAULT_HINGLISH_ADDON = """

## LANGUAGE — MANDATORY RULE
You MUST reply in Hinglish — natural mix of Hindi and English, exactly how urban Indians speak.

Examples of correct Hinglish responses:
- "Haan bilkul! Aapka business kya hai? Hum AI se aapki kaafi madad kar sakte hain."
- "Acha, toh aap ek free Zoom call book karna chahenge Kshitij ke saath? Woh personally baat karenge."
- "Theek hai! Main abhi ek slot check karta hoon aapke liye."
- "Bahut badiya! Aapka naam aur email bata dein, main booking confirm kar deta hoon."

Key Hindi words to use naturally: haan, nahi, acha, bilkul, theek hai, toh, kya, bataiye, zaroor, suniye, bahut, badiya, samjha, abhi, aapka, aapki, main, madad, kar, sakte

Rules:
- If caller speaks English → reply in light Hinglish (more English, some Hindi)
- If caller speaks Hindi → reply in heavy Hinglish (more Hindi, some English)
- NEVER reply in pure English — always mix some Hindi words
- Max 2 sentences per reply"""


# ── Config loader ─────────────────────────────────────────────────────────────
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            logger.info("[CONFIG] Loaded config.json")
            return cfg
    except FileNotFoundError:
        logger.info("[CONFIG] config.json not found — using hardcoded defaults (Coolify mode)")
        return {}
    except Exception as e:
        logger.warning(f"[CONFIG] Error loading config.json: {e} — using defaults")
        return {}


# ── Calendar Tools ────────────────────────────────────────────────────────────

@llm.function_tool(
    name="get_next_available_slot",
    description=(
        "Get the next available appointment slot for a Zoom consultation. "
        "Call this IMMEDIATELY when the caller agrees to book a meeting — do NOT wait. "
        "Returns the next open date and time."
    ),
)
async def get_next_available_slot() -> str:
    """Find the earliest available appointment slot from today onwards."""
    # Use env vars directly (works on Coolify where config.json doesn't exist)
    cal_api_key = os.environ.get("CAL_API_KEY", "cal_live_1d442a89cf031055925cfe4defbdc532")
    cal_event_id = os.environ.get("CAL_EVENT_TYPE_ID", "5804552")

    import requests as req
    try:
        today = date_type.today()
        for days_ahead in range(8):
            check_date = (today + timedelta(days=days_ahead)).isoformat()
            start_dt = f"{check_date}T00:00:00Z"
            end_dt   = f"{check_date}T23:59:59Z"

            resp = req.get(
                "https://api.cal.com/v2/slots",
                headers={
                    "Authorization": f"Bearer {cal_api_key}",
                    "cal-api-version": "2024-09-04",
                },
                params={"eventTypeId": cal_event_id, "start": start_dt, "end": end_dt},
                timeout=8,
            )

            if resp.status_code != 200:
                logger.warning(f"[CAL] Slots API {resp.status_code} for {check_date}")
                continue

            data = resp.json().get("data", {})
            raw_slots = []
            if isinstance(data, dict):
                raw_slots = data.get(check_date, [])
                if not raw_slots:
                    for v in data.values():
                        if isinstance(v, list) and v:
                            raw_slots = v
                            break

            if raw_slots:
                first = raw_slots[0]
                slot_utc = first.get("start") or first.get("time", "")
                if slot_utc:
                    clean = slot_utc.replace("Z", "+00:00")
                    dt_utc = datetime.fromisoformat(clean)
                    ist = timezone(timedelta(hours=5, minutes=30))
                    dt_ist = dt_utc.astimezone(ist)
                    time_label = dt_ist.strftime("%I:%M %p").lstrip("0")
                    time_24h   = dt_ist.strftime("%H:%M")

                    logger.info(f"[CAL] Next slot: {check_date} at {time_24h} IST")
                    return (
                        f"slot_date={check_date} slot_time={time_24h} label={time_label} IST. "
                        f"Tell the caller: The next available slot is {check_date} ({dt_ist.strftime('%A')}) at {time_label} IST. "
                        f"Ask if this time works. Once confirmed, call book_consultation."
                    )

        return (
            "No slots found in next 7 days. "
            "Tell the caller: Kshitij will personally reach out to schedule the Zoom call."
        )
    except Exception as e:
        logger.error(f"[CAL] get_next_available_slot error: {e}")
        return "Calendar check failed. Tell the caller Kshitij will reach out to schedule personally."


@llm.function_tool(
    name="book_consultation",
    description=(
        "Book the Zoom consultation in Cal.com. "
        "Call this after: (1) caller confirmed the time slot, (2) you have their name AND email. "
        "This actually creates the real booking — never say 'booked' without calling this first."
    ),
)
async def book_consultation(
    caller_name:  Annotated[str, "Caller's full name"],
    caller_email: Annotated[str, "Caller's email address"],
    date:         Annotated[str, "Appointment date YYYY-MM-DD"],
    time_slot:    Annotated[str, "Appointment time HH:MM in 24-hour format e.g. '10:00'"],
    notes:        Annotated[str, "Brief notes about what the caller needs"] = "",
) -> str:
    """Create the actual Cal.com booking."""
    # Direct env var access — works on Coolify
    cal_api_key  = os.environ.get("CAL_API_KEY", "cal_live_1d442a89cf031055925cfe4defbdc532")
    cal_event_id = int(os.environ.get("CAL_EVENT_TYPE_ID", "5804552"))
    caller_phone = os.environ.get("_CALLER_PHONE_", "unknown")

    # Build ISO 8601 in IST
    try:
        ist_offset = timedelta(hours=5, minutes=30)
        dt_naive   = datetime.strptime(f"{date} {time_slot}", "%Y-%m-%d %H:%M")
        dt_ist     = dt_naive.replace(tzinfo=timezone(ist_offset))
        start_iso  = dt_ist.isoformat()
    except Exception as e:
        logger.error(f"[CAL] datetime parse error: {e}")
        return "Couldn't parse date/time. Please confirm the appointment date and time again."

    payload = {
        "eventTypeId": cal_event_id,
        "start": start_iso,
        "attendee": {
            "name":        caller_name,
            "email":       caller_email,
            "phoneNumber": caller_phone,
            "timeZone":    "Asia/Kolkata",
            "language":    "en",
        },
        "bookingFieldsResponses": {
            "notes": notes or f"Zoom consultation. Phone: {caller_phone}",
        },
    }

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.cal.com/v2/bookings",
                headers={
                    "Authorization":   f"Bearer {cal_api_key}",
                    "cal-api-version": "2024-08-13",
                    "Content-Type":    "application/json",
                },
                json=payload,
            )

        if resp.status_code in (200, 201):
            uid = resp.json().get("data", {}).get("uid", "N/A")
            logger.info(f"[CAL] Booked! uid={uid} name={caller_name} email={caller_email} time={start_iso}")

            # Telegram notification (best-effort)
            try:
                from notify import notify_booking_confirmed
                notify_booking_confirmed(
                    caller_name=caller_name,
                    caller_phone=caller_phone,
                    booking_time_iso=start_iso,
                    booking_id=uid,
                    notes=f"Email: {caller_email}. {notes}",
                )
            except Exception as ne:
                logger.warning(f"[NOTIFY] Telegram failed: {ne}")

            return (
                f"Booking confirmed! {caller_name}'s Zoom consultation is booked for "
                f"{date} at {time_slot} IST. Booking ID: {uid}. "
                f"Kshitij will send the Zoom link to {caller_email} shortly."
            )
        else:
            logger.error(f"[CAL] Booking failed {resp.status_code}: {resp.text[:300]}")
            return f"Booking failed (error {resp.status_code}). Tell the caller Kshitij will reach out personally to schedule."

    except Exception as e:
        logger.error(f"[CAL] book_consultation error: {e}")
        return "Technical issue with booking. Tell the caller Kshitij will personally reach out to schedule the call."


# ── Prewarm ───────────────────────────────────────────────────────────────────

def prewarm(proc):
    """Pre-loads VAD so the first call has no cold-start delay."""
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.1,
        activation_threshold=0.5,
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────

async def entrypoint(ctx: JobContext):
    config = _load_config()
    logger.info(f"[AGENT] Room: {ctx.room.name}")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()
    caller_phone = participant.identity or "unknown"
    logger.info(f"[AGENT] Caller: {caller_phone}")

    # Store phone for booking tool
    os.environ["_CALLER_PHONE_"] = caller_phone

    # ── Build system prompt ────────────────────────────────────────────────────
    # Priority: config.json (dashboard) → hardcoded default → env var
    base_prompt = (
        config.get("agent_instructions")
        or os.environ.get("AGENT_INSTRUCTIONS")
        or DEFAULT_SYSTEM_PROMPT
    ).strip()

    # Always append Hinglish instruction (forces LLM to write Hinglish text)
    agent_instructions = base_prompt + DEFAULT_HINGLISH_ADDON
    logger.info(f"[AGENT] System prompt: {len(agent_instructions)} chars")

    # ── Settings ───────────────────────────────────────────────────────────────
    first_line     = config.get("first_line") or os.environ.get("AGENT_FIRST_LINE") or DEFAULT_FIRST_LINE
    llm_provider   = config.get("llm_provider") or os.environ.get("LLM_PROVIDER", "groq")
    llm_model      = config.get("llm_model") or os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
    tts_voice_name = config.get("tts_voice") or os.environ.get("TTS_VOICE", "kavya")
    lang_preset    = config.get("lang_preset") or os.environ.get("LANG_PRESET", "multilingual")
    tts_speed      = config.get("tts_speed") or os.environ.get("TTS_SPEED", "normal")
    min_ep_delay   = float(config.get("stt_min_endpointing_delay") or os.environ.get("STT_EP_DELAY", "0.2"))

    logger.info(f"[AGENT] LLM={llm_provider}/{llm_model} Voice={tts_voice_name} Lang={lang_preset} Speed={tts_speed}")

    # ── LLM ────────────────────────────────────────────────────────────────────
    if llm_provider == "groq":
        groq_key = os.environ.get("GROQ_API_KEY", "")
        active_llm = openai.LLM.with_groq(
            model=llm_model,
            api_key=groq_key,
            temperature=0.7,
        )
    else:
        active_llm = openai.LLM(model=llm_model, temperature=0.7)

    # ── TTS ─────────────────────────────────────────────────────────────────────
    SARVAM_VOICE_NAMES = {
        "kavya", "meera", "arya", "anushka", "manisha", "vidya",
        "abhilash", "karun", "hitesh", "shubh", "ritu", "rahul",
        "pooja", "simran", "amit", "ratan", "rohan", "dev", "ishita",
        "shreya", "manan", "sumit", "priya", "aditya", "kabir",
        "neha", "varun", "roopa", "aayan", "ashutosh", "advait",
    }

    use_sarvam = (
        lang_preset in ("hi-IN", "multilingual", "en-IN")
        or tts_voice_name.lower() in SARVAM_VOICE_NAMES
    )

    _sarvam_pace_map = {"slow": 0.80, "normal": 0.92, "fast": 1.05}
    _sarvam_pace = _sarvam_pace_map.get(tts_speed, 0.92)
    _cartesia_speed = tts_speed if tts_speed in ("slowest", "slow", "normal", "fast", "fastest") else "normal"

    if use_sarvam:
        try:
            from livekit.plugins import sarvam
            sarvam_key  = os.environ.get("SARVAM_API_KEY", "")
            sarvam_voice = tts_voice_name.lower() if tts_voice_name.lower() in SARVAM_VOICE_NAMES else "kavya"
            # en-IN = English text spoken with Indian rhythm — perfect for Hinglish output
            sarvam_lang  = "en-IN" if lang_preset in ("multilingual", "en-IN") else "hi-IN"
            active_tts = sarvam.TTS(
                api_key=sarvam_key,
                target_language_code=sarvam_lang,
                speaker=sarvam_voice,
                speech_sample_rate=16000,
                pace=_sarvam_pace,
                pitch=0.0,
            )
            logger.info(f"[TTS] Sarvam — voice={sarvam_voice}, lang={sarvam_lang}, pace={_sarvam_pace}")
        except Exception as e:
            logger.warning(f"[TTS] Sarvam failed ({e}), using Cartesia fallback")
            active_tts = cartesia.TTS(
                model="sonic-2",
                voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",
                language="en",
                speed=_cartesia_speed,
                word_timestamps=True,
            )
    else:
        cartesia_voice_id = (
            tts_voice_name if len(tts_voice_name) > 10
            else "f786b574-daa5-4673-aa0c-cbe3e8534c02"
        )
        active_tts = cartesia.TTS(
            model="sonic-2",
            voice=cartesia_voice_id,
            language="en",
            speed=_cartesia_speed,
            word_timestamps=True,
        )
        logger.info(f"[TTS] Cartesia — voice={cartesia_voice_id}, speed={_cartesia_speed}")

    # ── Agent ──────────────────────────────────────────────────────────────────
    agent = Agent(
        instructions=agent_instructions,
        tools=[get_next_available_slot, book_consultation],
    )

    # ── Session ────────────────────────────────────────────────────────────────
    _is_multilingual = lang_preset in ("multilingual", "en-IN", "hi-IN")
    _stt_language    = "hi" if (not _is_multilingual and "hi" in lang_preset) else "en-US"

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(
            model="nova-3",
            language=_stt_language,
            detect_language=_is_multilingual,
            interim_results=True,
            endpointing_ms=25,
            filler_words=True,
            smart_format=False,
            no_delay=True,
        ),
        llm=active_llm,
        tts=active_tts,
        min_endpointing_delay=min_ep_delay,
        max_endpointing_delay=1.2,
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=participant.identity,
            close_on_disconnect=True,
        ),
    )

    await asyncio.sleep(0.3)
    await session.say(first_line, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="inbound-voice-agent",
        )
    )