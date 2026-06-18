# AI-powered Generic Customer Support Agent

An interactive, AI-powered customer support application built with [Pipecat](https://pipecat.ai/), FastAPI, and Twilio.

This project allows customers to log a support issue (e.g., Network, Call Dropping, No Signal) and immediately receive a phone call from an intelligent AI support agent tailored to resolve their specific issue. The agent communicates fluently in **Tamil**.


## Features

- **Immediate AI Callback:** Instantly triggers an outbound Twilio voice call upon form submission.
- **SQLite Database Integration:** Automatically logs all customer support requests (Name, Phone, Issue Type, Status) to a local SQLite database (`customer_care.db`).
- **Context-Aware AI Agent:** Uses **Groq** (LLaMA 3.3) to power conversational logic, and **Cartesia** for realistic TTS. The AI knows the customer's name and issue before the call even starts.
- **Tamil Language Support:** The AI customer service agent interacts entirely in Tamil to provide a localized experience.
- **Glassmorphism UI:** A beautiful, responsive, single-page web interface for logging support tickets.

## Tech Stack

- **Backend:** Python 3.12, FastAPI, Uvicorn
- **AI Framework:** Pipecat 1.2.1
- **Telephony:** Twilio (Voice API)
- **Database:** SQLite3
- **LLM:** Groq (`llama-3.3-70b-versatile`)
- **Speech-to-Text (STT):** Deepgram
- **Text-to-Speech (TTS):** Cartesia

## Prerequisites

1. A [Twilio](https://www.twilio.com/) account with a purchased phone number.
2. A [Deepgram](https://deepgram.com/) API Key.
3. A [Cartesia](https://cartesia.ai/) API Key and a valid Voice ID.
4. A [Groq](https://groq.com/) API Key.
5. [ngrok](https://ngrok.com/) installed to expose your local FastAPI server to the internet.

## Setup Instructions

1. **Clone the repository and install dependencies:**
   Ensure you have a Python virtual environment activated, then install Pipecat and FastAPI dependencies.
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables:**
   Create a `.env` file in the root directory and fill in your credentials:
   ```env
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   TWILIO_PHONE_NUMBER=+1234567890
   
   DEEPGRAM_API_KEY=your_deepgram_key
   GROQ_API_KEY=your_groq_key
   
   CARTESIA_API_KEY=your_cartesia_key
   CARTESIA_VOICE_ID=your_cartesia_voice_id
   
   SERVER_URL=https://your-ngrok-url.ngrok-free.app
   ```

3. **Start Ngrok:**
   Expose port `8765` to the internet so Twilio can reach your webhooks.
   ```bash
   ngrok http 8765
   ```
   *Make sure to update the `SERVER_URL` in your `.env` file with the generated ngrok HTTPS URL.*

4. **Run the Application:**
   Start the FastAPI server.
   ```bash
   python server.py
   ```

5. **Test the Flow:**
   - Open your browser and navigate to `http://localhost:8765`.
   - Fill out your name and phone number (format: `+1234567890`).
   - Select an issue type and click **Request AI Callback**.
   - Answer the incoming call and interact with your new AI Support Agent in Tamil!

## Project Structure

- `server.py`: The main FastAPI application handling Web UI and Twilio Webhooks (`/twiml`, `/ws`).
- `bot.py`: The Pipecat pipeline definition. Handles audio streaming, STT, LLM context aggregation, and TTS.
- `db.py`: Simple SQLite database module to initialize tables and store incoming support requests.
- `templates/support_form.html`: The frontend UI.

## Troubleshooting

- **Webhooks failing:** Ensure `SERVER_URL` in `.env` is exactly your active ngrok URL (without a trailing slash).

## Demo

<video src='https://github.com/user-attachments/assets/10a102b7-8c7b-402f-ad09-0cd3d1245ea7'/>
