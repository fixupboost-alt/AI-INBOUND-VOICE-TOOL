import logging
from dotenv import load_dotenv
from livekit.agents import AgentServer, AgentSession, Agent, JobContext, cli, llm
from livekit.plugins import deepgram, openai, silero, cartesia

load_dotenv()
logger = logging.getLogger("voice-agent")

# Initialize the modern LiveKit Agent Server orchestrator
server = AgentServer()

@server.rtc_session()
async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to incoming telephony room: {ctx.room.name}")
    await ctx.connect()
    
    # Wait for the inbound phone caller to connect over the line
    participant = await ctx.wait_for_participant()
    logger.info(f"Telephony stream active for caller: {participant.identity}")

    # Set up your exact AgentroxAi system configuration instructions
    system_prompt = (
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
        "1. Greeting: Thank the caller for calling Agentrox Ai and ask how you can help.\n"
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
    )

    # Initialize the modern unified session agent handler
    session = AgentSession(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-3", language="multi"), # Bilingual language processing
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice="f786b574-daa5-4673-aa0c-cbe3e8534c02")
    )

    # Instantiate the structured AI Agent persona
    agent = Agent(instructions=system_prompt)

    # Start the conversation pipeline inside the session room
    await session.start(agent=agent, room=ctx.room)
    
    # Trigger the mandatory opening greeting dynamically
    await session.generate_reply(
        instructions="Greet the caller instantly using: 'Thank you for calling Agentrox Ai. This is the AI assistant. How can I help you today?'"
    )

if __name__ == "__main__":
    # Boot up the server app wrapper layout
    cli.run_app(server)