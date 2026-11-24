"""Pydantic request models — bodies only.

Responses are plain dicts/lists (see app/web/CLAUDE.md, "Web app") so
response shapes are NOT modeled here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str
    # Optional, and it has to stay that way: a blank name authenticates
    # against the single account a migrated install has, which is what keeps
    # an install that never created a second account signing in as it always
    # did. The form sends it since plans/43 step 5b.
    username: str = ""
    # The authenticator code, for an account that enrolled one (TODO B5).
    # Optional for the same shape of reason: the form does not know to ask
    # until the first attempt comes back saying so, and most accounts never
    # send it at all.
    code: str = ""


class UserCreateRequest(BaseModel):
    name: str
    password: str
    role: str = "viewer"


class UserRoleRequest(BaseModel):
    role: str


class UserPasswordRequest(BaseModel):
    password: str
    # The caller's own password, needed only when the account is the caller's
    # (2P-W1). Resetting somebody else's does not ask for it.
    current: str = ""


# ── instances (the Instances page, plans/62 D6) ─────────────────────────


class InstanceCreateRequest(BaseModel):
    name: str
    server_dir: str
    label: str = ""
    # Empty adds an instance with default settings; an instance id clones it.
    source: str = ""
    # "stable" | "experimental" (plans/66); None keeps the source's, or stable.
    branch: Literal["stable", "experimental"] | None = None


class InstanceLabelRequest(BaseModel):
    label: str


# ── two-factor sign-in, for your own account only (TODO B5) ─────────────
#
# Both of the first two carry a password. Changing your second factor requires
# your first one, in both directions: enrolling a phone an account's owner does
# not hold locks them out just as surely as disabling theirs lets somebody in,
# and a stolen session should not be able to do either.


class TotpStartRequest(BaseModel):
    password: str
    # The current code, needed once a second factor is on (`routers/auth.py`).
    code: str = ""


class TotpEnableRequest(BaseModel):
    code: str


class TotpDisableRequest(BaseModel):
    password: str
    code: str = ""


class UpdateRunRequest(BaseModel):
    steps: list[str] | None = None  # None/omitted = all steps
    dry_run: bool = False
    force: bool = False  # allow running while the server is up
    # Start the server after a clean run and watch it boot (plans/73).
    start_after: bool = False


class UpdateRollbackRequest(BaseModel):
    steps: list[str]


class UpdateTriageApplyRequest(BaseModel):
    """Set suspects of the last failed boot aside (plans/73 C)."""

    mods: list[str]
    backup_first: bool = True


class UpdateConflictsResolveRequest(BaseModel):
    keep: Literal["mine", "dayz"]
    files: list[str] | None = None  # None/omitted = every file waiting


class UpdateConflictValuesRequest(BaseModel):
    """Values of one merged file (plans/71): "mine" or "dayz" makes that side
    live, "ok" takes the rows off the list and leaves the file as it is."""
    file: str
    ids: list[str]
    keep: Literal["mine", "dayz", "ok"]


class ModAddRequest(BaseModel):
    name: str


class ModInstallRequest(BaseModel):
    names: list[str] = []
    ids: list[str] = []  # install by workshop id
    id_names: dict[str, str] = {}  # optional {workshop_id: "@Name"} for uncached ids
    install_deps: bool = True  # pull in missing deps (e.g. @CF for @TraderPlus)
    configure_xml: bool = True  # apply the mods' XML to the mission (the "auto config" step)
    reinstall: bool = False  # delete each requested mod's workshop cache copy first, so SteamCMD downloads it whole


class QuickSetupServer(BaseModel):
    """The "Your server" card on Quick setup: written into serverDZ.cfg right
    after the install. An empty `password` means players join without one."""

    hostname: str
    password: str = ""
    password_admin: str
    max_players: int
    map: str  # a key of seed_data.VANILLA_MAPS


class QuickSetupApplyRequest(BaseModel):
    # None keeps the server's own name, passwords, slots and map (the API before
    # the card, and the install card).
    server: QuickSetupServer | None = None
    preset: str = ""
    mod_ids: list[str] = []  # empty for a server without mods
    start: bool = True  # start the server and watch the boot when setup is done
    # Only the server install, from the page's install card. The setup stays
    # unfinished, so the cards stay offered for the preset and the mods.
    install_only: bool = False


class ModUninstallRequest(BaseModel):
    names: list[str]
    remove_deps: bool = False


class ModServerSideRequest(BaseModel):
    server_side: bool


class ModAutoConfigRequest(BaseModel):
    """The Auto configure dialog's three switches (`mod_autoconfig.AutoConfigOptions`)."""

    split_nominal: bool = True
    guess_category: bool = True
    starter_nominal: bool = True


class ModLoadOrderRequest(BaseModel):
    # Either or both: the enabled mods in their new order, and the manual
    # switch (plans/49). Switching on keeps the current order.
    order: list[str] | None = None
    manual: bool | None = None


class ExpansionMissionRequest(BaseModel):
    # Template folder name, e.g. "Chernarus" or "Namalsk/Regular". Empty = the
    # one matching the mission's terrain.
    template: str = ""


class ZoneMarketRow(BaseModel):
    """One priced item in POST /api/expansion/market/zone-offers."""

    classname: str
    min_price: int
    max_price: int
    min_stock: int
    max_stock: int


class ZoneMarketRequest(BaseModel):
    """POST /api/expansion/market/zone-offers (plans/48 D, D13)."""

    mod: str  # a zone_trader.ZONE_MODS key
    category: str  # becomes Market/<category>.json
    trader_ids: list[str] = []  # Traders/<id>.json
    mode: int = 1  # zone_trader.MARKET_MODES
    items: list[ZoneMarketRow]


class SpawnTestStartRequest(BaseModel):
    mode: str = "near_player"
    classname: str
    count: int
    # Only read when mode == "only_x"; required there (plans/37 B4).
    scope: str | None = None
    # Only read when mode == "at_location"; x and z required there. `location`
    # is the picked place's name, shown and logged only.
    x: float | None = None
    z: float | None = None
    location: str | None = None


class PlayerKickRequest(BaseModel):
    player_id: int
    reason: str = ""


class PlayerBanRequest(BaseModel):
    player_id: int
    duration_minutes: int = 0
    reason: str = ""


class PlayerMessageRequest(BaseModel):
    message: str


class BanAddRequest(BaseModel):
    identifier: str
    reason: str = ""
    duration_minutes: int = 0
    # No `added_by`: the route records the signed-in account. A body field let
    # any moderator write "admin" there, and an old
    # client that still sends one has it ignored, not refused.


class PriorityAddRequest(BaseModel):
    steam_id: str


class WhitelistAddRequest(BaseModel):
    steam_id: str


class RconCommandRequest(BaseModel):
    command: str


class WatchlistAddRequest(BaseModel):
    entry: str


class BackupCreateRequest(BaseModel):
    type: str  # "quick" | "full"
    players: bool = True
    vehicles: bool = True
    world: bool = True
    mod_data: bool = True
    include_mods: bool = False
    include_logs: bool = False
    note: str = ""


class BackupRestoreRequest(BaseModel):
    type: str  # "quick" | "full"
    name: str
    # Full-restore extras (ignored for quick restores):
    restore_app_config: bool = False  # also restore config.json from the zip
    redownload_mods: bool = False  # re-download + copy the listed mods after restore


class BackupDeleteRequest(BaseModel):
    type: str  # "quick" | "full"
    names: list[str]


class BundleDeleteRequest(BaseModel):
    names: list[str]


class BundleImportRequest(BaseModel):
    filename: str
    target_mission: str | None = None


class ScheduleTaskCreateRequest(BaseModel):
    id: str
    task_type: str
    cron: str
    enabled: bool = True
    options: dict = {}


class ScheduleTaskUpdateRequest(BaseModel):
    task_type: str | None = None
    cron: str | None = None
    enabled: bool | None = None
    options: dict | None = None


class CronValidateRequest(BaseModel):
    expr: str


class SettingsUpdateRequest(BaseModel):
    values: dict[str, str]


class PasswordChangeRequest(BaseModel):
    current: str
    new: str


class ConfirmRequest(BaseModel):
    confirm: bool = False


class CleanupWorkshopRequest(BaseModel):
    confirm: bool = False
    paths: list[str] | None = None  # None = remove all workshop mods


class CleanupModDataRequest(BaseModel):
    confirm: bool = False
    names: list[str] | None = None  # None = every leftover mod data dir


class CleanupStorageRequest(BaseModel):
    confirm: bool = False
    backup_before: bool = True


class CleanupMissionResetRequest(BaseModel):
    confirm: bool = False
    mission: str
    preserve_storage: bool = True


class MapDownloadRequest(BaseModel):
    map_id: str


class MapSwitchRequest(BaseModel):
    map_id: str
    # False: keep the current serverDZ.cfg even when a saved one for this map
    # exists (map_ops.switch_map_cfg). Defaults to True, so a client that does
    # not send it restores the saved config as before.
    restore_previous: bool = True
    # The switch dialog's other choices (plans/83, map_ops.SwitchChoices).
    # Every default is what a switch did before the dialog asked.
    apply_recommended: bool = True
    apply_mod_xml: bool = True
    disable_mods: list[str] = []
    leave_manual_files: list[str] = []


class TweakApplyRequest(BaseModel):
    multiplier: float = 1.0
    variant: str | None = None


class TweakResetRequest(BaseModel):
    variant: str | None = None


class TpPriceUpdateRequest(BaseModel):
    coefficient: float | None = None
    max_stock: int | None = None
    trade_quantity: float | None = None
    # -1 is "that direction is off"; anything below it means nothing (F80).
    buy_price: float | None = Field(None, ge=-1)
    sell_price: float | None = Field(None, ge=-1)


class TpPriceMultiplierRequest(BaseModel):
    multiplier: float = 1.0
    columns: list[str] | None = None  # None = all price columns


class TpZoneOfferRequest(BaseModel):
    """POST /api/tp-price/zone-offers: which zone mod, as what, sold by whom."""

    mod: str  # a zone_trader.ZONE_MODS key: "radzone" / "toxiczone"
    category: str
    trader_ids: list[int] = []
    # Price the items from the mod's own Trader.txt (ToxicZone only, D10).
    use_prices: bool = False


class TypesEntryUpdateRequest(BaseModel):
    source_file: str
    name: str
    nominal: int | None = None
    min: int | None = None
    lifetime: int | None = None
    restock: int | None = None
    quantmin: int | None = None
    quantmax: int | None = None
    cost: int | None = None
    category: str | None = None
    tag: str | None = None
    usages: list[str] | None = None
    values: list[str] | None = None
    flags: dict[str, int] | None = None
    # The row's tree (plans/76 step 3): the edited keys of its `nested`, and
    # those keys as the page loaded them, which a stale tab fails against.
    nested: dict | None = None
    nested_base: dict | None = None


class TypesFilterRequest(BaseModel):
    """The Types page's file and filters, which a bulk write matches as
    GET /api/types does (routers/types.py `_matching`), and the count its
    dialog showed: a different count is refused."""
    file: str
    category: str | None = None
    q: str = ""
    usage: str | None = None
    value: str | None = None
    nominal_min: int | None = None
    nominal_max: int | None = None
    expected: int | None = None


class TypesMultiplierRequest(TypesFilterRequest):
    nominal: float = 1.0
    min: float = 1.0
    lifetime: float = 1.0
    restock: float = 1.0


class TypesFillRequest(TypesFilterRequest):
    """The Filler: each number given is written where the row has 0, and a
    number left out (None) is not touched."""
    nominal: int | None = Field(default=None, ge=0)
    min: int | None = Field(default=None, ge=0)
    lifetime: int | None = Field(default=None, ge=0)
    restock: int | None = Field(default=None, ge=0)


class TypesRestoreRequest(BaseModel):
    file: str
