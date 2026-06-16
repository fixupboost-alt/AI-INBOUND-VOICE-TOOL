import logging
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import deepgram, openai, silero

load_dotenv()
logger = logging.getLogger("voice-agent")

def prewarm(proc):
    # Pre-loads Voice Activity Detection into server memory for faster response times
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to incoming call session room: {ctx.room.name}")

    # Establish audio transport tunnel
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for the inbound phone caller to connect over the trunk line
    participant = await ctx.wait_for_participant()
    logger.info(f"Telephony stream active for caller: {participant.identity}")

    # Initialize the agent with v1.x API
    agent = Agent(
        instructions=(
            "You are Alia, a professional, friendly, and concise AI voice assistant. "
            "Your job is to answer incoming telephone inquiries smoothly. "
            "Respond naturally, match the user's language, and keep your answers short."
        ),
    )

    # Create and start the AgentSession (replaces VoicePipelineAgent in v1.x)
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(),                     # Uses your DEEPGRAM_API_KEY environment variable
        llm=openai.LLM(model="gpt-4o-mini"),    # Uses your OPENAI_API_KEY environment variable
        tts=openai.TTS(),                       # Converts AI text responses back to fluent voice
        min_endpointing_delay=0.5,              # Ultra-low latency conversational tracking
        max_endpointing_delay=1.5,
    )

    # Boot the pipeline loop within the WebRTC room structure
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=None,
    )

    # Initial greeting trigger when the call connects successfully
    await session.say("Hello, thank you for calling! How can I help you today?", allow_interruptions=True)

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="inbound-voice-agent",  # Makes agent visible in LiveKit dashboard
        )
    )