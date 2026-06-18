# server.py
import argparse
import aiohttp
import json
import os
import urllib.error
import urllib.request
import uvicorn
import asyncio
from contextlib import asynccontextmanager
import uuid
import sqlite3
from typing import Optional
from bot import run_bot, run_text_bot
from fastapi import FastAPI, WebSocket, Request, Form, BackgroundTasks, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
from loguru import logger
from twilio.rest import Client
from pydantic import BaseModel
import db
from verification import build_verification_state, verification_prompt, verification_success_prompt

load_dotenv(override=True)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Generic Customer Support server...")
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")
FRONTEND_DIST_DIR = os.path.join(os.path.dirname(__file__), "frontend", "dist")
FRONTEND_ASSETS_DIR = os.path.join(FRONTEND_DIST_DIR, "assets")

if os.path.isdir(FRONTEND_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS_DIR), name="frontend-assets")

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

call_data_store = {}
call_status_store = {}


def _normalize_server_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


def _discover_ngrok_url() -> str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return ""

    for tunnel in payload.get("tunnels", []):
        public_url = _normalize_server_url(tunnel.get("public_url", ""))
        if public_url.startswith("https://"):
            return public_url
    return ""


def get_public_server_url() -> str:
    live_ngrok_url = _discover_ngrok_url()
    if live_ngrok_url:
        return live_ngrok_url
    configured_url = _normalize_server_url(os.getenv("SERVER_URL", ""))
    if "ngrok-free.dev" in configured_url or "ngrok.app" in configured_url:
        return ""
    return configured_url


def get_public_url_status() -> dict:
    live_ngrok_url = _discover_ngrok_url()
    configured_url = _normalize_server_url(os.getenv("SERVER_URL", ""))
    effective_url = live_ngrok_url or (
        "" if ("ngrok-free.dev" in configured_url or "ngrok.app" in configured_url) else configured_url
    )
    return {
        "configured_url": configured_url,
        "live_ngrok_url": live_ngrok_url,
        "effective_url": effective_url,
        "ready": bool(effective_url),
    }


async def transcribe_browser_audio(audio_bytes: bytes, content_type: str) -> str:
    deepgram_api_key = os.getenv("DEEPGRAM_API_KEY")
    if not deepgram_api_key:
        raise ValueError("DEEPGRAM_API_KEY environment variable is required.")

    url = (
        "https://api.deepgram.com/v1/listen"
        "?model=nova-2&language=en-IN&smart_format=true&punctuate=true"
    )
    headers = {
        "Authorization": f"Token {deepgram_api_key}",
        "Content-Type": content_type or "application/octet-stream",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=audio_bytes, timeout=aiohttp.ClientTimeout(total=45)) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise ValueError(payload.get("err_msg") or payload.get("message") or "Deepgram transcription failed.")

    try:
        return (
            payload["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        )
    except (KeyError, IndexError, TypeError):
        return ""


def frontend_index_response() -> FileResponse | None:
    index_path = os.path.join(FRONTEND_DIST_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return None

@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    frontend_response = frontend_index_response()
    if frontend_response:
        return frontend_response
    return templates.TemplateResponse(request=request, name="support_form.html")

@app.get("/support", response_class=HTMLResponse)
async def support_page(request: Request):
    frontend_response = frontend_index_response()
    if frontend_response:
        return frontend_response
    return templates.TemplateResponse(request=request, name="support_form.html")

@app.get("/favicon.svg")
async def frontend_favicon():
    favicon_path = os.path.join(FRONTEND_DIST_DIR, "favicon.svg")
    if os.path.isfile(favicon_path):
        return FileResponse(favicon_path)
    return JSONResponse(status_code=404, content={"error": "favicon not found"})

@app.get("/customers", response_class=HTMLResponse)
async def customers_page(request: Request):
    frontend_response = frontend_index_response()
    if frontend_response:
        return frontend_response
    return templates.TemplateResponse(request=request, name="customers.html")

class CustomerUpdate(BaseModel):
    name: str
    phone_no: str
    account_number: str
    account_status: str
    current_plan: str
    plan_expiry: str
    email: str = ""
    address: str = ""
    service_type: str = "Prepaid"
    kyc_verified: bool = False
    data_balance: str = "0 GB"
    billing_cycle: str = ""

@app.get("/api/customers")
async def api_get_customers():
    customers = db.get_all_customers()
    return JSONResponse(customers)

@app.post("/api/customers")
async def api_add_customer(customer: CustomerUpdate):
    try:
        customer_id = db.add_customer(customer.dict())
        return JSONResponse({"success": True, "id": customer_id})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.put("/api/customers/{customer_id}")
async def api_update_customer(customer_id: int, customer: CustomerUpdate):
    try:
        db.update_customer(customer_id, customer.dict())
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.delete("/api/customers/{customer_id}")
async def api_delete_customer(customer_id: int):
    try:
        db.delete_customer(customer_id)
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/database")
async def api_get_database():
    conn = sqlite3.connect("customer_care.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    data = {}
    tables = ["customers", "support_requests", "faq", "chat_messages"]
    for table in tables:
        try:
            cursor.execute(f"SELECT * FROM {table}")
            data[table] = [dict(row) for row in cursor.fetchall()]
        except:
            data[table] = []
            
    conn.close()
    return JSONResponse(data)

@app.get("/api/agents")
async def api_get_agents():
    # Import agent configurations dynamically
    from graph.agents.supervisor import supervisor_prompt
    from graph.agents.specialists import (
        account_prompt, billing_prompt, general_prompt,
        order_prompt, product_prompt, technical_prompt
    )
    from graph.prompts.verification import verification_prompt as verification_agent_prompt
    import graph.tools as t
    
    # Map tools to names
    def tool_names(tool_list):
        return [tool.name for tool in tool_list]
        
    agents_data = {
        "supervisor": {
            "name": "Supervisor Agent",
            "type": "Router / Intent Analyzer",
            "prompt": supervisor_prompt,
            "tools": []
        },
        "verification": {
            "name": "Verification Agent",
            "type": "Identity Verification / Gatekeeper",
            "prompt": verification_agent_prompt,
            "tools": []
        },
        "technical": {
            "name": "Technical Support Specialist",
            "type": "Specialist",
            "prompt": technical_prompt,
            "tools": ["remember_user_preference", "log_customer_issue"]
        },
        "billing": {
            "name": "Billing Support Specialist",
            "type": "Specialist",
            "prompt": billing_prompt,
            "tools": ["remember_user_preference", "log_customer_issue"]
        },
        "account": {
            "name": "Account Support Specialist",
            "type": "Specialist",
            "prompt": account_prompt,
            "tools": ["remember_user_preference", "log_customer_issue"]
        },
        "product": {
            "name": "Product Support Specialist",
            "type": "Specialist",
            "prompt": product_prompt,
            "tools": ["remember_user_preference", "log_customer_issue"]
        },
        "order": {
            "name": "Order Support Specialist",
            "type": "Specialist",
            "prompt": order_prompt,
            "tools": ["remember_user_preference", "log_customer_issue"]
        },
        "general": {
            "name": "General Support & Ticketing Specialist",
            "type": "Specialist",
            "prompt": general_prompt,
            "tools": ["remember_user_preference", "log_customer_issue"]
        }
    }
    return JSONResponse(agents_data)


@app.get("/api/callback-health")
async def api_callback_health():
    return JSONResponse(get_public_url_status())


@app.post("/api/transcribe-browser-audio")
async def api_transcribe_browser_audio(file: UploadFile = File(...)):
    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            return JSONResponse(status_code=400, content={"error": "No audio data received."})

        transcript = await transcribe_browser_audio(audio_bytes, file.content_type or "application/octet-stream")
        return {"text": transcript}
    except Exception as e:
        logger.error(f"Error transcribing browser audio: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    frontend_response = frontend_index_response()
    if frontend_response:
        return frontend_response
    return templates.TemplateResponse(request=request, name="agents.html")

@app.post("/request-callback")
async def request_callback(
    phone_no: str = Form(...)
):
    """Save request to DB and initiate immediate callback via Twilio"""
    try:
        # Pre-fetch account
        customer_details = db.get_customer_by_phone(phone_no) if phone_no else None
        name = customer_details["name"] if customer_details else "Customer"
        issue_type = "Unknown Issue - Please ask the user to explain their problem."

        # 1. Save to DB
        request_id = db.create_support_request(name, phone_no, issue_type)
        logger.info(f"Saved support request #{request_id} for {name}")

        # 2. Initiate Call via Twilio
        public_url_status = get_public_url_status()
        server_url = public_url_status["effective_url"]
        if not server_url:
            raise ValueError(
                "No live public callback URL is available. Start ngrok and keep it running before placing a call."
            )

        twiml_url = f"{server_url}/twiml"

        twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        logger.info(f"Creating outbound call from {twilio_phone_number} to {phone_no}")

        call = twilio_client.calls.create(
            to=phone_no,
            from_=twilio_phone_number,
            url=twiml_url,
            method="POST",
            status_callback=f"{server_url}/call-status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            timeout=55,
        )

        call_sid = call.sid
        logger.info(f"Outbound customer care call initiated: {call_sid} to {phone_no}")
        call_status_store[call_sid] = {
            "call_sid": call_sid,
            "status": getattr(call, "status", None) or "queued",
            "error_code": None,
            "error_message": None,
            "sip_response_code": None,
            "duration": None,
            "to": phone_no,
            "from": twilio_phone_number,
            "timestamp": None,
        }

        # Store data needed by the bot, keyed by call SID
        call_data_store[call_sid] = {
            "customer_name": name,
            "phone_no": phone_no,
            "issue_type": issue_type
        }

        return {
            "status": "success",
            "message": "Callback initiated. You will receive a call shortly.",
            "call_sid": call_sid,
            "from": twilio_phone_number,
        }

    except ValueError as e:
        logger.warning(f"Callback request blocked: {str(e)}")
        return JSONResponse(status_code=503, content={"error": str(e)})
    except Exception as e:
        logger.error(f"Error initiating callback: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

chat_sessions = {}

class ChatRequestMessage(BaseModel):
    stream_id: str
    message: str

@app.post("/request-chat")
async def request_chat(
    phone_no: str = Form(""),
    identifier: str = Form("")
):
    """Initiate a web chat session using LangGraph"""
    try:
        # Pre-fetch account
        lookup_value = identifier or phone_no
        customer_details = db.get_customer_by_identifier(lookup_value) if lookup_value else None
        if customer_details:
            phone_no = customer_details["phone_no"]
        elif not phone_no:
            phone_no = lookup_value or "Unknown"
        name = customer_details["name"] if customer_details else "Customer"
        account_number = customer_details["account_number"] if customer_details else "Unknown"
        
        # Inject an unknown issue type to prompt discovery
        issue_type = "Unknown Issue - Please ask the user to explain their problem."

        request_id = db.create_support_request(name, phone_no, issue_type)
        logger.info(f"Saved support request #{request_id} for {name} (Chat Mode)")

        stream_id = str(uuid.uuid4())
        
        from graph.workflow import graph
        
        config = {"configurable": {"thread_id": stream_id}}
        
        initial_state = build_verification_state(
            customer_name=name,
            phone_number=phone_no,
            account_number=account_number,
            customer_identifier=lookup_value,
        ) | {
            "issue_type": issue_type,
            "messages": [],
        }
        
        chat_sessions[stream_id] = initial_state

        return {"status": "success", "stream_id": stream_id}
    except Exception as e:
        logger.error(f"Error initiating chat: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/chat")
async def chat_endpoint(request: ChatRequestMessage):
    """Stateless chat endpoint for handling messages directly via LangGraph"""
    stream_id = request.stream_id
    message = request.message
    
    from graph.workflow import graph
    from langchain_core.messages import HumanMessage
    
    config = {"configurable": {"thread_id": stream_id}}
    
    try:
        # Check if we have initial state to inject
        if stream_id in chat_sessions:
            state_update = chat_sessions.pop(stream_id)
            phone_no = state_update.get("phone_number", "Unknown")
            
            if message == "INIT":
                graph.update_state(config, state_update)
                if state_update.get("is_verified"):
                    greeting = verification_success_prompt(state_update.get("customer_name"))
                    agent_name = "Verification Agent"
                else:
                    greeting = verification_prompt()
                    agent_name = "Verification Agent"
                db.save_chat_message(stream_id, phone_no, "agent", greeting)
                return {
                    "text": greeting,
                    "agent": agent_name,
                    "conversation_complete": False,
                }
            else:
                db.save_chat_message(stream_id, phone_no, "user", message)
                # Pass phone_number explicitly just in case state update dropped it
                state_update["messages"] = [HumanMessage(content=message)]
                result = graph.invoke(state_update, config=config)
        else:
            # Reliably get phone_no from DB
            phone_no = db.get_phone_by_stream_id(stream_id)
            
            if message == "INIT":
                current_state = graph.get_state(config)
                state_values = current_state.values if current_state and hasattr(current_state, "values") else {}
                if state_values.get("is_verified"):
                    greeting = verification_success_prompt(state_values.get("customer_name"))
                else:
                    greeting = verification_prompt()
                db.save_chat_message(stream_id, phone_no, "agent", greeting)
                return {
                    "text": greeting,
                    "agent": "Verification Agent",
                    "conversation_complete": False,
                }
            
            db.save_chat_message(stream_id, phone_no, "user", message)
            
            # Explicitly pass phone_number to the graph payload so it's never lost
            invoke_payload = {
                "messages": [HumanMessage(content=message)],
                "phone_number": phone_no
            }
            # Also get customer name if possible to keep it in state
            customer_details = db.get_customer_by_phone(phone_no) if phone_no != "Unknown" else None
            if customer_details:
                invoke_payload["customer_name"] = customer_details["name"]
                invoke_payload["account_number"] = customer_details["account_number"]
                
            result = graph.invoke(invoke_payload, config=config)
            
        last_message = result["messages"][-1]
        reply = last_message.content if hasattr(last_message, "content") else str(last_message)
        
        agent_id = result.get("next_agent", "supervisor")
        agent_display_names = {
            "verification": "Verification Agent",
            "technical": "Technical Agent",
            "billing": "Billing Agent",
            "account": "Account Agent",
            "product": "Product Agent",
            "order": "Order Agent",
            "general": "General Support Agent",
            "supervisor": "GenericAI Orchestrator",
            "FINISH": "GenericAI"
        }
        display_name = agent_display_names.get(agent_id, "GenericAI")
        
        db.save_chat_message(stream_id, phone_no, "agent", reply)
        return {
            "text": reply,
            "agent": display_name,
            "conversation_complete": agent_id == "FINISH",
        }
        
    except Exception as e:
        logger.error(f"Error in LangGraph chat generation: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/history/llm")
async def history_llm_page(request: Request):
    """Admin page to view all raw LangGraph checkpoints"""
    conn = sqlite3.connect("langgraph_checkpoints.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT thread_id, checkpoint_id, type, checkpoint, metadata FROM checkpoints ORDER BY thread_id DESC, checkpoint_id DESC LIMIT 100")
        rows = cursor.fetchall()
    except Exception as e:
        rows = []
    finally:
        conn.close()

    # Convert binary data to string representation for safe HTML rendering
    data = []
    for row in rows:
        cp = row['checkpoint']
        meta = row['metadata']
        data.append({
            "thread_id": row["thread_id"],
            "checkpoint_id": row["checkpoint_id"],
            "type": row["type"],
            "checkpoint_preview": repr(cp)[:500] + "..." if cp and len(repr(cp)) > 500 else repr(cp),
            "metadata_preview": repr(meta)[:500] + "..." if meta and len(repr(meta)) > 500 else repr(meta)
        })

    return templates.TemplateResponse(
        request=request,
        name="llm_history.html",
        context={"data": data}
    )

@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    frontend_response = frontend_index_response()
    if frontend_response:
        return frontend_response
    return templates.TemplateResponse(request=request, name="history.html")

@app.get("/api/history")
async def api_get_history():
    history = db.get_chat_history()
    return JSONResponse(history)


@app.api_route("/twiml", methods=["GET", "POST"])
async def get_twiml(request: Request):
    """Return TwiML for outbound calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    logger.info(f"Serving TwiML for outbound call, CallSid: {call_sid}, Status: {call_status}")

    public_base = get_public_server_url()
    if not public_base:
        public_base = str(request.base_url).strip().rstrip("/")
    websocket_url = public_base.replace("https://", "wss://").replace("http://", "ws://")

    twiml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Please hold while we connect you to our AI support agent.</Say>
    <Connect>
        <Stream url="{websocket_url}/ws"></Stream>
    </Connect>
    <Pause length="40"/>
</Response>"""

    return HTMLResponse(content=twiml_content, media_type="application/xml")

@app.post("/call-status")
async def call_status_callback(request: Request):
    """Receive status callbacks from Twilio for debugging"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    call_status = form_data.get("CallStatus")
    error_code = form_data.get("ErrorCode")
    error_msg = form_data.get("ErrorMessage") 
    sip_response_code = form_data.get("SipResponseCode")
    duration = form_data.get("Duration") or form_data.get("CallDuration")
    status_update = {
        "call_sid": call_sid,
        "status": call_status,
        "error_code": error_code,
        "error_message": error_msg,
        "sip_response_code": sip_response_code,
        "duration": duration,
        "to": form_data.get("To"),
        "from": form_data.get("From"),
        "timestamp": form_data.get("Timestamp"),
    }
    if call_sid:
        call_status_store[call_sid] = status_update
    logger.info(
        "Call Status Update - SID: {}, Status: {}, SIP: {}, Duration: {}, Error: {} {}",
        call_sid,
        call_status,
        sip_response_code,
        duration,
        error_code,
        error_msg,
    )
    return {"received": True}

@app.get("/call-status/{call_sid}")
async def get_call_status(call_sid: str):
    """Return the latest Twilio status callback received for a call."""
    status = call_status_store.get(call_sid)
    if not status:
        try:
            call = twilio_client.calls(call_sid).fetch()
            status = {
                "call_sid": call.sid,
                "status": call.status,
                "error_code": getattr(call, "error_code", None),
                "error_message": None,
                "sip_response_code": None,
                "duration": call.duration,
                "to": call.to,
                "from": call._from,
                "timestamp": str(call.date_updated or call.date_created),
            }
            call_status_store[call_sid] = status
        except Exception as e:
            logger.warning(f"Unable to fetch Twilio call status for {call_sid}: {e}")
            return {
                "call_sid": call_sid,
                "status": "pending",
                "error_code": None,
                "error_message": "Waiting for Twilio status callback.",
                "sip_response_code": None,
                "duration": None,
                "to": None,
                "from": None,
                "timestamp": None,
            }
    return status


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections from Twilio media streams"""
    await websocket.accept()
    logger.info("WebSocket connection accepted.")

    call_control_id = None
    try:
        # Twilio sends a connected event followed by a start event
        await websocket.receive_text()
        start_message = await websocket.receive_text()

        start_data = json.loads(start_message)

        # Extract stream ID and call SID from the start message
        stream_id = start_data["start"]["streamSid"]
        call_sid = start_data["start"]["callSid"]

        # Try to extract call_control_id if available in custom parameters
        call_control_id = start_data["start"].get("customParameters", {}).get("call_control_id", call_sid)

        call_info = call_data_store.pop(call_sid, None)
        if not call_info:
            logger.warning(f"No call data found for CallSid: {call_sid}. Using default values.")
            call_info = {"customer_name": "Customer", "phone_no": "", "issue_type": "General"}

        customer_name = call_info.get("customer_name", "Customer")
        phone_no = call_info.get("phone_no", "")
        issue_type = call_info.get("issue_type", "General")
        
        # Pre-fetch customer details based on phone number
        customer_details = db.get_customer_by_phone(phone_no) if phone_no else None
        account_number = customer_details["account_number"] if customer_details else "Unknown"

        testing = getattr(app.state, "testing", False)

        await run_bot(
            websocket,
            stream_id,
            testing,
            customer_name,
            issue_type,
            phone_no,
            account_number,
            call_control_id=call_control_id,
        )

    except Exception as e:
        logger.error(f"Error in WebSocket endpoint for call {call_control_id}: {str(e)}")
        await websocket.close(code=1011)

@app.websocket("/web-chat")
async def web_chat_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for the browser-based text chat"""
    await websocket.accept()
    logger.info("Web Chat WebSocket connection accepted.")

    try:
        stream_id = websocket.query_params.get("stream_id")
        if not stream_id:
            logger.error("No stream_id provided for web chat")
            await websocket.close(code=1008)
            return

        call_info = call_data_store.pop(stream_id, None)
        if not call_info:
            logger.warning(f"No call data found for stream_id: {stream_id}. Using default values.")
            call_info = {"customer_name": "Customer", "phone_no": "", "issue_type": "General"}

        customer_name = call_info.get("customer_name", "Customer")
        phone_no = call_info.get("phone_no", "")
        issue_type = call_info.get("issue_type", "General")
        
        customer_details = db.get_customer_by_phone(phone_no) if phone_no else None
        account_number = customer_details["account_number"] if customer_details else "Unknown"

        testing = getattr(app.state, "testing", False)
        
        await run_text_bot(
            websocket,
            stream_id,
            testing,
            customer_name,
            issue_type,
            phone_no,
            account_number
        )

    except Exception as e:
        logger.error(f"Error in Web Chat endpoint for stream {stream_id}: {str(e)}")
        await websocket.close(code=1011)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipecat Twilio Outbound Call Server")
    parser.add_argument("-t", "--test", action="store_true", default=False)
    args, _ = parser.parse_known_args()

    app.state.testing = args.test

    required_vars = [
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER", "SERVER_URL",
        "GROQ_API_KEY", "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "CARTESIA_VOICE_ID"
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {missing_vars}")
        exit(1)

    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=True)
