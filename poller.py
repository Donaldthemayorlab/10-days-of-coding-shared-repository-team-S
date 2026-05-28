import asyncio
import json
import os
import random
import time
from datetime import datetime
import requests
import websockets
import uuid
import base64
from dotenv import load_dotenv
import azure.cognitiveservices.speech as speechsdk

# 1. Load hidden environment variables from your local .env file
load_dotenv()

# Configuration
API_URL = "https://v3.football.api-sports.io/fixtures?live=all"
API_KEY = os.getenv("API_SPORTS_KEY")
POLL_INTERVAL = 30  # Seconds between ticks
SNAPSHOT_FILE = "snapshot.json"
WS_HOST = "localhost"
WS_PORT = 8080
AUDIO_OUTPUT_DIR = "audio_outputs"

# Create audio output directory if it doesn't exist
if not os.path.exists(AUDIO_OUTPUT_DIR):
    os.makedirs(AUDIO_OUTPUT_DIR)
    print(f"[System] Created audio folder layout directory: '{AUDIO_OUTPUT_DIR}/'")

# Track connected frontend client applications
connected_clients = set()

# Load the template system bank
try:
    with open("templates.json", "r") as f:
        templates = json.load(f)
    print("[Templates] Successfully loaded templates.json")
except Exception as e:
    print(f"[Templates Warning] Could not load templates.json: {e}")
    templates = {}


def load_snapshot():
    """Load the previous snapshot from your local file memory."""
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Memory Error] Failed to read snapshot file: {e}")
    return {}


def save_snapshot(snapshot):
    """Save the current state as the baseline snapshot for the next tick."""
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        print(f"[Memory Error] Failed to save snapshot file: {e}")


def generate_contextual_pidgin_alert(event_type, event_data, fixture_payload):
    """Python version of pickTemplate with context-aware evaluation features."""
    minute = event_data.get("minute", 0)
    
    # Calculate score gaps straight from live match tokens
    goals_info = fixture_payload.get("goals", {})
    home_score = goals_info.get("home") if goals_info.get("home") is not None else 0
    away_score = goals_info.get("away") if goals_info.get("away") is not None else 0
    score_delta = abs(home_score - away_score)

    # Trigger premium commentary strings for late match-saving equalizers
    if event_type == "goal" and minute >= 85 and score_delta == 0:
        variants = templates.get("late_equalizer")
        print("🔥 [Context Engine] High drama detected! Using late equalizer templates.")
    else:
        variants = templates.get(event_type)

    if not variants:
        return f"Match event happen ({event_type})!"

    chosen_template = random.choice(variants)
    try:
        return chosen_template.format(**event_data)
    except KeyError:
        return chosen_template


def translate_pidgin_text(text):
    """
    Sends the generated Pidgin text to Azure Translator.
    Returns a dictionary containing French and Hausa translations.
    """
    key = os.getenv("AZURE_TRANSLATOR_KEY")
    region = os.getenv("AZURE_REGION", "global")
    endpoint = "https://api.cognitive.microsofttranslator.com/translate"

    params = {
        "api-version": "3.0",
        "from": "en",
        "to": ["fr", "ha"]
    }

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Ocp-Apim-Subscription-Region": region,
        "Content-type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4())
    }

    body = [{"text": text}]

    try:
        response = requests.post(endpoint, params=params, headers=headers, json=body, timeout=5)
        response.raise_for_status()
        translations_data = response.json()[0]["translations"]

        translations = {"french": "", "hausa": ""}
        for item in translations_data:
            if item["to"] == "fr":
                translations["french"] = item["text"]
            elif item["to"] == "ha":
                translations["hausa"] = item["text"]
                
        return translations

    except Exception as e:
        print(f"[Azure Translator Error] Failed to fetch translation: {e}")
        return {"french": "[Translation Error]", "hausa": "[Translation Error]"}


def generate_tts_audio(text, event_id):
    """
    Sends Pidgin text to Azure Speech using the en-NG-EzinneNeural voice.
    Saves the resulting audio file locally and returns its base64 encoded string.
    """
    speech_key = os.getenv("AZURE_SPEECH_KEY")
    speech_region = os.getenv("AZURE_SPEECH_REGION")

    if not speech_key or not speech_region:
        print("[Azure TTS Error] Missing Speech Key or Region configuration credentials.")
        return None

    # Setup file output path
    output_filename = f"{event_id}.mp3"
    file_path = os.path.join(AUDIO_OUTPUT_DIR, output_filename)

    # Configure Azure Speech Engine Settings
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
    speech_config.speech_synthesis_voice_name = "en-NG-EzinneNeural"
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3)
    
    # Configure file routing
    audio_config = speechsdk.audio.AudioOutputConfig(filename=file_path)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)

    try:
        result = synthesizer.speak_text_async(text).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            print(f"🎙️  [Azure TTS] Successfully generated audio voiceover: {file_path}")
            
            # Read the audio file and convert to a Base64 string so it can pass cleanly over WebSockets
            with open(file_path, "rb") as audio_file:
                encoded_string = base64.b64encode(audio_file.read()).decode("utf-8")
            
            return {
                "file_path": file_path,
                "audio_base64": encoded_string
            }
        else:
            print(f"[Azure TTS Error] Audio synthesis failed. Reason: {result.reason}")
            # --- ADD THIS LOG SNARE TO SNIFF THE HIDDEN ERROR CODE ---
            if result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = result.cancellation_details
                print(f"❌ [Cancellation Error Details]: {cancellation_details.error_details}")
                print(f"❌ [Cancellation Error Code]: {cancellation_details.error_code}")
            return None

    except Exception as e:
        print(f"[Azure TTS Error] Failed during speech compilation execution: {e}")
        return None


async def register_client(websocket):
    """Register/unregister incoming frontend WebSocket connections."""
    connected_clients.add(websocket)
    print(f"[WebSocket] Frontend client connected. Total: {len(connected_clients)}")
    try:
        await websocket.wait_closed()
    finally:
        connected_clients.remove(websocket)
        print(f"[WebSocket] Frontend client disconnected. Total: {len(connected_clients)}")


async def broadcast_to_frontend(events):
    """Stream detected events to all connected web applications concurrently."""
    if not events or not connected_clients:
        return
    message = json.dumps({"type": "NEW_EVENTS", "data": events})
    await asyncio.gather(
        *[client.send(message) for client in connected_clients],
        return_exceptions=True,
    )
    print(f"[WebSocket] Broadcasted {len(events)} new event(s) to the frontend.")


async def poll_fixtures():
    """Main background engine running on a continuous 30s interval loop."""
    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"\n[Poller] Fetching latest live fixtures... ({current_time})")

        try:
            headers = {
                "x-apisports-key": API_KEY,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }

            response = requests.get(API_URL, headers=headers, timeout=10)
            response.raise_for_status()

            api_data = response.json()
            current_fixtures = api_data.get("response", [])

            old_snapshot = load_snapshot()
            detected_events = []
            new_snapshot = {}

            for fixture in current_fixtures:
                fixture_info = fixture.get("fixture", {})
                fixture_id = str(fixture_info.get("id"))

                if not fixture_id or fixture_id == "None":
                    continue

                current_events = fixture.get("events", [])
                new_snapshot[fixture_id] = current_events

                # Diff Engine Logic - Protected with startup gate condition
                if old_snapshot and fixture_id in old_snapshot:
                    old_event_signatures = {
                        f"{event.get('time', {}).get('elapsed')}_{event.get('team', {}).get('id')}_{event.get('type')}_{event.get('detail')}"
                        for event in old_snapshot[fixture_id]
                    }

                    for event in current_events:
                        event_sig = f"{event.get('time', {}).get('elapsed')}_{event.get('team', {}).get('id')}_{event.get('type')}_{event.get('detail')}"

                        if event_sig not in old_event_signatures:
                            teams_info = fixture.get("teams", {})
                            raw_type = str(event.get("type", "")).lower().replace(" ", "_")
                            team_name = event.get("team", {}).get("name") or "Team"

                            # --- Normalization Mappers ---
                            if raw_type == "subst":
                                raw_type = "substitution"
                            elif raw_type == "card":
                                raw_detail = str(event.get("detail", "")).lower()
                                if "yellow" in raw_detail:
                                    raw_type = "yellow_card"
                                elif "red" in raw_detail:
                                    raw_type = "red_card"
                                else:
                                    raw_type = "yellow_card"

                            # Contextual player safety fallback string assignments
                            actual_player = event.get("player", {}).get("name")
                            player_fallback = actual_player if actual_player else f"One {team_name} player"
                            
                            actual_assist = event.get("assist", {}).get("name")
                            assist_fallback = actual_assist if actual_assist else "On"

                            template_variables = {
                                "scorer": player_fallback,
                                "player": player_fallback,
                                "player_out": player_fallback,
                                "player_in": assist_fallback,
                                "minute": event.get("time", {}).get("elapsed") or 0,
                                "team": team_name,
                            }

                            # Unique Event Identity Token for filename assignments
                            event_id = str(uuid.uuid4())[:8]

                            # -----------------------------------------------------------
                            # 🚀 THE 4-STAGE PIPELINE WIREFRAME
                            # -----------------------------------------------------------
                            
                            # STAGE 1: Generate high-energy Pidgin commentary string baseline
                            pidgin_message = generate_contextual_pidgin_alert(
                                raw_type, template_variables, fixture
                            )

                            # STAGE 2: Request multilingual translations from Azure cloud
                            print(f"🔄 Translating text via Azure Translator...")
                            translations = translate_pidgin_text(pidgin_message)

                            # STAGE 3: Compile Speech Voiceover Audio via Azure Speech
                            print(f"🔊 Compiling audio speech file via Azure TTS...")
                            audio_payload = generate_tts_audio(pidgin_message, event_id)
                            
                            audio_base64_str = audio_payload["audio_base64"] if audio_payload else None

                            # STAGE 4: Package into feed entry structure list array
                            detected_events.append(
                                {
                                    "eventId": event_id,
                                    "fixtureId": fixture_id,
                                    "teams": f"{teams_info.get('home', {}).get('name')} vs {teams_info.get('away', {}).get('name')}",
                                    "type": raw_type,
                                    "pidgin_alert": pidgin_message,
                                    "french_alert": translations["french"],
                                    "hausa_alert": translations["hausa"],
                                    "audio_data": audio_base64_str,  # Directly streams down to UI player elements
                                    "raw_event": event,
                                }
                            )

            # Rewrite baseline memory log file
            save_snapshot(new_snapshot)

            if detected_events:
                print(f"[Diff] Detected {len(detected_events)} new event(s)!")
                for item in detected_events:
                    print(f"\n🏟️  [{item['teams']}]")
                    print(f"🇳🇬 PIDGIN: {item['pidgin_alert']}")
                    print(f"🇫🇷 FRENCH: {item['french_alert']}")
                    print(f"🇳🇬 HAUSA:  {item['hausa_alert']}")
                    if item["audio_data"]:
                        print(f"🎵 AUDIO SOUND: Compiled successfully ({item['eventId']}.mp3 saved!)")
                await broadcast_to_frontend(detected_events)
            else:
                print("[Diff] No new events detected on this tick.")

        except Exception as e:
            print(f"[Poller Error] Failed to fetch or process data: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def main():
    """Initializes background loop task servers."""
    async with websockets.serve(register_client, WS_HOST, WS_PORT):
        print(f"[WebSocket] Server running on ws://{WS_HOST}:{WS_PORT}")
        await poll_fixtures()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Poller] Shutting down gracefully.")