import json
import os
import threading
from pathlib import Path

import AVFoundation as av

PLUGIN_FILE = (Path.home() / "Library" / "Application Support"
               / "Arranger Keyboard" / "plugins.json")


class PluginCatalog:
    def __init__(self):
        self._lock = threading.RLock()
        self._descriptions = {}
        self._labels = {}
        self._added = self._load_added()
        self.discover()

    def _load_added(self):
        try:
            data = json.loads(PLUGIN_FILE.read_text(encoding="utf-8"))
            return {str(key) for key in data.get("added", [])
                    if isinstance(key, str)}
        except FileNotFoundError:
            return set()
        except Exception as exc:
            print("Plugin preferences unavailable (%s)." % exc)
            return set()

    def _save_added(self):
        PLUGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PLUGIN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"added": sorted(self._added)}, indent=2),
                       encoding="utf-8")
        os.replace(tmp, PLUGIN_FILE)

    def discover(self):
        descriptions = {}
        labels = {}
        try:
            manager = av.AVAudioUnitComponentManager.sharedAudioUnitComponentManager()
            components = manager.componentsPassingTest_(
                lambda component, stop: (
                    component.typeName() == av.AVAudioUnitTypeMusicDevice,
                    False,
                )
            )
            for component in components:
                description = component.audioComponentDescription()
                key = "au:%08x:%08x:%08x" % (
                    int(description[1]),
                    int(description[2]),
                    int(description[3]),
                )
                name = str(component.name())
                manufacturer = str(component.manufacturerName())
                label = name if not manufacturer else "%s — %s" % (
                    name,
                    manufacturer,
                )
                descriptions[key] = description
                labels[key] = label
        except Exception as exc:
            print("Audio Unit discovery unavailable (%s)." % exc)
        with self._lock:
            self._descriptions = descriptions
            self._labels = labels

    def instruments(self):
        with self._lock:
            return sorted(((key, label) for key, label in self._labels.items()
                           if key in self._added),
                          key=lambda item: item[1].lower())

    def manager_entries(self, query=""):
        query = query.strip().lower()
        with self._lock:
            entries = []
            for key, label in self._labels.items():
                if query and query not in label.lower() and query not in key:
                    continue
                entries.append({"key": key, "label": label,
                                "added": key in self._added})
            return sorted(entries, key=lambda item: (
                not item["added"], item["label"].lower()))

    def is_added(self, key):
        with self._lock:
            return key in self._added and key in self._descriptions

    def add(self, key):
        with self._lock:
            if key not in self._descriptions:
                return False
            self._added.add(key)
            self._save_added()
            return True

    def remove(self, key):
        with self._lock:
            if key not in self._added:
                return False
            self._added.discard(key)
            self._save_added()
            return True

    def description(self, key):
        with self._lock:
            if key not in self._added:
                return None
            return self._descriptions.get(key)
