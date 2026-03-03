import keyboard
from loguru import logger
from core.event_bus import EventBus
from core.constants import EventType


class KillSwitch:
    """Global hotkey kill switch. Stops all trading when activated."""

    def __init__(self, event_bus: EventBus, hotkey: str = "ctrl+shift+k"):
        self._event_bus = event_bus
        self._hotkey = hotkey
        self._active = False
        self._registered = False

    def register(self) -> None:
        if not self._registered:
            keyboard.add_hotkey(self._hotkey, self._on_trigger)
            self._registered = True
            logger.info(f"Kill switch registered: {self._hotkey}")

    def unregister(self) -> None:
        if self._registered:
            try:
                keyboard.remove_hotkey(self._hotkey)
            except Exception:
                pass
            self._registered = False

    def _on_trigger(self) -> None:
        self._active = True
        self._event_bus.publish_sync(EventType.KILL_SWITCH, {
            "message": "KILL SWITCH ACTIVATED via hotkey",
        })
        logger.critical(f"KILL SWITCH triggered via {self._hotkey}")

    @property
    def is_active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._active = False
