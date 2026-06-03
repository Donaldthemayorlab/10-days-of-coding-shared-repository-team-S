import asyncio
import websockets
import json
import requests
import os
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from azure.cognitiveservices.speech import SpeechConfig, SpeechSynthesizer, AudioConfig

# Load environment variables from your local .env file
load_dotenv()

# --- API & CLIENT CONFIGURATION ---
API_URL = "https://v3.football.api-sports.io/fixtures?live=all"
API_KEY = os.getenv("API_SPORTS_KEY")

# Initialize Gemini GenAI SDK Client
try:
    ai_client = genai.Client()
    print("[AI Engine] Gemini Fallback Client initialized successfully.")
except Exception as e:
    print(f"[AI Engine Warning] Failed to initialize Gemini Client: {e}")
    ai_client = None

# Initialize Azure Text-to-Speech Config
azure_key = os.getenv("AZURE_SPEECH_KEY")
azure_region = os.getenv("AZURE_SPEECH_REGION")
speech_config = None

if azure_key and azure_region:
    speech_config = SpeechConfig(subscription=azure_key, region=azure_region)
    # Using the beautiful Nigerian English voice profile
    speech_config.speech_synthesis_voice_name = "en-NG-EzinneNeural"
    print("[Voice Engine] Azure TTS Configured successfully.")
else:
    print("[Voice Engine Warning] Azure keys missing inside your .env file.")

# Local file storage definitions
SNAPSHOT_FILE = "snapshot.json"
TEMPLATES_FILE = "templates.json"

# Active WebSocket client connection pool tracking array
CONNECTED_CLIENTS = set()

# Load preset fallback commentary cards layout database structure
try:
    with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
        COMMENTARY_TEMPLATES = json.load(f)
    print(f"[Templates] Successfully loaded {TEMPLATES_FILE}")
except Exception as e:
    print(f"[Templates Error] Could not load template cards mapping: {e}")
    COMMENTARY_TEMPLATES = {}

# --- HELPER LOGIC FUNCTIONS ---

def load_snapshot():
    """Loads the last processed match snapshot dictionary to handle diffing logic."""
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_snapshot(data):
    """Saves the current live match state dictionary into local workspace file arrays."""
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"[Snapshot Error] Failed to write state tracking file: {e}")

def get_fallback_commentary(event_type, player_name, team_name):
    """Fallback compiler dictionary mapping values if cloud AI tools timeout."""
    templates = COMMENTARY_TEMPLATES.get(event_type, [
        "Chai! Big move on top the pitch as {player} make action for {team}!"
    ])
    import random
    selected = random.choice(templates)
    return selected.format(player=player_name, team=team_name)

def call_gemini_pidgin_engine(event_type, player_name, team_name, match_context=""):
    """Calls Gemini 2.5 Flash to generate creative, contextual Nigerian Pidgin commentary."""
    if not ai_client:
        return get_fallback_commentary(event_type, player_name, team_name)
        
    prompt = (
        f"You are a passionate, witty Nigerian football commentator broadcasting a live match. "
        f"Generate a short, explosive, 1-to-2 sentence commentary in authentic Nigerian Pidgin English "
        f"for this event: A '{event_type}' by player '{player_name}' playing for team '{team_name}'. "
        f"Context details: {match_context}. Make it incredibly raw, funny, and localized (e.g., use phrases "
        f"like 'Gbege!', 'Chai!', 'Ojoro', 'Everywhere burst!', 'No cap!'). Do not include any meta-text or tags."
    )
    
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text.strip()
    except Exception as e:
        print(f"[AI Fallback] Gemini call failed ({e}). Reverting to default template structures.")
        return get_fallback_commentary(event_type, player_name, team_name)

def generate_tts_audio_base64(text_content):
    """Synthesizes text input into a raw MP3 stream using Azure, returning a base64 string."""
    if not speech_config:
        return ""
        
    try:
        # Pull system audio output redirectors to direct bytes arrays memory variables buffers
        import base64
        buffer = io_bytes = None
        
        # We configure it to pull raw audio stream bytes output right out of memory lines
        pull_stream = AudioConfig(use_default_speaker=False)
        synthesizer = SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        
        result = synthesizer.speak_text_async(text_content).get()
        
        if result.reason.name == "SynthesizingAudioCompleted":
            raw_audio_data = result.audio_data
            return base64.b64encode(raw_audio_data).decode('utf-8')
        else:
            return ""
    except Exception as e:
        print(f"[Voice Error] Azure Speech engine processing failed: {e}")
        return ""

def process_new_event(fixture_id, event, home_team, away_team):
    """Assembles language payloads and synthesizes local speech files for any new event."""
    elapsed = event.get("time", {}).get("elapsed", 0)
    p_name = event.get("player", {}).get("name") or "Unknown Player"
    t_name = event.get("team", {}).get("name") or "Unknown Team"
    ev_type = event.get("type", "Goal").lower()
    ev_detail = event.get("detail", "")
    
    match_title = f"{home_team} vs {away_team}"
    context_string = f"Happening in the {elapsed} minute of the match between {match_title}."
    
    print(f"🔥 [Event Trigger] Processing new '{ev_type}' for {p_name} ({t_name}) at Minute {elapsed}'")
    
    # Generate Multilingual layout targets
    pidgin_text = call_gemini_pidgin_engine(ev_type, p_name, t_name, context_string)
    
    # Local placeholder fallbacks for alternative translations
    french_text = f"But! Action de {p_name} pour {t_name} à la minute {elapsed}."
    hausa_text = f"Haka ne! {p_name} ya nuna bajinta ga kungiyar {t_name} a minti na {elapsed}."
    
    # Generate Audio via Azure Speech pipeline
    audio_b64 = generate_tts_audio_base64(pidgin_text)
    
    event_payload = {
        "eventId": f"{fixture_id}_{elapsed}_{p_name.replace(' ', '_')}",
        "fixtureId": fixture_id,
        "teams": match_title,
        "type": ev_type,
        "detail": ev_detail,
        "pidgin_alert": pidgin_text,
        "french_alert": french_text,
        "hausa_alert": hausa_text,
        "audio_data": audio_b64,
        "raw_event": event
    }
    return event_payload

# --- DYNAMIC DIFFING ENGINE ---

def check_for_new_events(api_response_payload):
    """Compares incoming match responses against stored data snapshots to catch new goals."""
    old_snapshot = load_snapshot()
    new_snapshot = {}
    detected_events_list = []
    
    for match in api_response_payload:
        f_id = str(match["fixture"]["id"])
        home_team = match["teams"]["home"]["name"]
        away_team = match["teams"]["away"]["name"]
        
        # Capture ongoing list of events belonging to this fixture frame
        events = match.get("events", [])
        new_snapshot[f_id] = events
        
        # Compare incoming data arrays with our historical memory layout
        old_events = old_snapshot.get(f_id, [])
        
        if len(events) > len(old_events):
            # Isolate the newly appended event blocks
            newly_added_items = events[len(old_events):]
            for item in newly_added_items:
                processed_item = process_new_event(f_id, item, home_team, away_team)
                detected_events_list.append(processed_item)
                
    save_snapshot(new_snapshot)
    return detected_events_list

# --- CORE ASYNC LOOPS & RUNTIMES ---

async def broadcast_to_subscribers(message_dictionary):
    """Pushes compiled JSON event update arrays down the active socket pipeline."""
    if CONNECTED_CLIENTS:
        payload_string = json.dumps(message_dictionary)
        await asyncio.gather(*[client.send(payload_string) for client in CONNECTED_CLIENTS])

async def poll_fixtures():
    """Main background engine running on a continuous throttled interval loop."""
    print("[Poller Engine] Live match tracker engine starting loop intervals safely...")
    headers = {"x-apisports-key": API_KEY, "User-Agent": "Mozilla/5.0"}
    
    while True:
        try:
            # Make the network request out to the target endpoint switch loop
            response = requests.get(API_URL, headers=headers, timeout=10)
            response.raise_for_status()
            response_data = response.json().get("response", [])
            
            # Run data arrays through the diffing compiler
            new_alerts = check_for_new_events(response_data)
            
            if new_alerts:
                print(f"📣 [Broadcaster] Pushing {len(new_alerts)} fresh match event blocks to UI...")
                await broadcast_to_subscribers({
                    "type": "NEW_EVENTS",
                    "data": new_alerts
                })
                
        except Exception as e:
            print(f"[Poller Error] Failed to fetch or process data: {e}")
            
        # 🚀 RATE LIMIT SECURITY WALL: Set to 60 seconds to completely protect your API quotas!
        await asyncio.sleep(60)

async def handle_websocket_handshake(websocket):
    """Registers connected Streamlit UI views into the global broadcast pool framework."""
    CONNECTED_CLIENTS.add(websocket)
    print(f"🔌 [WebSocket] New client joined the workspace stream. Total pool: {len(CONNECTED_CLIENTS)}")
    try:
        async for message in websocket:
            # Sit open waiting for client keep-alive pings or incoming subscription arrays
            pass
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        CONNECTED_CLIENTS.remove(websocket)
        print(f"❌ [WebSocket] Client left the stream. Active pool remains: {len(CONNECTED_CLIENTS)}")

async def main():
    """Starts the WebSocket serving framework and binds the background interval poller loop."""
    server = await websockets.serve(handle_websocket_handshake, "localhost", 8080)
    print("[WebSocket] Communication server interface running cleanly on ws://localhost:8080")
    
    # Concurrently chain the long-running async worker tasks together
    await asyncio.gather(server.wait_closed(), poll_fixtures())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[System Shut-down] Poller engine closing down gracefully. Goodbye!")