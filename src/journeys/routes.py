from fastapi import APIRouter, BackgroundTasks, status, HTTPException
from src.journeys.models import JourneyRequest, PlaylistRequest
import googlemaps
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime
import os
from os import getenv
import asyncio
import aiosqlite  # Use aiosqlite instead of sqlite3



journey_router = APIRouter()



GOOGLE_MAPS_API_KEY = getenv("GOOGLE_MAPS_API_KEY")

SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = "http://localhost:8081/callback"

# Database path
DB_PATH = "history.db"

async def get_db_connection():
    """Get an asynchronous SQLite connection using aiosqlite."""
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        yield conn 

# Start the journey
@journey_router.post("/start-journey/")
async def start_journey(journey: JourneyRequest, background_tasks: BackgroundTasks):
    # Initialize Google Maps client inside the thread
    directions_result = await asyncio.to_thread(
        lambda: googlemaps.Client(key=GOOGLE_MAPS_API_KEY).directions(journey.origin, journey.destination, mode="driving")
    )
    if not directions_result:
        raise HTTPException(status_code=404, detail="Journey not found")

    # Extract journey details
    journey_time = directions_result[0]['legs'][0]['duration']['value']
    origin = directions_result[0]['legs'][0]['start_address']
    destination = directions_result[0]['legs'][0]['end_address']

    # Open a new database connection and save the journey details
    async for conn in get_db_connection():
        cursor = await conn.execute(
            'INSERT INTO journeys (user_id, origin, destination, journey_time, created_at) VALUES (?, ?, ?, ?, ?)',
            ("user_id_placeholder", origin, destination, journey_time, datetime.now())
        )
        await conn.commit()
        journey_id = cursor.lastrowid  # Get the last inserted ID

    # Start background task to monitor the journey
    background_tasks.add_task(monitor_journey, journey_id)

    # Return response
    return {"origin": origin, "destination": destination, "journey_time": journey_time, "journey_id": journey_id}

# Create a playlist
@journey_router.post("/generate-playlist/")
async def create_playlist(playlist_req: PlaylistRequest):
    """
    Create a Spotify playlist based on the journey time and genre(s).
    """
    # Fetch journey details from the database
    async for conn in get_db_connection():
        journey = await conn.execute(
            'SELECT * FROM journeys WHERE id = ?', (playlist_req.journey_id,)
        )
        journey = await journey.fetchone()

    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")

    journey_time = journey["journey_time"]
    total_duration = 0
    selected_tracks = []

    # Initialize Spotify client inside the thread
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="playlist-modify-public",
    open_browser=True,
    cache_path=".cache",   # Optional cache path for reusing tokens
    show_dialog=True,
))

    # Fetch songs from the requested genres
    for genre in playlist_req.genres:
        results = await asyncio.get_event_loop().run_in_executor(
            None, sp.search, f'genre:{genre}', 'track', 50
        )
        tracks = results['tracks']['items']

        for track in tracks:
            if total_duration >= journey_time:
                break

            selected_tracks.append(track['uri'])
            total_duration += track['duration_ms'] // 1000   # Convert to seconds

        if total_duration >= journey_time:
            break

    # Create a playlist on Spotify
    playlist_name = f"Journey Playlist - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    playlist = await asyncio.get_event_loop().run_in_executor(
        None, sp.user_playlist_create, "user_id_placeholder", playlist_name, True
    )

    # Add tracks to the playlist
    await asyncio.get_event_loop().run_in_executor(
        None, sp.user_playlist_add_tracks, "user_id_placeholder", playlist['id'], selected_tracks
    )

    # Update the journey with playlist URL and genres
    playlist_url = playlist['external_urls']['spotify']
    async for conn in get_db_connection():
        await conn.execute(
            'UPDATE journeys SET playlist_url = ?, genres = ? WHERE id = ?',
            (playlist_url, ','.join(playlist_req.genres), playlist_req.journey_id)
        )
        await conn.commit()

    return {"playlist_url": playlist_url}

# Background task to monitor journey time and update playlist dynamically
async def monitor_journey(journey_id: int):
    """
    Background task to monitor journey time updates from Google Maps and update playlist accordingly.
    """
    while True:
        # Fetch the journey from the database
        async for conn in get_db_connection():
            journey = await conn.execute(
                'SELECT * FROM journeys WHERE id = ?', (journey_id,)
            )
            journey = await journey.fetchone()

        if not journey:
            return

        origin = journey['origin']
        destination = journey['destination']
        old_journey_time = journey['journey_time']
        playlist_url = journey['playlist_url']

        # Initialize Google Maps client inside the thread
        directions_result = await asyncio.to_thread(
            lambda: googlemaps.Client(key=GOOGLE_MAPS_API_KEY).directions(origin, destination, "driving")
        )
        updated_journey_time = directions_result[0]['legs'][0]['duration']['value']  # in seconds

        # Check if the journey time has changed
        if updated_journey_time != old_journey_time:
            # Update journey time in the database
            async for conn in get_db_connection():
                await conn.execute(
                    'UPDATE journeys SET journey_time = ? WHERE id = ?',
                    (updated_journey_time, journey_id)
                )
                await conn.commit()

            # Call the playlist update function if journey time changes
            if playlist_url:
                await update_playlist_in_background(journey_id)

        # Sleep for a few minutes before checking again
        await asyncio.sleep(180)  # Check every 3 minutes

# Function to update the playlist in the background when journey time changes
async def update_playlist_in_background(journey_id: int):
    """
    Function to update the playlist in the background when journey time changes.
    """
    async for conn in get_db_connection():
        journey = await conn.execute(
            'SELECT * FROM journeys WHERE id = ?', (journey_id,)
        )
        journey = await journey.fetchone()

    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")

    # Get the current playlist from Spotify
    playlist_url = journey['playlist_url']
    playlist_id = playlist_url.split('/')[-1].split('?')[0]

    # Calculate updated playlist duration
    updated_journey_time = journey['journey_time']

    # Initialize Spotify client inside the thread
    sp = await asyncio.to_thread(
        lambda: spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="playlist-modify-public"
        ))
    )

    # Fetch current tracks
    playlist_tracks = await asyncio.get_event_loop().run_in_executor(
        None, sp.playlist_tracks, playlist_id
    )
    current_tracks = playlist_tracks['items']
    current_duration = sum(track['track']['duration_ms'] for track in current_tracks) // 1000  # in seconds

    # If journey time has increased, add more tracks
    if updated_journey_time > current_duration:
        additional_tracks = []
        genres = journey['genres'].split(',')
        for genre in genres:
            results = await asyncio.get_event_loop().run_in_executor(
                None, sp.search, f'genre:{genre}', 'track', 50
            )
            tracks = results['tracks']['items']

            for track in tracks:
                if current_duration >= updated_journey_time:
                    break

                additional_tracks.append(track['uri'])
                current_duration += track['duration_ms'] // 1000

            if current_duration >= updated_journey_time:
                break

        # Add new tracks to the playlist
        await asyncio.get_event_loop().run_in_executor(
            None, sp.user_playlist_add_tracks, "user_id_placeholder", playlist_id, additional_tracks
        )

    return

# Playlist History
@journey_router.get("/history/")
async def get_history():
    """
    Fetch journey and playlist history for the user.
    """
    async for conn in get_db_connection():
        cursor = await conn.execute('SELECT * FROM journeys')
        history = await cursor.fetchall()

    return {"history": [dict(row) for row in history]}


