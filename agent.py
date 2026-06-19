import logging
from dotenv import load_dotenv
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import deepgram, openai, silero, cartesia

load_dotenv()
logger = logging.getLogger("voice-agent")

def prewarm(proc):
    proc.userdata["vad"] = silero.VAD.load()

async def entrypoint(ctx: JobContext):
    initial_ctx = llm.ChatContext().append(
        role="system",
        text=(
            "You are an AI Sales Representative for AgentroxAi, an AI Automation Agency that helps businesses "
            "save time, reduce costs, and increase revenue through AI Agents and Business Automation.\n"
            "Your role is to professionally answer inbound calls, understand the caller's needs, explain our services, "
            "qualify the lead, and encourage them to book a Zoom consultation with our founder, Kshitij.\n\n"
            "## Personality\n"
            "Friendly, professional, confident but not pushy, conversational, natural, and concise. Keep responses short.\n\n"
            "## Company Information & Services\n"
            "Company Name: AgentroxAi\n"
            "Services: AI Voice Agents, AI Chat Agents, Customer Support Automation, Lead Qualification Automation, "
            "Appointment Booking Systems, CRM Automation, Business Process Automation, Custom AI Solutions.\n"
            "Value Proposition: We help businesses automate repetitive tasks, improve customer response times, "
            "generate more leads, reduce operational costs, and scale efficiently using AI.\n\n"
            "## Primary Objective\n"
            "Your primary goal is to book a Zoom meeting between the prospect and Kshitij. Do not try to close a sale on the call.\n\n"
            "## Call Flow Rules\n"
            "1. Greeting: Start with: 'Thank you for calling Agentrox Ai. This is the AI assistant. How can I help you today?'\n"
            "2. Discovery: Ask about their business type, employee count, and challenges.\n"
            "3. Qualify & Offer: If interested, offer a free Zoom consultation with Kshitij.\n"
            "4. Collect Info: Collect and confirm Full Name and Email Address.\n"
            "5. Confirmation: Thank them and confirm Kshitij will reach out.\n\n"
            "## Core Constraints\n"
            "Never pressure the caller. Never promise specific results. Never discuss pricing. If unsure, say: "
            "'I'll make a note of that so Kshitij can discuss it during the consultation.'\n\n"
            "## Language Rule\n"
            "You are fully bilingual. Speak in fluent English by default. However, if the user speaks to you in Hindi or Hinglish, "
            "you must instantly shift and reply in perfectly natural, conversational Hindi."
        ),
    )

    logger.info(f"Connecting to live incoming session room: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    participant = await ctx.wait_for_participant()
    logger.info(f"SIP Telephony channel linked: {participant.identity}")

    agent = VoicePipelineAgent(
        vad=ctx.proc.userdata["vad"],
        stt=deepgram.STT(language="en", model="nova-3", smart_format=True),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice="f786b574-daa5-4673-aa0c-cbe3e8534c02"),
        chat_ctx=initial_ctx,
        min_endpointing_delay=0.3,  
        max_endpointing_delay=0.8,  
    )

    agent.start(ctx.room, participant)
    await agent.say("Thank you for calling Agentrox Ai. This is the AI assistant. How can I help you today?", allow_interruptions=True)

if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="inbound-voice-agent",
        )
    )