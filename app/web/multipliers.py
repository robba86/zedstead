"""The one bound on a bulk multiplier sent by the web panel.

The Types page (``routers/types.py``), the TraderPlus category multiplier
(``routers/configs.py``) and the quick tweaks (``routers/presets.py``) check
through here; ``webui/src/multiplier.js``
repeats ``MAX_MULTIPLIER`` so the page can say which box is wrong first.
"""

from __future__ import annotations

from fastapi import HTTPException

# A multiplier is refused above this: x1000 was written as is (test pass 2,
# N28), and no preset goes past 2.5.
MAX_MULTIPLIER = 10.0


def check_multipliers(*, allow_zero: bool = False, **values: float) -> None:
    """422 for a multiplier the page should never send.

    ``Number('')`` is 0 in the browser, so an emptied box multiplied that field
    by 0 for every filtered type, and set every price in a TraderPlus category
    to 1; a huge one raised half way through the
    write. Both are refused before anything is read or kept.

    *allow_zero* lets 0 through for a quick tweak, whose None tier (no zombies)
    is a multiplier of 0.
    """
    low = "0 or above" if allow_zero else "above 0"
    for name, value in values.items():
        above_low = value >= 0 if allow_zero else value > 0
        if not (above_low and value <= MAX_MULTIPLIER):
            raise HTTPException(
                status_code=422,
                detail=f"The {name} multiplier must be {low} and at most {MAX_MULTIPLIER:g}.",
            )
