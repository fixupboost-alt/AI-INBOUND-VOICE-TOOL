import asyncio
import logging
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import deepgram, openai, silero

load_dotenv()
logger = logging.getLogger("voice-agent")

def prewarm(proc):
    # Pre-loads Voice Activity Detection into server memory for faster response times
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,   # Detect speech faster on CPU servers
        min_silence_duration=0.1,
        activation_threshold=0.5,
    )

async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to incoming call session room: {ctx.room.name}")

    # Establish audio transport tunnel
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the inbound phone caller to connect over the trunk line
    participant = await ctx.wait_for_participant()
    logger.info(f"Telephony stream active for caller: {participant.identity}")

    # Initialize the agent
    agent = Agent(
        instructions=(
            "You are Alia, a professional, friendly, and concise AI voice assistant. "
            "Your job is to answer incoming telephone inquiries smoothly. "
            "Respond naturally, match the user's language, and keep your answers short."
        ),
    )

    # Create the AgentSession
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(),                     # Uses DEEPGRAM_API_KEY
        llm=openai.LLM(model="gpt-4o-mini"),    # Uses OPENAI_API_KEY
        tts=openai.TTS(),                       # Converts AI text to voice
        min_endpointing_delay=0.5,
        max_endpointing_delay=1.5,
    )

    # Start the session using RoomOptions with participant_identity (correct v1.4.x API)
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=participant.identity,
            close_on_disconnect=True,
        ),
    )

    # Small buffer to ensure audio pipeline is ready before speaking
    await asyncio.sleep(0.5)

    # Greet the caller as soon as the call connects
    await session.say(
        "Hello, thank you for calling! How can I help you today?",
        allow_interruptions=True,
    )

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="inbound-voice-agent",   # Visible in LiveKit dashboard
        )
    )