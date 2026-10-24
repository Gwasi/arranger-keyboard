"""Offline synthesiser: renders notes and drum hits, plays them through pygame."""

import os
import threading
import time
from collections import OrderedDict
from functools import lru_cache

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import numpy as np
import pygame
from scipy.signal import butter, lfilter

from core import SR

CACHE_MAX = 160
LIVE_SECONDS = 4.0


def midi_hz(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


@lru_cache(maxsize=64)
def _coeffs(kind, fc, order):
    wn = min(0.99, max(0.001, fc / (SR / 2.0)))
    return butter(order, wn, btype=kind)


def _filt(x, kind, fc, order=2):
    b, a = _coeffs(kind, float(fc), order)
    return lfilter(b, a, x)


def _attack(n, seconds):
    a = max(1, int(seconds * SR))
    env = np.ones(n)
    a = min(a, n)
    env[:a] = np.linspace(0.0, 1.0, a)
    return env


def _finish(sig, dur, tau=0.07):
    n = sig.shape[0]
    t = np.arange(n) / SR
    if dur < t[-1]:
        env = np.ones(n)
        rel = t > dur
        env[rel] = np.exp(-(t[rel] - dur) / tau)
        cut = min(n, int((dur + tau * 5) * SR))
        sig = sig[:cut] * env[:cut]
    f = min(len(sig), int(0.005 * SR))
    if f > 1:
        sig[-f:] *= np.linspace(1.0, 0.0, f)
    return sig


def _tri(f, t):
    return 2.0 * np.abs(2.0 * ((f * t) % 1.0) - 1.0) - 1.0


def _saw(f, t):
    return 2.0 * ((f * t) % 1.0) - 1.0


def _square(f, t):
    return np.sign(np.sin(2 * np.pi * f * t))


class Synth:
    def __init__(self, master=0.6):
        pygame.mixer.pre_init(SR, -16, 1, 512)
        pygame.mixer.init(SR, -16, 1, 512)
        pygame.mixer.set_num_channels(48)
        self.master = master
        self.n = 48
        self.cache = OrderedDict()
        self.vols = [0.0] * self.n
        self.started = [0.0] * self.n
        self.live_idx = set()
        self.live = {}          # midi -> channel index
        self.timed = {}         # channel index -> voice
        self.rng = np.random.default_rng(20261021)
        self.reverb = 0.0
        self.chorus = 0.0
        self.sustain = 0.0

    # ------------------------------------------------------------------
    def _store(self, key, sound):
        self.cache[key] = sound
        self.cache.move_to_end(key)
        while len(self.cache) > CACHE_MAX:
            self.cache.popitem(last=False)
        return sound

    def _sound(self, voice, midi, dur, live=False):
        key = (voice, int(midi), -1 if live else round(float(dur), 2))
        snd = self.cache.get(key)
        if snd is not None:
            self.cache.move_to_end(key)
            return snd
        length = LIVE_SECONDS if live else max(0.06, float(dur))
        sig = self._render(voice, midi, length)
        return self._store(key, self._to_sound(sig))

    def drum(self, name, open_=False):
        key = ("drum", name, bool(open_))
        snd = self.cache.get(key)
        if snd is not None:
            self.cache.move_to_end(key)
            return snd
        sig = self._render_drum(name, open_)
        return self._store(key, self._to_sound(sig))

    @staticmethod
    def _to_sound(sig):
        peak = float(np.max(np.abs(sig))) if len(sig) else 0.0
        if peak > 1.0:
            sig = sig / peak
        arr = np.clip(sig * 32000.0, -32768, 32767).astype(np.int16)
        arr = np.ascontiguousarray(arr)
        return pygame.mixer.Sound(buffer=arr.tobytes())

    # ------------------------------------------------------------------
    def _render(self, voice, midi, dur):
        f = midi_hz(midi)
        total = dur + (0.05 if voice in ("piano", "ep") else 0.5)
        n = max(64, int(total * SR))
        t = np.arange(n) / SR

        if voice == "piano":
            sig = (0.62 * _tri(f, t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)
                   + 0.10 * np.sin(2 * np.pi * 3 * f * t))
            sig = _filt(sig, "low", 3500)
            env = _attack(n, 0.008) * np.exp(-t / 1.7)
            sig = sig * env * 0.9
            return _finish(sig, dur, 0.07)

        if voice == "ep":
            sig = (np.sin(2 * np.pi * f * t)
                   + 0.32 * np.sin(2 * np.pi * 2 * f * t)
                   + 0.10 * np.sin(2 * np.pi * 3.01 * f * t))
            sig += 0.10 * np.sin(2 * np.pi * 4.02 * f * t) * np.exp(-t / 0.18)
            sig = _filt(sig, "low", 4200)
            env = _attack(n, 0.005) * np.exp(-t / 1.25)
            return _finish(sig * env * 0.85, dur, 0.09)

        if voice == "organ":
            sig = (0.40 * np.sin(2 * np.pi * f * t)
                   + 0.30 * np.sin(2 * np.pi * 2 * f * t)
                   + 0.20 * np.sin(2 * np.pi * 3 * f * t)
                   + 0.10 * np.sin(2 * np.pi * 4 * f * t))
            sig = _filt(sig, "low", 5000)
            env = _attack(n, 0.012)
            return _finish(sig * env * 0.85, dur, 0.10)

        if voice == "strings":
            a = _saw(f * 2 ** (-7 / 1200), t)
            b = _saw(f * 2 ** (7 / 1200), t)
            sig = _filt(0.30 * a + 0.30 * b, "low", 1800)
            trem = 1.0 + 0.03 * np.sin(2 * np.pi * 5.2 * t)
            env = _attack(n, 0.12) * trem
            return _finish(sig * env * 0.95, dur, 0.32)

        if voice == "lead":
            sig = (0.30 * _square(f, t)
                   + 0.22 * _saw(f * 2 ** (5 / 1200), t))
            sig = _filt(sig, "low", 2800)
            env = _attack(n, 0.01)
            return _finish(sig * env * 0.9, dur, 0.12)

        if voice == "palm":
            sig = _saw(f, t) + 0.45 * _saw(f * 2, t)
            sig = np.tanh(2.6 * sig) / np.tanh(2.6)
            sig = _filt(sig, "low", 1700)
            env = _attack(n, 0.003) * np.exp(-t / 0.09)
            return _finish(sig * env * 0.55, dur, 0.04)

        if voice == "classgtr":
            sig = (0.55 * _tri(f, t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)
                   + 0.12 * np.sin(2 * np.pi * 3 * f * t))
            sig += 0.08 * np.sin(2 * np.pi * 4.01 * f * t) * np.exp(-t / 0.12)
            sig = _filt(sig, "low", 2600)
            env = _attack(n, 0.004) * np.exp(-t / 1.25)
            return _finish(sig * env * 0.85, dur, 0.11)

        if voice in ("guitar", "leadgtr"):
            sig = (_saw(f, t) + 0.55 * _saw(f * 2, t)
                   + 0.25 * _square(f * 0.5, t))
            drive = 2.8 if voice == "guitar" else 4.0
            sig = np.tanh(drive * sig) / np.tanh(drive)
            sig = _filt(sig, "low", 3200)
            env = _attack(n, 0.006) * np.exp(-t / 1.1)
            return _finish(sig * env * 0.55, dur, 0.09)

        # bass
        sig = 0.50 * _saw(f, t) + 0.60 * np.sin(2 * np.pi * 0.5 * f * t)
        sig = _filt(sig, "low", 500)
        env = _attack(n, 0.006) * (0.72 + 0.28 * np.exp(-t / 0.35))
        return _finish(sig * env * 0.9, dur, 0.08)

    def _render_drum(self, name, open_):
        if name == "kick":
            n = int(0.42 * SR)
            t = np.arange(n) / SR
            freq = 45.0 + 110.0 * np.exp(-t / 0.035)
            phase = 2 * np.pi * np.cumsum(freq) / SR
            sig = np.sin(phase) * np.exp(-t / 0.16)
            click = np.exp(-t / 0.004) * 0.25
            sig = sig + click * np.sin(2 * np.pi * 1300 * t)
            return _finish(sig * 0.95, 0.4, 0.04)

        if name == "snare":
            n = int(0.30 * SR)
            t = np.arange(n) / SR
            noise = self.rng.uniform(-1, 1, n)
            noise = _filt(noise, "high", 1500)
            body = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.045) * 0.5
            sig = noise * np.exp(-t / 0.085) * 0.8 + body
            return _finish(sig * 0.9, 0.25, 0.03)

        if name in ("hat", "openhat"):
            n = int((0.50 if open_ else 0.12) * SR)
            t = np.arange(n) / SR
            noise = _filt(self.rng.uniform(-1, 1, n), "high", 7000)
            env = np.exp(-t / (0.22 if open_ else 0.032))
            return _finish(noise * env * 0.55, 0.08 if not open_ else 0.4, 0.02)

        if name == "clap":
            n = int(0.34 * SR)
            t = np.arange(n) / SR
            noise = _filt(self.rng.uniform(-1, 1, n), "high", 1200)
            noise = _filt(noise, "low", 4500)
            env = np.zeros(n)
            for delay, gain in ((0.0, 1.0), (0.022, 0.7), (0.044, 0.45)):
                d = int(delay * SR)
                env[d:] += gain * np.exp(-(t[:n - d]) / 0.05)
            return _finish(noise * env * 0.42, 0.28, 0.03)

        if name == "crash":
            n = int(1.6 * SR)
            t = np.arange(n) / SR
            noise = _filt(self.rng.uniform(-1, 1, n), "high", 3800)
            sig = noise * np.exp(-t / 0.55)
            return _finish(sig * 0.45, 1.4, 0.08)

        # toms
        base = {"tom1": 210.0, "tom2": 160.0, "tom3": 120.0}.get(name, 170.0)
        n = int(0.45 * SR)
        t = np.arange(n) / SR
        freq = base * 0.7 + base * 0.3 * np.exp(-t / 0.06)
        phase = 2 * np.pi * np.cumsum(freq) / SR
        sig = np.sin(phase) * np.exp(-t / 0.16)
        return _finish(sig * 0.8, 0.4, 0.05)

    # ------------------------------------------------------------------
    def _alloc(self, live=False):
        free = []
        for i in range(self.n):
            if i in self.live_idx:
                continue
            if not pygame.mixer.Channel(i).get_busy():
                free.append(i)
        if free:
            return free[0]
        pool = [i for i in range(self.n) if i not in self.live_idx]
        if not pool:
            pool = list(range(self.n))
        return min(pool, key=lambda j: self.started[j])

    def available_instruments(self):
        return []

    def refresh_instruments(self):
        return []

    def instrument_ready(self, voice):
        return False

    def plugin_entries(self, query=""):
        return []

    def add_instrument(self, key):
        return False

    def remove_instrument(self, key):
        return False

    def ensure_instrument(self, voice):
        return False

    def play(self, voice, midi, dur, vel=0.4, live=False):
        idx = self._alloc(live)
        ch = pygame.mixer.Channel(idx)
        ch.stop()
        snd = self._sound(voice, midi, dur, live)
        self.vols[idx] = vel
        ch.set_volume(min(1.0, self.master * vel))
        ch.play(snd)
        self.started[idx] = time.perf_counter()
        if live:
            self.live_idx.add(idx)
            self.timed.pop(idx, None)
        else:
            self.live_idx.discard(idx)
            self.timed[idx] = voice
        return idx

    def play_live(self, voice, midi, vel=0.5):
        if midi in self.live:
            self.panic_live(midi)
        idx = self.play(voice, midi, LIVE_SECONDS, vel, live=True)
        self.live[midi] = idx
        return idx

    def stop_live(self, midi, fade_ms=150):
        """Key released: the sustain knob decides how long the note rings."""
        idx = self.live.pop(midi, None)
        if idx is None:
            return
        self.live_idx.discard(idx)
        delay = self.sustain * 4.0
        if delay > 0.01:
            timer = threading.Timer(delay, self._fade_channel, (idx, fade_ms))
            timer.daemon = True
            timer.start()
        else:
            self._fade_channel(idx, fade_ms)

    def release_live(self, midi):
        """Sustain pedal lifted: fade with a short natural decay."""
        self.stop_live(midi, 120)

    def panic_live(self, midi):
        """Stop a note right away, ignoring the sustain knob."""
        idx = self.live.pop(midi, None)
        if idx is None:
            return
        self.live_idx.discard(idx)
        pygame.mixer.Channel(idx).stop()
        self.vols[idx] = 0.0

    def _fade_channel(self, idx, fade_ms):
        pygame.mixer.Channel(idx).fadeout(fade_ms)
        self.vols[idx] = 0.0

    def stop_timed(self, voice=None):
        for idx, timed_voice in list(self.timed.items()):
            if voice is not None and timed_voice != voice:
                continue
            pygame.mixer.Channel(idx).fadeout(90)
            self.timed.pop(idx, None)
            self.vols[idx] = 0.0

    def play_drum(self, name, vel=1.0, open_=False):
        idx = self._alloc(False)
        ch = pygame.mixer.Channel(idx)
        ch.stop()
        self.timed.pop(idx, None)
        self.vols[idx] = vel
        ch.set_volume(min(1.0, self.master * vel))
        ch.play(self.drum(name, open_))
        self.started[idx] = time.perf_counter()
        self.live_idx.discard(idx)
        return idx

    def set_master(self, value):
        self.master = float(value)
        for i in range(self.n):
            if self.vols[i] > 0:
                pygame.mixer.Channel(i).set_volume(
                    min(1.0, self.master * self.vols[i]))

    def set_reverb(self, value):
        self.reverb = max(0.0, min(100.0, float(value)))

    def set_chorus(self, value):
        self.chorus = max(0.0, min(100.0, float(value)))

    def set_sustain(self, value):
        self.sustain = max(0.0, min(1.0, float(value) / 100.0))

    def fade_voice(self, *args):
        pass

    def all_notes_off(self):
        for midi in list(self.live):
            self.panic_live(midi)
        for i in range(self.n):
            if i not in self.live_idx:
                pygame.mixer.Channel(i).fadeout(80)
        self.live_idx.clear()
        self.live.clear()

    def shutdown(self):
        try:
            pygame.mixer.stop()
            pygame.mixer.quit()
        except Exception:
            pass
