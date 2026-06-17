"""
AgentroxAI — Inbound Voice Agent
- Hardcoded defaults so Coolify works without config.json
- Hinglish enforced in system prompt addon
- Cal.com booking via requests.post (thread-safe, no httpx issues)
- Speed: slightly slower (pace=0.85 Sarvam, speed=slow Cartesia)
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
# HARDCODED DEFAULTS — work on Coolify even when config.json doesn't exist
# ─────────────────────────────────────────────────────────────────────────────

CAL_API_KEY    = "cal_live_1d442a89cf031055925cfe4defbdc532"
CAL_EVENT_ID   = 5804552

DEFAULT_SYSTEM_PROMPT = """\
You are an AI Sales Representative for AgentroxAI, an AI Automation Agency that helps \
businesses save time, reduce costs, and increase revenue through AI Agents and Business Automation.

Your role is to professionally answer inbound calls, understand the caller's needs, \
explain our services, qualify the lead, and encourage them to book a Zoom consultation \
with our founder, Kshitij.

## Personality
- Friendly and professional
- Confident but not pushy
- Conversational and natural
- Focused on understanding the prospect's business challenges
- Keep responses short and suitable for voice conversations

## Company Information
Company Name: AgentroxAI

Services:
- AI Voice Agents
- AI Chat Agents
- Customer Support Automation
- Lead Qualification Automation
- Appointment Booking Systems
- CRM Automation
- Business Process Automation
- Custom AI Solutions

Target Customers: Local Businesses, Agencies, Service Businesses, Healthcare Clinics, \
Real Estate Companies, Educational Institutions, E-commerce Businesses.

Value Proposition: We help businesses automate repetitive tasks, improve customer \
response times, generate more leads, reduce operational costs, and scale efficiently using AI.

## Primary Objective
Your primary goal is to book a Zoom meeting between the prospect and Kshitij.
Do not try to close a sale on the call. Focus on: understanding needs, explaining how \
AI may help, and booking a consultation.

## Call Flow
Step 1 - Greeting: "Thank you for calling AgentroxAI. This is the AI assistant. How can I help you today?"

Step 2 - Discovery: Ask what type of business they run, how many employees, what \
challenge they face, what automation tools they currently use.

Step 3 - Qualify: Determine if they own or influence business decisions, have a \
genuine business need, and are interested in AI or automation.

Step 4 - Offer: "Based on what you've shared, I believe our team may be able to help. \
Would you like to schedule a free Zoom consultation with Kshitij to explore possible solutions?"

Step 5 - When they agree: IMMEDIATELY call get_next_available_slot. Tell them the slot. \
Once they confirm the time, ask for their Full Name, then their Email Address.
Confirm: "Just to confirm, your name is [name] and your email is [email]. Is that correct?"

Step 6 - Call book_consultation with name, email, date, time. \
Then say: "Perfect. Kshitij will reach out with the Zoom meeting information shortly. Thank you."

## Rules
- Never pressure the caller
- Never promise specific business results
- Never discuss pricing
- If unsure: "I'll make a note of that so Kshitij can discuss it during the consultation."
- Max 2 sentences per reply — this is a phone call
- NEVER say you booked without actually calling book_consultation\
"""

HINGLISH_ADDON = """

## LANGUAGE RULE
You speak primarily in English. However, if the caller uses Hindi or Hinglish, \
you may naturally mix in Hindi words to match their style.

Examples of natural Hinglish (use only if caller speaks Hindi/Hinglish):
- "Bilkul, main samajh sakta hoon. What kind of business do you run?"
- "Acha, so you're interested in AI automation? Let me check a slot for you."
- "Bahut badiya! Can I get your full name please?"

RULES:
- DEFAULT: Speak in clear, professional English
- If caller speaks Hindi/Hinglish: match their style with light Hindi mixing
- Never force Hindi on an English-speaking caller\
"""

DEFAULT_FIRST_LINE = "Thank you for calling AgentroxAI. This is the AI assistant. How can I help you today?"

# ── Config loader ─────────────────────────────────────────────────────────────
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            logger.info("[CONFIG] Loaded config.json from disk")
            return cfg
    except FileNotFoundError:
        logger.info("[CONFIG] No config.json — using hardcoded defaults")
        return {}
    except Exception as e:
        logger.warning(f"[CONFIG] Error: {e} — using hardcoded defaults")
        return {}


# ── Calendar Tools ────────────────────────────────────────────────────────────

@llm.function_tool(
    name="get_next_available_slot",
    description=(
        "Find the next available Zoom consultation slot. "
        "Call this IMMEDIATELY when the caller agrees to book — do NOT ask for name/email first. "
        "Returns the next open date and time."
    ),
)
async def get_next_available_slot(
    timezone_preference: Annotated[str, "Caller's preferred timezone, default is IST (India Standard Time)"] = "IST",
) -> str:
    """Find the earliest open slot in Cal.com starting from today."""
    import requests

    api_key  = os.environ.get("CAL_API_KEY", CAL_API_KEY)
    event_id = os.environ.get("CAL_EVENT_TYPE_ID", str(CAL_EVENT_ID))

    def _fetch_slots():
        today = date_type.today()
        for days in range(8):
            check = (today + timedelta(days=days)).isoformat()
            try:
                r = requests.get(
                    "https://api.cal.com/v2/slots",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "cal-api-version": "2024-09-04",
                    },
                    params={
                        "eventTypeId": event_id,
                        "start": f"{check}T00:00:00Z",
                        "end":   f"{check}T23:59:59Z",
                    },
                    timeout=8,
                )
                if r.status_code != 200:
                    continue
                data = r.json().get("data", {})
                slots = []
                if isinstance(data, dict):
                    slots = data.get(check, [])
                    if not slots:
                        for v in data.values():
                            if isinstance(v, list) and v:
                                slots = v
                                break
                if slots:
                    s = slots[0]
                    t = s.get("start") or s.get("time", "")
                    if t:
                        dt_utc = datetime.fromisoformat(t.replace("Z", "+00:00"))
                        ist = timezone(timedelta(hours=5, minutes=30))
                        dt_ist = dt_utc.astimezone(ist)
                        return {
                            "date":    check,
                            "time24":  dt_ist.strftime("%H:%M"),
                            "label":   dt_ist.strftime("%I:%M %p").lstrip("0"),
                            "weekday": dt_ist.strftime("%A"),
                        }
            except Exception as ex:
                logger.warning(f"[CAL] Slot check error for {check}: {ex}")
        return None

    try:
        slot = await asyncio.to_thread(_fetch_slots)
        if slot:
            logger.info(f"[CAL] Next slot: {slot['date']} {slot['time24']} IST")
            return (
                f"SLOT FOUND: date={slot['date']}, time={slot['time24']}, "
                f"label={slot['label']} IST, day={slot['weekday']}. "
                f"Tell caller: 'Next available slot is {slot['weekday']} {slot['date']} "
                f"at {slot['label']} IST. Does that work?' "
                f"Once confirmed, collect name and email, then call book_consultation."
            )
        return (
            "No slots found in next 7 days. "
            "Tell caller: Kshitij will personally reach out to schedule the meeting."
        )
    except Exception as e:
        logger.error(f"[CAL] get_next_available_slot error: {e}")
        return "Calendar unavailable. Tell caller Kshitij will reach out to schedule."


@llm.function_tool(
    name="book_consultation",
    description=(
        "Book the Zoom consultation in Cal.com. "
        "Call this ONLY after: (1) caller confirmed the time slot, "
        "(2) you have their name AND email. "
        "This creates the REAL booking — never say 'booked' without calling this."
    ),
)
async def book_consultation(
    caller_name:  Annotated[str, "Caller's full name"],
    caller_email: Annotated[str, "Caller's email address"],
    date:         Annotated[str, "Date in YYYY-MM-DD format"],
    time_slot:    Annotated[str, "Time in HH:MM 24h format e.g. '10:00'"],
    notes:        Annotated[str, "Brief notes about caller's needs"] = "",
) -> str:
    """Create the real Cal.com booking via requests (thread-safe)."""
    import requests

    api_key  = os.environ.get("CAL_API_KEY", CAL_API_KEY)
    event_id = int(os.environ.get("CAL_EVENT_TYPE_ID", str(CAL_EVENT_ID)))
    caller_phone = os.environ.get("_CALLER_PHONE_", "unknown")

    # Build IST ISO timestamp
    try:
        ist = timezone(timedelta(hours=5, minutes=30))
        dt  = datetime.strptime(f"{date} {time_slot}", "%Y-%m-%d %H:%M").replace(tzinfo=ist)
        start_iso = dt.isoformat()
    except Exception as e:
        logger.error(f"[CAL] datetime parse error: {e}")
        return "Couldn't parse that date/time. Please confirm the date and time again."

    payload = {
        "eventTypeId": event_id,
        "start": start_iso,
        "attendee": {
            "name":        caller_name,
            "email":       caller_email,
            "phoneNumber": caller_phone,
            "timeZone":    "Asia/Kolkata",
            "language":    "en",
        },
        "bookingFieldsResponses": {
            "notes": notes or f"Booked via AI agent. Email: {caller_email}",
        },
    }

    def _do_book():
        return requests.post(
            "https://api.cal.com/v2/bookings",
            headers={
                "Authorization":   f"Bearer {api_key}",
                "cal-api-version": "2024-08-13",
                "Content-Type":    "application/json",
            },
            json=payload,
            timeout=10,
        )

    try:
        resp = await asyncio.to_thread(_do_book)
        logger.info(f"[CAL] POST /v2/bookings → {resp.status_code}: {resp.text[:200]}")

        if resp.status_code in (200, 201):
            uid = resp.json().get("data", {}).get("uid", "N/A")
            logger.info(f"[CAL] Booking created! uid={uid} for {caller_name} <{caller_email}>")

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
                logger.warning(f"[NOTIFY] Telegram: {ne}")

            return (
                f"Booking confirmed! {caller_name}'s Zoom consultation is booked for "
                f"{date} at {time_slot} IST. Kshitij will send the Zoom link to {caller_email} shortly."
            )
        else:
            err = resp.json().get("error", {}).get("message", resp.text[:100])
            logger.error(f"[CAL] Booking failed {resp.status_code}: {err}")
            return (
                f"Booking issue: {err}. "
                "Tell caller: Kshitij will personally reach out to confirm the meeting."
            )
    except Exception as e:
        logger.error(f"[CAL] book_consultation error: {e}")
        return "Technical issue. Tell caller Kshitij will personally reach out to schedule."


# ── Prewarm ───────────────────────────────────────────────────────────────────

def prewarm(proc):
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
    os.environ["_CALLER_PHONE_"] = caller_phone
    logger.info(f"[AGENT] Caller: {caller_phone}")

    # ── System prompt: config.json → env var → hardcoded default ──────────────
    base_prompt = (
        config.get("agent_instructions")
        or os.environ.get("AGENT_INSTRUCTIONS")
        or DEFAULT_SYSTEM_PROMPT
    ).strip()

    # Always append Hinglish rules — this is what makes the LLM write Hinglish
    agent_instructions = base_prompt + HINGLISH_ADDON
    logger.info(f"[AGENT] Prompt: {len(agent_instructions)} chars")

    # ── Settings ───────────────────────────────────────────────────────────────
    first_line     = config.get("first_line") or DEFAULT_FIRST_LINE
    llm_provider   = config.get("llm_provider") or os.environ.get("LLM_PROVIDER", "openai")
    llm_model      = config.get("llm_model") or os.environ.get("LLM_MODEL", "gpt-4o-mini")
    tts_voice_name = config.get("tts_voice") or "anushka"
    lang_preset    = config.get("lang_preset") or "multilingual"
    tts_speed      = config.get("tts_speed") or "normal"  # normal pace = natural human speed
    min_ep_delay   = float(config.get("stt_min_endpointing_delay") or 0.15)

    logger.info(f"[AGENT] LLM={llm_provider}/{llm_model} Voice={tts_voice_name} Lang={lang_preset}")

    # NOTE: openai.LLM.with_groq() does NOT exist in livekit-plugins-openai 1.4.2
    # Groq exposes an OpenAI-compatible API — pass base_url instead
    if llm_provider == "groq":
        groq_key = os.environ.get("GROQ_API_KEY", "")
        active_llm = openai.LLM(
            model=llm_model,
            base_url="https://api.groq.com/openai/v1",
            api_key=groq_key,
            temperature=0.7,
        )
        logger.info(f"[LLM] Groq (OpenAI-compat): {llm_model}")
    else:
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        active_llm = openai.LLM(
            model=llm_model,
            api_key=openai_key or None,
            temperature=0.7,
        )
        logger.info(f"[LLM] OpenAI: {llm_model}")

    # ── TTS ─────────────────────────────────────────────────────────────────────
    SARVAM_VOICES = {
        "kavya","meera","arya","anushka","manisha","vidya","abhilash","karun",
        "hitesh","shubh","ritu","rahul","pooja","simran","amit","ratan","rohan",
        "dev","ishita","shreya","manan","sumit","priya","aditya","kabir",
        "neha","varun","roopa","aayan","ashutosh","advait",
    }

    use_sarvam = (
        lang_preset in ("hi-IN", "multilingual", "en-IN")
        or tts_voice_name.lower() in SARVAM_VOICES
    )

    # tts_speed → Sarvam pace: slow=0.87, normal=1.0, fast=1.15
    pace_map = {"slow": 0.87, "normal": 1.0, "fast": 1.15}
    sarvam_pace = pace_map.get(tts_speed, 1.0)
    # tts_speed → Cartesia speed param
    cartesia_speed = tts_speed if tts_speed in ("slowest","slow","normal","fast","fastest") else "normal"

    if use_sarvam:
        try:
            from livekit.plugins import sarvam
            sarvam_key   = os.environ.get("SARVAM_API_KEY", "")
            sarvam_voice = tts_voice_name.lower() if tts_voice_name.lower() in SARVAM_VOICES else "anushka"
            # en-IN = English text spoken with Indian rhythm → perfect for Hinglish
            sarvam_lang  = "en-IN" if lang_preset in ("multilingual", "en-IN") else "hi-IN"
            active_tts = sarvam.TTS(
                api_key=sarvam_key,
                target_language_code=sarvam_lang,
                speaker=sarvam_voice,
                speech_sample_rate=16000,
                pace=sarvam_pace,
                pitch=0.0,
            )
            logger.info(f"[TTS] Sarvam: voice={sarvam_voice}, lang={sarvam_lang}, pace={sarvam_pace}")
        except Exception as e:
            logger.warning(f"[TTS] Sarvam failed ({e}), using Cartesia")
            active_tts = cartesia.TTS(
                model="sonic-2-2025-03-07",
                voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",
                language="en",
                speed=cartesia_speed,
                word_timestamps=True,
            )
    else:
        cid = tts_voice_name if len(tts_voice_name) > 10 else "f786b574-daa5-4673-aa0c-cbe3e8534c02"
        active_tts = cartesia.TTS(
            model="sonic-2-2025-03-07",
            voice=cid,
            language="en",
            speed=cartesia_speed,
            word_timestamps=True,
        )
        logger.info(f"[TTS] Cartesia: voice={cid}, speed={cartesia_speed}")

    # ── Agent ──────────────────────────────────────────────────────────────────
    agent = Agent(
        instructions=agent_instructions,
        tools=[get_next_available_slot, book_consultation],
    )

    # ── Session ────────────────────────────────────────────────────────────────
    is_multi  = lang_preset in ("multilingual", "en-IN", "hi-IN")
    stt_lang  = "en-US"

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(
            model="nova-3",
            # 'detect_language' is NOT supported in streaming mode.
            # Use language="multi" for multilingual (Hindi+English) streaming.
            language="multi" if is_multi else "en-US",
            interim_results=True,
            endpointing_ms=25,
            filler_words=True,
            smart_format=False,
            no_delay=True,
        ),
        llm=active_llm,
        tts=active_tts,
        min_endpointing_delay=min_ep_delay,
        max_endpointing_delay=0.6,  # respond within 0.6s of caller stopping — feels natural
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
    logger.info(f"[AGENT] Greeting said: {first_line}")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="inbound-voice-agent",
        )
    )