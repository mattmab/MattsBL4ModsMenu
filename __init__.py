from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import mods_base
import unrealsdk
from mods_base import CoopSupport, Game, build_mod, command
from unrealsdk import logging
from unrealsdk.hooks import Type, Block

VERSION = "v1.1.13-sdk-mods-button-label"
PREFIX = "[MattsBL4ModsMenu]"
DEBUG_LOGGING = False

# Hook IDs are intentionally stable and production-named.
HOOK_TICK = "native_mods_menu_camera_poll_v2"
HOOK_PAUSE_OPEN = "native_mods_menu_pause_open_v1"
HOOK_PAUSE_CLOSE = "native_mods_menu_pause_close_v1"
HOOK_REBIND_PREFIX = "native_mods_menu_rebind_input_v1"
HOOK_BLOCK_PREFIX = "native_mods_menu_block_pause_input_v1"
HOOK_ESCAPE_BLOCK = "native_mods_menu_escape_input_block_v1"

# Menu_Main launcher UMG can crash when a runtime-created UMG widget survives
# gameplay/title transitions. Keep it off by default; pause launcher remains
# event-driven and stable. This constant must exist because the menu hooks
# reference it when filtering menu definitions.
MAIN_MENU_LAUNCHER_ENABLED = True
MAIN_MENU_LAUNCHER_DELAY_SEC = 1.0
USER_SETTINGS_FILENAME = "native_mods_menu_user_settings.json"
LAUNCHER_MARGIN = 60.0
LAUNCHER_POSITION_PRESETS = ("top_left", "top_right", "bottom_left", "bottom_right", "custom")
LAUNCHER_POSITION_LABELS = {
    "top_left": "Top Left",
    "top_right": "Top Right",
    "bottom_left": "Bottom Left",
    "bottom_right": "Bottom Right",
    "custom": "Custom",
}
# After pause closes into title/main-menu unload, defer UMG work briefly.
MENU_WORLD_TRANSITION_SEC = 2.0
MENU_EVENT_DEBOUNCE_SEC = 0.35
DIALOG_LAUNCHER_SUPPRESSION_SEC = MENU_WORLD_TRANSITION_SEC + 0.35
DIALOG_CANCEL_RESTORE_DELAY_SEC = MENU_WORLD_TRANSITION_SEC + 0.45
DIALOG_CANCEL_RESTORE_TIMEOUT_SEC = 8.0

SCREEN_MAIN = "main"
SCREEN_SETTINGS = "settings"
SCREEN_KEYBINDS = "keybinds"
SCREEN_BUTTON_POSITION = "button_position"

MENU_UI_STATE_TAG = "CINEMATIC"
PAUSE_MENU_UI_STATE_TAG = "MENU_PAUSE"

TEXT_SCALE_MIN = 0.75
TEXT_SCALE_MAX = 2.75
TEXT_SCALE_STEP = 0.10
TEXT_SCALE_DEFAULT = 1.35

# InputKey hooks do not reliably fire while BL4 native UMG owns focus. Rebinding
# therefore uses the same proven model as button clicks: camera-tick polling of
# PlayerController.IsInputKeyDown against a known key list.
REBIND_KEY_NAMES = [
    # Letters
    *list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),

    # Top-row numbers and numpad
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "NumPadZero", "NumPadOne", "NumPadTwo", "NumPadThree", "NumPadFour",
    "NumPadFive", "NumPadSix", "NumPadSeven", "NumPadEight", "NumPadNine", "Decimal",

    # Function keys
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",

    # Common keyboard controls
    "Escape", "Tab", "Enter", "SpaceBar", "BackSpace", "Delete", "Insert",
    "Home", "End", "PageUp", "PageDown", "Left", "Right", "Up", "Down",
    "LeftShift", "RightShift", "LeftControl", "RightControl", "LeftAlt", "RightAlt", "CapsLock",

    # Punctuation / symbols
    "Semicolon", "Equals", "Comma", "Hyphen", "Period", "Slash", "Tilde",
    "LeftBracket", "Backslash", "RightBracket", "Apostrophe",

    # Gamepad common UE key names
    "Gamepad_FaceButton_Bottom", "Gamepad_FaceButton_Right", "Gamepad_FaceButton_Left", "Gamepad_FaceButton_Top",
    "Gamepad_LeftShoulder", "Gamepad_RightShoulder", "Gamepad_LeftTrigger", "Gamepad_RightTrigger",
    "Gamepad_Special_Left", "Gamepad_Special_Right",
    "Gamepad_LeftThumbstick", "Gamepad_RightThumbstick",
    "Gamepad_DPad_Up", "Gamepad_DPad_Down", "Gamepad_DPad_Left", "Gamepad_DPad_Right",
]

REBIND_DISPLAY_ALIASES = {
    "Zero": "0", "One": "1", "Two": "2", "Three": "3", "Four": "4",
    "Five": "5", "Six": "6", "Seven": "7", "Eight": "8", "Nine": "9",
    "SpaceBar": "Space",
    "Decimal": "Decimal",
    "LeftControl": "LeftCtrl",
    "RightControl": "RightCtrl",
}

TEXT_INPUT_KEY_NAMES = [
    *list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    "Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "NumPadZero", "NumPadOne", "NumPadTwo", "NumPadThree", "NumPadFour",
    "NumPadFive", "NumPadSix", "NumPadSeven", "NumPadEight", "NumPadNine", "Decimal",
    "SpaceBar", "BackSpace", "Delete", "Enter", "Return", "NumPadEnter", "Escape",
    "Period", "Comma", "Hyphen", "Equals", "Slash", "Backslash", "Semicolon",
    "Apostrophe", "LeftBracket", "RightBracket",
]

# Keys consumed while a MattsBL4ModsMenu text field owns input. These are blocked
# from BL4's underlying menus so Enter commits search/options and Esc cancels
# the field instead of also activating/backing out another screen.
TEXT_INPUT_CONSUME_KEY_NAMES = set(TEXT_INPUT_KEY_NAMES) | {"Return", "NumPadEnter"}

TEXT_INPUT_CHARS = {
    **{c: c.lower() for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
    "Zero": "0", "One": "1", "Two": "2", "Three": "3", "Four": "4",
    "Five": "5", "Six": "6", "Seven": "7", "Eight": "8", "Nine": "9",
    "NumPadZero": "0", "NumPadOne": "1", "NumPadTwo": "2", "NumPadThree": "3",
    "NumPadFour": "4", "NumPadFive": "5", "NumPadSix": "6", "NumPadSeven": "7",
    "NumPadEight": "8", "NumPadNine": "9", "Decimal": ".",
    "SpaceBar": " ", "Period": ".", "Comma": ",", "Hyphen": "-", "Equals": "=",
    "Slash": "/", "Backslash": "\\", "Semicolon": ";", "Apostrophe": "'",
    "LeftBracket": "[", "RightBracket": "]",
}


# ---------------------------------------------------------------------------
# Low-level safe helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    # Keep in-menu status text updated, but do not spam the console in release builds.
    try:
        STATE.last_status = str(msg)
    except Exception:
        pass
    if not DEBUG_LOGGING:
        return
    try:
        logging.info(f"{PREFIX} {STATE.last_status}")
    except Exception:
        pass


def strip_html(text: Any) -> str:
    try:
        s = str(text)
    except Exception:
        return ""
    s = re.sub(r"<[^>]*>", "", s)
    return (
        s.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return strip_html(str(value))
    except Exception:
        return default


def vec2(x: float, y: float):
    return unrealsdk.make_struct("Vector2D", X=float(x), Y=float(y))


def color(r: float, g: float, b: float, a: float = 1.0):
    return unrealsdk.make_struct(
        "LinearColor", R=float(r), G=float(g), B=float(b), A=float(a)
    )


def class_obj(path: str):
    return unrealsdk.find_object("Class", path)


def construct(path: str, outer: Any):
    return unrealsdk.construct_object(class_obj(path), outer)


def try_call(obj: Any, name: str, *args) -> bool:
    if obj is None:
        return False
    try:
        getattr(obj, name)(*args)
        return True
    except Exception:
        return False


def live(obj: Any) -> bool:
    if obj is None:
        return False
    try:
        if hasattr(obj, "IsValid") and not bool(obj.IsValid()):
            return False
        _ = obj.Name
        return True
    except Exception:
        return False


def debounce_menu_event(key: str) -> bool:
    now = time.monotonic()
    if key == STATE.last_menu_event_key and (now - STATE.last_menu_event_at) < MENU_EVENT_DEBOUNCE_SEC:
        return False
    STATE.last_menu_event_key = key
    STATE.last_menu_event_at = now
    return True


def mark_world_transition(reason: str = "") -> None:
    STATE.world_transition_until = time.monotonic() + MENU_WORLD_TRANSITION_SEC
    STATE.needs_launcher_teardown = True
    if reason:
        log(f"World transition guard: {reason}")


def in_world_transition() -> bool:
    return time.monotonic() < STATE.world_transition_until


def launcher_suppressed() -> bool:
    return time.monotonic() < STATE.launcher_suppressed_until


def set_launcher_suppression(seconds: float, reason: str = "") -> None:
    STATE.launcher_suppressed_until = max(STATE.launcher_suppressed_until, time.monotonic() + max(0.0, seconds))
    if reason:
        log(f"Launcher suppressed for {seconds:.1f}s: {reason}")


def clear_launcher_suppression(reason: str = "") -> None:
    if STATE.launcher_suppressed_until > time.monotonic():
        STATE.launcher_suppressed_until = 0.0
        if reason:
            log(f"Launcher suppression cleared: {reason}")


def current_launcher_context() -> str:
    if STATE.pause_menu_active:
        return "Pause"
    if STATE.main_menu_active:
        return "Main"
    if STATE.last_launcher_context in ("Pause", "Main"):
        return STATE.last_launcher_context
    return ""


def schedule_dialog_cancel_restore(context: str = "") -> None:
    ctx = context or current_launcher_context()
    if ctx not in ("Pause", "Main"):
        return
    now = time.monotonic()
    STATE.pending_dialog_restore_context = ctx
    STATE.pending_dialog_restore_at = max(STATE.pending_dialog_restore_at, now + DIALOG_CANCEL_RESTORE_DELAY_SEC)
    STATE.pending_dialog_restore_expires_at = max(
        STATE.pending_dialog_restore_expires_at,
        now + DIALOG_CANCEL_RESTORE_TIMEOUT_SEC,
    )


def clear_dialog_cancel_restore() -> None:
    STATE.pending_dialog_restore_context = ""
    STATE.pending_dialog_restore_at = 0.0
    STATE.pending_dialog_restore_expires_at = 0.0


def pc_has_cursor_visible() -> bool:
    pc = get_pc_safe()
    if pc is None:
        return False
    try:
        return bool(pc.bShowMouseCursor)
    except Exception:
        return False


def process_dialog_cancel_restore() -> None:
    ctx = STATE.pending_dialog_restore_context
    if ctx not in ("Pause", "Main"):
        return

    now = time.monotonic()
    if now < STATE.pending_dialog_restore_at:
        return
    if STATE.pending_dialog_restore_expires_at and now > STATE.pending_dialog_restore_expires_at:
        log(f"{ctx} launcher restore expired")
        clear_dialog_cancel_restore()
        return

    # If no definitive menu-open event arrived after a quit/dialog box, assume
    # the dialog was cancelled only when the underlying context is still stable.
    # Do not clear the pending restore just because the cursor is briefly hidden:
    # BL4 hides the title cursor for a few frames while rebuilding the frontend
    # HUD, and clearing here was the cause of the missing title launcher.
    if ctx == "Pause":
        if not player_appears_in_game() or not pc_has_cursor_visible():
            return
        STATE.pause_menu_active = True
        STATE.main_menu_active = False
        STATE.last_launcher_context = "Pause"
    else:
        if player_appears_in_game():
            clear_dialog_cancel_restore()
            return
        if not pc_has_cursor_visible():
            return
        STATE.pause_menu_active = False
        STATE.main_menu_active = True
        STATE.last_launcher_context = "Main"
        STATE.title_menu_pending_until = min(STATE.title_menu_pending_until or 0.0, now)

    clear_dialog_cancel_restore()
    STATE.world_transition_until = 0.0
    clear_launcher_suppression(f"restored {ctx} after cancelled dialog")
    log(f"{ctx} launcher restored after cancelled dialog")


def get_pc_safe() -> Any | None:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        return None
    return pc if live(pc) else None


def launcher_is_healthy() -> bool:
    if not live(STATE.pause_launcher_widget) or not live(STATE.pause_launcher_root):
        return False
    if get_pc_safe() is None:
        return False
    for ref in STATE.launcher_buttons:
        if ref.enabled and not live(ref.button):
            return False
    return True


def drop_launcher_refs() -> None:
    STATE.launcher_buttons.clear()
    STATE.pause_launcher_root = None
    STATE.pause_launcher_tree = None
    STATE.pause_launcher_widget = None
    STATE.pause_launcher_text = None
    STATE.pause_launcher_was_down = False


def drop_menu_refs() -> None:
    STATE.buttons.clear()
    STATE.textures.clear()
    STATE.menu_canvas = None
    STATE.root_canvas = None
    STATE.widget_tree = None
    STATE.overlay_widget = None
    STATE.is_open = False
    STATE.rebind_target = None
    STATE.rebind_status = ""
    STATE.rebind_key_state.clear()
    STATE.text_input_target = None
    STATE.text_input_buffer = ""
    STATE.text_input_key_state.clear()
    STATE.text_input_button_block_until = 0.0
    STATE.text_input_button_block_wait_keys = ()
    STATE.escape_was_down = False
    STATE.input_snapshot = InputSnapshot()
    STATE.launcher_dragging = False
    STATE.launcher_drag_block_until = 0.0
    STATE.position_preview_widget = None
    STATE.position_preview_tree = None
    STATE.position_preview_root = None
    STATE.position_preview_button = None
    STATE.position_preview_visual = None
    STATE.position_preview_text = None


def process_deferred_launcher_work() -> None:
    """Run deferred UMG teardown on the camera tick, never inside menu hooks.

    During gameplay->frontend transitions BL4 can invalidate UMG objects before
    Python wrappers know it.  Touching those wrappers with live(), RemoveFromParent
    or RemoveFromViewport can hard-crash pyunrealsdk. In a transition window we
    therefore only drop Python references and let the engine clean up its own UI.
    """
    if not STATE.needs_launcher_teardown:
        return
    STATE.needs_launcher_teardown = False
    if in_world_transition():
        drop_launcher_refs()
        if STATE.is_open:
            drop_menu_refs()
        return
    remove_pause_launcher()
    if STATE.is_open and in_world_transition():
        drop_menu_refs()


def remove_widget(widget: Any) -> None:
    if widget is None:
        return
    if not live(widget):
        return
    for name in ("RemoveFromParent", "RemoveFromViewport"):
        try:
            getattr(widget, name)()
        except Exception:
            pass


def set_visible_enabled(widget: Any, enabled: bool = True) -> None:
    try_call(widget, "SetVisibility", 0)
    try_call(widget, "SetIsEnabled", bool(enabled))
    try_call(widget, "SetRenderOpacity", 1.0 if enabled else 0.35)


def set_hit_test_invisible(widget: Any) -> None:
    # BL4 maps 3 to HitTestInvisible in the tested environment.
    # This makes the widget and its children non-hit-testable. Use this only
    # for decorative text/backing widgets.
    try_call(widget, "SetVisibility", 3)


def set_self_hit_test_invisible(widget: Any) -> None:
    # UE standard enum: SelfHitTestInvisible = 4. This lets children receive
    # pointer input while the container itself does not act like a glass pane.
    try_call(widget, "SetVisibility", 4)


def estimated_text_height(box_h: float, scale: float, wrap: bool = False) -> float:
    """Best-effort single-line text box height for vertical centering.

    BL4's runtime TextBlock rendering tends to draw at the top of the assigned
    CanvasPanelSlot. Instead of giving labels the entire button height, we size
    the label slot to an estimated text height and place that slot in the
    vertical middle of the container. Wrapped blocks still receive most of the
    available height so multi-line text can flow naturally.
    """
    box_h = max(1.0, float(box_h))
    scale = max(0.10, float(scale))
    if wrap:
        return max(1.0, box_h - 12.0)
    # Runtime TextBlock content draws from the top of its slot. Use a visual
    # line-height estimate, not the full container height, then center that slot.
    # Keep this conservative; a small downward bias is applied in text().
    est = 18.0 * text_render_scale(scale)
    return max(10.0, min(box_h - 4.0, est))


def configure_text_block(tb: Any, *, center: bool = False, wrap: bool = False,
                         wrap_at: float = 0.0, clip: bool = True) -> None:
    """Apply safe text settings shared by labels, rows, and panel headers.

    Border-owned TextBlocks do not automatically center in BL4, especially once
    RenderScale is involved.  We set justification on the text block and also
    ask the parent content widget/border to center its child where supported.
    """
    if center:
        # UE ETextJustify::Center is 1. Pyunrealsdk accepts enum ints for this.
        try_call(tb, "SetJustification", 1)
    if wrap:
        try_call(tb, "SetAutoWrapText", True)
        if wrap_at > 0:
            try_call(tb, "SetWrapTextAt", float(wrap_at))
    else:
        try_call(tb, "SetAutoWrapText", False)
    if clip:
        # Enum value varies by binding, but SetClipping(1) has been harmless in
        # prior probes and prevents large text from visually spilling forever.
        try_call(tb, "SetClipping", 1)


def configure_content_alignment(widget: Any, *, center: bool = True) -> None:
    if not center:
        return
    # HAlign_Center = 2, VAlign_Center = 1 in UE.  Not all BL4 widgets expose
    # these setters, so each call is best-effort.
    try_call(widget, "SetHorizontalAlignment", 2)
    try_call(widget, "SetVerticalAlignment", 1)


def normalize_ui_state_tag(tag: str) -> str:
    tag = str(tag).strip()
    if not tag:
        raise ValueError("Empty UI state tag")
    # gbx.ui.view.stateadd/stateremove expects bare gameplay tag leaf names here,
    # e.g. CINEMATIC and MENU_PAUSE, not UI.Tag.CINEMATIC.
    if tag.startswith("UI.Tag."):
        return tag.split(".")[-1]
    return tag


def get_kismet_system_library() -> Any | None:
    try:
        return class_obj("/Script/Engine.KismetSystemLibrary").ClassDefaultObject
    except Exception:
        return None


def get_console_world_context() -> Any | None:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
        if pc is not None:
            return pc
    except Exception:
        pass
    try:
        return unrealsdk.find_object("Engine", "/Engine/Transient.Engine_0")
    except Exception:
        return None


def run_kismet_console_command(command_text: str) -> bool:
    command_text = safe_str(command_text).strip()
    if not command_text:
        return False
    ksl = get_kismet_system_library()
    if ksl is None:
        log(f"Kismet console command failed: KismetSystemLibrary missing for {command_text}")
        return False

    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    world_context = pc or get_console_world_context()
    specific_player = pc

    if world_context is None:
        log(f"Kismet console command failed: no world context for {command_text}")
        return False

    # UE signature is ExecuteConsoleCommand(WorldContextObject, Command, SpecificPlayer).
    # Passing all three args avoids the pyunrealsdk missing positional args error.
    try:
        ksl.ExecuteConsoleCommand(world_context, command_text, specific_player)
        return True
    except Exception as exc:
        log(f"Kismet console command failed: {command_text}: {exc}")
        return False


def run_ui_state_console_command(action: str, tag: str) -> bool:
    normalized = normalize_ui_state_tag(tag)
    command_text = f"gbx.ui.view.{action} {normalized}"
    ok = run_kismet_console_command(command_text)
    if ok:
        log(f"Ran console command: {command_text}")
    return ok


def push_menu_ui_state() -> None:
    if STATE.pushed_cinematic_tag:
        return
    if run_ui_state_console_command("stateadd", MENU_UI_STATE_TAG):
        STATE.pushed_cinematic_tag = True
        log(f"Pushed UI state {normalize_ui_state_tag(MENU_UI_STATE_TAG)}")


def pop_menu_ui_state() -> None:
    if not STATE.pushed_cinematic_tag:
        return
    try:
        run_ui_state_console_command("stateremove", MENU_UI_STATE_TAG)
        log(f"Popped UI state {normalize_ui_state_tag(MENU_UI_STATE_TAG)}")
    finally:
        STATE.pushed_cinematic_tag = False


def push_pause_menu_ui_state() -> None:
    if run_ui_state_console_command("stateadd", PAUSE_MENU_UI_STATE_TAG):
        log(f"Pushed UI state {normalize_ui_state_tag(PAUSE_MENU_UI_STATE_TAG)}")


def should_restore_pause_menu_state() -> bool:
    return bool(STATE.last_launcher_context == "Pause" or STATE.pause_menu_active)


def pop_cinematic_then_restore_pause_if_needed() -> None:
    restore_pause = should_restore_pause_menu_state()
    pop_menu_ui_state()
    if restore_pause:
        push_pause_menu_ui_state()


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class ButtonRef:
    button: Any
    action: Callable[[], None]
    label: str = ""
    enabled: bool = True
    was_pressed: bool = False
    visual: Any = None
    rect: tuple[float, float, float, float] | None = None
    manual_was_down: bool = False


@dataclass
class SliderRef:
    slider: Any
    last_value: float = 0.0


@dataclass
class InputSnapshot:
    mouse_cursor: Optional[bool] = None
    ignore_look: Optional[bool] = None
    ignore_move: Optional[bool] = None
    block_input: Optional[bool] = None


@dataclass
class MattsBL4ModsMenuState:
    is_open: bool = False
    screen: str = SCREEN_MAIN

    overlay_widget: Any = None
    widget_tree: Any = None
    root_canvas: Any = None
    menu_canvas: Any = None

    pause_launcher_widget: Any = None
    pause_launcher_tree: Any = None
    pause_launcher_root: Any = None
    pause_launcher_text: Any = None
    pause_menu_active: bool = False
    main_menu_active: bool = False
    last_launcher_context: str = ""
    force_pause_launcher: bool = False
    pause_launcher_was_down: bool = False
    launcher_position_mode: str = "top_left"
    launcher_custom_x: float = 60.0
    launcher_custom_y: float = 60.0
    launcher_dragging: bool = False
    launcher_drag_offset_x: float = 0.0
    launcher_drag_offset_y: float = 0.0
    launcher_drag_last_rebuild: float = 0.0
    launcher_drag_block_until: float = 0.0
    launcher_place_picked_up: bool = False
    launcher_place_click_was_down: bool = False
    launcher_place_started_at: float = 0.0
    launcher_place_offset_x: float = 0.0
    launcher_place_offset_y: float = 0.0
    launcher_place_saved_until: float = 0.0
    launcher_place_saved_last_count: int = -1
    position_preview_widget: Any = None
    position_preview_tree: Any = None
    position_preview_root: Any = None
    position_preview_button: Any = None
    position_preview_visual: Any = None
    position_preview_text: Any = None

    # Main-menu fallback detection is intentionally throttled. Scanning every
    # live UserWidget on every camera tick is expensive during map load.
    main_menu_last_probe: float = 0.0
    main_menu_cached_detected: bool = False
    main_menu_last_logged_detected: bool = False
    main_menu_probe_interval: float = 3.0
    main_menu_loading_cooldown_until: float = 0.0
    world_transition_until: float = 0.0
    needs_launcher_teardown: bool = False
    last_menu_event_key: str = ""
    last_menu_event_at: float = 0.0
    launcher_suppressed_until: float = 0.0
    title_menu_pending_until: float = 0.0
    pending_dialog_restore_context: str = ""
    pending_dialog_restore_at: float = 0.0
    pending_dialog_restore_expires_at: float = 0.0

    text_scale: float = TEXT_SCALE_DEFAULT
    text_scale_slider: SliderRef | None = None
    text_scale_slider_pending: float | None = None
    text_scale_last_rebuild: float = 0.0

    records: list[dict[str, Any]] = field(default_factory=list)
    buttons: list[ButtonRef] = field(default_factory=list)
    launcher_buttons: list[ButtonRef] = field(default_factory=list)
    textures: list[Any] = field(default_factory=list)

    selected_idx: int = 0
    scroll_offset: int = 0
    search_text: str = ""
    filter_mode: str = "all"

    settings_option_scroll: int = 0
    settings_keybind_scroll: int = 0
    keybinds_scroll: int = 0

    rebind_target: Optional[dict[str, Any]] = None
    rebind_status: str = ""
    rebind_key_state: dict[str, bool] = field(default_factory=dict)
    rebind_started_at: float = 0.0

    text_input_target: Optional[dict[str, Any]] = None
    text_input_status: str = ""
    text_input_buffer: str = ""
    text_input_key_state: dict[str, bool] = field(default_factory=dict)
    text_input_button_block_until: float = 0.0
    text_input_button_block_wait_keys: tuple[str, ...] = ()

    blocked_pause_events: int = 0
    blocked_input_keys: int = 0

    mod_started_at: float = field(default_factory=time.monotonic)
    main_menu_launcher_delay_logged: bool = False

    input_snapshot: InputSnapshot = field(default_factory=InputSnapshot)
    last_status: str = "Ready"
    reload_refresh_at: float = 0.0
    reload_refresh_name: str = ""
    last_tick: float = 0.0

    # Runtime/layout gates. Hooks can remain installed after the mod is disabled,
    # so every repeating/event path checks runtime_enabled before drawing.
    runtime_enabled: bool = True
    pushed_cinematic_tag: bool = False
    # Raw rendered viewport pixels reported by BL4/UMG.
    viewport_w: float = 1920.0
    viewport_h: float = 1080.0
    # BL4 applies UMG DPI scale to CanvasPanelSlot coordinates. Internal layout
    # must therefore be built in DPI-unscaled layout units.
    viewport_dpi_scale: float = 1.0
    layout_w: float = 1920.0
    layout_h: float = 1080.0
    ui_scale: float = 1.0
    viewport_scale_x: float = 1.0
    viewport_scale_y: float = 1.0
    viewport_probe: str = "unprobed"
    last_mouse: tuple[bool, float, float, bool] = (False, 0.0, 0.0, False)
    escape_was_down: bool = False
    escape_block_until: float = 0.0
    escape_block_wait_release: bool = False
    escape_defer_ui_pop: bool = False
    escape_pending_close: bool = False
    escape_pending_release_seen_at: float = 0.0
    escape_pending_context: str = ""



STATE = MattsBL4ModsMenuState()


# ---------------------------------------------------------------------------
# User settings persistence
# ---------------------------------------------------------------------------

def _sdk_mods_dir_from_file() -> Path:
    """Locate the real sdk_mods folder for the canonical settings path.

    The settings file intentionally has exactly one supported location:
        <Borderlands 4>/sdk_mods/settings/MattsBL4ModsMenu/native_mods_menu_user_settings.json

    Packed .sdkmod imports still include the real sdk_mods path in __file__, so
    parse that and do not fall back to AppData, home, or the archive folder.
    """
    raw = str(__file__)
    norm = raw.replace("\\", "/")
    lower = norm.lower()
    marker = "/sdk_mods/"
    idx = lower.rfind(marker)
    if idx >= 0:
        return Path(norm[:idx + len(marker) - 1])
    parts = norm.split("/")
    for i, part in enumerate(parts):
        if part.lower() == "sdk_mods":
            return Path("/".join(parts[:i + 1]))
    raise RuntimeError(f"Could not locate sdk_mods directory from __file__={raw!r}")


def _settings_base_dir() -> Path:
    return _sdk_mods_dir_from_file() / "settings" / "MattsBL4ModsMenu"


def user_settings_path() -> Path:
    path = _settings_base_dir() / USER_SETTINGS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_user_settings() -> None:
    try:
        path = user_settings_path()
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        mode = safe_str(data.get("launcher_position_mode"), "top_left")
        if mode not in LAUNCHER_POSITION_PRESETS:
            mode = "top_left"
        STATE.launcher_position_mode = mode
        STATE.launcher_custom_x = float(data.get("launcher_custom_x", STATE.launcher_custom_x))
        STATE.launcher_custom_y = float(data.get("launcher_custom_y", STATE.launcher_custom_y))
        if "text_scale" in data:
            STATE.text_scale = clamp_text_scale(data.get("text_scale"))
    except Exception as exc:
        log(f"User settings load failed from canonical path: {exc}")


def save_user_settings() -> None:
    data = {
        "version": VERSION,
        "launcher_position_mode": STATE.launcher_position_mode,
        "launcher_custom_x": float(STATE.launcher_custom_x),
        "launcher_custom_y": float(STATE.launcher_custom_y),
        "text_scale": float(STATE.text_scale),
    }
    try:
        path = user_settings_path()
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        log(f"User settings save failed to canonical path: {exc}")

# ---------------------------------------------------------------------------
# Viewport scaling
# ---------------------------------------------------------------------------

def _vec2_xy(value: Any) -> tuple[float, float] | None:
    try:
        return float(value.X), float(value.Y)
    except Exception:
        pass
    try:
        return float(value.x), float(value.y)
    except Exception:
        pass
    try:
        vals = list(value)
        if len(vals) >= 2:
            return float(vals[0]), float(vals[1])
    except Exception:
        pass
    return None


def _valid_viewport_size(w: float, h: float) -> bool:
    # Ignore tiny/default/debug values, but allow normal 720p+ viewports.
    return w >= 1000.0 and h >= 600.0


def get_viewport_size() -> tuple[float, float]:
    """Probe the real game viewport using several BL4/UE paths.

    The previous build depended mostly on PlayerController.GetViewportSize, which
    can fail or return a smaller logical size depending on the binding path.  We
    now prefer WidgetLayoutLibrary because it reports the viewport size UMG is
    mapping against, then fall back through PlayerController/GameViewport paths.
    """
    candidates: list[tuple[str, float, float]] = []

    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None

    # UMG's own viewport-size helper is usually the coordinate space we need.
    try:
        if pc is not None:
            wll = class_obj("/Script/UMG.WidgetLayoutLibrary").ClassDefaultObject
            res = wll.GetViewportSize(pc)
            xy = _vec2_xy(res)
            if xy is not None:
                candidates.append(("WidgetLayoutLibrary.GetViewportSize", xy[0], xy[1]))
    except Exception:
        pass

    # PlayerController out-param variations.
    try:
        if pc is not None:
            for args in ((0, 0), (0.0, 0.0)):
                try:
                    res = pc.GetViewportSize(*args)
                    if isinstance(res, tuple):
                        vals = list(res)
                        if len(vals) >= 3:
                            candidates.append(("PlayerController.GetViewportSize[3]", float(vals[1]), float(vals[2])))
                        elif len(vals) >= 2:
                            candidates.append(("PlayerController.GetViewportSize[2]", float(vals[0]), float(vals[1])))
                except Exception:
                    pass
    except Exception:
        pass

    # GameViewport / Viewport object paths. These vary by build, so keep them
    # best-effort and only use sane results.
    try:
        engine = unrealsdk.find_object("Engine", "/Engine/Transient.Engine_0")
    except Exception:
        engine = None
    try:
        gv = getattr(engine, "GameViewport", None) if engine is not None else None
        vp = getattr(gv, "Viewport", None) if gv is not None else None
        if vp is not None:
            for meth in ("GetSizeXY", "GetViewportSize"):
                try:
                    res = getattr(vp, meth)()
                    xy = _vec2_xy(res)
                    if xy is not None:
                        candidates.append((f"GameViewport.Viewport.{meth}", xy[0], xy[1]))
                except Exception:
                    pass
    except Exception:
        pass

    sane = [(name, max(1.0, w), max(1.0, h)) for name, w, h in candidates if _valid_viewport_size(float(w), float(h))]
    if sane:
        # Choose the largest area. On ultrawide/high-res setups this avoids a
        # smaller logical fallback that makes the menu draw in the top-left.
        name, w, h = max(sane, key=lambda item: item[1] * item[2])
        STATE.viewport_probe = f"{name}={int(w)}x{int(h)} candidates={[(n, int(a), int(b)) for n, a, b in candidates]}"
        return w, h

    STATE.viewport_probe = f"fallback=1920x1080 candidates={[(n, int(a), int(b)) for n, a, b in candidates]}"
    return 1920.0, 1080.0


def get_viewport_dpi_scale() -> float:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
        wll = class_obj("/Script/UMG.WidgetLayoutLibrary").ClassDefaultObject
        scale = float(wll.GetViewportScale(pc) or 1.0)
        if 0.05 <= scale <= 8.0:
            return scale
    except Exception:
        pass
    return 1.0


def update_layout_metrics() -> None:
    raw_w, raw_h = get_viewport_size()
    dpi = get_viewport_dpi_scale()
    if dpi <= 0.0:
        dpi = 1.0

    STATE.viewport_w = raw_w
    STATE.viewport_h = raw_h
    STATE.viewport_dpi_scale = dpi

    # CanvasPanelSlot coordinates are DPI-scaled by UMG before they reach the
    # rendered viewport.  Use DPI-unscaled layout units internally so a full
    # backdrop/corner probe really covers the visible viewport.
    STATE.layout_w = max(1.0, raw_w / dpi)
    STATE.layout_h = max(1.0, raw_h / dpi)

    # Map the old 1920x1080 design to the true DPI-unscaled layout space.
    STATE.viewport_scale_x = max(0.10, min(8.0, STATE.layout_w / 1920.0))
    STATE.viewport_scale_y = max(0.10, min(8.0, STATE.layout_h / 1080.0))
    STATE.ui_scale = max(0.50, min(3.0, min(STATE.viewport_scale_x, STATE.viewport_scale_y)))


def sx(value: float) -> float:
    return float(value) * float(STATE.viewport_scale_x)


def sy(value: float) -> float:
    return float(value) * float(STATE.viewport_scale_y)


def screen_x(value: float) -> float:
    return sx(value) * float(STATE.viewport_dpi_scale)


def screen_y(value: float) -> float:
    return sy(value) * float(STATE.viewport_dpi_scale)


def scaled_rect(x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
    # Mouse positions come back in rendered viewport pixels, while widgets are
    # placed in DPI-unscaled UMG layout units. Store hit rects in mouse space.
    return screen_x(x), screen_y(y), screen_x(w), screen_y(h)


def clamp_text_scale(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = TEXT_SCALE_DEFAULT
    return max(TEXT_SCALE_MIN, min(TEXT_SCALE_MAX, v))


def text_scale_to_slider_value(value: float) -> float:
    value = clamp_text_scale(value)
    span = max(0.001, TEXT_SCALE_MAX - TEXT_SCALE_MIN)
    return max(0.0, min(1.0, (value - TEXT_SCALE_MIN) / span))


def slider_value_to_text_scale(value: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = text_scale_to_slider_value(STATE.text_scale)
    v = max(0.0, min(1.0, v))
    return clamp_text_scale(TEXT_SCALE_MIN + v * (TEXT_SCALE_MAX - TEXT_SCALE_MIN))


def text_render_scale(base_scale: float) -> float:
    # CanvasPanel coordinates must be DPI-unscaled, but text visual size still
    # needs a user-facing multiplier. STATE.ui_scale tracks layout growth;
    # STATE.text_scale lets users tune readability at runtime.
    return float(base_scale) * float(STATE.ui_scale) * float(STATE.text_scale)


def set_text_scale(value: float, *, rebuild: bool = True) -> None:
    old = STATE.text_scale
    new = clamp_text_scale(value)
    # Snap to tenths so the UI is stable/readable.
    new = round(new / TEXT_SCALE_STEP) * TEXT_SCALE_STEP
    new = clamp_text_scale(new)
    if abs(old - new) < 0.001:
        return
    STATE.text_scale = new
    save_user_settings()
    log(f"Text scale set to {new:.2f}x")
    if rebuild and STATE.is_open:
        rebuild_menu()


def adjust_text_scale(delta: float) -> None:
    set_text_scale(STATE.text_scale + float(delta), rebuild=True)


def launcher_render_size() -> tuple[float, float]:
    """Return the launcher's actual rendered size in raw screen pixels.

    The standalone viewport widget is sized in UMG layout units and then drawn
    through the current viewport DPI scale.  The saved button position is now
    raw screen pixels, so hit testing, dragging, and previews use this rendered
    size instead of the unscaled LAUNCHER_W/LAUNCHER_H constants.
    """
    update_layout_metrics()
    dpi = max(0.05, float(STATE.viewport_dpi_scale or 1.0))
    return float(LAUNCHER_W) * dpi, float(LAUNCHER_H) * dpi


def clamp_launcher_custom_position(x: float, y: float) -> tuple[float, float]:
    update_layout_metrics()
    rw, rh = launcher_render_size()
    max_x = max(0.0, float(STATE.viewport_w) - rw)
    max_y = max(0.0, float(STATE.viewport_h) - rh)
    return max(0.0, min(max_x, float(x))), max(0.0, min(max_y, float(y)))


def launcher_position() -> tuple[float, float]:
    update_layout_metrics()
    margin = LAUNCHER_MARGIN
    rw, rh = launcher_render_size()
    max_x = max(0.0, float(STATE.viewport_w) - rw - margin)
    max_y = max(0.0, float(STATE.viewport_h) - rh - margin)
    mode = STATE.launcher_position_mode
    if mode == "top_right":
        return max_x, margin
    if mode == "bottom_left":
        return margin, max_y
    if mode == "bottom_right":
        return max_x, max_y
    if mode == "custom":
        return clamp_launcher_custom_position(STATE.launcher_custom_x, STATE.launcher_custom_y)
    return margin, margin


def launcher_position_label() -> str:
    label = LAUNCHER_POSITION_LABELS.get(STATE.launcher_position_mode, "Top Left")
    if STATE.launcher_position_mode == "custom":
        x, y = launcher_position()
        return f"Custom ({int(x)}, {int(y)})"
    return label


def apply_launcher_position_change(*, rebuild: bool = True) -> None:
    if STATE.launcher_position_mode == "custom":
        STATE.launcher_custom_x, STATE.launcher_custom_y = clamp_launcher_custom_position(
            STATE.launcher_custom_x, STATE.launcher_custom_y
        )
    save_user_settings()
    sync_launcher_geometry()
    if rebuild and STATE.is_open:
        rebuild_menu()


def set_launcher_position_mode(mode: str) -> None:
    mode = safe_str(mode, "top_left")
    if mode not in LAUNCHER_POSITION_PRESETS:
        mode = "top_left"
    STATE.launcher_position_mode = mode
    apply_launcher_position_change(rebuild=True)
    log(f"Matt's Mods button position: {launcher_position_label()}")



def nudge_launcher_custom(dx: float, dy: float) -> None:
    x, y = launcher_position()
    STATE.launcher_position_mode = "custom"
    STATE.launcher_custom_x, STATE.launcher_custom_y = clamp_launcher_custom_position(x + float(dx), y + float(dy))
    apply_launcher_position_change(rebuild=True)
    log(f"Matt's Mods custom position set to {int(STATE.launcher_custom_x)}, {int(STATE.launcher_custom_y)}")


def set_launcher_custom_position(x: float, y: float, *, rebuild: bool = True, quiet: bool = False) -> None:
    STATE.launcher_position_mode = "custom"
    STATE.launcher_custom_x, STATE.launcher_custom_y = clamp_launcher_custom_position(float(x), float(y))
    apply_launcher_position_change(rebuild=rebuild)
    if not quiet:
        log(f"Matt's Mods custom position set to {int(STATE.launcher_custom_x)}, {int(STATE.launcher_custom_y)}")



def sync_launcher_position_preview_geometry() -> None:
    if not live(STATE.position_preview_widget):
        return
    px, py = launcher_position()
    try_call(STATE.position_preview_widget, "SetAlignmentInViewport", vec2(0, 0))
    try_call(STATE.position_preview_widget, "SetPositionInViewport", vec2(px, py), True)
    try_call(STATE.position_preview_widget, "SetDesiredSizeInViewport", vec2(LAUNCHER_W, LAUNCHER_H))
    try_call(STATE.position_preview_widget, "SetRenderOpacity", 0.62 if not STATE.launcher_dragging else 0.82)
    if live(STATE.position_preview_button):
        set_raw_slot(STATE.position_preview_button, 0, 0, LAUNCHER_W, LAUNCHER_H, 10)
    if live(STATE.position_preview_visual):
        set_raw_slot(STATE.position_preview_visual, 0, 0, LAUNCHER_W, LAUNCHER_H, 5)
    if live(STATE.position_preview_text):
        set_raw_slot(STATE.position_preview_text, 10, 27, LAUNCHER_W - 20, 44, 20)


def remove_launcher_position_preview(*, safe: bool = True) -> None:
    widget = STATE.position_preview_widget
    root = STATE.position_preview_root
    STATE.position_preview_widget = None
    STATE.position_preview_tree = None
    STATE.position_preview_root = None
    STATE.position_preview_button = None
    STATE.position_preview_visual = None
    STATE.position_preview_text = None
    STATE.launcher_dragging = False
    if not safe or in_world_transition() or get_pc_safe() is None:
        return
    remove_widget(root)
    remove_widget(widget)


def build_launcher_position_preview() -> None:
    """Draw the draggable launcher preview as its own viewport widget.

    This uses the exact same viewport path as the real Matt's Mods launcher instead
    of approximating the position inside the menu canvas.  It avoids DPI/layout
    drift and gives the polling drag code a single raw-screen rectangle to track.
    """
    if not (STATE.is_open and STATE.screen == SCREEN_BUTTON_POSITION):
        remove_launcher_position_preview()
        return
    try:
        pc = get_pc_safe()
        outer = pc or get_main_hud()
        if outer is None or not live(outer):
            return
        if not live(STATE.position_preview_widget) or not live(STATE.position_preview_root):
            remove_launcher_position_preview()
            widget = construct("/Script/UMG.UserWidget", outer)
            widget.WidgetTree = construct("/Script/UMG.WidgetTree", widget)
            root = construct("/Script/UMG.CanvasPanel", widget.WidgetTree)
            widget.WidgetTree.RootWidget = root
            set_visible_enabled(root)
            try_call(widget, "SetAlignmentInViewport", vec2(0, 0))
            try_call(widget, "SetDesiredSizeInViewport", vec2(LAUNCHER_W, LAUNCHER_H))
            try_call(widget, "AddToViewport", 1000001)
            try_call(widget, "SetVisibility", 0)
            try_call(widget, "ForceLayoutPrepass")

            btn = construct("/Script/UMG.Button", widget.WidgetTree)
            root.AddChild(btn)
            set_visible_enabled(btn, True)
            try_call(btn, "SetRenderOpacity", 0.03)
            set_raw_slot(btn, 0, 0, LAUNCHER_W, LAUNCHER_H, 10)

            bg = construct("/Script/UMG.Border", widget.WidgetTree)
            try_call(bg, "SetBrushColor", color(0.00, 0.90, 0.40, 0.72))
            set_hit_test_invisible(bg)
            root.AddChild(bg)
            set_raw_slot(bg, 0, 0, LAUNCHER_W, LAUNCHER_H, 5)

            tb = construct("/Script/UMG.TextBlock", widget.WidgetTree)
            try_call(tb, "SetText", "SDK MODS")
            try_call(tb, "SetJustification", 1)
            try_call(tb, "SetRenderScale", vec2(1.55, 1.55))
            set_hit_test_invisible(tb)
            root.AddChild(tb)
            set_raw_slot(tb, 10, 27, LAUNCHER_W - 20, 44, 20)

            STATE.position_preview_widget = widget
            STATE.position_preview_tree = widget.WidgetTree
            STATE.position_preview_root = root
            STATE.position_preview_button = btn
            STATE.position_preview_visual = bg
            STATE.position_preview_text = tb
        sync_launcher_position_preview_geometry()
    except Exception as exc:
        log(f"Position preview failed: {exc}")

def begin_launcher_position_input(axis: str) -> None:
    axis = "y" if safe_str(axis).lower() == "y" else "x"
    x, y = launcher_position()
    STATE.rebind_target = None
    STATE.rebind_key_state.clear()
    STATE.text_input_target = {"launcher_axis": axis}
    STATE.text_input_buffer = str(int(y if axis == "y" else x))
    STATE.text_input_key_state = snapshot_text_input_key_state()
    STATE.text_input_status = f"Editing launcher {axis.upper()}"
    log(STATE.text_input_status)
    rebuild_menu()


def launcher_text_field_value(axis: str) -> str:
    axis = "y" if safe_str(axis).lower() == "y" else "x"
    if STATE.text_input_target and STATE.text_input_target.get("launcher_axis") == axis:
        return STATE.text_input_buffer if STATE.text_input_buffer else ""
    x, y = launcher_position()
    return str(int(y if axis == "y" else x))


def launcher_text_field_active(axis: str) -> bool:
    axis = "y" if safe_str(axis).lower() == "y" else "x"
    return bool(STATE.text_input_target and STATE.text_input_target.get("launcher_axis") == axis)


def poll_launcher_position_drag() -> None:
    """Click-pickup / click-drop placement for the Matt's Mods launcher.

    BL4 does not expose a reliable held-left-click state for runtime UMG
    widgets, so true drag-hold fails.  This uses the proven V3 behavior:
    click the translucent button once to pick it up, move the mouse, then
    click again to drop/save it.  After saving, it shows a short countdown
    before returning to the main Mods screen so the transition is not abrupt.
    """
    if not (STATE.is_open and STATE.screen == SCREEN_BUTTON_POSITION):
        STATE.launcher_dragging = False
        STATE.launcher_place_picked_up = False
        STATE.launcher_place_click_was_down = False
        STATE.launcher_place_saved_until = 0.0
        STATE.launcher_place_saved_last_count = -1
        return

    build_launcher_position_preview()
    now = time.monotonic()

    # Saved state: keep the placement overlay visible for a readable countdown,
    # then return to the main Mods screen.
    if STATE.launcher_place_saved_until > 0.0:
        remaining = STATE.launcher_place_saved_until - now
        if remaining <= 0.0:
            STATE.launcher_place_saved_until = 0.0
            STATE.launcher_place_saved_last_count = -1
            remove_launcher_position_preview()
            set_screen_main()
            return
        count = max(1, int(remaining) + 1)
        if count != STATE.launcher_place_saved_last_count:
            STATE.launcher_place_saved_last_count = count
            STATE.last_status = f"Location saved. Returning to Mods menu in {count}..."
            build_button_position_screen()
        return

    ok, mx, my = get_mouse_position_safe()
    if not ok:
        return

    px, py = launcher_position()
    rw, rh = launcher_render_size()
    rect = (px, py, rw, rh)

    preview_pressed = False
    preview_hovered = False
    if live(STATE.position_preview_button):
        try:
            preview_pressed = bool(STATE.position_preview_button.IsPressed())
        except Exception:
            preview_pressed = False
        try:
            preview_hovered = bool(STATE.position_preview_button.IsHovered())
        except Exception:
            preview_hovered = False

    # PC LeftMouseButton stays false in this UI state, but keep it as a harmless
    # fallback if a future build routes it.
    pc_down = left_mouse_down_safe()
    click_pulse = bool(preview_pressed or pc_down)
    just_clicked = click_pulse and not STATE.launcher_place_click_was_down

    if not STATE.launcher_place_picked_up:
        if just_clicked and (preview_pressed or preview_hovered or rect_contains(rect, mx, my)):
            STATE.launcher_place_picked_up = True
            STATE.launcher_dragging = True
            STATE.launcher_place_started_at = time.monotonic()
            STATE.launcher_place_offset_x = float(mx) - float(px)
            STATE.launcher_place_offset_y = float(my) - float(py)
            STATE.launcher_position_mode = "custom"
            STATE.last_status = "Move Matt's Mods button: move mouse, click again to place"
            sync_launcher_position_preview_geometry()
            build_button_position_screen()
    else:
        nx = float(mx) - float(STATE.launcher_place_offset_x)
        ny = float(my) - float(STATE.launcher_place_offset_y)
        STATE.launcher_position_mode = "custom"
        STATE.launcher_custom_x, STATE.launcher_custom_y = clamp_launcher_custom_position(nx, ny)
        sync_launcher_position_preview_geometry()

        if just_clicked and time.monotonic() - float(STATE.launcher_place_started_at or 0.0) > 0.25:
            STATE.launcher_place_picked_up = False
            STATE.launcher_dragging = False
            save_user_settings()
            sync_launcher_geometry()
            sync_launcher_position_preview_geometry()
            STATE.launcher_place_saved_until = time.monotonic() + 3.0
            STATE.launcher_place_saved_last_count = -1
            STATE.last_status = f"Location saved. Returning to Mods menu in 3..."
            log(f"Matt's Mods custom position set to {int(STATE.launcher_custom_x)}, {int(STATE.launcher_custom_y)}")
            build_button_position_screen()

    STATE.launcher_place_click_was_down = bool(click_pulse)


# ---------------------------------------------------------------------------
# Native UMG factory - all production menu widgets come from one WidgetTree.
# ---------------------------------------------------------------------------

class NativeUMGFactory:
    def __init__(self, owner_widget: Any):
        self.owner = owner_widget
        self.tree = owner_widget.WidgetTree

    def widget(self, path: str):
        return construct(path, self.tree)

    def add_child(self, parent: Any, child: Any) -> None:
        if parent is None or child is None:
            return
        if hasattr(parent, "AddChild"):
            parent.AddChild(child)
        elif hasattr(parent, "SetContent"):
            parent.SetContent(child)

    def set_slot(self, widget: Any, x: float, y: float, w: float, h: float, z: int = 0) -> None:
        # Do not use render translation for layout. CanvasPanelSlot is the source of truth.
        # Incoming coordinates are design-space 1920x1080 and are scaled here.
        slot = getattr(widget, "slot", None)
        if slot is not None:
            try_call(slot, "SetPosition", vec2(sx(x), sy(y)))
            try_call(slot, "SetSize", vec2(sx(w), sy(h)))
            try_call(slot, "SetZOrder", int(z))
            try_call(slot, "SetAutoSize", False)

    def canvas(self, parent: Any | None, x: float, y: float, w: float, h: float, z: int = 0):
        c = self.widget("/Script/UMG.CanvasPanel")
        set_visible_enabled(c)
        if parent is not None:
            self.add_child(parent, c)
            self.set_slot(c, x, y, w, h, z)
        return c

    def border(self, parent: Any, x: float, y: float, w: float, h: float,
               fill: tuple[float, float, float, float], z: int = 0, *,
               hit_test: bool = False):
        b = self.widget("/Script/UMG.Border")
        try_call(b, "SetBrushColor", color(*fill))
        if hit_test:
            set_visible_enabled(b)
        else:
            set_hit_test_invisible(b)
        self.add_child(parent, b)
        self.set_slot(b, x, y, w, h, z)
        return b

    def text(self, parent: Any, value: str, x: float, y: float, w: float, h: float,
             scale: float = 1.0, z: int = 10, *, center: bool = False,
             wrap: bool = False):
        tb = self.widget("/Script/UMG.TextBlock")
        try_call(tb, "SetText", str(value))
        render_scale = text_render_scale(scale)
        try_call(tb, "SetRenderScale", vec2(render_scale, render_scale))
        try_call(tb, "SetRenderTransformPivot", vec2(0.5, 0.5))
        configure_text_block(tb, center=center, wrap=wrap, wrap_at=sx(max(0.0, w - 24.0)))
        set_hit_test_invisible(tb)
        self.add_child(parent, tb)

        slot_h = estimated_text_height(h, scale, wrap=wrap)
        # BL4 TextBlock glyphs sit slightly above the visual midpoint. Add a
        # small downward bias after centering; keep it bounded so large text
        # scales do not drift out of compact buttons.
        down_bias = 0.0 if wrap else min(max(4.0, 5.5 * render_scale), max(4.0, h * 0.22))
        slot_y = y + max(0.0, (h - slot_h) * 0.5) + down_bias
        slot = getattr(tb, "slot", None)
        if slot is not None:
            try_call(slot, "SetPosition", vec2(sx(x), sy(slot_y)))
            try_call(slot, "SetSize", vec2(sx(w), sy(slot_h)))
            try_call(slot, "SetZOrder", int(z))
            try_call(slot, "SetAutoSize", False)
            try_call(slot, "SetAlignment", vec2(0.0, 0.0))
        return tb

    def border_text(self, parent: Any, value: str, x: float, y: float, w: float, h: float,
                    fill: tuple[float, float, float, float], scale: float = 0.72,
                    z: int = 20, *, center: bool = True, wrap: bool = False):
        # Keep text as a CanvasPanel sibling instead of Border.SetContent. In BL4,
        # Border-owned TextBlocks report only desired size, so large RenderScale
        # values drift/clamp instead of staying centered inside the full card. A
        # sibling TextBlock with an explicit centered slot keeps headers, rows,
        # and button labels visually centered across resolutions and text scales.
        b = self.border(parent, x, y, w, h, fill, z)
        if wrap:
            self.text(parent, str(value), x + 14, y + 8, max(1.0, w - 28), max(1.0, h - 16), scale, z + 1, center=False, wrap=True)
        else:
            self.text(parent, str(value), x + 8, y + 2, max(1.0, w - 16), max(1.0, h - 4), scale, z + 1, center=center, wrap=False)
        return b

    def button(self, parent: Any, label: str, x: float, y: float, w: float, h: float,
               action: Callable[[], None], *, fill=(0.0, 0.20, 0.24, 0.96),
               scale: float = 0.58, z: int = 50, enabled: bool = True,
               registry: list[ButtonRef] | None = None):
        # Use the proven input pattern: a real UMG Button for hit testing, but
        # make the stock UE button nearly transparent. The visible row/button
        # color is our own Border sibling behind a HitTestInvisible TextBlock.
        # This restores the ON/OFF enabled/disabled row colors instead of the
        # default grey stock button style taking over the whole row.
        back = self.border(parent, x, y, w, h, fill, z, hit_test=False)

        btn = self.widget("/Script/UMG.Button")
        self.add_child(parent, btn)
        self.set_slot(btn, x, y, w, h, z + 1)
        set_visible_enabled(btn, enabled)
        try_call(btn, "SetRenderOpacity", 0.03 if enabled else 0.015)

        # Draw the label above the invisible hit target. The text helper sizes
        # and centers the label slot vertically inside the row/button.
        self.text(parent, str(label), x + 8, y + 2, max(1.0, w - 16), max(1.0, h - 4), scale, z + 2, center=True, wrap=False)

        ref = ButtonRef(
            button=btn,
            action=action,
            label=label,
            enabled=enabled,
            visual=back,
            rect=scaled_rect(x, y, w, h),
        )
        (registry if registry is not None else STATE.buttons).append(ref)
        return btn

    def slider(self, parent: Any, x: float, y: float, w: float, h: float,
               value: float, z: int = 90):
        sld = self.widget("/Script/UMG.Slider")
        self.add_child(parent, sld)
        self.set_slot(sld, x, y, w, h, z)
        set_visible_enabled(sld, True)
        # Use the default 0..1 slider range for maximum compatibility and map
        # that normalized value to TEXT_SCALE_MIN..TEXT_SCALE_MAX in polling.
        try_call(sld, "SetValue", float(value))
        try_call(sld, "SetStepSize", 0.01)
        return sld

    def panel(self, parent: Any, title: str, x: float, y: float, w: float, h: float):
        self.border(parent, x + 6, y + 6, w, h, (0.0, 0.0, 0.0, 0.38), 7)
        self.border(parent, x, y, w, h, (0.015, 0.050, 0.062, 0.985), 8)
        self.border_text(parent, title, x, y, w, 58, (0.00, 0.48, 0.55, 0.99), 0.46, 20)

    def fullscreen_backdrop(self, parent: Any, fill=(0.0, 0.015, 0.020, 0.965), z: int = 1):
        # Use actual viewport pixels for the backdrop so ultrawide/high-res screens
        # are covered even though the content composition remains 16:9 scaled.
        b = self.widget("/Script/UMG.Border")
        try_call(b, "SetBrushColor", color(*fill))
        set_hit_test_invisible(b)
        self.add_child(parent, b)
        slot = getattr(b, "slot", None)
        if slot is not None:
            try_call(slot, "SetPosition", vec2(0, 0))
            try_call(slot, "SetSize", vec2(STATE.layout_w, STATE.layout_h))
            try_call(slot, "SetZOrder", int(z))
            try_call(slot, "SetAutoSize", False)
        return b

    def scrollbar(self, parent: Any, x: float, y: float, h: float, total: int, visible: int,
                  offset: int, up_action: Callable[[], None], down_action: Callable[[], None]):
        max_off = max(0, total - visible)
        self.button(parent, "▲", x, y, 34, 36, up_action, fill=(0.0, 0.30, 0.34, 0.96), scale=0.42, z=70, enabled=offset > 0)
        track_y = y + 46
        track_h = h - 92
        self.border(parent, x + 9, track_y, 16, track_h, (0.0, 0.06, 0.07, 0.98), 40)
        thumb_h = track_h if total <= visible else max(48, track_h * (visible / max(1, total)))
        thumb_y = track_y if max_off <= 0 else track_y + (track_h - thumb_h) * (offset / max_off)
        self.border(parent, x + 11, thumb_y, 12, thumb_h, (0.10, 0.90, 1.0, 0.99), 50)
        self.button(parent, "▼", x, y + h - 36, 34, 36, down_action, fill=(0.0, 0.30, 0.34, 0.96), scale=0.42, z=70, enabled=offset < max_off)


# ---------------------------------------------------------------------------
# Overlay ownership
# ---------------------------------------------------------------------------

def get_main_hud() -> Any | None:
    try:
        huds = [
            w for w in unrealsdk.find_all("UserWidget", False)
            if "WBP_MainHud_C_" in str(w) and "Default__" not in str(w) and hasattr(w, "WidgetTree")
        ]
        for hud in huds:
            try:
                if hud.WidgetTree and hud.WidgetTree.RootWidget:
                    return hud
            except Exception:
                pass
        return huds[0] if huds else None
    except Exception:
        return None


def create_overlay() -> Any:
    if live(STATE.overlay_widget) and live(STATE.root_canvas):
        return STATE.overlay_widget

    pc = None
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pass
    outer = pc or get_main_hud()
    if outer is None:
        raise RuntimeError("No player controller or live HUD found. Load into gameplay first.")

    widget = construct("/Script/UMG.UserWidget", outer)
    widget.WidgetTree = construct("/Script/UMG.WidgetTree", widget)
    root = construct("/Script/UMG.CanvasPanel", widget.WidgetTree)
    widget.WidgetTree.RootWidget = root
    set_visible_enabled(root)

    # Match the proven CreateRemoveButton pattern: set viewport alignment,
    # position, and desired size before AddToViewport.
    update_layout_metrics()
    try_call(widget, "SetAlignmentInViewport", vec2(0, 0))
    try_call(widget, "SetPositionInViewport", vec2(0, 0), False)
    try_call(widget, "SetDesiredSizeInViewport", vec2(STATE.layout_w, STATE.layout_h))
    try_call(widget, "AddToViewport", 999999)
    try_call(widget, "SetVisibility", 0)
    try_call(widget, "SetRenderOpacity", 1.0)
    try_call(widget, "ForceLayoutPrepass")

    STATE.overlay_widget = widget
    STATE.widget_tree = widget.WidgetTree
    STATE.root_canvas = root
    log("Created native UMG viewport overlay")
    return widget


def factory() -> NativeUMGFactory:
    return NativeUMGFactory(create_overlay())


def clear_menu_canvas() -> tuple[NativeUMGFactory, Any]:
    update_layout_metrics()
    if live(STATE.overlay_widget):
        try_call(STATE.overlay_widget, "SetDesiredSizeInViewport", vec2(STATE.layout_w, STATE.layout_h))
    f = factory()
    STATE.buttons.clear()
    STATE.text_scale_slider = None
    remove_widget(STATE.menu_canvas)
    STATE.menu_canvas = f.canvas(STATE.root_canvas, 0, 0, 1920, 1080, 9999)
    return f, STATE.menu_canvas


# ---------------------------------------------------------------------------
# Input ownership
# ---------------------------------------------------------------------------

def capture_menu_input() -> None:
    snap = STATE.input_snapshot
    try:
        pc = mods_base.get_pc()
        if snap.mouse_cursor is None:
            try:
                snap.mouse_cursor = bool(pc.bShowMouseCursor)
            except Exception:
                pass
        if snap.ignore_look is None:
            try:
                snap.ignore_look = bool(pc.IsLookInputIgnored())
            except Exception:
                pass
        if snap.ignore_move is None:
            try:
                snap.ignore_move = bool(pc.IsMoveInputIgnored())
            except Exception:
                pass

        try:
            pc.bShowMouseCursor = True
            pc.bEnableMouseOverEvents = True
        except Exception:
            pass
        if snap.block_input is None:
            try:
                snap.block_input = bool(pc.bBlockInput)
            except Exception:
                pass
        try:
            pc.bBlockInput = True
        except Exception:
            pass
        try_call(pc, "SetIgnoreLookInput", True)
        try_call(pc, "SetIgnoreMoveInput", True)
    except Exception:
        pass


def sustain_menu_input() -> None:
    if not STATE.is_open:
        return
    try:
        pc = mods_base.get_pc(possibly_loading=True)
        if pc is None:
            return
        pc.bShowMouseCursor = True
        pc.bEnableMouseOverEvents = True
        try:
            pc.bBlockInput = True
        except Exception:
            pass
        try_call(pc, "SetIgnoreLookInput", True)
        try_call(pc, "SetIgnoreMoveInput", True)
    except Exception:
        pass


def apply_menu_input_mode() -> None:
    try:
        pc = mods_base.get_pc()
        pc.bShowMouseCursor = True
        pc.bEnableMouseOverEvents = True
        focus = None
        for ref in STATE.buttons:
            if live(ref.button) and ref.enabled:
                focus = ref.button
                break
        if focus is None and live(STATE.menu_canvas):
            focus = STATE.menu_canvas
        if focus is None:
            return
        try_call(focus, "SetUserFocus", pc)
        try_call(focus, "SetKeyboardFocus")
        try:
            wbl = class_obj("/Script/UMG.WidgetBlueprintLibrary").ClassDefaultObject
            try_call(wbl, "SetInputMode_GameAndUIEx", pc, focus, 0, False, False)
        except Exception:
            pass
    except Exception as exc:
        log(f"Input mode failed: {exc}")


def restore_menu_input() -> None:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    if pc is not None:
        # If the full native menu was opened from BL4's pause/title menu, do not
        # force GameOnly or hide the cursor on close.  The underlying BL4 menu is
        # still active and needs normal mouse routing.  For gameplay/manual open,
        # restore the old GameOnly behavior.
        menu_context = bool(
            STATE.pause_menu_active
            or STATE.main_menu_active
            or STATE.last_launcher_context in ("Pause", "Main")
        )
        if not menu_context:
            try:
                wbl = class_obj("/Script/UMG.WidgetBlueprintLibrary").ClassDefaultObject
                try_call(wbl, "SetInputMode_GameOnly", pc, True)
                try_call(wbl, "SetFocusToGameViewport")
            except Exception:
                pass
        try_call(pc, "SetIgnoreLookInput", False)
        try_call(pc, "SetIgnoreMoveInput", False)
        try_call(pc, "ResetIgnoreLookInput")
        try_call(pc, "ResetIgnoreMoveInput")
        try_call(pc, "ResetIgnoreInputFlags")
        try:
            snap = STATE.input_snapshot
            pc.bBlockInput = bool(snap.block_input) if snap.block_input is not None else False
        except Exception:
            pass
        try:
            if menu_context:
                pc.bShowMouseCursor = True
                pc.bEnableMouseOverEvents = True
            else:
                pc.bShowMouseCursor = bool(STATE.input_snapshot.mouse_cursor) if STATE.input_snapshot.mouse_cursor is not None else False
                pc.bEnableMouseOverEvents = False
        except Exception:
            pass
    STATE.input_snapshot = InputSnapshot()


# ---------------------------------------------------------------------------
# mods_base data model
# ---------------------------------------------------------------------------

def first_attr(obj: Any, names: tuple[str, ...], default: Any = None) -> Any:
    for name in names:
        try:
            value = getattr(obj, name)
            if value is not None:
                return value
        except Exception:
            pass
    return default


def mod_name(m: Any) -> str:
    return safe_str(first_attr(m, ("name", "display_name"), str(m)))


def mod_desc(m: Any) -> str:
    return safe_str(first_attr(m, ("description", "desc"), ""))


def mod_author(m: Any) -> str:
    return safe_str(first_attr(m, ("author", "authors", "creator", "created_by"), "Unknown"), "Unknown")


def mod_version(m: Any) -> str:
    return safe_str(first_attr(m, ("version", "__version__", "Version", "VERSION"), "Unknown"), "Unknown")


def mod_type_name(m: Any) -> str:
    try:
        return safe_str(m.__class__.__name__, "Unknown")
    except Exception:
        return "Unknown"


def mod_path(m: Any) -> str:
    for name in ("module", "__module__"):
        try:
            value = getattr(m, name)
            if value:
                return safe_str(value)
        except Exception:
            pass
    return ""


def mod_reload_identifier(m: Any, display_name: str = "") -> str:
    """Best-effort argument for the SDK console `rlm <modname>` command.

    The console reload command is the source of truth. Different SDK mods expose
    different metadata, so prefer explicit module/package-ish fields, then fall
    back to the displayed mod name. The console command receives the full string
    after `rlm`, so display names with spaces are still useful as a fallback.
    """
    candidates: list[str] = []
    for name in (
        "module_name", "module", "import_name", "import_path", "package",
        "folder", "mod_folder", "mod_dir", "filename", "file_name",
        "__module__",
    ):
        try:
            value = getattr(m, name)
            if value:
                candidates.append(safe_str(value))
        except Exception:
            pass

    for raw in candidates:
        s = safe_str(raw).strip()
        if not s:
            continue
        s = s.replace("\\", "/")
        # mods_base Mod objects report their own class module as mods_base.mod;
        # that is not the reloadable mod module.  In that case the displayed
        # mod name/folder is the useful rlm argument.
        if s in ("mods_base.mod_list", "mods_base", "mods_base.mod") or s.startswith("mods_base."):
            continue
        # Convert path/module-ish strings to the likely top-level package/folder.
        if "/" in s:
            s = s.rstrip("/").split("/")[-1]
        if s.endswith(".py"):
            s = s[:-3]
        if "." in s:
            s = s.split(".")[0]
        if s and s != "__init__":
            return s

    return safe_str(display_name or mod_name(m)).strip()


def _callable_children(obj: Any):
    if callable(obj):
        yield obj
    for attr in ("callback", "func", "function", "handler", "call", "run", "invoke", "action"):
        try:
            child = getattr(obj, attr)
            if callable(child):
                yield child
        except Exception:
            pass


def _find_rlm_handlers() -> list[Callable[..., Any]]:
    handlers: list[Callable[..., Any]] = []
    seen: set[int] = set()

    def add_callable(fn: Callable[..., Any]) -> None:
        ident = id(fn)
        if ident not in seen:
            seen.add(ident)
            handlers.append(fn)

    def add_object(obj: Any) -> None:
        if obj is None:
            return
        for fn in _callable_children(obj):
            add_callable(fn)

    # Match the proven NMM_ReloadAnotherModTest discovery logic.  The previous
    # production version accidentally inspected the imported @command decorator
    # function, not the mods_base.command module/registry, so it found zero rlm
    # handlers from inside the menu callback.
    candidate_modules: dict[str, Any] = {}
    for module_name in (
        "mods_base",
        "mods_base.commands",
        "mods_base.command",
        "unrealsdk",
        "unrealsdk.commands",
        "unrealsdk.command",
        "unrealsdk.console",
        "unrealsdk.console_command",
    ):
        try:
            mod = importlib.import_module(module_name)
            candidate_modules[module_name] = mod
        except Exception:
            pass

    try:
        for module_name, mod in list(sys.modules.items()):
            lname = safe_str(module_name).lower()
            if any(key in lname for key in ("mods_base", "unrealsdk", "command", "console", "reload")):
                candidate_modules.setdefault(module_name, mod)
    except Exception:
        pass

    for _module_name, mod in sorted(candidate_modules.items(), key=lambda item: item[0]):
        if mod is None:
            continue
        try:
            attr_names = list(dir(mod))
        except Exception:
            continue
        for attr_name in attr_names:
            low = safe_str(attr_name).lower()
            try:
                obj = getattr(mod, attr_name)
            except Exception:
                continue

            if low in ("rlm", "reload", "reload_mods", "reload_module"):
                add_object(obj)

            if isinstance(obj, dict):
                try:
                    items = list(obj.items())
                except Exception:
                    items = []
                for key, value in items:
                    if safe_str(key).lower() == "rlm":
                        add_object(value)

    return handlers


def run_rlm_reload(reload_arg: str) -> bool:
    reload_arg = safe_str(reload_arg).strip()
    if not reload_arg:
        return False

    # This is the same path proven by NMM_ReloadAnotherModTest:
    # mods_base.command.rlm.callback(argparse.Namespace(modules=[target])).
    args = argparse.Namespace(modules=[reload_arg])
    for fn in _find_rlm_handlers():
        try:
            fn(args)
            return True
        except SystemExit:
            return True
        except Exception:
            pass

    return False


def normalized_mod_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", safe_str(value).lower())


def record_is_native_mods_menu(rec: dict[str, Any], reload_arg: str = "") -> bool:
    m = rec.get("mod")
    values = [reload_arg, rec.get("name"), mod_name(m), mod_path(m)]
    for attr in ("module_name", "module", "import_name", "import_path", "package", "folder", "mod_folder", "mod_dir", "filename", "file_name", "__module__"):
        try:
            values.append(getattr(m, attr))
        except Exception:
            pass
    return any(normalized_mod_identifier(v) == "bl4nativemodsmenu" for v in values if v)


def schedule_reload_refresh(name: str) -> None:
    # The SDK reload command can replace the target module/mod object after this
    # click callback returns. Refresh the list on the next tick instead of
    # rebuilding while the button poll is still iterating over old widgets.
    STATE.reload_refresh_name = safe_str(name, "mod")
    STATE.reload_refresh_at = time.monotonic() + 1.0


def poll_reload_refresh() -> None:
    if STATE.reload_refresh_at <= 0.0 or time.monotonic() < STATE.reload_refresh_at:
        return
    name = STATE.reload_refresh_name
    STATE.reload_refresh_at = 0.0
    STATE.reload_refresh_name = ""
    try:
        old_name = safe_str(name)
        refresh_records()
        if old_name:
            for idx, rec in enumerate(STATE.records):
                if safe_str(rec.get("name")) == old_name:
                    STATE.selected_idx = idx
                    break
        log(f"Reload refresh completed: {old_name or 'mod'}")
        if STATE.is_open and STATE.screen == SCREEN_MAIN:
            build_main_screen()
    except Exception as exc:
        log(f"Reload refresh failed: {exc}")


def reload_record(rec: dict[str, Any]) -> None:
    try:
        m = rec.get("mod")
        name = safe_str(rec.get("name", "mod"), "mod")
        reload_arg = mod_reload_identifier(m, name) if m is not None else safe_str(name)
        command_text = f"rlm {reload_arg}"
        is_self_reload = record_is_native_mods_menu(rec, reload_arg)

        if is_self_reload:
            # Reloading this mod must still release the viewport first, otherwise
            # the old UMG wrappers can survive into the new module instance.
            log("Reloading MattsBL4ModsMenu; closing overlay first")
            if STATE.is_open:
                close_menu()
        else:
            log(f"Reloading {name}...")

        if run_rlm_reload(reload_arg):
            if not is_self_reload:
                log(f"Reload command sent: {name}")
                schedule_reload_refresh(name)
        else:
            log(f"Reload command failed: {command_text}")
            if STATE.is_open and STATE.screen == SCREEN_MAIN:
                schedule_reload_refresh(name)
    except Exception as exc:
        log(f"Reload failed: {exc}")


def mod_enabled(m: Any) -> bool | None:
    try:
        return bool(m.is_enabled)
    except Exception:
        return None


def mod_toggleable(m: Any) -> bool:
    try:
        return not bool(getattr(m, "enabling_locked", False)) and hasattr(m, "enable") and hasattr(m, "disable")
    except Exception:
        return False


def refresh_records() -> None:
    try:
        mods = list(mods_base.get_ordered_mod_list())
    except Exception as exc:
        log(f"get_ordered_mod_list failed: {exc}")
        mods = []
    STATE.records = [
        {
            "idx": i,
            "mod": m,
            "name": mod_name(m),
            "desc": mod_desc(m),
            "enabled": mod_enabled(m),
            "toggleable": mod_toggleable(m),
        }
        for i, m in enumerate(mods)
    ]
    clamp_mod_scroll()


def _iter_possible_option_children(option: Any) -> list[Any]:
    """Return child options for mods_base GroupedOption-like objects.

    BL4/mods_base often exposes top-level ``GroupedOption`` rows such as
    "Options" and "Keybinds". The actual editable options can live under
    different attribute names depending on SDK/mod version, so this stays broad
    but only returns list/tuple/set-like values.
    """
    children: list[Any] = []
    for attr in (
        "children", "options", "grouped_options", "display_options", "items",
        "sub_options", "suboptions", "option_list", "option_items", "value",
        "values",
    ):
        try:
            val = getattr(option, attr)
        except Exception:
            continue
        if val is None or isinstance(val, (str, bytes, bool, int, float)):
            continue
        try:
            seq = list(val)
        except Exception:
            continue
        for item in seq:
            if item is not None and item is not option:
                children.append(item)
    return children


def _looks_like_grouped_option(option: Any) -> bool:
    try:
        cn = option.__class__.__name__.lower()
    except Exception:
        cn = ""
    name = opt_name(option).strip().lower() if "opt_name" in globals() else ""
    return "group" in cn or name in ("options", "keybinds")


def _flatten_options(options: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[int] = set()

    def visit(opt: Any) -> None:
        if opt is None:
            return
        oid = id(opt)
        if oid in seen:
            return
        seen.add(oid)
        children = _iter_possible_option_children(opt)
        if children:
            for child in children:
                visit(child)
            return
        # Do not show empty grouping headers as editable options.
        if _looks_like_grouped_option(opt):
            return
        out.append(opt)

    for option in options:
        visit(option)
    return out


def _module_option_fallbacks(m: Any) -> list[Any]:
    """Recover options kept in a mod module but hidden behind GroupedOption rows.

    ActorScriptDeployer stores its editable logo rows in ``_LOGO_OPTIONS`` while
    the public menu probe only showed GroupedOption headers. This fallback is
    intentionally generic enough for similar SDK mods, but only uses globals that
    look like option collections.
    """
    out: list[Any] = []
    import sys

    candidates: list[Any] = []
    for attr in ("module", "__module__"):
        try:
            name = getattr(m, attr)
            if name and name in sys.modules:
                candidates.append(sys.modules[name])
        except Exception:
            pass

    try:
        mod_name_l = mod_name(m).replace(" ", "").lower()
    except Exception:
        mod_name_l = ""
    if mod_name_l:
        for name, module in list(sys.modules.items()):
            key = name.replace("_", "").replace(".", "").lower()
            if mod_name_l in key or key.endswith(mod_name_l):
                candidates.append(module)

    seen_mods: set[int] = set()
    for module in candidates:
        if module is None or id(module) in seen_mods:
            continue
        seen_mods.add(id(module))
        for name, value in list(getattr(module, "__dict__", {}).items()):
            lname = name.lower()
            if not (lname.endswith("options") or lname.endswith("_options") or "options" in lname):
                continue
            if isinstance(value, (str, bytes, dict)):
                continue
            try:
                seq = list(value)
            except Exception:
                continue
            for item in seq:
                if item is not None and not _looks_like_grouped_option(item):
                    out.append(item)
    return out


def discover_options(m: Any) -> list[Any]:
    if m is None:
        return []
    base: list[Any] = []
    try:
        if hasattr(m, "iter_display_options"):
            base.extend(list(m.iter_display_options()))
    except Exception:
        pass
    try:
        base.extend(list(getattr(m, "options", []) or []))
    except Exception:
        pass

    flattened = _flatten_options(base)
    # If all public options collapsed to headers, try module-level fallbacks like
    # ActorScriptDeployer._LOGO_OPTIONS.
    if not flattened:
        flattened = _flatten_options(_module_option_fallbacks(m))

    seen: set[int] = set()
    out: list[Any] = []
    for opt in flattened:
        if id(opt) in seen:
            continue
        seen.add(id(opt))
        out.append(opt)
    return out


def keybind_identity(k: Any) -> str:
    """Stable identity for matching duplicate KeybindOption objects.

    Some mods expose a KeybindOption in iter_display_options/options and also a
    separate runtime keybind object in mod.keybinds. Console Mod Menu edits the
    display option path, while the runtime hotkey may read the keybind path.  We
    match them by their display/name so a native rebind updates both objects.
    """
    return keybind_name(k).strip().lower()


def _declared_keybinds(m: Any) -> list[Any]:
    out: list[Any] = []
    for name in ("keybinds", "keybind_list", "bindings"):
        try:
            out.extend(list(getattr(m, name, []) or []))
        except Exception:
            pass
    return out


def discover_keybinds(m: Any) -> list[Any]:
    if m is None:
        return []

    # Prefer the same public display option objects Console Mod Menu uses.  For
    # mods like MattsSDKBoostingTools this avoids showing one value in Dynamic
    # Options and a different value in Keybinds. Declared keybind collections are
    # still used as a fallback / for mods which don't expose keybinds as options.
    out: list[Any] = []
    for opt in discover_options(m):
        try:
            if option_kind(opt) == "keybind":
                out.append(opt)
        except Exception:
            pass

    declared = _declared_keybinds(m)
    known_names = {keybind_identity(x) for x in out if keybind_identity(x)}
    for kb in declared:
        ident = keybind_identity(kb)
        if ident and ident in known_names:
            continue
        out.append(kb)
        if ident:
            known_names.add(ident)

    seen: set[int] = set()
    uniq: list[Any] = []
    for item in out:
        if id(item) in seen:
            continue
        seen.add(id(item))
        uniq.append(item)
    return uniq

def opt_name(o: Any) -> str:
    return safe_str(first_attr(o, ("display_name", "identifier", "name"), str(o)), "Option")


def option_value_raw(o: Any) -> Any:
    for name in ("value", "current_value", "selected_value", "key"):
        try:
            return getattr(o, name)
        except Exception:
            pass
    return None


def opt_value(o: Any) -> str:
    value = option_value_raw(o)
    return "" if value is None else safe_str(value)


def option_kind(o: Any) -> str:
    try:
        cn = o.__class__.__name__.lower()
    except Exception:
        cn = ""
    v = option_value_raw(o)
    name_hint = (opt_name(o) + " " + safe_str(o)).lower()
    if "bool" in cn or isinstance(v, bool):
        return "bool"
    # Keybind options are distinct from editable text/number options. Only mark
    # as keybind if the option class/name clearly says keybind/binding, not just
    # because it stores a string/number value.
    if "keybind" in cn or "keybind" in name_hint or "binding" in cn or "binding" in name_hint:
        return "keybind"
    if "slider" in cn or "float" in cn or isinstance(v, float):
        return "float"
    if "int" in cn or isinstance(v, int):
        return "int"
    if "enum" in cn or "spinner" in cn or "dropdown" in cn:
        return "enum"
    if "key" in cn and not isinstance(v, (str, int, float)):
        return "keybind"
    return "text"


def option_is_text_editable(o: Any) -> bool:
    return option_kind(o) in ("text", "int", "float")


def convert_option_input_value(o: Any, buffer: str) -> Any:
    kind = option_kind(o)
    raw = safe_str(buffer)
    if kind == "int":
        try:
            return int(raw.strip())
        except Exception:
            return option_value_raw(o)
    if kind == "float":
        try:
            return float(raw.strip())
        except Exception:
            return option_value_raw(o)
    return raw


def set_option_value(o: Any, value: Any) -> bool:
    ok = False
    for meth in ("set_value", "set", "set_current_value"):
        try:
            getattr(o, meth)(value)
            ok = True
            break
        except Exception:
            pass
    for attr in ("current_value", "value"):
        try:
            setattr(o, attr, value)
            ok = True
        except Exception:
            pass
    for meth in ("save", "commit", "apply"):
        try:
            getattr(o, meth)()
            ok = True
        except Exception:
            pass
    return ok


def toggle_option(o: Any) -> bool:
    v = option_value_raw(o)
    return set_option_value(o, not v) if isinstance(v, bool) else False


def keybind_name(k: Any) -> str:
    return safe_str(first_attr(k, ("display_name", "name", "identifier", "id"), str(k)), "Keybind")


def keybind_value(k: Any) -> str:
    # Match Console Mod Menu semantics: a KeybindOption value of None means
    # explicitly unbound, not "show/use the default key".  Do not fall through
    # to default_value after seeing value/current_value == None.
    sentinel = object()
    for name in ("current_value", "value", "key"):
        try:
            v = getattr(k, name, sentinel)
        except Exception:
            continue
        if v is sentinel:
            continue
        if v is None:
            return "Unbound"
        return display_key_name(safe_str(v))
    try:
        v = getattr(k, "default_value", sentinel)
        if v is not sentinel and v is not None:
            return display_key_name(safe_str(v))
    except Exception:
        pass
    return "Unbound"


def visible_records() -> list[dict[str, Any]]:
    q = STATE.search_text.strip().lower()
    out = []
    for rec in STATE.records:
        if STATE.filter_mode == "enabled" and rec.get("enabled") is not True:
            continue
        if STATE.filter_mode == "disabled" and rec.get("enabled") is not False:
            continue
        if STATE.filter_mode == "toggleable" and not rec.get("toggleable"):
            continue
        if q:
            m = rec.get("mod")
            hay = " ".join([
                rec.get("name", ""),
                rec.get("desc", ""),
                mod_author(m),
                mod_version(m),
                mod_type_name(m),
                mod_path(m),
            ]).lower()
            if q not in hay:
                continue
        out.append(rec)
    return out


def selected_record() -> dict[str, Any] | None:
    rows = visible_records()
    if not rows:
        return None
    idx = max(0, min(STATE.selected_idx, len(rows) - 1))
    return rows[idx]


def clamp_mod_scroll() -> None:
    rows = visible_records()
    max_off = max(0, len(rows) - 9)
    STATE.scroll_offset = max(0, min(max_off, STATE.scroll_offset))
    STATE.selected_idx = max(0, min(max(0, len(rows) - 1), STATE.selected_idx))


def set_record_enabled(rec: dict[str, Any], value: bool) -> None:
    try:
        m = rec["mod"]
        if not rec.get("toggleable"):
            log(f"{rec.get('name', 'Mod')} is not toggleable")
            return
        if value:
            m.enable()
        else:
            m.disable()
        log(("Enabled " if value else "Disabled ") + rec.get("name", "mod"))
        refresh_records()
        rebuild_menu()
    except Exception as exc:
        log(f"Toggle failed: {exc}")


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

def build_text_scale_control(f: NativeUMGFactory, menu: Any) -> None:
    # Top-right runtime readability control.  The slider is normalized 0..1 and
    # mapped to TEXT_SCALE_MIN..TEXT_SCALE_MAX; +/- buttons provide a reliable
    # fallback if a platform does not route UMG Slider drag events.
    x, y, w = 1438, 24, 430
    f.border(menu, x, y, w, 78, (0.0, 0.055, 0.070, 0.94), 80)
    f.border_text(menu, f"Text Scale  {STATE.text_scale:.2f}x", x + 14, y + 8, 178, 26, (0.0, 0.08, 0.10, 0.0), 0.38, 92)
    f.button(menu, "−", x + 198, y + 8, 42, 30, lambda: adjust_text_scale(-TEXT_SCALE_STEP), fill=(0.00, 0.28, 0.32, 0.98), scale=0.42, z=95)
    f.button(menu, "+", x + 246, y + 8, 42, 30, lambda: adjust_text_scale(TEXT_SCALE_STEP), fill=(0.00, 0.34, 0.38, 0.98), scale=0.42, z=95)
    f.button(menu, "Reset", x + 300, y + 8, 92, 30, lambda: set_text_scale(TEXT_SCALE_DEFAULT, rebuild=True), fill=(0.00, 0.28, 0.32, 0.98), scale=0.34, z=95)
    sld = f.slider(menu, x + 18, y + 44, w - 36, 24, text_scale_to_slider_value(STATE.text_scale), z=95)
    STATE.text_scale_slider = SliderRef(slider=sld, last_value=text_scale_to_slider_value(STATE.text_scale))


def build_header(f: NativeUMGFactory, menu: Any, title: str) -> None:
    f.border_text(menu, title, 36, 26, 360, 58, (0.0, 0.08, 0.10, 0.98), 0.62, 20)
    f.button(menu, "Mods", 430, 34, 130, 44, set_screen_main,
             fill=(0.00, 0.48, 0.55, 0.98) if STATE.screen == SCREEN_MAIN else (0.00, 0.28, 0.32, 0.98),
             scale=0.43, z=60, enabled=STATE.screen != SCREEN_MAIN)
    f.button(menu, "Keybinds", 580, 34, 170, 44, build_keybinds_screen,
             fill=(0.00, 0.48, 0.55, 0.98) if STATE.screen == SCREEN_KEYBINDS else (0.00, 0.28, 0.32, 0.98),
             scale=0.43, z=60, enabled=STATE.screen != SCREEN_KEYBINDS)
    if STATE.screen in (SCREEN_MAIN, SCREEN_KEYBINDS):
        f.button(menu, "Move Menu Button", 780, 34, 220, 44, begin_launcher_button_placement,
                 fill=(0.00, 0.34, 0.38, 0.98), scale=0.35, z=60)
    build_text_scale_control(f, menu)


def build_menu_shell(f: NativeUMGFactory, menu: Any) -> None:
    f.fullscreen_backdrop(menu, (0.0, 0.015, 0.020, 0.965), 1)


def begin_launcher_button_placement() -> None:
    STATE.screen = SCREEN_BUTTON_POSITION
    STATE.launcher_place_picked_up = False
    STATE.launcher_place_click_was_down = False
    STATE.launcher_dragging = False
    STATE.launcher_place_saved_until = 0.0
    STATE.launcher_place_saved_last_count = -1
    STATE.last_status = "Click the translucent Matt's Mods button, move it, then click again to place"
    build_button_position_screen()


def cancel_launcher_button_placement() -> None:
    STATE.launcher_place_picked_up = False
    STATE.launcher_place_click_was_down = False
    STATE.launcher_dragging = False
    STATE.launcher_place_saved_until = 0.0
    STATE.launcher_place_saved_last_count = -1
    remove_launcher_position_preview()
    set_screen_main()


def build_button_position_screen() -> None:
    """Minimal full-screen placement overlay for the Matt's Mods launcher.

    This is intentionally not a settings submenu.  The user enters placement
    mode from the main Mods screen, clicks the translucent Matt's Mods button to
    pick it up, moves the mouse, then clicks again to save/drop it.
    """
    STATE.screen = SCREEN_BUTTON_POSITION
    f, menu = clear_menu_canvas()
    build_menu_shell(f, menu)
    build_launcher_position_preview()

    px, py = launcher_position()
    rw, rh = launcher_render_size()

    if STATE.launcher_place_saved_until > 0.0:
        remaining = max(0.0, STATE.launcher_place_saved_until - time.monotonic())
        count = max(1, int(remaining) + 1)
        title = f"LOCATION SAVED — RETURNING TO MODS MENU IN {count}"
        body = "Your Matt's Mods button position has been cached. The Mods menu will reopen automatically."
        title_fill = (0.04, 0.30, 0.10, 0.965)
    elif STATE.launcher_place_picked_up:
        title = "MOVE THE MOUSE, THEN CLICK AGAIN TO PLACE"
        body = "The translucent Matt's Mods button is following your cursor. Click again to save the new position."
        title_fill = (0.04, 0.30, 0.10, 0.965)
    else:
        title = "CLICK THE TRANSLUCENT SDK MODS BUTTON TO PICK IT UP"
        body = "Then move your mouse and click again where you want the launcher placed. Press Cancel or Esc to return."
        title_fill = (0.0, 0.12, 0.15, 0.965)

    # Simple floating instructions only.  No nested position submenu.
    ix, iy, iw = 420, 165, 1080
    f.border_text(menu, "MOVE SDK MODS BUTTON", ix, iy, iw, 64,
                  (0.00, 0.48, 0.55, 0.99), 0.58, 90)
    f.border_text(menu, title, ix, iy + 86, iw, 62,
                  title_fill, 0.46, 90)
    f.border_text(menu, body, ix, iy + 166, iw, 78,
                  (0.0, 0.08, 0.10, 0.94), 0.36, 90, center=True, wrap=True)
    f.border_text(menu,
                  f"Current raw top-left: X {int(px)}   Y {int(py)}   |   Rendered size: {int(rw)} x {int(rh)}   |   Viewport {int(STATE.viewport_w)} x {int(STATE.viewport_h)}",
                  ix, iy + 266, iw, 48,
                  (0.0, 0.10, 0.12, 0.94), 0.32, 90)

    f.border_text(menu, f"{VERSION} | {STATE.last_status}", 36, 970, 1050, 54,
                  (0.0, 0.08, 0.10, 0.98), 0.34, 20)
    f.button(menu, "Cancel", 1280, 974, 140, 48, cancel_launcher_button_placement,
             fill=(0.00, 0.28, 0.32, 0.98), scale=0.40, z=60)
    f.button(menu, "Close", 1630, 974, 140, 48, close_menu,
             fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=60)
    apply_menu_input_mode()


def build_main_screen() -> None:
    remove_launcher_position_preview()
    STATE.screen = SCREEN_MAIN
    clamp_mod_scroll()
    f, menu = clear_menu_canvas()
    rows = visible_records()
    rec = selected_record()

    build_menu_shell(f, menu)
    build_header(f, menu, "MATT'S BL4 MODS MENU")
    search_active = bool(STATE.text_input_target and STATE.text_input_target.get("search"))
    search_msg = STATE.text_input_buffer if search_active else (STATE.search_text if STATE.search_text else "click to type search")
    search_fill = (0.16, 0.22, 0.03, 0.98) if search_active else (0.0, 0.08, 0.10, 0.98)
    f.button(menu, "Search: " + search_msg, 36, 98, 650, 50, begin_search_input, fill=search_fill, scale=0.40, z=60)
    f.button(menu, "Clear Search", 710, 98, 170, 50, clear_search_filter, fill=(0.00, 0.28, 0.32, 0.98), scale=0.36, z=60)
    f.button(menu, f"Filter: {STATE.filter_mode}", 900, 98, 180, 50, cycle_filter_mode, fill=(0.00, 0.30, 0.34, 0.98), scale=0.40, z=60)
    f.button(menu, "Refresh", 1100, 98, 150, 50, lambda: (refresh_records(), build_main_screen()), fill=(0.00, 0.28, 0.32, 0.98), scale=0.38, z=60)
    if search_active:
        shown = STATE.text_input_buffer if STATE.text_input_buffer else "<empty>"
        f.border_text(menu, "Enter commits, Esc closes: " + shown, 1270, 98, 160, 50, (0.18, 0.28, 0.02, 0.98), 0.26, 70)

    lx, ly, lw, lh = 36, 170, 570, 775
    mx, my, mw, mh = 640, 170, 590, 775
    rx, ry, rw, rh = 1265, 170, 610, 775
    f.panel(menu, "INSTALLED SDK MODS", lx, ly, lw, lh)
    f.border_text(menu, f"{len(rows)} / {len(STATE.records)} mods", lx + lw - 178, ly + 10, 150, 38, (0.0, 0.34, 0.39, 0.98), 0.34, 24)
    f.panel(menu, "SELECTED MOD", mx, my, mw, mh)
    f.panel(menu, "OPTIONS PREVIEW", rx, ry, rw, rh)

    row_y, row_h, gap, visible_count = ly + 78, 64, 10, 9
    for local_i, rrec in enumerate(rows[STATE.scroll_offset:STATE.scroll_offset + visible_count]):
        visible_idx = STATE.scroll_offset + local_i
        y = row_y + local_i * (row_h + gap)
        enabled = rrec.get("enabled") is True
        selected = rec is rrec
        status = "ON" if enabled else "OFF"
        ver = mod_version(rrec.get("mod"))
        ver_s = "" if not ver or ver == "Unknown" else f"  v{ver[:10]}"
        text_line = f"{status}   {rrec.get('name', '')[:34]}{ver_s}"
        fill = (0.00, 0.56, 0.64, 0.98) if selected else (0.02, 0.38, 0.30, 0.96) if enabled else (0.58, 0.10, 0.08, 0.96)
        f.button(menu, text_line, lx + 24, y, lw - 86, row_h, lambda idx=visible_idx: select_mod(idx), fill=fill, scale=0.46, z=50)

    f.scrollbar(menu, lx + lw - 48, row_y, visible_count * (row_h + gap) - gap, len(rows), visible_count, STATE.scroll_offset, lambda: scroll_mods(-1), lambda: scroll_mods(1))

    if rec:
        m = rec["mod"]
        f.border_text(menu, rec["name"][:34], mx + 26, my + 78, mw - 52, 62, (0.0, 0.22, 0.26, 0.98), 0.54, 25)
        desc = (rec.get("desc") or "No description.")[:145]
        f.border_text(menu, "Description: " + desc, mx + 26, my + 152, mw - 52, 82, (0.0, 0.08, 0.10, 0.96), 0.34, 25, center=False, wrap=True)
        y = my + 252
        details = [
            ("Author", mod_author(m)),
            ("Version", mod_version(m)),
            ("Type", mod_type_name(m)),
            ("Enabled", "On" if rec.get("enabled") else "Off" if rec.get("enabled") is False else "Unknown"),
            ("Toggleable", "Yes" if rec.get("toggleable") else "No"),
            ("Options", str(len(discover_options(m)))),
            ("Keybinds", str(len(discover_keybinds(m)))),
            ("Module", mod_path(m)[:34] or "Unknown"),
        ]
        for label, value in details:
            fill = (0.0, 0.10, 0.12, 0.96)
            if label == "Enabled":
                fill = (0.02, 0.40, 0.18, 0.96) if value == "On" else (0.55, 0.07, 0.05, 0.96)
            f.border_text(menu, f"{label}: {value}", mx + 26, y, mw - 52, 50, fill, 0.39, 25)
            y += 58

        f.button(menu, "Enable", mx + 28, my + mh - 70, 116, 48, lambda rec=rec: set_record_enabled(rec, True), fill=(0.02, 0.48, 0.22, 0.98), scale=0.39, z=60, enabled=bool(rec.get("toggleable") and rec.get("enabled") is not True))
        f.button(menu, "Disable", mx + 158, my + mh - 70, 116, 48, lambda rec=rec: set_record_enabled(rec, False), fill=(0.58, 0.06, 0.04, 0.98), scale=0.39, z=60, enabled=bool(rec.get("toggleable") and rec.get("enabled") is not False))
        f.button(menu, "Reload", mx + 288, my + mh - 70, 116, 48, lambda rec=rec: reload_record(rec), fill=(0.66, 0.34, 0.02, 0.98), scale=0.39, z=60)
        f.button(menu, "Settings", mx + 418, my + mh - 70, 132, 48, lambda: open_settings(STATE.selected_idx), fill=(0.00, 0.34, 0.38, 0.98), scale=0.39, z=60)
    else:
        f.border_text(menu, "No mods match the current filter.", mx + 26, my + 78, mw - 52, 60, (0.0, 0.12, 0.15, 0.96), 0.46, 25)

    if rec:
        opts = discover_options(rec["mod"])
        if not opts:
            f.border_text(menu, "No options exposed by this mod.", rx + 26, ry + 78, rw - 52, 60, (0.0, 0.12, 0.15, 0.96), 0.46, 25)
        else:
            y = ry + 78
            for opt in opts[:9]:
                kind = option_kind(opt)
                tag = "input" if option_is_text_editable(opt) else kind
                line = f"{opt_name(opt)[:30]}    [{tag}]    {opt_value(opt)[:18]}"
                f.border_text(menu, line, rx + 26, y, rw - 52, 58, (0.0, 0.13, 0.16, 0.96), 0.43, 30)
                y += 68

    f.border_text(menu, f"{VERSION} | {STATE.last_status}", 36, 970, 1350, 54, (0.0, 0.08, 0.10, 0.98), 0.34, 20)
    f.button(menu, "Close", 1630, 974, 140, 48, close_menu, fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=60)
    apply_menu_input_mode()


def build_settings_screen() -> None:
    STATE.screen = SCREEN_SETTINGS
    f, menu = clear_menu_canvas()
    rec = selected_record()
    mod_obj = rec["mod"] if rec else None
    title = rec["name"] if rec else "Settings"

    build_menu_shell(f, menu)
    build_header(f, menu, "MOD SETTINGS")

    if STATE.rebind_target:
        f.border_text(menu, STATE.rebind_status or "Press a key...", 690, 30, 650, 52, (0.38, 0.24, 0.02, 0.98), 0.40, 70)
        f.button(menu, "Cancel", 1360, 34, 130, 44, cancel_rebind, fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=80)
    elif STATE.text_input_target:
        shown = STATE.text_input_buffer if STATE.text_input_buffer else "<empty>"
        f.border_text(menu, f"{STATE.text_input_status}: {shown}", 690, 30, 650, 52, (0.18, 0.28, 0.02, 0.98), 0.34, 70)
        f.button(menu, "Cancel", 1360, 34, 130, 44, lambda: cancel_text_input(close=False), fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=80)

    lx, ly, lw, lh = 36, 110, 520, 820
    ox, oy, ow, oh = 590, 110, 620, 820
    kx, ky, kw, kh = 1245, 110, 630, 820
    f.panel(menu, title[:32], lx, ly, lw, lh)
    f.panel(menu, "DYNAMIC OPTIONS", ox, oy, ow, oh)
    f.panel(menu, "KEYBINDS", kx, ky, kw, kh)

    if rec:
        y = ly + 76
        for label, value in [
            ("Author", mod_author(mod_obj)),
            ("Version", mod_version(mod_obj)),
            ("Enabled", "On" if rec.get("enabled") else "Off" if rec.get("enabled") is False else "Unknown"),
            ("Toggleable", "Yes" if rec.get("toggleable") else "No"),
            ("Options", str(len(discover_options(mod_obj)))),
            ("Keybinds", str(len(discover_keybinds(mod_obj)))),
        ]:
            f.border_text(menu, f"{label}: {value}", lx + 26, y, lw - 52, 52, (0.0, 0.10, 0.12, 0.96), 0.48, 25)
            y += 62
        f.border_text(menu, "Description: " + (rec.get("desc") or "No description.")[:130], lx + 26, y + 12, lw - 52, 90, (0.0, 0.08, 0.10, 0.96), 0.34, 25, center=False, wrap=True)

    opts = discover_options(mod_obj)
    y = oy + 76
    if not opts:
        f.border_text(menu, "No mod.options exposed.", ox + 26, y, ow - 52, 58, (0.0, 0.12, 0.15, 0.96), 0.46, 25)
    else:
        visible_opts = opts[STATE.settings_option_scroll:STATE.settings_option_scroll + 10]
        for opt in visible_opts:
            kind = option_kind(opt)
            line = f"{opt_name(opt)[:28]}    =>    {opt_value(opt)[:18]}"
            active_edit = STATE.text_input_target and STATE.text_input_target.get("option") is opt
            if kind == "bool":
                f.button(menu, f"{line}    [bool]", ox + 26, y, ow - 52, 58, lambda opt=opt: (toggle_option(opt), build_settings_screen()), fill=(0.0, 0.15, 0.18, 0.96), scale=0.45, z=60)
            elif option_is_text_editable(opt):
                shown = STATE.text_input_buffer if active_edit else opt_value(opt)
                label = f"{opt_name(opt)[:24]}    [{kind} input]    {shown[:24]}"
                fill = (0.16, 0.22, 0.03, 0.98) if active_edit else (0.0, 0.13, 0.16, 0.96)
                f.button(menu, label, ox + 26, y, ow - 52, 58, lambda rec=rec, opt=opt: begin_text_input(rec, opt), fill=fill, scale=0.42, z=60)
            elif kind == "keybind":
                f.border_text(menu, f"{line}    [keybind option]", ox + 26, y, ow - 52, 58, (0.0, 0.13, 0.16, 0.96), 0.42, 30)
            else:
                f.border_text(menu, f"{line}    [{kind}]", ox + 26, y, ow - 52, 58, (0.0, 0.13, 0.16, 0.96), 0.42, 30)
            y += 68

    binds = discover_keybinds(mod_obj)
    y = ky + 76
    if not binds:
        f.border_text(menu, "No keybinds exposed.", kx + 26, y, kw - 52, 58, (0.0, 0.12, 0.15, 0.96), 0.46, 25)
    else:
        for kb in binds[STATE.settings_keybind_scroll:STATE.settings_keybind_scroll + 10]:
            line = f"{keybind_name(kb)[:30]}    =>    {keybind_value(kb)[:16]}"
            f.button(menu, line, kx + 26, y, kw - 52, 58, lambda rec=rec, kb=kb: begin_rebind(rec, kb), fill=(0.0, 0.15, 0.18, 0.96), scale=0.46, z=60)
            y += 68

    f.border_text(menu, "Click keybind rows to rebind. Click [text/int/float input] option rows to edit values. Enter commits; Esc closes.", 36, 970, 1150, 54, (0.0, 0.08, 0.10, 0.98), 0.34, 20)
    f.button(menu, "Back", 1280, 974, 140, 48, set_screen_main, fill=(0.00, 0.28, 0.32, 0.98), scale=0.40, z=60)
    f.button(menu, "Close", 1630, 974, 140, 48, close_menu, fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=60)
    apply_menu_input_mode()


def build_keybinds_screen() -> None:
    remove_launcher_position_preview()
    STATE.screen = SCREEN_KEYBINDS
    f, menu = clear_menu_canvas()
    build_menu_shell(f, menu)
    build_header(f, menu, "SDK KEYBINDS")

    if STATE.rebind_target:
        f.border_text(menu, STATE.rebind_status or "Press a key...", 690, 30, 650, 52, (0.38, 0.24, 0.02, 0.98), 0.40, 70)
        f.button(menu, "Cancel", 1360, 34, 130, 44, cancel_rebind, fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=80)
    elif STATE.text_input_target:
        shown = STATE.text_input_buffer if STATE.text_input_buffer else "<empty>"
        f.border_text(menu, f"{STATE.text_input_status}: {shown}", 690, 30, 650, 52, (0.18, 0.28, 0.02, 0.98), 0.34, 70)
        f.button(menu, "Cancel", 1360, 34, 130, 44, lambda: cancel_text_input(close=False), fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=80)

    x, y, w, h = 36, 110, 1840, 820
    f.panel(menu, "ALL SDK KEYBINDS", x, y, w, h)
    rows = []
    for rec in STATE.records:
        for kb in discover_keybinds(rec.get("mod")):
            rows.append((rec, kb))

    row_y, row_h, gap, visible_count = y + 76, 60, 10, 11
    if not rows:
        f.border_text(menu, "No SDK keybind metadata found.", x + 26, row_y, w - 92, 58, (0.0, 0.12, 0.15, 0.96), 0.46, 25)
    else:
        for local_i, (rec, kb) in enumerate(rows[STATE.keybinds_scroll:STATE.keybinds_scroll + visible_count]):
            yy = row_y + local_i * (row_h + gap)
            line = f"{rec.get('name','')[:30]}    |    {keybind_name(kb)[:38]}    =>    {keybind_value(kb)[:16]}"
            f.button(menu, line, x + 26, yy, w - 98, row_h, lambda rec=rec, kb=kb: begin_rebind(rec, kb), fill=(0.0, 0.13, 0.16, 0.96), scale=0.45, z=60)

    f.scrollbar(menu, x + w - 54, row_y, visible_count * (row_h + gap) - gap, len(rows), visible_count, STATE.keybinds_scroll, lambda: scroll_keybinds(-1), lambda: scroll_keybinds(1))
    f.border_text(menu, f"{VERSION} | Click a keybind row, then press a key.", 36, 970, 1050, 54, (0.0, 0.08, 0.10, 0.98), 0.34, 20)
    f.button(menu, "Back", 1280, 974, 140, 48, set_screen_main, fill=(0.00, 0.28, 0.32, 0.98), scale=0.40, z=60)
    f.button(menu, "Close", 1630, 974, 140, 48, close_menu, fill=(0.55, 0.06, 0.04, 0.98), scale=0.40, z=60)
    apply_menu_input_mode()


def rebuild_menu() -> None:
    if not STATE.is_open:
        return
    if STATE.screen == SCREEN_SETTINGS:
        build_settings_screen()
    elif STATE.screen == SCREEN_KEYBINDS:
        build_keybinds_screen()
    elif STATE.screen == SCREEN_BUTTON_POSITION:
        build_button_position_screen()
    else:
        build_main_screen()


def set_screen_main() -> None:
    STATE.launcher_place_picked_up = False
    STATE.launcher_place_click_was_down = False
    STATE.launcher_dragging = False
    remove_launcher_position_preview()
    STATE.screen = SCREEN_MAIN
    build_main_screen()


def select_mod(idx: int) -> None:
    STATE.selected_idx = int(idx)
    build_main_screen()


def open_settings(idx: int) -> None:
    STATE.selected_idx = int(idx)
    STATE.screen = SCREEN_SETTINGS
    STATE.settings_option_scroll = 0
    STATE.settings_keybind_scroll = 0
    build_settings_screen()


def scroll_mods(delta: int) -> None:
    rows = visible_records()
    STATE.scroll_offset += int(delta)
    clamp_mod_scroll()
    if STATE.selected_idx < STATE.scroll_offset:
        STATE.selected_idx = STATE.scroll_offset
    if STATE.selected_idx > STATE.scroll_offset + 8:
        STATE.selected_idx = min(len(rows) - 1, STATE.scroll_offset + 8)
    build_main_screen()


def scroll_keybinds(delta: int) -> None:
    rows = []
    for rec in STATE.records:
        for kb in discover_keybinds(rec.get("mod")):
            rows.append((rec, kb))
    STATE.keybinds_scroll = max(0, min(max(0, len(rows) - 11), STATE.keybinds_scroll + int(delta)))
    build_keybinds_screen()


def set_search_text(value: str) -> None:
    STATE.search_text = safe_str(value)
    STATE.selected_idx = 0
    STATE.scroll_offset = 0
    clamp_mod_scroll()
    rebuild_menu()


def cycle_filter_mode() -> None:
    modes = ["all", "enabled", "disabled", "toggleable"]
    try:
        idx = modes.index(STATE.filter_mode)
    except Exception:
        idx = 0
    STATE.filter_mode = modes[(idx + 1) % len(modes)]
    STATE.selected_idx = 0
    STATE.scroll_offset = 0
    clamp_mod_scroll()
    rebuild_menu()


def clear_search_filter() -> None:
    STATE.search_text = ""
    STATE.filter_mode = "all"
    STATE.selected_idx = 0
    STATE.scroll_offset = 0
    clamp_mod_scroll()
    rebuild_menu()


# ---------------------------------------------------------------------------
# Runtime enabled gate
# ---------------------------------------------------------------------------

def native_menu_enabled() -> bool:
    if not STATE.runtime_enabled:
        return False
    try:
        current_mod = globals().get("mod")
        if current_mod is not None and hasattr(current_mod, "is_enabled"):
            return bool(current_mod.is_enabled)
    except Exception:
        pass
    return True


def on_mod_enable() -> None:
    STATE.runtime_enabled = True
    log("Enabled")

# ---------------------------------------------------------------------------
# Menu lifecycle
# ---------------------------------------------------------------------------

def open_menu(from_pause: bool = False) -> None:
    if not native_menu_enabled():
        remove_pause_launcher()
        log("Cannot open: mod is disabled")
        return
    if STATE.is_open:
        rebuild_menu()
        return
    try:
        if from_pause:
            remove_pause_launcher()
        capture_menu_input()
        refresh_records()
        STATE.is_open = True
        STATE.screen = SCREEN_MAIN
        push_menu_ui_state()
        create_overlay()
        build_main_screen()
        log("Opened")
    except Exception as exc:
        STATE.is_open = False
        pop_menu_ui_state()
        restore_menu_input()
        log(f"Open failed: {exc}")


def close_menu(defer_ui_state_pop: bool = False) -> None:
    if not STATE.is_open and STATE.menu_canvas is None and STATE.overlay_widget is None:
        return
    # If the world/frontend is transitioning, do not touch UMG wrappers at all.
    # This avoids pyunrealsdk access violations from stale UserWidget pointers.
    if in_world_transition() or get_pc_safe() is None:
        try:
            if not defer_ui_state_pop:
                pop_cinematic_then_restore_pause_if_needed()
        except Exception:
            pass
        drop_menu_refs()
        log("Closed during transition")
        return
    remove_launcher_position_preview()
    if not defer_ui_state_pop:
        try:
            pop_cinematic_then_restore_pause_if_needed()
        except Exception as exc:
            log(f"UI state pop skipped: {exc}")
    canvas = STATE.menu_canvas
    overlay = STATE.overlay_widget
    drop_menu_refs()
    remove_widget(canvas)
    remove_widget(overlay)
    restore_menu_input()
    log("Closed")


def toggle_menu() -> None:
    if STATE.is_open:
        close_menu()
    else:
        open_menu(False)


# ---------------------------------------------------------------------------
# Button polling - one click model for menus and launcher
# ---------------------------------------------------------------------------

def poll_button_registry(registry: list[ButtonRef], *, require_hover: bool = False) -> None:
    # Primary path: the proven native UMG callback model from CreateRemoveButton:
    # pressed starts when IsPressed() becomes True; clicked fires when it becomes
    # False after being pressed.  Do not require hover here, because BL4 may not
    # update IsHovered for our standalone overlay even when IsPressed works.
    #
    # Fallback path: raw mouse position + stored scaled rect. This keeps buttons
    # usable when Slate does not route pointer state to our overlay at all.
    ok_mouse, mx, my = get_mouse_position_safe()
    mouse_down = left_mouse_down_safe()
    STATE.last_mouse = (ok_mouse, mx, my, mouse_down)

    for ref in list(registry):
        if not live(ref.button) or not ref.enabled:
            ref.was_pressed = False
            ref.manual_was_down = False
            continue

        action_fired = False
        try:
            pressed = bool(ref.button.IsPressed())
        except Exception:
            pressed = False
        try:
            hovered = bool(ref.button.IsHovered())
        except Exception:
            hovered = False

        if pressed and not ref.was_pressed:
            ref.was_pressed = True
        elif ref.was_pressed and not pressed:
            ref.was_pressed = False
            if (not require_hover) or hovered:
                try:
                    ref.action()
                    action_fired = True
                    if ref.label:
                        log("Clicked " + ref.label)
                except Exception as exc:
                    log(f"Button action failed: {exc}")

        if action_fired:
            ref.manual_was_down = False
            continue

        if ok_mouse and ref.rect is not None:
            over = rect_contains(ref.rect, mx, my)
            if ref.manual_was_down and (not mouse_down) and over:
                ref.manual_was_down = False
                try:
                    ref.action()
                    if ref.label:
                        log("Clicked " + ref.label)
                except Exception as exc:
                    log(f"Button action failed: {exc}")
            else:
                ref.manual_was_down = bool(mouse_down and over)
        else:
            ref.manual_was_down = False

def poll_text_scale_slider() -> None:
    ref = STATE.text_scale_slider
    if ref is None or not live(ref.slider) or not STATE.is_open:
        return
    try:
        raw = float(ref.slider.GetValue())
    except Exception:
        return
    raw = max(0.0, min(1.0, raw))
    if abs(raw - ref.last_value) < 0.0125:
        return
    ref.last_value = raw
    STATE.text_scale_slider_pending = slider_value_to_text_scale(raw)

    # Avoid destroying the slider while the user is still dragging it. Rebuild
    # shortly after mouse release, which keeps the control responsive and stable.
    if left_mouse_down_safe():
        return
    now = time.monotonic()
    if now - STATE.text_scale_last_rebuild < 0.15:
        return
    STATE.text_scale_last_rebuild = now
    pending = STATE.text_scale_slider_pending
    STATE.text_scale_slider_pending = None
    if pending is not None:
        set_text_scale(pending, rebuild=True)


def escape_block_active() -> bool:
    return bool(STATE.escape_block_wait_release or time.monotonic() < STATE.escape_block_until)


def begin_escape_close_block(duration: float = 1.25) -> None:
    # When Esc closes the native overlay, BL4 may still see the same held key on
    # the underlying pause/title menu a few frames later.  Keep only Esc/Back
    # blocked until the physical key is released, plus a tiny cooldown.
    STATE.escape_block_wait_release = True
    STATE.escape_block_until = max(STATE.escape_block_until, time.monotonic() + float(duration))


def update_escape_close_block(pc: Any = None) -> None:
    now = time.monotonic()
    if pc is None:
        try:
            pc = mods_base.get_pc(possibly_loading=True)
        except Exception:
            pc = None

    # If Esc was used to close the native overlay, do not actually remove the
    # overlay or pop the CINEMATIC UI state until the physical Esc key has been
    # released. BL4's pause/title menus appear to consume the same release/cancel
    # event if we close on key-down, even if a short post-close block is active.
    if STATE.escape_pending_close:
        released = not is_input_key_down_safe(pc, "Escape")
        if released and STATE.escape_pending_release_seen_at <= 0.0:
            STATE.escape_pending_release_seen_at = now
            STATE.escape_block_until = max(STATE.escape_block_until, now + 0.35)
        if STATE.escape_pending_release_seen_at > 0.0 and now - STATE.escape_pending_release_seen_at >= 0.08:
            # Pause menu still receives a delayed Back/Esc after our overlay is
            # removed if CINEMATIC is popped immediately. For pause-origin Esc
            # closes, remove only our overlay now, keep CINEMATIC pushed for a
            # short shield window, then pop it below after the timeout. Main/title
            # already behaves correctly with the normal immediate pop path.
            pending_context = safe_str(getattr(STATE, "escape_pending_context", ""))
            STATE.escape_pending_close = False
            STATE.escape_pending_release_seen_at = 0.0
            STATE.escape_pending_context = ""
            if pending_context == "Pause":
                close_menu(defer_ui_state_pop=True)
                STATE.escape_defer_ui_pop = True
                STATE.escape_block_wait_release = True
                STATE.escape_block_until = max(STATE.escape_block_until, now + 0.85)
            else:
                close_menu(defer_ui_state_pop=False)
                STATE.escape_block_wait_release = False
                STATE.escape_block_until = max(STATE.escape_block_until, now + 0.35)
        return

    if not STATE.escape_block_wait_release:
        return
    released = not is_input_key_down_safe(pc, "Escape")
    timed_out = now >= STATE.escape_block_until
    if released or timed_out:
        STATE.escape_block_wait_release = False
        STATE.escape_block_until = max(STATE.escape_block_until, now + 0.20)
        if STATE.escape_defer_ui_pop:
            STATE.escape_defer_ui_pop = False
            try:
                pop_cinematic_then_restore_pause_if_needed()
            except Exception as exc:
                log(f"Deferred UI state pop skipped: {exc}")


def poll_menu_control_keys() -> None:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None

    update_escape_close_block(pc)

    if not STATE.is_open:
        STATE.escape_was_down = False
        return

    esc_down = is_input_key_down_safe(pc, "Escape")
    if esc_down and not STATE.escape_was_down:
        # Esc has special meaning while capturing a keybind: unbind that keybind
        # and keep the native mods menu open.  Do not close the native menu or let
        # BL4's underlying pause/title menu consume the same Escape press.
        if STATE.rebind_target:
            finish_rebind_with_key("Escape")
            STATE.escape_was_down = True
            return

        # Text entry owns Esc: cancel the active field and eat the same keypress.
        # Do not continue into the native-menu close flow, because BL4 can also
        # see the Esc release and back out of the underlying pause/inventory UI.
        if STATE.text_input_target:
            begin_text_input_button_block(("Escape", "Esc"), duration=0.75)
            cancel_text_input(close=False)
            begin_escape_close_block(duration=1.0)
            STATE.escape_was_down = True
            return

        # Do not close immediately on key-down. Keep the native overlay and
        # CINEMATIC ownership alive until Esc is released; then close on the next
        # tick. This prevents the underlying pause/title menu from seeing the
        # same Esc and backing out/opening its quit dialog.
        STATE.escape_pending_close = True
        STATE.escape_pending_release_seen_at = 0.0
        STATE.escape_pending_context = STATE.last_launcher_context or ("Pause" if STATE.pause_menu_active else ("Main" if STATE.main_menu_active else ""))
        begin_escape_close_block(duration=3.0)
        STATE.escape_was_down = True
        log("Esc close pending until release")
        return
    STATE.escape_was_down = esc_down



def reset_menu_button_press_state() -> None:
    for ref in list(getattr(STATE, "buttons", []) or []):
        try:
            ref.was_pressed = False
            ref.manual_was_down = False
        except Exception:
            pass


def text_input_focused_button_accept_pressed() -> bool:
    """Detect BL4/Slate's focused-button Enter activation while a text field owns input.

    In this runtime UMG setup PlayerController.IsInputKeyDown can miss Enter, but
    Slate may still press the currently focused native Button (usually Mods or
    Keybinds). While text input is active, any focused button press is treated as
    the text field's accept key instead of letting that button action run.
    """
    if not STATE.text_input_target:
        return False
    for ref in list(getattr(STATE, "buttons", []) or []):
        if not live(getattr(ref, "button", None)) or not getattr(ref, "enabled", False):
            continue
        try:
            if bool(ref.button.IsPressed()):
                reset_menu_button_press_state()
                return True
        except Exception:
            continue
    return False

def poll_buttons() -> None:
    if in_world_transition():
        return

    if STATE.launcher_buttons and not launcher_is_healthy():
        remove_pause_launcher()

    if STATE.launcher_buttons:
        poll_button_registry(STATE.launcher_buttons, require_hover=False)

    if STATE.is_open:
        if get_pc_safe() is None:
            close_menu()
            return
        sustain_menu_input()
        poll_menu_control_keys()
        if not STATE.is_open:
            return
        poll_launcher_position_drag()
        if not STATE.is_open:
            return
        if STATE.launcher_dragging or time.monotonic() < STATE.launcher_drag_block_until:
            return
        if STATE.text_input_target:
            if poll_text_input_keys():
                return
            if not STATE.is_open:
                return
            if text_input_focused_button_accept_pressed():
                begin_text_input_button_block((), duration=0.75)
                commit_text_input()
                return
            # While a MattsBL4ModsMenu text field owns input, do not poll normal
            # menu buttons at all. This prevents Slate focused-button activation
            # from toggling Mods/Keybinds or clicking whatever was focused when
            # Enter is pressed/released.
            reset_menu_button_press_state()
            return
        if text_input_button_block_active(get_pc_safe()):
            return
        poll_rebind_keys()
        if not STATE.is_open:
            return
        poll_button_registry(STATE.buttons, require_hover=False)
        if not STATE.is_open:
            return
        poll_text_scale_slider()


# ---------------------------------------------------------------------------
# Pause launcher - isolated from full menu input ownership
# ---------------------------------------------------------------------------

def set_raw_slot(widget: Any, x: float, y: float, w: float, h: float, z: int = 0) -> None:
    slot = getattr(widget, "slot", None)
    if slot is None:
        return
    try_call(slot, "SetPosition", vec2(float(x), float(y)))
    try_call(slot, "SetSize", vec2(float(w), float(h)))
    try_call(slot, "SetZOrder", int(z))
    try_call(slot, "SetAutoSize", False)


LAUNCHER_X = 60.0  # legacy default; launcher_position() is the runtime source of truth
LAUNCHER_Y = 60.0  # legacy default; launcher_position() is the runtime source of truth
LAUNCHER_W = 320.0
LAUNCHER_H = 92.0


def sync_launcher_geometry() -> None:
    """Force the small launcher back to raw probe geometry.

    This corrects old/overscaled launcher widgets after reloads and avoids the
    first-frame giant button caused by the full-menu DPI scale being applied to
    a small standalone launcher.
    """
    lx, ly = launcher_position()
    if live(STATE.pause_launcher_widget):
        try_call(STATE.pause_launcher_widget, "SetAlignmentInViewport", vec2(0, 0))
        try_call(STATE.pause_launcher_widget, "SetPositionInViewport", vec2(lx, ly), True)
        try_call(STATE.pause_launcher_widget, "SetDesiredSizeInViewport", vec2(LAUNCHER_W, LAUNCHER_H))
        try_call(STATE.pause_launcher_widget, "SetRenderScale", vec2(1.0, 1.0))
    if STATE.launcher_buttons:
        ref = STATE.launcher_buttons[0]
        if live(ref.button):
            set_raw_slot(ref.button, 0, 0, LAUNCHER_W, LAUNCHER_H, 10)
        if live(ref.visual):
            set_raw_slot(ref.visual, 0, 0, LAUNCHER_W, LAUNCHER_H, 5)
        rw, rh = launcher_render_size()
        ref.rect = (lx, ly, rw, rh)
    if live(STATE.pause_launcher_text):
        set_raw_slot(STATE.pause_launcher_text, 10, 27, LAUNCHER_W - 20, 44, 20)


def make_pause_launcher_overlay() -> NativeUMGFactory:
    """Create a passive, button-sized pause launcher overlay.

    Important: this is intentionally NOT a fullscreen interactive canvas. The
    old fullscreen root sat above Menu_Pause and stole hit-testing from the real
    pause menu. This mirrors the reliable test-button shape: a small UserWidget
    placed in the viewport with one Button at local 0,0.
    """
    if live(STATE.pause_launcher_widget) and live(STATE.pause_launcher_root):
        if not launcher_is_healthy():
            remove_pause_launcher()
        else:
            return NativeUMGFactory(STATE.pause_launcher_widget)

    pc = get_pc_safe()
    outer = pc or get_main_hud()
    if outer is None or not live(outer):
        raise RuntimeError("No live player controller or HUD found for pause launcher.")

    # Keep the launcher in raw viewport/layout coordinates, matching the proven
    # MainMenuLauncherProbe. The full menu needs DPI-scaled slots, but a small
    # standalone launcher widget can be positioned/sized directly. Scaling it
    # through sx()/sy() here caused an oversized first frame on Menu_Main until
    # the first click/rebuild.
    update_layout_metrics()
    lx, ly = launcher_position()
    lw, lh = LAUNCHER_W, LAUNCHER_H

    widget = construct("/Script/UMG.UserWidget", outer)
    widget.WidgetTree = construct("/Script/UMG.WidgetTree", widget)
    root = construct("/Script/UMG.CanvasPanel", widget.WidgetTree)
    widget.WidgetTree.RootWidget = root
    # The root should not consume pause/main-menu input; only its child Button should.
    set_self_hit_test_invisible(root)

    try_call(widget, "SetAlignmentInViewport", vec2(0, 0))
    try_call(widget, "SetPositionInViewport", vec2(lx, ly), True)
    try_call(widget, "SetDesiredSizeInViewport", vec2(lw, lh))
    try_call(widget, "AddToViewport", 999999)
    try_call(widget, "SetVisibility", 0)
    try_call(widget, "SetRenderOpacity", 1.0)
    try_call(widget, "ForceLayoutPrepass")

    STATE.pause_launcher_widget = widget
    STATE.pause_launcher_tree = widget.WidgetTree
    STATE.pause_launcher_root = root
    return NativeUMGFactory(widget)

def build_pause_launcher() -> None:
    if STATE.is_open or in_world_transition():
        return
    if get_pc_safe() is None:
        remove_pause_launcher()
        return
    if live(STATE.pause_launcher_root) and STATE.launcher_buttons:
        if not launcher_is_healthy():
            remove_pause_launcher()
            return
        sync_launcher_geometry()
        return
    try:
        make_pause_launcher_overlay()
        STATE.launcher_buttons.clear()
        root = STATE.pause_launcher_root
        tree = STATE.pause_launcher_tree
        if root is None or tree is None:
            return

        # Build this one button in raw local coordinates instead of using the
        # general menu factory, because the factory applies full-menu DPI/layout
        # scaling. This keeps the launcher stable on its first Menu_Main frame.
        btn = construct("/Script/UMG.Button", tree)
        root.AddChild(btn)
        set_raw_slot(btn, 0, 0, LAUNCHER_W, LAUNCHER_H, 10)
        set_visible_enabled(btn, True)
        try_call(btn, "SetRenderOpacity", 0.02)

        bg = construct("/Script/UMG.Border", tree)
        try_call(bg, "SetBrushColor", color(0.00, 0.62, 0.28, 0.98))
        set_hit_test_invisible(bg)
        root.AddChild(bg)
        set_raw_slot(bg, 0, 0, LAUNCHER_W, LAUNCHER_H, 5)

        tb = construct("/Script/UMG.TextBlock", tree)
        try_call(tb, "SetText", "SDK MODS")
        try_call(tb, "SetJustification", 1)
        try_call(tb, "SetRenderScale", vec2(1.55, 1.55))
        set_hit_test_invisible(tb)
        root.AddChild(tb)
        set_raw_slot(tb, 10, 27, LAUNCHER_W - 20, 44, 20)

        STATE.launcher_buttons.append(ButtonRef(
            button=btn,
            action=lambda: open_menu(True),
            label="SDK MODS",
            enabled=True,
            visual=bg,
            rect=(*launcher_position(), *launcher_render_size()),
        ))
        STATE.pause_launcher_text = tb
        sync_launcher_geometry()
        try_call(STATE.pause_launcher_widget, "ForceLayoutPrepass")
        ctx = STATE.last_launcher_context or "Menu"
        log(f"{ctx} Mods launcher installed")
    except Exception as exc:
        log(f"Pause launcher failed: {exc}")


def remove_pause_launcher() -> None:
    widget = STATE.pause_launcher_widget
    root = STATE.pause_launcher_root
    drop_launcher_refs()
    # If the PC is gone or the world/frontend is transitioning, just drop refs.
    # Calling live()/RemoveFromViewport on orphaned widgets can crash the engine.
    if in_world_transition() or get_pc_safe() is None:
        return
    remove_widget(root)
    remove_widget(widget)


def menu_def_name_from_args(args: Any) -> str:
    try:
        return safe_str(getattr(args, "OwningWidgetDef", ""))
    except Exception:
        try:
            return safe_str(args)
        except Exception:
            return ""


def is_launcher_menu_def(args: Any) -> bool:
    """Return True for menus where the Matt's Mods launcher should appear."""
    name = menu_def_name_from_args(args).lower()
    if "def_menu_pause" in name or "menu_pause" in name:
        return True
    if MAIN_MENU_LAUNCHER_ENABLED and (
        "def_menu_main" in name
        or "menu_main" in name
        or "def_title_menu_oak" in name
        or "title_menu" in name
    ):
        return True
    return False


def launcher_menu_label(args: Any) -> str:
    name = menu_def_name_from_args(args).lower()
    if MAIN_MENU_LAUNCHER_ENABLED and ("main" in name or "title" in name):
        return "Main"
    if "pause" in name:
        return "Pause"
    return "Game"


def main_menu_launcher_allowed() -> bool:
    return (time.monotonic() - STATE.mod_started_at) >= MAIN_MENU_LAUNCHER_DELAY_SEC


def on_launcher_menu_open(menu_label: str = "Game") -> None:
    if not debounce_menu_event(f"open:{menu_label}"):
        return
    if not native_menu_enabled():
        STATE.needs_launcher_teardown = True
        return
    if menu_label in ("Pause", "Main"):
        # A real Pause/Main/Title open supersedes any speculative dialog-cancel
        # restore and should not remain suppressed by an old cancelled dialog.
        clear_dialog_cancel_restore()
        clear_launcher_suppression(f"{menu_label} menu open")
    if menu_label == "Main":
        # Title/frontend opens during travel. Do not immediately construct UMG;
        # wait for the title menu to sit stable briefly.
        STATE.title_menu_pending_until = max(STATE.title_menu_pending_until, time.monotonic() + MAIN_MENU_LAUNCHER_DELAY_SEC)
        if not main_menu_launcher_allowed():
            if not STATE.main_menu_launcher_delay_logged:
                STATE.main_menu_launcher_delay_logged = True
                remaining = MAIN_MENU_LAUNCHER_DELAY_SEC - (time.monotonic() - STATE.mod_started_at)
                log(f"Main menu launcher deferred ({max(0.0, remaining):.1f}s remaining)")
            return
    STATE.pause_menu_active = (menu_label == "Pause")
    STATE.main_menu_active = (menu_label == "Main")
    STATE.last_launcher_context = menu_label
    STATE.world_transition_until = 0.0
    log(f"{menu_label} menu open")


def on_launcher_menu_close(menu_label: str = "Game") -> None:
    if not debounce_menu_event(f"close:{menu_label}"):
        return
    STATE.pause_menu_active = False
    if menu_label == "Main":
        STATE.main_menu_active = False
    STATE.needs_launcher_teardown = True
    log(f"{menu_label} menu close")


def menu_def_is_dialog_or_travel(args: Any) -> bool:
    name = menu_def_name_from_args(args).lower()
    return (
        "def_dialog_box" in name
        or "dialog" in name
        or "travel" in name
        or "loading" in name
    )


def pre_transition_remove_launcher(reason: str) -> None:
    """Remove active native launcher while the old world is still valid.

    The draw probe proved a UMG widget left alive across gameplay->title can
    crash BL4/pyunrealsdk. The safest moment is the quit/dialog menu event,
    before the world is torn down. If the user cancels that dialog, a delayed
    context restore below rebuilds the launcher once the underlying menu is
    stable again.
    """
    ctx = current_launcher_context()
    schedule_dialog_cancel_restore(ctx)
    set_launcher_suppression(DIALOG_LAUNCHER_SUPPRESSION_SEC, reason)
    STATE.pause_menu_active = False
    STATE.main_menu_active = False
    STATE.force_pause_launcher = False
    try:
        remove_pause_launcher()
    except Exception as exc:
        log(f"pre-transition launcher remove failed: {exc}")
        drop_launcher_refs()


def _pause_menu_open_cb(obj, args, ret, func):
    try:
        if menu_def_is_dialog_or_travel(args):
            pre_transition_remove_launcher("dialog/travel menu open")
            return None
        if is_launcher_menu_def(args):
            on_launcher_menu_open(launcher_menu_label(args))
    except Exception as exc:
        log(f"Menu open hook failed: {exc}")
    return None


def _pause_menu_close_cb(obj, args, ret, func):
    try:
        if menu_def_is_dialog_or_travel(args):
            pre_transition_remove_launcher("dialog/travel menu close")
            mark_world_transition("dialog/travel menu close")
            return None
        if is_launcher_menu_def(args):
            on_launcher_menu_close(launcher_menu_label(args))
    except Exception as exc:
        log(f"Menu close hook failed: {exc}")
    return None


def player_appears_in_game() -> bool:
    """Cheap check for leaving frontend Menu_Main.

    The previous main-menu fallback called an undefined helper here, which meant
    the camera tick could throw/log every frame. Keep this intentionally cheap:
    no find_all scans, just PlayerController pawn/character-style fields.
    """
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    if pc is None:
        return False

    for meth in ("GetPawn", "K2_GetPawn", "GetCharacter"):
        try:
            pawn = getattr(pc, meth)()
            if live(pawn):
                return True
        except Exception:
            pass

    for attr in ("Pawn", "AcknowledgedPawn", "Character"):
        try:
            if live(getattr(pc, attr)):
                return True
        except Exception:
            pass
    return False


def scan_main_menu_context_once() -> bool:
    """One expensive-ish live-widget scan for frontend/main-menu fallback.

    This combines the previous primary-layout and HUD scans into a single pass
    and is called only by the throttled detector below. Calling find_all on every
    camera tick caused heavy frame loss while transitioning from Menu_Main into
    gameplay, before the pause-menu event path took over.
    """
    if STATE.pause_menu_active or STATE.is_open:
        return False

    # Cheap early-out: once the player controller has a pawn/character, we are
    # not sitting at frontend Menu_Main anymore. Avoid any UserWidget scan.
    if player_appears_in_game():
        return False

    saw_primary_layout = False
    saw_main_hud = False
    try:
        for w in unrealsdk.find_all("UserWidget", False) or []:
            try:
                name = safe_str(getattr(w, "Name", ""))
                if "default__" in name.lower():
                    continue
                cls = safe_str(getattr(getattr(w, "Class", None), "Name", ""))
                path = safe_str(w)
                blob = (name + " " + cls + " " + path).lower()
                if "wbp_mainhud_c_" in blob:
                    saw_main_hud = True
                    break
                if "wbp_primary_layout" in blob or "primarygamelayout" in blob:
                    saw_primary_layout = True
            except Exception:
                pass
    except Exception:
        return False

    return bool(saw_primary_layout and not saw_main_hud)


def detect_main_menu_context() -> bool:
    # Main/title fallback is allowed only while there is no pawn.  This keeps the
    # title-screen rebuild fix, but prevents normal in-game cursor menus
    # (inventory, backpack, skills, etc.) from being treated like Pause.
    return bool(MAIN_MENU_LAUNCHER_ENABLED and pc_has_cursor_visible() and not player_appears_in_game())


def cursor_visible_launcher_context() -> str:
    """Return the safe launcher context for the current frame.

    Cursor visibility is useful, but it is not enough by itself: inventory and
    other in-game menus also show the cursor.  Use the actual BL4 menu events for
    gameplay Pause, and use cursor-visible/no-pawn only as the title/frontend
    recovery path for the missed title MenuOpen case.
    """
    if not MAIN_MENU_LAUNCHER_ENABLED:
        return ""
    if not pc_has_cursor_visible():
        return ""
    if not main_menu_launcher_allowed():
        return ""

    in_game = player_appears_in_game()
    if in_game:
        # In gameplay, draw only for the real pause-menu state set by
        # def_menu_pause/MenuOpen.  Do not show on inventory/backpack/map/etc.
        return "Pause" if STATE.pause_menu_active else ""

    # Frontend/title has no pawn, so cursor visible is a safe fallback for the
    # title rebuild bug where BL4 never sends a new title MenuOpen event.
    return "Main"


def apply_cursor_visible_launcher_gate() -> bool:
    ctx = cursor_visible_launcher_context()
    if not ctx:
        if not STATE.force_pause_launcher:
            # Do not erase a real pause/menu event while the cursor is merely
            # hidden for a transient frame.  The MenuClose hook owns clearing the
            # event-backed state; this branch only removes the launcher.
            if not pc_has_cursor_visible():
                STATE.pause_menu_active = False
                STATE.main_menu_active = False
            elif not player_appears_in_game():
                STATE.pause_menu_active = False
            else:
                STATE.main_menu_active = False
        return False

    STATE.pause_menu_active = (ctx == "Pause")
    STATE.main_menu_active = (ctx == "Main")
    STATE.last_launcher_context = ctx
    if ctx == "Main":
        # Cursor visible + no pawn is already the stability gate for title/main.
        STATE.title_menu_pending_until = 0.0
    return True


def update_pause_launcher() -> None:
    process_deferred_launcher_work()
    if not in_world_transition():
        process_dialog_cancel_restore()

    if not native_menu_enabled():
        STATE.pause_menu_active = False
        STATE.main_menu_active = False
        STATE.main_menu_cached_detected = False
        remove_pause_launcher()
        return

    if get_pc_safe() is None:
        STATE.pause_menu_active = False
        STATE.main_menu_active = False
        remove_pause_launcher()
        return

    if STATE.is_open:
        remove_pause_launcher()
        return

    if in_world_transition() or launcher_suppressed():
        return

    if not launcher_is_healthy() and STATE.pause_launcher_widget is not None:
        remove_pause_launcher()

    # Launcher gate:
    # - gameplay: only the real Pause menu event may show the launcher
    # - frontend/title: cursor visible + no pawn may recover missed title events
    # Cursor alone is not enough, because inventory/backpack/map also show it.
    should_show = bool(apply_cursor_visible_launcher_gate() or STATE.force_pause_launcher)

    if should_show:
        if STATE.force_pause_launcher and STATE.last_launcher_context not in ("Pause", "Main"):
            STATE.last_launcher_context = "Forced"
        build_pause_launcher()
        poll_manual_launcher_hitbox()
    else:
        remove_pause_launcher()


def get_mouse_position_safe() -> tuple[bool, float, float]:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
        if pc is None:
            return False, 0.0, 0.0
        for args in ((0.0, 0.0), (0, 0)):
            try:
                res = pc.GetMousePosition(*args)
                if isinstance(res, tuple):
                    vals = list(res)
                    if len(vals) >= 3:
                        return bool(vals[0]), float(vals[1]), float(vals[2])
                    if len(vals) >= 2:
                        return True, float(vals[0]), float(vals[1])
            except Exception:
                pass
    except Exception:
        pass
    return False, 0.0, 0.0


def left_mouse_down_safe() -> bool:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
        if pc is None:
            return False
        key = unrealsdk.make_struct("Key", KeyName="LeftMouseButton")
        return bool(pc.IsInputKeyDown(key))
    except Exception:
        return False


def rect_contains(rect: Any, x: float, y: float) -> bool:
    try:
        rx, ry, rw, rh = rect
        return float(rx) <= x <= float(rx) + float(rw) and float(ry) <= y <= float(ry) + float(rh)
    except Exception:
        return False


def poll_manual_launcher_hitbox() -> None:
    # Fallback only. Does not set Game+UI and does not steal gameplay input.
    if STATE.is_open or not live(STATE.pause_launcher_root):
        STATE.pause_launcher_was_down = False
        return
    ok, mx, my = get_mouse_position_safe()
    down = left_mouse_down_safe()
    over_ref = None
    if ok:
        for ref in STATE.launcher_buttons:
            if rect_contains(ref.rect, mx, my):
                over_ref = ref
                break
    if STATE.pause_launcher_was_down and not down and over_ref is not None:
        STATE.pause_launcher_was_down = False
        try:
            over_ref.action()
            log("Manual launcher click")
        except Exception as exc:
            log(f"Manual launcher failed: {exc}")
        return
    STATE.pause_launcher_was_down = bool(down and over_ref is not None)


# ---------------------------------------------------------------------------
# Text/number option input support
# ---------------------------------------------------------------------------

def snapshot_text_input_key_state() -> dict[str, bool]:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    return {key: is_input_key_down_safe(pc, key) for key in TEXT_INPUT_KEY_NAMES}


def begin_search_input() -> None:
    STATE.rebind_target = None
    STATE.rebind_key_state.clear()
    STATE.text_input_target = {"search": True}
    STATE.text_input_buffer = STATE.search_text
    STATE.text_input_key_state = snapshot_text_input_key_state()
    STATE.text_input_status = "Editing Search"
    log(STATE.text_input_status)
    rebuild_menu()


def begin_text_input(rec: dict[str, Any], opt: Any) -> None:
    STATE.rebind_target = None
    STATE.rebind_key_state.clear()
    STATE.text_input_target = {"record": rec, "option": opt}
    STATE.text_input_buffer = opt_value(opt)
    STATE.text_input_key_state = snapshot_text_input_key_state()
    STATE.text_input_status = f"Editing {opt_name(opt)}"
    log(STATE.text_input_status)
    rebuild_menu()


def cancel_text_input(*, close: bool = False) -> None:
    STATE.text_input_target = None
    STATE.text_input_buffer = ""
    STATE.text_input_key_state.clear()
    STATE.text_input_status = "Text input cancelled"
    log(STATE.text_input_status)
    if close:
        close_menu()
    else:
        rebuild_menu()


def commit_text_input() -> None:
    target = STATE.text_input_target
    if not target:
        return
    if target.get("search"):
        STATE.search_text = safe_str(STATE.text_input_buffer)
        STATE.selected_idx = 0
        STATE.scroll_offset = 0
        clamp_mod_scroll()
        STATE.text_input_status = "Search set: " + STATE.search_text
        STATE.text_input_target = None
        STATE.text_input_buffer = ""
        STATE.text_input_key_state.clear()
        log(STATE.text_input_status)
        build_main_screen()
        return

    axis = target.get("launcher_axis")
    if axis in ("x", "y"):
        old_x, old_y = launcher_position()
        raw = safe_str(STATE.text_input_buffer).strip()
        try:
            num = float(raw)
        except Exception:
            num = old_y if axis == "y" else old_x
        new_x = old_x
        new_y = old_y
        if axis == "x":
            new_x = num
        else:
            new_y = num
        STATE.text_input_target = None
        STATE.text_input_buffer = ""
        STATE.text_input_key_state.clear()
        set_launcher_custom_position(new_x, new_y, rebuild=False, quiet=True)
        STATE.text_input_status = f"Set launcher {safe_str(axis).upper()} -> {int(launcher_position()[1] if axis == 'y' else launcher_position()[0])}"
        log(STATE.text_input_status)
        build_button_position_screen()
        return

    opt = target.get("option")
    value = convert_option_input_value(opt, STATE.text_input_buffer)
    ok = set_option_value(opt, value)
    # Save mod settings where mods_base exposes that method.
    rec = target.get("record")
    try:
        mod_obj = rec.get("mod") if rec else None
        if mod_obj is not None and hasattr(mod_obj, "save_settings"):
            mod_obj.save_settings()
    except Exception:
        pass
    STATE.text_input_status = ("Set " if ok else "Failed to set ") + opt_name(opt) + " -> " + safe_str(value)
    STATE.text_input_target = None
    STATE.text_input_buffer = ""
    STATE.text_input_key_state.clear()
    log(STATE.text_input_status)
    rebuild_menu()


def begin_text_input_button_block(keys: tuple[str, ...] = (), duration: float = 0.45) -> None:
    # UMG keeps a focused button even while our polling owns text entry. Enter can
    # briefly look like a pressed focused button after committing a search field,
    # which previously fired the Keybinds tab. Block native-menu button polling
    # until the commit/cancel key is released plus a tiny cooldown.
    STATE.text_input_button_block_wait_keys = tuple(keys or ())
    STATE.text_input_button_block_until = max(STATE.text_input_button_block_until, time.monotonic() + float(duration))
    for ref in STATE.buttons:
        ref.was_pressed = False
        ref.manual_was_down = False


def text_input_button_block_active(pc: Any = None) -> bool:
    now = time.monotonic()
    keys = tuple(getattr(STATE, "text_input_button_block_wait_keys", ()) or ())
    if keys:
        if pc is None:
            try:
                pc = mods_base.get_pc(possibly_loading=True)
            except Exception:
                pc = None
        if any(is_input_key_down_safe(pc, key) for key in keys):
            STATE.text_input_button_block_until = max(STATE.text_input_button_block_until, now + 0.18)
            for ref in STATE.buttons:
                ref.was_pressed = False
                ref.manual_was_down = False
            return True
        STATE.text_input_button_block_wait_keys = ()
        STATE.text_input_button_block_until = max(STATE.text_input_button_block_until, now + 0.14)
        for ref in STATE.buttons:
            ref.was_pressed = False
            ref.manual_was_down = False
        return True
    if now < getattr(STATE, "text_input_button_block_until", 0.0):
        for ref in STATE.buttons:
            ref.was_pressed = False
            ref.manual_was_down = False
        return True
    return False


def text_input_key_to_char(key_name: str, pc: Any) -> str:
    ch = TEXT_INPUT_CHARS.get(key_name, "")
    if not ch:
        return ""
    # Best-effort shift capitalization for letters. We keep punctuation simple
    # for this first pass so numeric/text options work reliably.
    if len(ch) == 1 and ch.isalpha():
        shifted = (
            is_input_key_down_safe(pc, "LeftShift")
            or is_input_key_down_safe(pc, "RightShift")
            or is_input_key_down_safe(pc, "CapsLock")
        )
        return ch.upper() if shifted else ch
    return ch


def poll_text_input_keys() -> bool:
    if not STATE.text_input_target:
        return False
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    if pc is None:
        return False
    if not STATE.text_input_key_state:
        STATE.text_input_key_state = {key: False for key in TEXT_INPUT_KEY_NAMES}

    for key_name in TEXT_INPUT_KEY_NAMES:
        down = is_input_key_down_safe(pc, key_name)
        was = bool(STATE.text_input_key_state.get(key_name, False))
        STATE.text_input_key_state[key_name] = down
        if not (down and not was):
            continue

        if key_name in ("Escape", "Esc"):
            begin_text_input_button_block(("Escape", "Esc"), duration=0.75)
            cancel_text_input(close=False)
            begin_escape_close_block(duration=1.0)
            return True
        if key_name in ("Enter", "Return", "NumPadEnter"):
            begin_text_input_button_block(("Enter", "Return", "NumPadEnter"), duration=0.75)
            commit_text_input()
            return True
        if key_name in ("BackSpace", "Delete"):
            begin_text_input_button_block((key_name,), duration=0.20)
            STATE.text_input_buffer = STATE.text_input_buffer[:-1]
            rebuild_menu()
            return True

        ch = text_input_key_to_char(key_name, pc)
        if ch:
            # Keep fields bounded so long accidental input does not spill.
            begin_text_input_button_block((key_name,), duration=0.10)
            STATE.text_input_buffer = (STATE.text_input_buffer + ch)[:96]
            rebuild_menu()
            return True
    return False

# ---------------------------------------------------------------------------
# Keybind rebind support
# ---------------------------------------------------------------------------

def make_key(name: str):
    return unrealsdk.make_struct("Key", KeyName=str(name))


def runtime_key_name(raw_key_name: str) -> str:
    """Return the SDK/Unreal key name which must be stored on KeybindOption.

    Console Mod Menu stores and validates names like ``Six``/``Seven`` rather
    than display glyphs like ``6``/``7``.  The native menu may poll raw UE key
    names, but console commands or older UI state can pass aliases, so normalize
    them before writing to the keybind object.
    """
    s = normalize_key_name(safe_str(raw_key_name)).strip()
    if not s:
        return ""
    alias_map = {
        "0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
        "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine",
        "Esc": "Escape", "Escape": "Escape",
        "Space": "SpaceBar", "Spacebar": "SpaceBar", "SpaceBar": "SpaceBar",
        "LeftCtrl": "LeftControl", "RightCtrl": "RightControl",
        "Ctrl": "LeftControl", "Control": "LeftControl",
    }
    return alias_map.get(s, s)


def display_key_name(raw_key_name: str) -> str:
    # Match Console Mod Menu/keybinds runtime naming for keybinds.  Do not show
    # or store top-row digits as ``6``/``7``; those are not valid SDK key names.
    return runtime_key_name(raw_key_name)


def is_input_key_down_safe(pc: Any, key_name: str) -> bool:
    if pc is None:
        return False
    try:
        return bool(pc.IsInputKeyDown(make_key(key_name)))
    except Exception:
        return False


def snapshot_rebind_key_state() -> dict[str, bool]:
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    return {key: is_input_key_down_safe(pc, key) for key in REBIND_KEY_NAMES}


def finish_rebind_with_key(raw_key_name: str) -> None:
    runtime_name = runtime_key_name(raw_key_name)
    display_name = display_key_name(runtime_name)
    if runtime_name in ("Escape", "Esc"):
        target = STATE.rebind_target
        if not target:
            return
        kb = target.get("keybind")
        rec = target.get("record")
        mod_obj = rec.get("mod") if isinstance(rec, dict) else None
        ok = set_keybind_value(kb, None, mod_obj=mod_obj)
        STATE.rebind_status = ("Unbound " if ok else "Failed to unbind ") + keybind_name(kb)
        STATE.rebind_target = None
        STATE.rebind_key_state.clear()
        log(STATE.rebind_status)
        rebuild_menu()
        return

    target = STATE.rebind_target
    if not target:
        return
    kb = target.get("keybind")
    rec = target.get("record")
    mod_obj = rec.get("mod") if isinstance(rec, dict) else None
    ok = set_keybind_value(kb, runtime_name, mod_obj=mod_obj)
    STATE.rebind_status = ("Bound " if ok else "Failed to bind ") + keybind_name(kb) + " -> " + display_name
    STATE.rebind_target = None
    STATE.rebind_key_state.clear()
    log(STATE.rebind_status)
    rebuild_menu()


def poll_rebind_keys() -> None:
    """Poll rebind input using PlayerController.IsInputKeyDown.

    InputKey hooks do not fire in this BL4 UI focus state, but camera-tick
    polling was verified to catch letters, numbers, Escape, mouse, and other
    keys. We use only keyboard/gamepad keys here so clicking Cancel or rows
    does not accidentally bind LeftMouseButton.
    """
    if not STATE.rebind_target:
        return
    try:
        pc = mods_base.get_pc(possibly_loading=True)
    except Exception:
        pc = None
    if pc is None:
        return

    if not STATE.rebind_key_state:
        STATE.rebind_key_state = {key: False for key in REBIND_KEY_NAMES}

    for key_name in REBIND_KEY_NAMES:
        down = is_input_key_down_safe(pc, key_name)
        was = bool(STATE.rebind_key_state.get(key_name, False))
        STATE.rebind_key_state[key_name] = down
        if down and not was:
            finish_rebind_with_key(key_name)
            return


def normalize_key_name(raw: str) -> str:
    s = safe_str(raw).strip()
    if not s:
        return ""
    m = re.search(r"KeyName: '([^']+)'", s)
    if m:
        return m.group(1)
    m = re.search(r"KeyName=([^,}\\s]+)", s)
    if m:
        return m.group(1).strip("'\"")
    m = re.search(r"Key'([^']+)'", s)
    if m:
        return m.group(1)
    if "::" in s:
        s = s.split("::")[-1]
    return s.strip("'\" ")


def matching_keybind_objects(mod_obj: Any, k: Any) -> list[Any]:
    """Return every object which appears to represent the same keybind.

    Console Mod Menu writes the KeybindOption found through iter_display_options,
    but some mods also keep a separate object in mod.keybinds for runtime hooks.
    Updating only one side makes the native screen disagree with itself and can
    leave the active hotkey stuck on the old value.
    """
    out: list[Any] = []
    target_ident = keybind_identity(k)

    def add(candidate: Any) -> None:
        if candidate is None:
            return
        if id(candidate) in {id(x) for x in out}:
            return
        if candidate is k or (target_ident and keybind_identity(candidate) == target_ident):
            out.append(candidate)

    add(k)
    if mod_obj is not None:
        for opt in discover_options(mod_obj):
            try:
                if option_kind(opt) == "keybind":
                    add(opt)
            except Exception:
                pass
        for kb in _declared_keybinds(mod_obj):
            add(kb)
    return out


def set_keybind_value(k: Any, key_name: Any, mod_obj: Any = None) -> bool:
    """Set/unbind a keybind using Console Mod Menu's public value path.

    Console Mod Menu unbinds by writing ``None`` to the KeybindOption value.
    Non-None keys must be stored as SDK runtime names like ``Six``/``Seven``.
    Duplicate display/runtime keybind objects are synced by display name.
    """
    if key_name is None:
        stored_key = None
    else:
        stored_key = runtime_key_name(key_name)
        if not stored_key:
            return False

    ok = False
    targets = matching_keybind_objects(mod_obj, k) or [k]

    for target in targets:
        wrote = False
        try:
            setattr(target, "value", stored_key)
            wrote = True
            ok = True
        except Exception as exc:
            log(f"Keybind value set failed for {keybind_name(target)}: {exc}")

        # Fallback only for non-standard wrappers with no public ValueOption
        # value property. Do not call this on normal mods_base KeybindOption.
        if not wrote and stored_key is not None and not hasattr(target, "value"):
            for meth in ("_rebind", "rebind"):
                try:
                    getattr(target, meth)(stored_key)
                    ok = True
                    break
                except Exception:
                    pass

    try:
        if mod_obj is not None and hasattr(mod_obj, "save_settings"):
            mod_obj.save_settings()
            ok = True
    except Exception as exc:
        log(f"Keybind save_settings failed for {keybind_name(k)}: {exc}")

    return ok

def begin_rebind(rec: dict[str, Any], kb: Any) -> None:
    STATE.rebind_target = {"record": rec, "keybind": kb}
    STATE.rebind_key_state = snapshot_rebind_key_state()
    STATE.rebind_started_at = time.monotonic()
    STATE.rebind_status = f"Press a key for {keybind_name(kb)}"
    log(STATE.rebind_status)
    rebuild_menu()


def cancel_rebind() -> None:
    STATE.rebind_target = None
    STATE.rebind_status = "Rebind cancelled"
    STATE.rebind_key_state.clear()
    rebuild_menu()


def key_name_from_input_args(args: Any) -> str:
    for attr in ("Key", "key", "InKey", "InputKey", "KeyName"):
        try:
            name = normalize_key_name(str(getattr(args, attr)))
            if name:
                return name
        except Exception:
            pass
    try:
        return normalize_key_name(str(args))
    except Exception:
        return ""


def input_is_pressed(args: Any) -> bool:
    try:
        low = str(args).lower()
    except Exception:
        return True
    if any(x in low for x in ("released", "ie_released", "completed", "canceled", "cancelled")):
        return False
    if any(x in low for x in ("pressed", "ie_pressed", "triggered", "started")):
        return True
    return True


def _native_rebind_input_cb(obj, args, ret, func):
    if not STATE.rebind_target or not input_is_pressed(args):
        return None
    key_name = key_name_from_input_args(args)
    if not key_name:
        return None
    if key_name in ("Escape", "Esc"):
        STATE.rebind_target = None
        STATE.rebind_status = "Rebind cancelled"
        rebuild_menu()
        return Block

    runtime_name = runtime_key_name(key_name)
    kb = STATE.rebind_target.get("keybind")
    rec = STATE.rebind_target.get("record")
    mod_obj = rec.get("mod") if isinstance(rec, dict) else None
    ok = set_keybind_value(kb, runtime_name, mod_obj=mod_obj)
    STATE.rebind_status = ("Bound " if ok else "Failed to bind ") + keybind_name(kb) + " -> " + display_key_name(runtime_name)
    STATE.rebind_target = None
    log(STATE.rebind_status)
    rebuild_menu()
    return Block


# ---------------------------------------------------------------------------
# Hook installation
# ---------------------------------------------------------------------------

def should_block_pause_input(args: Any = None) -> bool:
    # Block menu back/cancel events while the native overlay is open, and keep
    # blocking briefly after Esc closes it until Esc has been released.
    return bool(STATE.is_open or escape_block_active())


def _block_pause_input_cb(obj, args, ret, func):
    if should_block_pause_input(args):
        STATE.blocked_pause_events += 1
        return Block
    return None


def _block_escape_input_cb(obj, args, ret, func):
    # Defensive lower-level input block while the native menu is open. Escape is
    # always eaten by our overlay/short release gate. While text input is active,
    # consume text keys too so Enter/Esc/Search typing cannot leak into BL4's
    # underlying pause, inventory, or title widgets.
    if not (STATE.is_open or escape_block_active()):
        return None
    try:
        key_name = runtime_key_name(key_name_from_input_args(args))
        if STATE.is_open and key_name in TEXT_INPUT_CONSUME_KEY_NAMES and (STATE.text_input_target or STATE.text_input_button_block_wait_keys or time.monotonic() < STATE.text_input_button_block_until):
            STATE.blocked_pause_events += 1
            return Block
        if key_name == "Escape":
            STATE.blocked_pause_events += 1
            return Block
    except Exception:
        pass
    return None


def _menu_input_block_paths() -> tuple[str, ...]:
    script_bases = (
        "/Game/UI/Scripts/ui_script_menu_base.ui_script_menu_base_C",
    )
    events = (
        "EscapeInput",
        "BackInput",
        "CancelInput",
        "EnterInput",
        "AcceptInput",
        "ConfirmInput",
        "SubmitInput",
        "SelectInput",
        "HandleBack",
        "HandleAccept",
        "HandleConfirm",
        "HandleSubmit",
        "OnBack",
        "OnAccept",
        "OnConfirm",
        "OnSubmit",
        "OnHandleBackAction",
        "ElementClicked",
        "ElementFocused",
        "ElementUnfocused",
    )
    paths = [f"{base}:{event}" for base in script_bases for event in events]
    paths.extend((
        "/Game/UI/Scripts/script_status_menu_nav_bar.script_status_menu_nav_bar_C:NavBackward",
        "/Game/UI/Scripts/script_status_menu_nav_bar.script_status_menu_nav_bar_C:NavForward",
        "/Game/UI/Scripts/script_status_menu_nav_bar.script_status_menu_nav_bar_C:NavEntered",
        "/Game/UI/Scripts/script_status_menu_nav_bar.script_status_menu_nav_bar_C:NavExited",
    ))
    return tuple(paths)


def _ui_tick_cb(obj, args, ret, func):
    # The only repeating production poll loop.
    if not native_menu_enabled():
        remove_pause_launcher()
        return None
    STATE.last_tick = time.monotonic()
    try:
        update_pause_launcher()
        poll_reload_refresh()
        poll_menu_control_keys()
        poll_buttons()
    except Exception as exc:
        log(f"Tick failed: {exc}")
    return None


def install_hooks() -> None:
    try:
        try:
            unrealsdk.hooks.remove_hook(
                "/Script/Engine.CameraModifier:BlueprintModifyCamera",
                Type.POST,
                HOOK_TICK,
            )
        except Exception:
            pass
        unrealsdk.hooks.add_hook(
            "/Script/Engine.CameraModifier:BlueprintModifyCamera",
            Type.POST,
            HOOK_TICK,
            _ui_tick_cb,
        )
    except Exception as exc:
        log(f"Failed to bind camera poll: {exc}")

    try:
        menu_hook_paths = (
            "/Game/UI/Scripts/ui_script_menu_base.ui_script_menu_base_C",
        )
        for idx, base_path in enumerate(menu_hook_paths):
            unrealsdk.hooks.add_hook(
                base_path + ":MenuOpen",
                Type.POST,
                f"{HOOK_PAUSE_OPEN}_{idx}",
                _pause_menu_open_cb,
            )
            unrealsdk.hooks.add_hook(
                base_path + ":MenuClose",
                Type.POST,
                f"{HOOK_PAUSE_CLOSE}_{idx}",
                _pause_menu_close_cb,
            )
    except Exception as exc:
        log(f"Failed to bind pause menu events: {exc}")

    # Rebind input is handled by poll_rebind_keys() inside the camera poll.
    # Add one narrow Escape-only PRE hook while the menu is open so the
    # underlying BL4 pause/title menu cannot also consume Esc and close itself.
    for idx, path in enumerate((
        "/Script/GbxInput.GbxEnhancedPlayerInput:InputKey",
        "/Script/EnhancedInput.EnhancedPlayerInput:InputKey",
        "/Script/Engine.PlayerInput:InputKey",
        "/Script/Engine.PlayerController:InputKey",
    )):
        try:
            unrealsdk.hooks.add_hook(path, Type.PRE, f"{HOOK_ESCAPE_BLOCK}_{idx}", _block_escape_input_cb)
        except Exception as exc:
            log(f"Failed Escape input block hook {path}: {exc}")

    for idx, path in enumerate(_menu_input_block_paths()):
        try:
            unrealsdk.hooks.add_hook(path, Type.PRE, f"{HOOK_BLOCK_PREFIX}_{idx}", _block_pause_input_cb)
        except Exception as exc:
            log(f"Failed pause input block hook {path}: {exc}")

    # Global InputKey use is limited to the Escape-only block above and is inert
    # unless the native menu is open.


# ---------------------------------------------------------------------------
# Commands and mod registration
# ---------------------------------------------------------------------------

@command("native_mods_menu", description="Open/close the native UMG mods menu.")
def native_mods_menu_cmd(_args) -> None:
    toggle_menu()


@command("native_mods_menu_close", description="Close the native UMG mods menu.")
def native_mods_menu_close_cmd(_args) -> None:
    close_menu()


@command("native_mods_menu_refresh", description="Refresh/rebuild the native mods menu.")
def native_mods_menu_refresh_cmd(_args) -> None:
    refresh_records()
    rebuild_menu()


@command("native_mods_menu_reload", description="Reload the currently selected mod using the SDK rlm command.")
def native_mods_menu_reload_cmd(_args) -> None:
    rec = selected_record()
    if not rec:
        log("No selected mod to reload")
        return
    reload_record(rec)


@command("native_mods_menu_search", description="Set native mods menu search text.")
def native_mods_menu_search_cmd(args) -> None:
    try:
        value = " ".join(str(x) for x in args)
    except Exception:
        value = safe_str(args)
    STATE.text_input_target = None
    STATE.text_input_buffer = ""
    STATE.text_input_key_state.clear()
    set_search_text(value)
    log(f"Search set: {value}")


@command("native_mods_menu_clear_search", description="Clear native mods menu search/filter.")
def native_mods_menu_clear_search_cmd(_args) -> None:
    clear_search_filter()
    log("Search/filter cleared")


@command("native_mods_menu_filter", description="Cycle native mods menu filter mode.")
def native_mods_menu_filter_cmd(_args) -> None:
    cycle_filter_mode()
    log(f"Filter mode: {STATE.filter_mode}")


@command("native_mods_menu_bind_key", description="Bind the active rebind target to a key name.")
def native_mods_menu_bind_key_cmd(args) -> None:
    try:
        key_name = " ".join(str(x) for x in args).strip()
    except Exception:
        key_name = safe_str(args).strip()
    if not STATE.rebind_target:
        log("No active rebind target. Click a keybind row first.")
        return
    runtime_name = runtime_key_name(key_name)
    kb = STATE.rebind_target.get("keybind")
    rec = STATE.rebind_target.get("record")
    mod_obj = rec.get("mod") if isinstance(rec, dict) else None
    ok = set_keybind_value(kb, runtime_name, mod_obj=mod_obj)
    STATE.rebind_status = ("Bound " if ok else "Failed to bind ") + keybind_name(kb) + " -> " + display_key_name(runtime_name)
    STATE.rebind_target = None
    log(STATE.rebind_status)
    rebuild_menu()



@command("native_mods_menu_viewport_probe", description="Log viewport/mouse/button coordinate probe info.")
def native_mods_menu_viewport_probe_cmd(_args) -> None:
    update_layout_metrics()
    ok, mx, my = get_mouse_position_safe()
    down = left_mouse_down_safe()
    first = None
    if STATE.buttons:
        r = STATE.buttons[0]
        try:
            first = {
                "label": r.label,
                "rect": r.rect,
                "hover": bool(r.button.IsHovered()) if live(r.button) else None,
                "pressed": bool(r.button.IsPressed()) if live(r.button) else None,
                "manual_was_down": r.manual_was_down,
            }
        except Exception as exc:
            first = {"label": r.label, "rect": r.rect, "state_error": str(exc)}
    elif STATE.launcher_buttons:
        r = STATE.launcher_buttons[0]
        try:
            first = {
                "label": r.label,
                "rect": r.rect,
                "hover": bool(r.button.IsHovered()) if live(r.button) else None,
                "pressed": bool(r.button.IsPressed()) if live(r.button) else None,
                "manual_was_down": r.manual_was_down,
            }
        except Exception as exc:
            first = {"label": r.label, "rect": r.rect, "state_error": str(exc)}
    log(f"viewport={int(STATE.viewport_w)}x{int(STATE.viewport_h)} dpi={STATE.viewport_dpi_scale:.3f} layout={int(STATE.layout_w)}x{int(STATE.layout_h)} sx={STATE.viewport_scale_x:.3f} sy={STATE.viewport_scale_y:.3f} mouse=({ok},{mx:.1f},{my:.1f}) down={down} buttons={len(STATE.buttons)} launcher={len(STATE.launcher_buttons)} first={first} probe={STATE.viewport_probe}")

@command("native_mods_menu_input_status", description="Report native mods menu input/debug state.")
def native_mods_menu_input_status_cmd(_args) -> None:
    log(
        f"is_open={STATE.is_open} rebind={bool(STATE.rebind_target)} "
        f"blocked_pause_events={STATE.blocked_pause_events} "
        f"blocked_input_keys={STATE.blocked_input_keys} "
        f"main_launcher_ready={main_menu_launcher_allowed()} "
        f"world_transition={in_world_transition()} "
        f"overlay={live(STATE.overlay_widget)} menu={live(STATE.menu_canvas)} "
        f"pause_active={STATE.pause_menu_active} launcher={live(STATE.pause_launcher_root)} "
        f"viewport={int(STATE.viewport_w)}x{int(STATE.viewport_h)} "
        f"dpi={STATE.viewport_dpi_scale:.3f} layout={int(STATE.layout_w)}x{int(STATE.layout_h)} "
        f"scale=({STATE.viewport_scale_x:.3f},{STATE.viewport_scale_y:.3f}) "
        f"mouse={STATE.last_mouse} probe={STATE.viewport_probe}"
    )


@command("native_mods_menu_button_probe", description="Log native and manual state for current menu buttons.")
def native_mods_menu_button_probe_cmd(_args) -> None:
    ok, mx, my = get_mouse_position_safe()
    down = left_mouse_down_safe()
    rows = []
    for i, r in enumerate((STATE.launcher_buttons + STATE.buttons)[:12]):
        try:
            hov = bool(r.button.IsHovered()) if live(r.button) else None
        except Exception as exc:
            hov = f"err:{exc}"
        try:
            prs = bool(r.button.IsPressed()) if live(r.button) else None
        except Exception as exc:
            prs = f"err:{exc}"
        rows.append(f"{i}:{r.label} live={live(r.button)} hov={hov} prs={prs} was={r.was_pressed} rect={r.rect} over={rect_contains(r.rect, mx, my) if r.rect else None}")
    log(f"button_probe mouse=({ok},{mx:.1f},{my:.1f}) down={down} :: " + " | ".join(rows))

@command("native_mods_menu_unstuck", description="Emergency restore game input and close native mods menu.")
def native_mods_menu_unstuck_cmd(_args) -> None:
    remove_pause_launcher()
    close_menu()
    restore_menu_input()
    log("Unstuck / input restored")


@command("native_mods_menu_text_scale", description="Set MattsBL4ModsMenu text scale, for example: native_mods_menu_text_scale 1.6")
def native_mods_menu_text_scale_cmd(args) -> None:
    try:
        if isinstance(args, (list, tuple)):
            value = float(args[0]) if args else STATE.text_scale
        else:
            value = float(str(args).strip().split()[0])
        set_text_scale(value, rebuild=True)
    except Exception as exc:
        log(f"Text scale command failed: {exc}")



@command("native_mods_menu_button_position", description="Set Matt's Mods launcher position: top_left, top_right, bottom_left, bottom_right, custom [x y].")
def native_mods_menu_button_position_cmd(args) -> None:
    try:
        parts = [safe_str(a) for a in list(args or [])]
    except Exception:
        parts = []
    if not parts:
        log(f"Matt's Mods button position: {launcher_position_label()}")
        return
    mode = parts[0].lower().replace("-", "_")
    if mode == "custom" and len(parts) >= 3:
        try:
            STATE.launcher_custom_x = float(parts[1])
            STATE.launcher_custom_y = float(parts[2])
        except Exception:
            log("Usage: native_mods_menu_button_position custom <x> <y>")
            return
    set_launcher_position_mode(mode)

@command("native_mods_menu_force_launcher", description="Toggle forcing the pause Mods launcher visible for debugging.")
def native_mods_menu_force_launcher_cmd(_args) -> None:
    STATE.force_pause_launcher = not STATE.force_pause_launcher
    if STATE.force_pause_launcher:
        build_pause_launcher()
    else:
        remove_pause_launcher()
    log(f"force_pause_launcher={STATE.force_pause_launcher}")



def on_mod_disable() -> None:
    STATE.runtime_enabled = False
    STATE.force_pause_launcher = False
    STATE.pause_menu_active = False
    STATE.main_menu_active = False
    remove_pause_launcher()
    close_menu()
    restore_menu_input()


COMMANDS = [
    native_mods_menu_cmd,
    native_mods_menu_close_cmd,
    native_mods_menu_refresh_cmd,
    native_mods_menu_reload_cmd,
    native_mods_menu_search_cmd,
    native_mods_menu_clear_search_cmd,
    native_mods_menu_filter_cmd,
    native_mods_menu_bind_key_cmd,
    native_mods_menu_viewport_probe_cmd,
    native_mods_menu_button_probe_cmd,
    native_mods_menu_input_status_cmd,
    native_mods_menu_unstuck_cmd,
    native_mods_menu_text_scale_cmd,
    native_mods_menu_button_position_cmd,
    native_mods_menu_force_launcher_cmd,
]

try:
    load_user_settings()
except Exception as exc:
    log(f"Failed to load user settings: {exc}")

try:
    install_hooks()
except Exception as exc:
    log(f"Failed to install hooks: {exc}")

_BUILD_MOD_KWARGS = dict(
    cls=mods_base.Mod,
    name="MattsBL4ModsMenu",
    author="Mattmab",
    description=(
        "Unofficial community-made BL4 PythonSDK mod manager UI by Matt. "
        "Not part of, endorsed by, or maintained by the PythonSDK/bl-sdk project. "
        "Adds an in-game UMG menu for browsing, searching, configuring, "
        "reloading, and rebinding installed SDK mods."
    ),
    supported_games=Game.BL4,
    coop_support=CoopSupport.ClientSide,
    keybinds=[],
    commands=COMMANDS,
    on_disable=on_mod_disable,
)

try:
    mod = build_mod(on_enable=on_mod_enable, **_BUILD_MOD_KWARGS)
except TypeError:
    # Older mods_base builds may not accept on_enable. The disabled-state bug is
    # still fixed through on_disable + the runtime_enabled gate; re-enable will
    # work after module reload, and newer builds get on_enable above.
    mod = build_mod(**_BUILD_MOD_KWARGS)
