CREATE TABLE journeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    journey_time INTEGER NOT NULL,
    playlist_url TEXT,
    created_at TEXT NOT NULL
);