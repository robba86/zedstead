"""Helpers for config editors — no CLI surface of its own.

Holds shared data structures (e.g. `_CFG_CATEGORIES` for serverDZ.cfg
editing) imported by the web app's editors. There is no `config` command
group; editing lives in the web UI.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# serverDZ.cfg categories
# ---------------------------------------------------------------------------

_SERVER_IDENTITY = [
    ("Hostname", "hostname", "string"),
    ("Description", "description", "string"),
    ("Messages of the day", "motd", "list"),
    ("Message of the day interval (seconds)", "motdInterval", "float"),
    ("Password", "password", "string"),
    ("Admin password", "passwordAdmin", "string"),
    ("Mission", "template", "mission"),
    ("", "disableBanlist", "bool"),
    ("", "disablePrioritylist", "bool"),
    ("Save folder id (instanceId)", "instanceId", "int"),
]

_PLAYERS = [
    ("Max players", "maxPlayers", "int"),
    ("Login queue (Concurrent Players)", "loginQueueConcurrentPlayers", "int"),
    ("Login queue (Max Players)", "loginQueueMaxPlayers", "int"),
    ("Re-spawn time", "respawnTime", "int"),
    ("Verify signatures", "verifySignatures", "int"),
    ("Force same build", "forceSameBuild", "bool"),
    ("VonCodec quality", "vonCodecQuality", "int"),
    ("Guaranteed updates", "guaranteedUpdates", "int"),
    ("Storage auto fix", "storageAutoFix", "bool"),
    ("Simulated players batch", "simulatedPlayersBatch", "int"),
]

_TIME_SETTINGS = [
    ("Time", "serverTime", "string"),
    ("Time acceleration", "serverTimeAcceleration", "float"),
    ("Nighttime acceleration", "serverNightTimeAcceleration", "float"),
    ("Time persistent", "serverTimePersistent", "bool"),
]

_NETWORK = [
    ("SteamQuery port", "steamQueryPort", "int"),
    ("Client port", "clientPort", "int"),
    ("Network range - Close", "networkRangeClose", "float"),
    ("Network range - Near", "networkRangeNear", "float"),
    ("Network range - Far", "networkRangeFar", "float"),
    ("Network range - Distant effect", "networkRangeDistantEffect", "float"),
    ("Network-object batch - Send create", "networkObjectBatchSendCreate", "int"),
    ("Network-object batch - Send delete", "networkObjectBatchSendDelete", "int"),
    ("Network-object batch - Compute", "networkObjectBatchCompute", "int"),
    ("Network-object batch - Log slow (seconds)", "networkObjectBatchLogSlow", "float"),
    ("Network-object batch - Enforce bandwidth limits", "networkObjectBatchEnforceBandwidthLimits", "bool"),
    ("Network-object batch - Use estimated bandwidth", "networkObjectBatchUseEstimatedBandwidth", "bool"),
    ("Network-object batch - Use dynamic maximum bandwidth", "networkObjectBatchUseDynamicMaximumBandwidth", "bool"),
    ("Network-object batch - Bandwidth limit", "networkObjectBatchBandwidthLimit", "float"),
    ("Multithreaded replication", "multithreadedReplication", "bool"),
]

_GAMEPLAY_TOGGLES = [
    ("", "disableVoN", "bool"),
    ("", "disable3rdPerson", "bool"),
    ("3rd person view (1.30+)", "thirdPersonView", "int"),
    ("", "disableCrosshair", "bool"),
    ("Whitelist", "enableWhitelist", "bool"),
    ("", "disablePersonalLight", "bool"),
    ("", "disableBaseDamage", "bool"),
    ("", "disableContainerDamage", "bool"),
    ("", "disableRespawnDialog", "bool"),
    ("", "disableMultiAccountMitigation", "bool"),
    ("Gameplay file (cfggameplay.json)", "enableCfgGameplayFile", "bool"),
    ("Debug monitor", "enableDebugMonitor", "bool"),
    ("Allow file-patching", "allowFilePatching", "bool"),
    ("Speedhack detection", "speedhackDetection", "float"),
    ("Shot validation", "shotValidation", "bool"),
]

_LOGGING = [
    ("Log file", "logFile", "string"),
    ("Log average FPS every N seconds (0 = off)", "logAverageFps", "int"),
    ("Log memory every N seconds (0 = off)", "logMemory", "int"),
    ("Log player count every N seconds (0 = off)", "logPlayers", "int"),
    ("Timestamp format", "timeStampFormat", "string"),
    ("Admin log (Player hits only)", "adminLogPlayerHitsOnly", "bool"),
    ("Admin log (Placement)", "adminLogPlacement", "bool"),
    ("Admin log (Build actions)", "adminLogBuildActions", "bool"),
    ("Admin log (Player list)", "adminLogPlayerList", "bool"),
]

_VISIBILITY_RENDERING = [
    ("Default visibility", "defaultVisibility", "float"),
    ("Default object-view distance", "defaultObjectViewDistance", "float"),
    ("Lighting config", "lightingConfig", "int"),
]

_PING_PERFORMANCE = [
    ("Ping warning", "pingWarning", "int"),
    ("Ping critical", "pingCritical", "int"),
    ("Max ping", "MaxPing", "int"),
    ("Server FPS warning", "serverFpsWarning", "int"),
]

# What the game accepts for a serverDZ.cfg number, from Bohemia's wiki
# (dayz-docs/wiki/pages/server-configuration.wiki; the line is on each entry).
# A number field not listed here gets only the obvious floor, 0: every one of
# them is a count, a time, a distance, a port or an id. No upper limit is
# invented where the wiki states none. The web form refuses a value outside
# its range before anything is written (plans/55 A, F30).
_CFG_RANGES: dict[str, tuple[float | None, float | None]] = {
    "maxPlayers": (1, None),                    # :25, a server for nobody
    "verifySignatures": (2, 2),                 # :27 "(only 2 is supported)"
    # :32 says "(values 0-20)", but the serverDZ.cfg Bohemia ships with 1.29
    # says "(values 0-30)" on the same key; the file's range is the one used.
    "vonCodecQuality": (0, 30),
    "serverTimeAcceleration": (0.1, 64),        # :41 "(0.1-64)"
    "serverNightTimeAcceleration": (0.1, 64),   # :44 "(0.1-64)"
    "guaranteedUpdates": (1, 1),                # :50 "(use only number 1)"
    # :57 names no range; the server refuses anything else at start ("must be
    # valid 32-bit integer"), and -1 is valid (it selects storage_-1).
    "instanceId": (-2147483648, 2147483647),
    "speedhackDetection": (1, 10),              # :101 "values 1-10 ... can be float"
    # :121 stock DayZ has 0, 1 and 2, but a terrain mod brings its own: Namalsk's
    # recommended cfgs set 223 (Regular) and 222 (Hardcore), and a map switch
    # writes them (map_ops.MAP_CFG_KEYS). So any whole number.
    "lightingConfig": (0, None),
    "thirdPersonView": (0, 2),                  # :136 "(0 = disabled, 1 = enabled, 2 = enabled for vehicles only)"
    "serverFpsWarning": (11, None),             # :131 "(minimum value is 11)"
}
CFG_DEFAULT_RANGE: tuple[float | None, float | None] = (0, None)


def cfg_range(key: str) -> tuple[float | None, float | None]:
    """The allowed range of a serverDZ.cfg number field."""
    return _CFG_RANGES.get(key, CFG_DEFAULT_RANGE)


# Text fields the wiki gives a closed set of values (the line is on each
# entry). The form offers them as a select and the save refuses any other.
_CFG_CHOICES: dict[str, tuple[str, ...]] = {
    "timeStampFormat": ("Short", "Full"),       # :78 "(value Full/Short)"
    "thirdPersonView": ("0", "1", "2"),         # off, on, only in a vehicle (cfg_range holds it too)
}

# Fields with known values that a mod may extend: the form offers them and
# accepts a typed one. lightingConfig: Namalsk's cfgs set 222 and 223.
# serverTime: wiki :38-39, SystemTime or a typed YYYY/MM/DD/HH/MM time.
_CFG_OPEN_CHOICES: dict[str, tuple[str, ...]] = {
    "lightingConfig": ("0", "1", "2"),
    "serverTime": ("SystemTime",),
}


# What each listed value means, shown under it in the web form's picker.
_CFG_CHOICE_HELP: dict[str, dict[str, str]] = {
    "timeStampFormat": {"Short": "The time only.", "Full": "The date and the time."},
    "thirdPersonView": {"0": "Off.", "1": "On.", "2": "Only while in a vehicle."},
    "lightingConfig": {"0": "Brighter night.", "1": "Darker night.", "2": "Sakhal lighting."},
    "serverTime": {"SystemTime": "The machine's clock. Type YYYY/MM/DD/HH/MM for a fixed start time."},
}


def cfg_choice_help(key: str) -> dict[str, str] | None:
    """What each listed value of a serverDZ.cfg field means, or None."""
    return _CFG_CHOICE_HELP.get(key)


def cfg_choices(key: str) -> tuple[str, ...] | None:
    """The values a serverDZ.cfg text field may hold, or None for free text."""
    return _CFG_CHOICES.get(key)


def cfg_open_choices(key: str) -> tuple[str, ...] | None:
    """The known values of a serverDZ.cfg field that also takes others, or None."""
    return _CFG_OPEN_CHOICES.get(key)


_CFG_CATEGORIES = {
    "Server Identity": _SERVER_IDENTITY,
    "Players": _PLAYERS,
    "Time Settings": _TIME_SETTINGS,
    "Network": _NETWORK,
    "Gameplay Toggles": _GAMEPLAY_TOGGLES,
    "Logging": _LOGGING,
    "Visibility & Rendering": _VISIBILITY_RENDERING,
    "Ping & Performance": _PING_PERFORMANCE,
}

