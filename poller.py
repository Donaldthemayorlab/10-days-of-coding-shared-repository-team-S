import asyncio
import json
import os
from dotenv import load_dotenv
import time
from datetime import datetime
import requests
import websockets

load_dotenv()

# Configuration
API_URL = "https://v3.football.api-sports.io/fixtures?live=all"
API_KEY = os.getenv("API_SPORTS_KEY")  
POLL_INTERVAL = 30  
SNAPSHOT_FILE = "snapshot.json"
WS_HOST = "localhost"
WS_PORT = 8080

connected_clients = set()

def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Error] Failed to read snapshot file: {e}")
    return {}

def save_snapshot(snapshot):
    try:
        with open(SNAPSHOT_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        print(f"[Error] Failed to save snapshot file: {e}")

async def register_client(websocket):
    connected_clients.add(websocket)
    print(f"[WebSocket] Frontend client connected. Total: {len(connected_clients)}")
    try:
        await websocket.wait_closed()
    finally:
        connected_clients.remove(websocket)
        print(f"[WebSocket] Frontend client disconnected. Total: {len(connected_clients)}")

async def broadcast_to_frontend(events):
    if not events or not connected_clients:
        return
    message = json.dumps({"type": "NEW_EVENTS", "data": events})
    await asyncio.gather(
        *[client.send(message) for client in connected_clients],
        return_exceptions=True
    )
    print(f"[WebSocket] Broadcasted {len(events)} new event(s) to frontend.")

async def poll_fixtures():
    while True:
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"\n[Poller] Fetching latest live fixtures... ({current_time})")

        try:
            # Added User-Agent identity alongside your key to pass security firewalls
            headers = {
                'x-apisports-key': API_KEY,
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
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

                # FIXED: Corrected the loop variable context from line 97-99 here
                if fixture_id in old_snapshot:
                    old_event_signatures = {
                        f"{event.get('time', {}).get('elapsed')}_{event.get('team', {}).get('id')}_{event.get('type')}_{event.get('detail')}"
                        for event in old_snapshot[fixture_id]
                    }

                    for event in current_events:
                        event_sig = f"{event.get('time', {}).get('elapsed')}_{event.get('team', {}).get('id')}_{event.get('type')}_{event.get('detail')}"
                        
                        if event_sig not in old_event_signatures:
                            teams_info = fixture.get("teams", {})
                            detected_events.append({
                                "fixtureId": fixture_id,
                                "teams": f"{teams_info.get('home', {}).get('name')} vs {teams_info.get('away', {}).get('name')}",
                                "event": event
                            })

            save_snapshot(new_snapshot)

            if detected_events:
                print(f"[Diff] Detected {len(detected_events)} new event(s)!")
                print(json.dumps(detected_events, indent=2))
                await broadcast_to_frontend(detected_events)
            else:
                print("[Diff] No new events detected on this tick.")

        except Exception as e:
            print(f"[Poller Error] Failed to fetch or process data: {e}")

        await asyncio.sleep(POLL_INTERVAL)

async def main():
    async with websockets.serve(register_client, WS_HOST, WS_PORT):
        print(f"[WebSocket] Server running on ws://{WS_HOST}:{WS_PORT}")
        await poll_fixtures()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Poller] Shutting down gracefully.")