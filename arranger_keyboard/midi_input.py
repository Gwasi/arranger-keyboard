"""External MIDI keyboard input via pygame.midi (PortMidi / CoreMIDI)."""

import time

import pygame
import pygame.midi


class MidiInput:
    """Opens the first available MIDI input and turns its messages into callbacks.

    The device is rescanned while none is connected, so a controller can be
    plugged in after the app has started.
    """

    def __init__(self, note_on, note_off, sustain=None, all_notes_off=None):
        self._note_on = note_on
        self._note_off = note_off
        self._sustain = sustain
        self._all_off = all_notes_off
        self.device = None
        self.name = ""
        self.status = "MIDI: none"
        self._last_scan = 0.0
        try:
            pygame.midi.init()
        except Exception as exc:
            self.status = "MIDI: unavailable (%s)" % exc
            return
        self._scan(True)

    # ------------------------------------------------------------------
    def _scan(self, force=False):
        now = time.time()
        if not force and now - self._last_scan < 2.0:
            return
        self._last_scan = now
        if not force:
            try:
                pygame.midi.quit()
                pygame.midi.init()
            except Exception:
                self.status = "MIDI: unavailable"
                return
        try:
            count = pygame.midi.get_count()
        except Exception:
            self.status = "MIDI: unavailable"
            return
        for i in range(count):
            try:
                info = pygame.midi.get_device_info(i)
            except Exception:
                continue
            if not info or not info[2]:
                continue
            name = info[1]
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            try:
                self.device = pygame.midi.Input(i)
            except Exception:
                continue
            self.name = name or "input %d" % i
            self.status = "MIDI: " + self.name
            return
        self.status = "MIDI: none"

    def _close_device(self):
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
        self.device = None
        self.name = ""
        self.status = "MIDI: disconnected"

    # ------------------------------------------------------------------
    def handle(self, status, data1, data2):
        kind = status & 0xF0
        if kind == 0x90 and data2 > 0:
            self._note_on(data1, data2 / 127.0)
        elif kind == 0x80 or (kind == 0x90 and data2 == 0):
            self._note_off(data1)
        elif kind == 0xB0:
            if data1 == 64:
                if self._sustain is not None:
                    self._sustain(data2 >= 64)
            elif data1 in (120, 123):
                if self._all_off is not None:
                    self._all_off()

    def poll(self):
        if self.device is None:
            self._scan()
            return
        try:
            if not self.device.poll():
                return
            events = self.device.read(64)
        except Exception:
            self._close_device()
            return
        for event in events:
            msg = event[0]
            if len(msg) >= 3:
                self.handle(msg[0], msg[1], msg[2])

    def close(self):
        self._close_device()
        try:
            pygame.midi.quit()
        except Exception:
            pass
