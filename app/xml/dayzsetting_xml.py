"""dayzsetting.xml field help text.

dayzsetting.xml has no dedicated parser module; this holds only the shared
field-help map (used by the web UI's Core Settings editor).
"""
from __future__ import annotations


# Field help text (shared with the web UI, which strips the Rich
# console markup; see app/web/editors/_help.py).
FIELD_DESCRIPTIONS: dict[str, str] = {
    "noshadergen": (
        "Disable shader generation. Ignored on dedicated server — leftover from the client config schema. "
        "Conventionally left at 1 by BI."
    ),
    "screenwidth": (
        "Window width in pixels. Ignored on dedicated server — the headless server has no display."
    ),
    "screenheight": (
        "Window height in pixels. Ignored on dedicated server — the headless server has no display."
    ),
    "vsync": (
        "Vertical sync. Ignored on dedicated server — leftover from client config."
    ),
    "fullscreen": (
        "Fullscreen mode toggle. Ignored on dedicated server — leftover from client config."
    ),
    "gamma": (
        "Display gamma. Ignored on dedicated server — leftover from client config."
    ),
    "shadow": (
        "Shadow quality preset. Ignored on dedicated server — leftover from client config."
    ),
    "geometricdetail": (
        "Geometric detail preset for the renderer. Ignored on dedicated server — leftover from client config. "
        "For server-side view distance, edit serverDZ.cfg (defaultObjectViewDistance)."
    ),
    "postprocess": (
        "Post-processing quality. Ignored on dedicated server — leftover from client config."
    ),
    "reflections": (
        "Reflection quality. Ignored on dedicated server — leftover from client config."
    ),
    "waterdetail": (
        "Water rendering detail. Ignored on dedicated server — leftover from client config."
    ),
    "terraindetail": (
        "Terrain rendering detail. Ignored on dedicated server — leftover from client config. "
        "Server-side terrain view distance lives in serverDZ.cfg (defaultVisibility)."
    ),
    "globalqueue": (
        "Engine task scheduler global ring buffer size. Default: 4096. No public BI tuning guidance — "
        "leave at default unless you've profiled a queue overflow."
    ),
    "threadqueue": (
        "Engine task scheduler per-thread ring buffer size. Default: 1024. No public BI tuning guidance — "
        "leave at default unless you've profiled a queue overflow."
    ),
    "maxcores": (
        "Cap on CPU cores the engine's job system will use. Vanilla default: 2. Only takes effect when "
        "serverDZ.cfg has multithreadedReplication=1. Worker threads = max(1, maxcores − reservedcores). "
        "On big-iron hardware, raise this to your physical core count for replication-heavy servers, "
        "but the engine historically scales poorly past 4-8 workers."
    ),
    "reservedcores": (
        "CPU cores held back from the job system's worker pool. Vanilla default: 1. Per BI feedback ticket T186512, "
        "changing this value has no measurable effect on the server — setting to 0 is safe. Worker threads = "
        "max(1, maxcores − reservedcores)."
    ),
}
