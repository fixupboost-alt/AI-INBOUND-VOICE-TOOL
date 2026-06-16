import asyncio
import logging
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import deepgram, openai, silero, cartesia

load_dotenv()
logger = logging.getLogger("voice-agent")

def prewarm(proc):
    # Pre-loads VAD into memory so first call doesn't have cold-start delay
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.1,
        activation_threshold=0.5,
    )

async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to incoming call session room: {ctx.room.name}")

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    participant = await ctx.wait_for_participant()
    logger.info(f"Telephony stream active for caller: {participant.identity}")

    agent = Agent(
        instructions=(
            "You are Alia, a professional, friendly, and warm AI voice assistant. "
            "Answer incoming telephone inquiries smoothly and naturally. "
            "Keep responses SHORT — 1-2 sentences max unless more detail is needed. "
            "Never say you are an AI unless directly asked. "
            "Speak like a real human: use natural fillers like 'sure', 'of course', 'absolutely'. "
            "Match the caller's pace and language."
        ),
    )

    session = AgentSession(
        vad=ctx.proc.userdata["vad"],

        # ── STT: Deepgram Nova-3 ──────────────────────────────────────────────
        # Fastest speech-to-text, ~200ms latency, real-time streaming
        stt=deepgram.STT(
            model="nova-3",
            language="en-US",
            interim_results=True,       # Start processing before caller finishes
            endpointing_ms=25,          # Ultra-low endpointing for fast response
            filler_words=True,          # Handle "um", "uh" naturally
            smart_format=False,         # Disable for speed
            no_delay=True,              # Minimize buffering
        ),

        # ── LLM: OpenAI GPT-4o-mini ──────────────────────────────────────────
        # Fast + smart, streams tokens as they generate
        llm=openai.LLM(
            model="gpt-4o-mini",
            temperature=0.7,
        ),

        # ── TTS: Cartesia Sonic-2 ─────────────────────────────────────────────
        # ~80ms latency vs OpenAI TTS ~1000ms — sounds completely human
        # Voice: "Savannah" — warm, professional American female
        tts=cartesia.TTS(
            model="sonic-2",
            voice="f786b574-daa5-4673-aa0c-cbe3e8534c02",  # Warm professional female
            language="en",
            speed=None,                 # Natural speed (not too fast/slow)
            word_timestamps=True,       # Better lip sync / timing
        ),

        # ── Tuned for minimum response latency ───────────────────────────────
        min_endpointing_delay=0.2,      # Wait only 0.2s of silence before responding
        max_endpointing_delay=0.8,      # Max wait before cutting off and responding
    )

    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=participant.identity,
            close_on_disconnect=True,
        ),
    )

    # Brief pause to let audio pipeline initialize
    await asyncio.sleep(0.3)

    # Greet the caller immediately
    await session.say(
        "Hello, thank you for calling! How can I help you today?",
        allow_interruptions=True,
    )

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="inbound-voice-agent",
        )
    )