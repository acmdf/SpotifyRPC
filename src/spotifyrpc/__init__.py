import os
import sys
import time
import tomllib
import requests

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from pypresence import Presence
from pypresence.types import ActivityType

# ============================================================
# Configuration
# ============================================================


@dataclass
class Config:
    # Spotify
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str

    # Discord
    discord_client_id: str
    discord_asset_name: str

    # Settings
    use_spotify_asset: bool
    timeout: int
    song_status_icon: bool
    song_status_icon_play: str
    song_status_icon_pause: str
    debug: bool
    print_secrets: bool
    stalker_message: str

    # Config file
    path: Path

    @property
    def spotify_cache_path(self) -> Path:
        return self.path.parent / ".spotify_cache"

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        """
        Load configuration from a TOML file.

        If no path is provided, use:

            $XDG_CONFIG_HOME/spotifyrpc/config.toml

        or, if XDG_CONFIG_HOME is not set:

            ~/.config/spotifyrpc/config.toml
        """

        if path is None:
            xdg_config_home = Path(
                os.environ.get(
                    "XDG_CONFIG_HOME",
                    Path.home() / ".config",
                )
            )

            path = xdg_config_home / "spotifyrpc" / "config.toml"
        else:
            path = path.expanduser()

        if not path.is_file():
            print(f"⛔ Configuration file not found: {path}")
            sys.exit(1)

        try:
            with path.open("rb") as f:
                data = tomllib.load(f)

        except tomllib.TOMLDecodeError as e:
            print(f"⛔ Invalid TOML configuration: {e}")
            sys.exit(1)

        try:
            spotify = data["spotify"]
            discord = data["discord"]
            settings = data.get("settings", {})

            return cls(
                # Spotify
                spotify_client_id=spotify["client_id"],
                spotify_client_secret=spotify["client_secret"],
                spotify_redirect_uri=spotify["redirect_uri"],
                # Discord
                discord_client_id=discord["client_id"],
                discord_asset_name=discord.get(
                    "asset_name",
                    "spotify",
                ),
                # Settings
                use_spotify_asset=settings.get(
                    "use_spotify_asset",
                    True,
                ),
                timeout=settings.get(
                    "timeout",
                    5,
                ),
                song_status_icon=settings.get(
                    "song_status_icon",
                    False,
                ),
                song_status_icon_play=settings.get(
                    "song_status_icon_play",
                    "play",
                ),
                song_status_icon_pause=settings.get(
                    "song_status_icon_pause",
                    "pause",
                ),
                debug=settings.get(
                    "debug",
                    False,
                ),
                print_secrets=settings.get(
                    "print_secrets",
                    False,
                ),
                stalker_message=settings.get("stalker_message", ""),
                path=path,
            )

        except KeyError as e:
            print(f"⛔ Missing configuration value: {e}")
            sys.exit(1)

    def log(self, *args, **kwargs):
        if self.debug:
            print("[DEBUG]", *args, **kwargs)

    def log_config(self):
        self.log("✅ Configuration loaded:")
        self.log(f"  Config file:              {self.path}")

        self.log(
            f"  SPOTIFY_CLIENT_ID:       "
            f"{self.spotify_client_id if self.print_secrets else '***'}"
        )

        self.log(
            f"  SPOTIFY_CLIENT_SECRET:   "
            f"{self.spotify_client_secret if self.print_secrets else '***'}"
        )

        self.log(f"  SPOTIFY_REDIRECT_URI:    " f"{self.spotify_redirect_uri}")

        self.log(
            f"  DISCORD_CLIENT_ID:       "
            f"{self.discord_client_id if self.print_secrets else '***'}"
        )

        self.log(f"  DISCORD_ASSET_NAME:      " f"{self.discord_asset_name}")

        self.log(f"  USE_SPOTIFY_ASSET:       " f"{self.use_spotify_asset}")

        self.log(f"  TIMEOUT:                 " f"{self.timeout}")

        self.log(f"  SONG_STATUS_ICON:        " f"{self.song_status_icon}")

        self.log(f"  SONG_STATUS_ICON_PLAY:   " f"{self.song_status_icon_play}")

        self.log(f"  SONG_STATUS_ICON_PAUSE:  " f"{self.song_status_icon_pause}")

        self.log(f"  DEBUG:                   " f"{self.debug}")


# ============================================================
# Runtime State
# ============================================================


@dataclass
class State:
    """
    Runtime state for the application.

    This object is created by main() and passed to the functions
    that need to read or modify application state.
    """

    spotify: Spotify
    rpc: Presence

    last_track_uri: str | None = None
    last_is_playing: bool | None = None
    last_metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================
# Spotify
# ============================================================


def wait_for_spotify_auth(config: Config) -> Spotify:
    while True:
        try:
            auth = SpotifyOAuth(
                client_id=config.spotify_client_id,
                client_secret=config.spotify_client_secret,
                redirect_uri=config.spotify_redirect_uri,
                scope="user-read-playback-state",
                cache_path=str(config.spotify_cache_path),
            )

            spotify = Spotify(auth_manager=auth)

            # Test the connection.
            spotify.current_playback()

            print("✅ Spotify authenticated and reachable.")

            return spotify

        except (
            SpotifyException,
            requests.exceptions.RequestException,
        ) as e:
            print("⛔ Spotify not reachable — waiting for internet...")

            config.log(
                "Failed to connect to Spotify:",
                e,
            )

            time.sleep(5)


# ============================================================
# Discord RPC
# ============================================================


def connect_rpc(config: Config) -> Presence:
    rpc = Presence(config.discord_client_id)

    try:
        rpc.connect()
        print("✅ Discord Rich Presence connected.")

    except Exception as e:
        print("⛔ Discord Rich Presence not available.")

        config.log(
            "Discord RPC connection failed:",
            e,
        )

    return rpc


def update_rpc(
    config: Config,
    state: State,
    activity: dict[str, Any],
) -> None:
    """
    Update Discord Rich Presence.

    If the connection has been lost, attempt to reconnect.
    """

    activity["activity_type"] = ActivityType.LISTENING

    try:
        state.rpc.update(**activity)

    except Exception as e:
        config.log(
            "Discord RPC update failed:",
            e,
        )

        try:
            config.log("Attempting to reconnect to Discord...")

            state.rpc.connect()
            state.rpc.update(**activity)

            config.log("Discord RPC reconnected.")

        except Exception as reconnect_error:
            config.log(
                "Discord RPC reconnect failed:",
                reconnect_error,
            )


def clear_rpc(
    config: Config,
    state: State,
) -> None:
    """Clear the current Discord Rich Presence."""

    try:
        state.rpc.clear()

    except Exception as e:
        config.log(
            "Failed to clear Discord RPC:",
            e,
        )

        try:
            state.rpc.connect()
            state.rpc.clear()

        except Exception as reconnect_error:
            config.log(
                "Failed to clear RPC after reconnect:",
                reconnect_error,
            )


# ============================================================
# Presence
# ============================================================


def update_presence(
    config: Config,
    state: State,
) -> None:
    """
    Fetch the current Spotify playback state and update Discord.

    Runtime state is stored in the State object.
    """

    try:
        playback = state.spotify.current_playback()

    except (
        SpotifyException,
        requests.exceptions.RequestException,
    ) as e:
        config.log("🔁 Re-authenticating Spotify due to network error...")

        config.log("Error:", e)

        state.spotify = wait_for_spotify_auth(config)

        return

    config.log("Fetched playback.")

    # ========================================================
    # Nothing playing
    # ========================================================

    if (
        not playback
        or playback.get("item") is None
        or playback.get("progress_ms") is None
    ):
        if state.last_track_uri:
            config.log("Clearing RPC presence (nothing playing).")

            clear_rpc(config, state)

        state.last_track_uri = None
        state.last_is_playing = None
        state.last_metadata = {}

        return

    # ========================================================
    # Track information
    # ========================================================

    is_playing = playback["is_playing"]
    track = playback["item"]

    track_uri = track["uri"]
    title = track["name"]

    artist = ", ".join(artist["name"] for artist in track["artists"])

    duration = track["duration_ms"] // 1000
    progress = playback["progress_ms"] // 1000

    album_name = track["album"]["name"]

    if config.use_spotify_asset and track["album"]["images"]:
        album_image_url = track["album"]["images"][0]["url"]
    else:
        album_image_url = config.discord_asset_name

    # ========================================================
    # Playback context
    # ========================================================

    play_name = None
    context = playback.get("context")

    if context:
        context_type = context.get("type")
        context_uri = context.get("uri")

        config.log(
            "Context found:",
            context_type,
            context_uri,
        )

        if context_type == "playlist":
            playlist_id = context_uri.split(":")[-1]

            try:
                playlist = state.spotify.playlist(playlist_id)

                play_name = "playlist '" + playlist["name"] + "'"

            except Exception as e:
                config.log(
                    "Could not fetch playlist name:",
                    e,
                )

        elif context_type == "album":
            album_id = context_uri.split(":")[-1]

            try:
                album = state.spotify.album(album_id)

                play_name = "album '" + album["name"] + "'"

            except Exception as e:
                config.log(
                    "Could not fetch album name:",
                    e,
                )

        elif context_uri and ":collection" in context_uri:
            play_name = "Liked Songs"

    # ========================================================
    # Logging
    # ========================================================

    config.log(
        f"Current track: {title} by {artist} "
        f"({'playing' if is_playing else 'paused'})"
    )

    config.log(
        f"Fetched data: "
        f"duration={duration}s, "
        f"progress={progress}s, "
        f"album={album_name}, "
        f"image={album_image_url}",
    )

    # ========================================================
    # Playing
    # ========================================================

    if is_playing:
        activity = {
            "details": f"{title} by {artist}",
            "state": (
                f"Listening to {play_name} on Spotify"
                if play_name
                else "Listening to Spotify"
            ),
            "large_image": album_image_url,
            "large_text": album_name,
            "start": int(time.time()) - progress,
            "end": (int(time.time()) + (duration - progress)),
        }

        if config.song_status_icon:
            activity["small_image"] = config.song_status_icon_play

            activity["small_text"] = config.stalker_message

        config.log(
            "Updating RPC (playing):",
            title,
        )

        update_rpc(
            config,
            state,
            activity,
        )

        state.last_track_uri = track_uri
        state.last_is_playing = True

        state.last_metadata = {
            "title": title,
            "artist": artist,
            "duration": duration,
            "progress": progress,
            "album": album_name,
            "image": album_image_url,
        }

        return

    # ========================================================
    # Paused
    # ========================================================

    if state.last_is_playing and state.last_metadata:
        activity = {
            "details": f"{title} by {artist} (Paused)",
            "state": (
                f"Listening to {play_name} on Spotify"
                if play_name
                else "Listening to Spotify"
            ),
            "large_image": album_image_url,
            "large_text": album_name,
        }

        if config.song_status_icon:
            activity["small_image"] = config.song_status_icon_pause

            activity["small_text"] = config.stalker_message

        config.log(
            "Updating RPC (paused):",
            state.last_metadata["title"],
        )

        update_rpc(
            config,
            state,
            activity,
        )

    state.last_track_uri = track_uri
    state.last_is_playing = False


# ============================================================
# Main
# ============================================================


def main() -> None:
    # --------------------------------------------------------
    # Command line
    # --------------------------------------------------------

    if len(sys.argv) > 2:
        print(f"Usage: {sys.argv[0]} [config.toml]")
        sys.exit(1)

    config = Config.load(
        Path(sys.argv[1]) if len(sys.argv) == 2 else None,
    )

    config.log_config()

    # --------------------------------------------------------
    # Runtime state
    # --------------------------------------------------------

    state = State(
        spotify=wait_for_spotify_auth(config),
        rpc=connect_rpc(config),
    )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    print("🎧 Spotify → Discord (with pypresence) started.")

    print(f"📄 Using config: {config.path}")

    try:
        while True:
            update_presence(
                config,
                state,
            )

            time.sleep(config.timeout)

    except KeyboardInterrupt:
        config.log("Shutting down...")

    finally:
        try:
            state.rpc.clear()
        except Exception:
            pass

        try:
            state.rpc.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
