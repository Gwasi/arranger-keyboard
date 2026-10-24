"""Audio Unit parameter access used by the in-app plugin editor.

The plugins available on this machine are Audio Unit v2 instruments.  Apple
exposes their parameters through two different worlds:

* AudioToolbox's ``AUAudioUnit`` bridge knows the parameter *tree* (names,
  ranges, defaults) but it is a second, separate instance of the plugin.
* The instance that is actually playing inside the app can be addressed with
  the classic Audio Unit C API, where a parameter is addressed by its
  ``AUParameterID``.

Both use the same identifier for the same parameter, so the bridge supplies
the labels while the classic API reads and writes the live instrument.
"""

import ctypes
import threading

import AVFoundation as av

_GLOBAL_SCOPE = 0
_MAIN_ELEMENT = 0
_TOOLBOX_PATH = ("/System/Library/Frameworks/AudioToolbox.framework/"
                 "AudioToolbox")

_toolbox = None


def _audio_toolbox():
    global _toolbox
    if _toolbox is None:
        lib = ctypes.cdll.LoadLibrary(_TOOLBOX_PATH)
        lib.AudioUnitGetParameter.restype = ctypes.c_int32
        lib.AudioUnitGetParameter.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_uint32),
        ]
        lib.AudioUnitSetParameter.restype = ctypes.c_int32
        lib.AudioUnitSetParameter.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.c_float, ctypes.c_uint32,
        ]
        _toolbox = lib
    return _toolbox


def live_handle(unit):
    """Return the classic Audio Unit handle of a playing instance."""
    if unit is None:
        return None
    try:
        pointer = unit.audioUnit()
    except Exception:
        return None
    address = getattr(pointer, "pointerAsInteger", None)
    if address is None:
        try:
            address = int(pointer)
        except Exception:
            return None
    if not address:
        return None
    return ctypes.c_void_p(address)


def read_parameter(unit, parameter_id):
    """Read a parameter of a playing instance, or None when unavailable."""
    handle = live_handle(unit)
    if handle is None:
        return None
    value = ctypes.c_float()
    size = ctypes.c_uint32(4)
    status = _audio_toolbox().AudioUnitGetParameter(
        handle, int(parameter_id), _GLOBAL_SCOPE, _MAIN_ELEMENT,
        ctypes.byref(value), ctypes.byref(size))
    if status != 0:
        return None
    return value.value


def write_parameter(unit, parameter_id, value):
    """Write a parameter of a playing instance."""
    handle = live_handle(unit)
    if handle is None:
        return False
    status = _audio_toolbox().AudioUnitSetParameter(
        handle, int(parameter_id), _GLOBAL_SCOPE, _MAIN_ELEMENT,
        ctypes.c_float(float(value)), 0)
    return status == 0


def _number(item, attribute):
    try:
        value = getattr(item, attribute)
    except Exception:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    try:
        return float(value)
    except Exception:
        return None


class ParameterCatalog:
    """Caches the parameter tree of every plugin the user has added."""

    def __init__(self):
        self._lock = threading.RLock()
        self._bridges = {}

    def _bridge(self, key, description):
        with self._lock:
            if key in self._bridges:
                return self._bridges[key]
            bridge = None
            try:
                bridge = av.AUAudioUnit.alloc(
                ).initWithComponentDescription_options_error_(
                    description, 0, None)
            except Exception as exc:
                print("Plugin editor unavailable for %s (%s)." % (key, exc))
            self._bridges[key] = bridge
            return bridge

    def parameters(self, key, description):
        """Return the parameter descriptions of a plugin."""
        bridge = self._bridge(key, description)
        if bridge is None:
            return []
        try:
            tree = bridge.parameterTree()
            if tree is None:
                return []
            items = list(tree.allParameters())
        except Exception:
            return []
        params = []
        for item in items:
            try:
                identifier = item.identifier()
            except Exception:
                continue
            try:
                parameter_id = int(identifier)
            except Exception:
                continue
            try:
                name = str(item.displayName())
            except Exception:
                name = "Parameter %d" % parameter_id
            low = _number(item, "minValue")
            high = _number(item, "maxValue")
            default = _number(item, "_defaultValue")
            if low is None:
                low = 0.0
            if high is None:
                high = 1.0
            if high <= low:
                high = low + 1.0
            params.append({
                "id": parameter_id,
                "name": name,
                "min": low,
                "max": high,
                "default": default,
            })
        params.sort(key=lambda item: item["id"])
        return params

    def presets(self, key, description):
        """Return the factory presets of a plugin."""
        bridge = self._bridge(key, description)
        if bridge is None:
            return []
        try:
            raw = bridge.factoryPresets()
        except Exception:
            return []
        if not raw:
            return []
        presets = []
        try:
            items = list(raw)
        except Exception:
            return []
        for item in items:
            try:
                number = int(item.number())
            except Exception:
                number = len(presets)
            try:
                name = str(item.name())
            except Exception:
                name = "Preset %d" % number
            presets.append({"number": number, "name": name})
        return presets

    def release(self, key):
        with self._lock:
            self._bridges.pop(key, None)
