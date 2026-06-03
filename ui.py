import streamlit as st
import asyncio
import websockets
import json
import base64
import requests
import os
import io
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

# Load hidden environment variables from your local .env file
load_dotenv()

st.set_page_config(page_title="Multi-Match Hub", page_icon="🏟️", layout="wide")

# Live / Testing API Configuration Endpoint Switch
API_URL = "https://v3.football.api-sports.io/fixtures?live=all"
API_KEY = os.getenv("API_SPORTS_KEY")

# Initialize session state tracking arrays
if "events_log" not in st.session_state:
    st.session_state.events_log = []
if "subscribed_fixtures" not in st.session_state:
    st.session_state.subscribed_fixtures = []  # Holds up to 3 selected fixture IDs
if "active_audio_id" not in st.session_state:
    st.session_state.active_audio_id = None

# 🚀 RATE LIMIT CONTROL: Caches match listings for 60 seconds to prevent 429 server blocks
@st.cache_data(ttl=60)
def fetch_live_matches():
    """Fetches active live games from the API sports feed with client caching."""
    try:
        headers = {"x-apisports-key": API_KEY, "User-Agent": "Mozilla/5.0"}
        response = requests.get(API_URL, headers=headers, timeout=5)
        response.raise_for_status()
        return response.json().get("response", [])
    except Exception as e:
        st.sidebar.error(f"Failed to refresh match list: {e}")
        return []

def generate_shareable_image(teams, minute, commentary_text):
    """Draws a stylized goal highlight graphic card and returns it as raw bytes."""
    img = Image.new("RGB", (800, 450), color="#111827")
    draw = ImageDraw.Draw(img)
    draw.rectangle([10, 10, 790, 440], outline="#10B981", width=5)
    
    try:
        font_title = ImageFont.load_default(size=28)
        font_sub = ImageFont.load_default(size=22)
        font_body = ImageFont.load_default(size=18)
    except Exception:
        font_title = font_sub = font_body = ImageFont.load_default()

    draw.text((40, 40), "⚽ GOAL HIGHLIGHT ALERT!", fill="#10B981", font=font_title)
    draw.text((40, 90), f"🏟️ Match: {teams}", fill="#FFFFFF", font=font_sub)
    draw.text((40, 130), f"🕒 Time-stamp: Minute {minute}'", fill="#9CA3AF", font=font_sub)
    draw.line([40, 180, 760, 180], fill="#374151", width=2)
    draw.text((40, 200), "🇳🇬 Live Pidgin Commentary Clip:", fill="#F59E0B", font=font_sub)
    
    words = commentary_text.split(" ")
    lines = []
    current_line = ""
    for word in words:
        if len(current_line + " " + word) < 65:
            current_line += " " + word if current_line else word
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
        
    y_offset = 245
    for line in lines[:5]:
        draw.text((40, y_offset), line, fill="#E5E7EB", font=font_body)
        y_offset += 30
        
    draw.text((40, 400), "Powered by Multilingual Live Match Hub AI", fill="#4B5563", font=font_body)
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()

def render_ui_feed():
    """Renders timeline cards belonging exclusively to the chosen fixture tabs."""
    match_options = {}
    for game in live_games:
        f_id = str(game["fixture"]["id"])
        home = game["teams"]["home"]["name"]
        away = game["teams"]["away"]["name"]
        h_score = game["goals"]["home"] if game["goals"]["home"] is not None else 0
        a_score = game["goals"]["away"] if game["goals"]["away"] is not None else 0
        minute = game["fixture"]["status"]["elapsed"]
        match_options[f_id] = f"{home} {h_score}-{a_score} {away} ({minute}')"

    with feed_placeholder.container():
        if not st.session_state.subscribed_fixtures:
            st.info("Your dashboard workspace is empty. Use the sidebar menu on the left to subscribe to your target live fixtures! ⚽")
            return

        tab_titles = [match_options.get(fid, f"Match ID: {fid}") for fid in st.session_state.subscribed_fixtures]
        match_tabs = st.tabs(tab_titles)

        for index, current_tab in enumerate(match_tabs):
            target_fixture_id = st.session_state.subscribed_fixtures[index]
            
            with current_tab:
                tab_events = [
                    ev for ev in st.session_state.events_log 
                    if str(ev.get("fixtureId")) == target_fixture_id
                ]
                
                if not tab_events:
                    st.info("Subscribed! Waiting for goals, cards, or changes from this match to slide in... 🕒")
                    continue
                    
                for idx, event in enumerate(tab_events):
                    ev_type = str(event.get("type", "goal")).lower()
                    minute = event['raw_event']['time']['elapsed']
                    event_unique_key = event.get('eventId', f"{target_fixture_id}_{idx}")
                    teams_heading = event.get('teams', "Live Match")
                    
                    icon = "⚽" if "goal" in ev_type else "🟥" if "red" in ev_type else "🟨" if "yellow" in ev_type else "🔄"
                        
                    with st.container(border=True):
                        st.markdown(f"### {icon} Minute {minute}'")
                        
                        lang_choice = st.radio(
                            "Select Commentary Language:",
                            options=["Pidgin 🇳🇬", "Français 🇫🇷", "Hausa 🇳🇬"],
                            horizontal=True,
                            key=f"lang_toggle_{event_unique_key}"
                        )
                        
                        st.markdown("#### Commentary:")
                        if "Pidgin" in lang_choice:
                            display_text = event['pidgin_alert']
                        elif "Français" in lang_choice:
                            display_text = event['french_alert']
                        elif "Hausa" in lang_choice:
                            display_text = event['hausa_alert']
                        st.write(display_text)
                        
                        action_col1, action_col2 = st.columns([2, 1])
                        
                        with action_col1:
                            if event.get("audio_data"):
                                is_currently_active = st.session_state.active_audio_id == event_unique_key
                                
                                if not is_currently_active:
                                    if st.button("▶️ Load Voiceover Commentary Track", key=f"btn_{event_unique_key}"):
                                        st.session_state.active_audio_id = event_unique_key
                                        st.rerun()
                                else:
                                    st.caption("🎵 Playing track...")
                                    audio_bytes = base64.b64decode(event["audio_data"])
                                    st.audio(audio_bytes, format="audio/mp3", autoplay=True)
                                    
                                    if st.button("⏹️ Stop Audio Engine Instance", key=f"stop_{event_unique_key}"):
                                        st.session_state.active_audio_id = None
                                        st.rerun()
                        
                        with action_col2:
                            if "goal" in ev_type:
                                image_data_bytes = generate_shareable_image(teams_heading, minute, event['pidgin_alert'])
                                st.download_button(
                                    label="📥 Download Clip",
                                    data=image_data_bytes,
                                    file_name=f"goal_{event_unique_key}.png",
                                    mime="image/png",
                                    key=f"share_btn_{event_unique_key}"
                                )

async def listen_to_poller():
    """Listens to WebSocket streams and forces an immediate front-end UI update."""
    uri = "ws://localhost:8080"
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                st.toast("🔌 Connected to match commentary feed stream!", icon="⚡")
                while True:
                    message = await websocket.recv()
                    payload = json.loads(message)
                    
                    if payload.get("type") == "NEW_EVENTS":
                        new_items = payload.get("data", [])
                        
                        existing_ids = {ev.get("eventId") for ev in st.session_state.events_log if ev.get("eventId")}
                        filtered_new_items = [item for item in new_items if item.get("eventId") not in existing_ids]
                        
                        if filtered_new_items:
                            st.session_state.events_log = filtered_new_items + st.session_state.events_log
                            
                            if filtered_new_items[0].get("audio_data"):
                                st.session_state.active_audio_id = filtered_new_items[0].get("eventId")
                                
                            # Force Streamlit to draw the incoming match cards live on the tab feeds
                            st.rerun()
                        
        except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, ConnectionResetError):
            await asyncio.sleep(3)

# --- SIDEBAR: MULTI-MATCH SUBSCRIPTION TRACKER ---
st.sidebar.title("➕ Subscription Control")
live_games = fetch_live_matches()

if not live_games:
    st.sidebar.info("No live matches currently in progress.")
else:
    match_options = {}
    for game in live_games:
        f_id = str(game["fixture"]["id"])
        home = game["teams"]["home"]["name"]
        away = game["teams"]["away"]["name"]
        h_score = game["goals"]["home"] if game["goals"]["home"] is not None else 0
        a_score = game["goals"]["away"] if game["goals"]["away"] is not None else 0
        minute = game["fixture"]["status"]["elapsed"]
        match_options[f_id] = f"{home} {h_score}-{a_score} {away} ({minute}')"

    available_options = [k for k in match_options.keys() if k not in st.session_state.subscribed_fixtures]

    if not available_options:
        st.sidebar.info("You have subscribed to all available matches!")
    else:
        # State-locking key preserves selector selections across multi-step layout updates
        selected_id = st.sidebar.selectbox(
            "Choose a match to track:",
            options=available_options,
            format_func=lambda x: match_options[x],
            key="match_selector_dropdown"
        )

        if st.sidebar.button("➕ Subscribe to Selected Match"):
            if len(st.session_state.subscribed_fixtures) >= 3:
                st.sidebar.warning("❌ Maximum limit reached! You can only track up to 3 matches at a time.")
            elif selected_id:
                st.session_state.subscribed_fixtures.append(selected_id)
                st.rerun()

    if st.sidebar.button("🗑️ Clear All Active Tracks"):
        st.session_state.subscribed_fixtures = []
        st.session_state.active_audio_id = None
        st.rerun()

    if st.session_state.subscribed_fixtures:
        st.sidebar.markdown("---")
        st.sidebar.subheader("📺 Currently Tracking:")
        for fid in st.session_state.subscribed_fixtures:
            if fid in match_options:
                st.sidebar.caption(f"✓ {match_options[fid]}")

# --- MAIN SCREEN LOGIC SETUP ---
st.title("🏟️ Multilingual Multi-Match Commentary Suite")
st.markdown("---")
feed_placeholder = st.empty()

if __name__ == "__main__":
    render_ui_feed()
    asyncio.run(listen_to_poller())