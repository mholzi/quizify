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

# Error codes
ERR_NAME_TAKEN = "NAME_TAKEN"
ERR_NAME_INVALID = "NAME_INVALID"
ERR_GAME_NOT_STARTED = "GAME_NOT_STARTED"
ERR_GAME_ALREADY_STARTED = "GAME_ALREADY_STARTED"
ERR_GAME_ENDED = "GAME_ENDED"
ERR_ROUND_EXPIRED = "ROUND_EXPIRED"
ERR_ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
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
