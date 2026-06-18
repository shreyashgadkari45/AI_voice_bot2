# bot.py
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import datetime
import io
import os
import sys
import time
import wave
import json

import aiofiles
from dotenv import load_dotenv
from fastapi import WebSocket
from graph.intent_router import classify_issue, normalize_issue_text
from loguru import logger
import db
from verification import (
    build_verification_state,
    is_identifier_message,
    resolve_customer,
    verification_prompt,
    verification_retry_prompt,
    verification_success_prompt,
)

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import (
    EndFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame, 
    TextFrame, 
    LLMFullResponseStartFrame, 
    LLMFullResponseEndFrame
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="WARNING")
logger.add("backend_debug.log", level="DEBUG", rotation="2 MB", retention=3)

CONVERSATION_LOG_DIR = "conversations"
os.makedirs(CONVERSATION_LOG_DIR, exist_ok=True)

# Directory for storing audio recordings
RECORDINGS_DIR = "recordings"
os.makedirs(RECORDINGS_DIR, exist_ok=True)

def is_rate_limit_error(error: Exception) -> bool:
    text = str(error).lower()
    return "rate limit" in text or "rate_limit_exceeded" in text or "error code: 429" in text

def offline_specialist_reply(customer_message: str) -> str:
    route = classify_issue(customer_message)
    agent = route["next_agent"] if route else None
    return specialist_final_reply(agent)

def specialist_final_reply(agent: str | None) -> str:
    if agent == "technical":
        return "Sir, your technical issue has been routed to our Technical Support team and will get resolved soon. Thank you."
    if agent == "billing":
        return "Sir, your billing issue has been routed to our Billing Support team and will get resolved soon. Thank you."
    if agent == "account":
        return "Sir, your account issue has been routed to our Account Support team and will get resolved soon. Thank you."
    if agent == "product":
        return "Sir, your product or service issue has been routed to our Product Support team and will get resolved soon. Thank you."
    if agent == "order":
        return "Sir, your order issue has been routed to our Order Support team and will get resolved soon. Thank you."
    return "Please tell me if your issue is related to technical support, billing, account, product, or order."

def category_options_prompt(agent: str) -> str:
    options = {
        "technical": "Is it app not working, website error, login page issue, device issue, or slow service?",
        "billing": "Is it wrong charge, refund pending, payment failed, invoice issue, or duplicate charge?",
        "account": "Is it login problem, account blocked, password reset, profile update, or verification issue?",
        "product": "Is it activation, cancellation, upgrade, downgrade, feature issue, or service change?",
        "order": "Is it delivery delay, tracking issue, return, replacement, wrong item, or damaged item?",
    }
    return f"Sure sir. What is the main problem in {agent}? {options.get(agent, 'Please tell me the exact problem.')}"

def is_category_only_message(message: str, agent: str | None) -> bool:
    normalized = normalize_issue_text(message)
    category_terms = {
        "technical": {"technical", "technical issue", "tech issue", "app issue", "website issue", "service issue"},
        "billing": {"billing", "billing issue", "bill issue", "bill"},
        "account": {"account", "account issue", "login issue", "profile issue"},
        "product": {"product", "product issue", "service", "service issue", "plan issue"},
        "order": {"order", "order issue", "delivery issue", "return issue"},
    }
    return bool(agent and normalized in category_terms.get(agent, set()))

def category_question_prompt() -> str:
    return "Please tell me if your issue is related to technical support, billing, account, product, or order."

def is_incomplete_filler_message(message: str) -> bool:
    normalized = normalize_issue_text(message)
    filler_messages = {
        "the problem is",
        "my problem is",
        "problem is",
        "my issue is",
        "issue is",
        "i have issue",
        "i have a issue",
        "i have a problem",
        "i am facing",
        "i am having",
        "i want to say",
    }
    return normalized in filler_messages

class LangGraphProcessor(FrameProcessor):
    def __init__(self, stream_id, customer_name, phone_no, account_number, issue_type):
        super().__init__()
        self.stream_id = stream_id
        verification_state = build_verification_state(
            customer_name=customer_name,
            phone_number=phone_no,
            account_number=account_number,
            customer_identifier=phone_no or account_number,
        )
        self.customer_name = verification_state["customer_name"]
        self.phone_no = verification_state["phone_number"]
        self.account_number = verification_state["account_number"]
        self.customer_identifier = verification_state["customer_identifier"]
        self.issue_type = issue_type
        self.is_verified = verification_state["is_verified"]
        self.verification_status = verification_state["verification_status"]
        self.last_processed_text = ""
        self.last_processed_at = 0.0
        self.issue_handled = False
        self.pending_category = None
        self.asked_category_question = False
        self.ignore_transcripts_until = 0.0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame):
            logger.debug(f"Ignoring interim transcription: {frame.text}")
            return
        
        if isinstance(frame, TranscriptionFrame) or isinstance(frame, TextFrame):
            customer_message = frame.text.strip()
            if not customer_message:
                return
            if self.issue_handled:
                logger.debug(f"Ignoring transcription after issue was handled: {customer_message}")
                return
            normalized_message = normalize_issue_text(customer_message)
            now = time.monotonic()
            if customer_message != "INIT" and now < self.ignore_transcripts_until:
                if not classify_issue(customer_message) and not self.pending_category:
                    logger.debug(f"Ignoring likely bot audio echo during cooldown: {customer_message}")
                    return
            bot_prompt_echoes = (
                "please tell me the problem",
                "please describe whether",
                "i have noted your issue",
                "is there any other problem",
                "your issue has been routed",
                "will get resolved soon",
                "before we continue",
                "your profile has been verified",
                "what is the main problem",
                "app not working website error",
                "wrong charge refund pending",
                "login problem account blocked",
                "activation cancellation upgrade",
                "delivery delay tracking issue",
            )
            if any(phrase in normalized_message for phrase in bot_prompt_echoes):
                logger.debug(f"Ignoring bot prompt echo from STT: {customer_message}")
                return
            if normalized_message == self.last_processed_text and now - self.last_processed_at < 8:
                logger.debug(f"Ignoring duplicate transcription: {customer_message}")
                return
            if customer_message != "INIT" and is_incomplete_filler_message(customer_message):
                logger.debug(f"Ignoring incomplete filler transcription: {customer_message}")
                return
            self.last_processed_text = normalized_message
            self.last_processed_at = now
                
            print(f"\n[USER] Customer said: {customer_message}")
            
            from graph.workflow import graph
            from langchain_core.messages import AIMessage, HumanMessage
            
            config = {"configurable": {"thread_id": self.stream_id}}
            
            # Special case for initialization message
            if customer_message == "INIT":
                greeting = (
                    verification_success_prompt(self.customer_name)
                    if self.is_verified
                    else verification_prompt()
                )
                graph.update_state(config, {
                    "customer_name": self.customer_name,
                    "phone_number": self.phone_no,
                    "account_number": self.account_number,
                    "customer_identifier": self.customer_identifier,
                    "issue_type": self.issue_type,
                    "is_verified": self.is_verified,
                    "verification_status": self.verification_status,
                    "verification_action": "wait",
                    "messages": [AIMessage(content=greeting)],
                })
                logger.info(f"Seeded call state for stream {self.stream_id}")
                print(f"\n[TTS] Bot replied: {greeting}")
                await self.push_frame(LLMFullResponseStartFrame(), direction)
                await self.push_frame(TextFrame(text=greeting), direction)
                await self.push_frame(LLMFullResponseEndFrame(), direction)
                self.ignore_transcripts_until = time.monotonic() + 1.0
                return
            else:
                state_update = {
                    "customer_name": self.customer_name,
                    "phone_number": self.phone_no,
                    "account_number": self.account_number,
                    "customer_identifier": self.customer_identifier,
                    "issue_type": customer_message,
                    "is_verified": self.is_verified,
                    "verification_status": self.verification_status,
                    "verification_action": "continue" if self.is_verified else "wait",
                    "messages": [HumanMessage(content=customer_message)]
                }
                if not self.is_verified:
                    customer, matched_identifier = resolve_customer(
                        identifier=customer_message,
                        phone_number=self.phone_no,
                        account_number=self.account_number,
                    )
                    if customer and (is_identifier_message(customer_message) or self.customer_name != "Customer"):
                        self.is_verified = True
                        self.verification_status = "verified"
                        self.customer_name = customer["name"]
                        self.phone_no = customer["phone_no"]
                        self.account_number = customer["account_number"]
                        self.customer_identifier = matched_identifier or self.customer_identifier
                        final_response = verification_success_prompt(self.customer_name)
                        logger.info(f"Verified caller for stream {self.stream_id}: {self.customer_identifier}")
                    else:
                        self.verification_status = "failed" if is_identifier_message(customer_message) else "pending"
                        final_response = (
                            verification_retry_prompt()
                            if is_identifier_message(customer_message)
                            else verification_prompt()
                        )
                        logger.info(f"Verification pending for stream {self.stream_id}: {customer_message}")
                    graph.update_state(config, {
                        "customer_name": self.customer_name,
                        "phone_number": self.phone_no,
                        "account_number": self.account_number,
                        "customer_identifier": self.customer_identifier,
                        "issue_type": self.issue_type,
                        "is_verified": self.is_verified,
                        "verification_status": self.verification_status,
                        "verification_action": "wait" if not self.is_verified else "continue",
                        "messages": [HumanMessage(content=customer_message), AIMessage(content=final_response)],
                    })
                    print(f"\n[TTS] Bot replied: {final_response}")
                    await self.push_frame(LLMFullResponseStartFrame(), direction)
                    await self.push_frame(TextFrame(text=final_response), direction)
                    await self.push_frame(LLMFullResponseEndFrame(), direction)
                    self.ignore_transcripts_until = time.monotonic() + 1.0
                    return
                if self.pending_category:
                    final_response = specialist_final_reply(self.pending_category)
                    self.issue_handled = True
                    logger.info(
                        f"Handled pending {self.pending_category} detail for stream {self.stream_id}: {customer_message}"
                    )
                    print(f"\n[TTS] Bot replied: {final_response}")
                    await self.push_frame(LLMFullResponseStartFrame(), direction)
                    await self.push_frame(TextFrame(text=final_response), direction)
                    await self.push_frame(LLMFullResponseEndFrame(), direction)
                    self.ignore_transcripts_until = time.monotonic() + 1.0
                    await self.push_frame(EndFrame(), direction)
                    return

                local_route = classify_issue(customer_message)
                if local_route:
                    graph.update_state(config, state_update | local_route)
                    if is_category_only_message(customer_message, local_route["next_agent"]):
                        self.pending_category = local_route["next_agent"]
                        final_response = category_options_prompt(self.pending_category)
                        logger.info(
                            f"Asked follow-up options for {self.pending_category} in stream {self.stream_id}"
                        )
                        print(f"\n[TTS] Bot replied: {final_response}")
                        await self.push_frame(LLMFullResponseStartFrame(), direction)
                        await self.push_frame(TextFrame(text=final_response), direction)
                        await self.push_frame(LLMFullResponseEndFrame(), direction)
                        self.ignore_transcripts_until = time.monotonic() + 1.0
                        return

                    if local_route["next_agent"] == "general":
                        final_response = category_question_prompt()
                        self.asked_category_question = True
                        logger.info(f"Asked category question for stream {self.stream_id}: {customer_message}")
                        print(f"\n[TTS] Bot replied: {final_response}")
                        await self.push_frame(LLMFullResponseStartFrame(), direction)
                        await self.push_frame(TextFrame(text=final_response), direction)
                        await self.push_frame(LLMFullResponseEndFrame(), direction)
                        self.ignore_transcripts_until = time.monotonic() + 1.0
                        return

                    final_response = offline_specialist_reply(customer_message)
                    logger.info(
                        f"Handled with local {local_route['next_agent']} route for stream {self.stream_id}: {customer_message}"
                    )
                    print(f"\n[TTS] Bot replied: {final_response}")
                    await self.push_frame(LLMFullResponseStartFrame(), direction)
                    await self.push_frame(TextFrame(text=final_response), direction)
                    await self.push_frame(LLMFullResponseEndFrame(), direction)
                    if local_route["next_agent"] != "general":
                        self.issue_handled = True
                        await self.push_frame(EndFrame(), direction)
                    return

                final_response = category_question_prompt()
                self.asked_category_question = True
                logger.info(f"Asked category question for unclassified stream {self.stream_id}: {customer_message}")
                print(f"\n[TTS] Bot replied: {final_response}")
                await self.push_frame(LLMFullResponseStartFrame(), direction)
                await self.push_frame(TextFrame(text=final_response), direction)
                await self.push_frame(LLMFullResponseEndFrame(), direction)
                self.ignore_transcripts_until = time.monotonic() + 1.0
                return
            
            try:
                import asyncio
                # Run the synchronous graph.invoke in a separate thread so we don't block the Pipecat audio event loop
                result = await asyncio.to_thread(graph.invoke, state_update, config)
                final_response = result["messages"][-1].content
                
                end_call = False
                if "[END_CALL]" in final_response:
                    end_call = True
                    final_response = final_response.replace("[END_CALL]", "").strip()
                else:
                    end_call = True
                self.issue_handled = True

                print(f"\n[TTS] Bot replied: {final_response}")
                
                await self.push_frame(LLMFullResponseStartFrame(), direction)
                await self.push_frame(TextFrame(text=final_response), direction)
                await self.push_frame(LLMFullResponseEndFrame(), direction)
                
                if end_call:
                    await self.push_frame(EndFrame(), direction)
                
            except Exception as e:
                logger.error(f"Error invoking graph: {e}")
                if is_rate_limit_error(e):
                    error_msg = offline_specialist_reply(customer_message)
                    fallback_route = classify_issue(customer_message)
                    self.issue_handled = bool(fallback_route and fallback_route["next_agent"] != "general")
                    logger.warning("Groq rate limit hit; using offline specialist fallback.")
                else:
                    error_msg = "I'm sorry, I am having trouble connecting to my backend systems right now."
                print(f"\n[TTS] Bot replied: {error_msg}")
                await self.push_frame(LLMFullResponseStartFrame(), direction)
                await self.push_frame(TextFrame(text=error_msg), direction)
                await self.push_frame(LLMFullResponseEndFrame(), direction)
                if self.issue_handled:
                    await self.push_frame(EndFrame(), direction)
        else:
            await self.push_frame(frame, direction)

async def save_audio(server_name: str, audio: bytes, sample_rate: int, num_channels: int):
    """
    Saves audio data to a WAV file in the 'recordings' directory.
    The filename includes the server name and a timestamp for uniqueness.
    """
    if len(audio) > 0:
        filename = os.path.join(
            RECORDINGS_DIR,
            f"{server_name}_recording_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        )
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2) # 16-bit audio
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(filename, "wb") as file:
                await file.write(buffer.getvalue())
        logger.info(f"Merged audio saved to {filename}")
    else:
        logger.info("No audio data to save")


async def run_bot(websocket_client: WebSocket, stream_id: str, testing: bool,
                  customer_name: str, issue_type: str, phone_no: str, account_number: str = "Unknown",
                  call_control_id: str = None, api_key: str = None,
                  inbound_encoding: str = "PCMU", outbound_encoding: str = "PCMU"):
    """
    Runs the AI interview bot, handling WebSocket communication, STT, LLM, and TTS.
    It also manages conversation logging and audio recording.
    """
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False, # Twilio expects raw mulaw audio without WAV headers
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    stop_secs=1.5,
                    confidence=0.5,
                )
            ),
            vad_audio_passthrough=True, # Pass VAD audio to STT
            serializer=TwilioFrameSerializer(
                stream_sid=stream_id,
                call_sid=call_control_id,
                account_sid=os.getenv("TWILIO_ACCOUNT_SID"),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN"),
            ), # Serialize frames for Twilio
        ),
    )

    # We still need Deepgram for STT and Cartesia for TTS
    deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
    cartesia_api_key = os.getenv("CARTESIA_API_KEY")
    cartesia_voice_id = os.getenv("CARTESIA_VOICE_ID")

    if not deepgram_api_key:
        logger.error("DEEPGRAM_API_KEY environment variable is missing.")
        raise ValueError("DEEPGRAM_API_KEY environment variable is required.")
    if not cartesia_api_key:
        logger.error("CARTESIA_API_KEY environment variable is missing.")
        raise ValueError("CARTESIA_API_KEY environment variable is required.")
    if not cartesia_voice_id:
        logger.error("CARTESIA_VOICE_ID environment variable is missing.")
        raise ValueError("CARTESIA_VOICE_ID environment variable is required.")

    stt = DeepgramSTTService(
        api_key=deepgram_api_key,
        audio_passthrough=True,
        settings=DeepgramSTTService.Settings(
            model="nova-2",
            language="en-IN",
            endpointing=1000,
        ),
    )

    tts = CartesiaTTSService(
        api_key=cartesia_api_key,
        sample_rate=8000,
        settings=CartesiaTTSService.Settings(
            model="sonic-3.5",
            voice=cartesia_voice_id,
            language="en",
        ),
        push_silence_after_stop=True,
    )

    langgraph_proc = LangGraphProcessor(stream_id, customer_name, phone_no, account_number, issue_type)

    # AudioBufferProcessor captures all audio passing through it
    audiobuffer = AudioBufferProcessor(user_continuous_stream=not testing)

    # Define the pipeline for audio and text processing
    pipeline = Pipeline(
        [
            transport.input(),  # WebSocket input from Twilio (audio from caller)
            stt,  # Speech-To-Text: Converts caller's audio to text
            langgraph_proc, # Intercepts text, consults LangGraph, returns text
            tts,  # Text-To-Speech: Converts bot's text response to audio
            transport.output(),  # WebSocket output to Twilio (audio to caller)
            audiobuffer,  # Buffers all audio (inbound and outbound) for recording
        ]
    )

    # Define pipeline task parameters
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000, # Twilio audio input sample rate
            audio_out_sample_rate=8000, # Twilio audio output sample rate
            allow_interruptions=True, # Allow bot to be interrupted by user speech
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        """
        Handler for when the WebSocket client (Twilio) connects.
        Starts audio recording and kicks off the conversation.
        """
        await audiobuffer.start_recording()
        logger.debug("Sending initial context frame to LLM to kick off conversation.")
        # Sending an INIT frame triggers LangGraph to generate its first response
        await task.queue_frames([TranscriptionFrame(text="INIT", user_id="user", timestamp="0")])


    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        """
        Handler for when the WebSocket client (Twilio) disconnects.
        Saves any remaining buffered audio and the full conversation log.
        """
        logger.info("Client disconnected. Audio chunks are being saved in the 'recordings' directory.")
        
        # Save conversation log to JSON file
        # The stream_id provides a unique identifier for each call session
        conversation_filename = os.path.join(
            CONVERSATION_LOG_DIR,
            f"conversation_{stream_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        try:
            from graph.workflow import graph
            config = {"configurable": {"thread_id": stream_id}}
            state = graph.get_state(config)
            
            # Extract messages and serialize to dicts
            messages = []
            if state and hasattr(state, 'values') and 'messages' in state.values:
                for msg in state.values['messages']:
                    messages.append({"role": msg.type, "content": msg.content})

            async with aiofiles.open(conversation_filename, "w") as f:
                await f.write(json.dumps(messages, indent=2))
            logger.info(f"Conversation log saved to {conversation_filename}")
        except Exception as e:
            logger.error(f"Error saving conversation log: {e}")

        # Cancel the pipeline task to clean up resources
        await task.cancel()
        logger.info("Pipeline task cancelled.")

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        """
        Handler for when AudioBufferProcessor has accumulated audio data.
        This is called periodically to save chunks of the conversation audio.
        """
        # Safely get port for server_name, fallback to 'unknown' if not available
        port = getattr(getattr(websocket_client, 'client', None), 'port', 'unknown')
        server_name = f"server_{port}"
        # The event handler signature should provide audio, sample_rate, num_channels
        # If not, log an error
        if audio is None or sample_rate is None or num_channels is None:
            logger.error("on_audio_data handler missing required parameters: audio, sample_rate, num_channels")
            return
        await save_audio(server_name, audio, sample_rate, num_channels)

    # Initialize and run the pipeline runner
    runner = PipelineRunner(handle_sigint=False, force_gc=True)

    # Run the main pipeline task until it's cancelled
    await runner.run(task)

from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.frames.frames import TextFrame, Frame, LLMMessagesAppendFrame, OutputTransportMessageFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

class ChatSerializer(FrameSerializer):
    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, TextFrame):
            return json.dumps({"text": frame.text})
        elif isinstance(frame, OutputTransportMessageFrame):
            return json.dumps(frame.message)
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        try:
            message = json.loads(data)
            if "text" in message:
                return LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": message["text"]}],
                    run_llm=True
                )
        except Exception:
            pass
        return None

class TextToTransportProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        
        if isinstance(frame, TextFrame) and frame.text.strip():
            await self.push_frame(OutputTransportMessageFrame(message={"text": frame.text}), direction)

async def run_text_bot(websocket_client: WebSocket, stream_id: str, testing: bool,
                  customer_name: str, issue_type: str, phone_no: str, account_number: str = "Unknown"):
    """
    Runs the AI interview bot for text-based web chat, omitting STT and TTS.
    """
    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=False,
            audio_out_enabled=False,
            vad_enabled=False,
            serializer=ChatSerializer(),
        ),
    )

    langgraph_proc = LangGraphProcessor(stream_id, customer_name, phone_no, account_number, issue_type)

    pipeline = Pipeline(
        [
            transport.input(),
            langgraph_proc,
            TextToTransportProcessor(),
            transport.output(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=False, 
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.debug("Sending initial text frame to LangGraph to kick off text conversation.")
        await task.queue_frames([TextFrame(text="INIT")])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Chat Client disconnected.")
        conversation_filename = os.path.join(
            CONVERSATION_LOG_DIR,
            f"chat_{stream_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        try:
            from graph.workflow import graph
            config = {"configurable": {"thread_id": stream_id}}
            state = graph.get_state(config)
            
            messages = []
            if state and hasattr(state, 'values') and 'messages' in state.values:
                for msg in state.values['messages']:
                    messages.append({"role": msg.type, "content": msg.content})

            async with aiofiles.open(conversation_filename, "w") as f:
                await f.write(json.dumps(messages, indent=2))
            logger.info(f"Chat log saved to {conversation_filename}")
        except Exception as e:
            logger.error(f"Error saving chat log: {e}")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False, force_gc=True)
    await runner.run(task)

