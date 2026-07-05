"""Constants for Quizify."""

DOMAIN = "quizify"

# Game configuration
MAX_PLAYERS = 20
# Solo play is allowed: a single player can run a full game (great for practice
# / a quick round on the couch with one phone). Comparative end-of-game awards
# still gate on MIN_PLAYERS_FOR_AWARDS — see game/highlights.py.
MIN_PLAYERS = 1
# Comparative end-of-game awards ("Comeback King", "Fastest Finger", …) only
# make sense with at least two players to compare. Solo games skip these and
# just show the personal stats card.
MIN_PLAYERS_FOR_AWARDS = 2
DEFAULT_ROUND_DURATION = 30  # seconds
MAX_NAME_LENGTH = 20
MIN_NAME_LENGTH = 1
LOBBY_DISCONNECT_GRACE_PERIOD = 5  # seconds before removing disconnected player

# Default difficulty (use Difficulty enum from game.types for type-safe comparisons)
DIFFICULTY_DEFAULT = "medium"

# Group-level adaptive difficulty (#40). When the host picks this instead of a
# fixed easy/medium/hard, the game starts at medium and nudges the difficulty of
# upcoming questions up/down based on the whole table's recent correct-rate.
# Per-player adaptive difficulty is a separate, deferred feature (#186).
DIFFICULTY_AUTO = "auto"
# Where "auto" starts before any group signal has accumulated.
DIFFICULTY_AUTO_START = "medium"

# Error codes
ERR_NAME_TAKEN = "NAME_TAKEN"
ERR_NAME_INVALID = "NAME_INVALID"
ERR_GAME_NOT_STARTED = "GAME_NOT_STARTED"
ERR_GAME_ALREADY_STARTED = "GAME_ALREADY_STARTED"
ERR_GAME_ENDED = "GAME_ENDED"
ERR_ROUND_EXPIRED = "ROUND_EXPIRED"
ERR_ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
ERR_FROZEN = "FROZEN"  # frozen player tried to submit during the freeze lockout (#300)
ERR_NOT_IN_GAME = "NOT_IN_GAME"
ERR_INVALID_ACTION = "INVALID_ACTION"
ERR_GAME_FULL = "GAME_FULL"
ERR_NO_QUESTIONS_REMAINING = "NO_QUESTIONS_REMAINING"

# Question structure
ANSWERS_PER_QUESTION = 3

# Question bank configuration
QUESTIONS_DIR = "quizify/questions"

# Options-flow config keys. Held in ConfigEntry.options (not .data) so
# users can change them without re-creating the integration. All three
# are optional — when unset, the corresponding HA-integration feature
# (party lights / TTS announcements) silently no-ops.
CONF_PARTY_LIGHT_ENTITIES = "party_light_entities"  # list[str], domain=light
CONF_TTS_ENTITY = "tts_entity"  # str, single, domain=tts
CONF_MEDIA_PLAYER_ENTITY = "media_player_entity"  # str, single, domain=media_player
# Master toggle for the HA event backbone (#366, "The House Plays Along").
# Off by default: the integration stays silent on the event bus until the host
# opts in, so broad event-triggered automations are never surprised by a stream
# of quizify_* events. Mirrors the opt-in posture of the TTS/lights features.
CONF_HOUSE_EVENTS_ENABLED = "house_events_enabled"  # bool, single
DEFAULT_HOUSE_EVENTS_ENABLED = False
# URL of an audio file to loop in the lobby while waiting for players. Empty by
# default — the lobby-music mechanism stays completely inert until the user
# points this at their own file (e.g. "/local/quizify-lobby.mp3" served from
# Home Assistant's www/ folder). No audio asset ships with the integration, so
# there is nothing to play unless the user supplies one.
CONF_LOBBY_MUSIC_URL = "lobby_music_url"  # str, single, free-text URL

# Per-cue room-SFX override URLs (#494 Phase 3, "The House Plays Along"). Each is
# an OPTIONAL free-text URL to a short one-shot sound the integration plays on the
# shared media_player at a game milestone. Empty => the bundled CC0 default at
# www/sfx/<cue>.mp3 is used instead, IF present (none ship in the repo — the host
# supplies their own). Empty AND no bundled file => that cue stays silent.
CONF_SFX_CORRECT_URL = "sfx_correct_url"  # str, single, free-text URL
CONF_SFX_WRONG_URL = "sfx_wrong_url"  # str, single, free-text URL
CONF_SFX_STREAK_URL = "sfx_streak_url"  # str, single, free-text URL
CONF_SFX_WINNER_URL = "sfx_winner_url"  # str, single, free-text URL

# Endpoint a composed community question pack is POSTed to so it lands as a
# GitHub issue for review (#180). Empty by default — the whole in-app pack
# submission feature stays completely inert (UI hidden, endpoints accept
# nothing) until the user points this at a worker URL that holds the GitHub
# token and creates the issue. The token never lives in the browser or the
# integration; the worker is separate infrastructure (see issue #180, step 5).
CONF_COMMUNITY_SUBMIT_URL = "community_submit_url"  # str, single, free-text URL

# Shared secret the integration sends as an ``X-Quizify-Secret`` header on every
# pack submission (#256). When the worker has a matching ``SHARED_SECRET`` env
# secret set it rejects any request without this header, closing the open-proxy
# hole. Empty by default and fully back-compatible: with no secret configured
# the header is omitted and the worker (which only enforces when *its* secret is
# set) behaves exactly as before. Set BOTH the worker secret and this option to
# lock the endpoint down. See cf-workers/README.md.
CONF_COMMUNITY_SUBMIT_SECRET = "community_submit_secret"  # str, single, free-text

# ---------------------------------------------------------------------------
# Community pack submission (#180)
# ---------------------------------------------------------------------------
# Validation caps for a pasted/composed community pack. Kept in sync with the
# community-pack loader limits (game/questions.py) so a pack that validates in
# the browser also loads once it lands on disk.
SUBMIT_MAX_QUESTIONS = 500
SUBMIT_MIN_QUESTIONS = 1
SUBMIT_ANSWERS_PER_QUESTION = ANSWERS_PER_QUESTION
SUBMIT_MAX_PACK_BYTES = 1_048_576  # 1 MiB, mirrors MAX_COMMUNITY_PACK_BYTES
# How many submission records we keep on disk per HA instance, and how often
# the server reconciles their status against the GitHub issue state.
SUBMIT_MAX_RECORDS = 100
SUBMIT_POLL_INTERVAL_SECONDS = 3600  # ~hourly, like Beatify's PlaylistRequestsView
SUBMIT_POLL_TIMEOUT_SECONDS = 12
SUBMIT_RECORDS_FILE = "pack_submissions.json"

# Submission status values (issue-state derived; see pack_submission.py).
SUBMIT_STATUS_PENDING = "pending"
SUBMIT_STATUS_ACCEPTED = "accepted"  # issue closed as completed
SUBMIT_STATUS_DECLINED = "declined"  # issue closed as not_planned

# Localized error codes surfaced by the submit flow (#180). The frontend maps
# these to i18n keys (errors.<CODE>) with a fallback to the raw worker message.
ERR_SUBMIT_INVALID_FORMAT = "INVALID_FORMAT"
ERR_SUBMIT_RATE_LIMITED = "RATE_LIMITED"
ERR_SUBMIT_GITHUB_ERROR = "GITHUB_ERROR"
ERR_SUBMIT_DISABLED = "SUBMIT_DISABLED"
