"""Arranger engine: chord recognition, styles, sections and the step clock."""

import threading
import time

from core import (BLACK_PCS, CHORD_TYPES, NOTE_NAMES, SPLIT, STYLES,
                  TWO_KEY, VOICES)


class Engine:
    def __init__(self, synth):
        self.synth = synth
        self.lock = threading.RLock()
        self.style_index = 0
        self._custom_style = None
        self.tempo = STYLES[0]["bpm"]
        self.melody_voice = "piano"
        self.voice_choices = []
        self.voice_labels = {}
        self.refresh_voices()
        self.transpose = 0
        self.balance = 50
        self.live = {}
        self.mode = "f"                 # 'f' two-key / 's' single finger
        self.acmp = True
        self.sync = False
        self.harmony = False
        self.main = "A"
        self.sec = "A"
        self.pend = None
        self.waiting = None             # None | True | 'I'
        self.intro_left = 0
        self.step = 0
        self.beat = -1
        self.crash_next = True
        self.chord = None               # (root pc, type)
        self.held = set()
        self.active = {}                # sustained style parts
        self.running = False
        self.next_t = 0.0
        self.alive = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------
    @property
    def style(self):
        if self._custom_style is not None:
            return self._custom_style
        return STYLES[self.style_index]

    def voice_label(self, voice):
        return self.voice_labels.get(voice, voice)

    def refresh_voices(self):
        plugins = []
        refresh = getattr(self.synth, "refresh_instruments", None)
        try:
            if refresh is not None:
                plugins = refresh()
            else:
                plugins = self.synth.available_instruments()
        except Exception:
            plugins = []
        with self.lock:
            self.voice_choices = list(VOICES) + list(plugins)
            self.voice_labels = dict(self.voice_choices)
            if self.melody_voice not in self.voice_labels:
                self.melody_voice = "piano"

    def detect(self, notes):
        a = sorted(notes)
        n = len(a)
        if n == 0:
            return None
        if n == 1:
            return (a[0] % 12, "M")
        if self.mode == "s":
            root = a[-1]
            lower = [k for k in a[:-1] if root - k <= 3]
            bk = any((k % 12) in BLACK_PCS for k in lower)
            wk = any((k % 12) not in BLACK_PCS for k in lower)
            t = "m7" if (bk and wk) else "m" if bk else "7" if wk else "M"
            return (root % 12, t)
        if n == 2:
            return (a[0] % 12, TWO_KEY.get((a[1] - a[0]) % 12, "M"))
        pcs = sorted({k % 12 for k in a})
        for root in [k % 12 for k in a]:
            rel = tuple(sorted((p - root) % 12 for p in pcs))
            for name, (_, iv) in CHORD_TYPES.items():
                if tuple(iv) == rel:
                    return (root, name)
        return (a[0] % 12, "M")

    def chord_label(self):
        if not self.chord:
            return "\u2013"
        root, typ = self.chord
        root = (root + self.transpose) % 12
        return NOTE_NAMES[root] + CHORD_TYPES[typ][0]

    # ------------------------------------------------------------------
    def note_on(self, midi, velocity=0.5):
        with self.lock:
            if midi < SPLIT and self.acmp:
                old = self.chord
                self.held.add(midi)
                self.chord = self.detect(self.held)
                if old != self.chord:
                    self._revoice()
                if self.waiting:
                    self._start(self.waiting == "I")
                return "chord"
            voice = "piano" if midi < SPLIT else self.melody_voice
            outs = [midi + self.transpose]
            if self.harmony and midi >= SPLIT:
                outs.append(midi + self.transpose + 7)
            self.live[midi] = outs
            gain = self._voice_gain()
            if gain > 0.0:
                for i, out in enumerate(outs):
                    self.synth.play_live(
                        voice, out,
                        self._gain_velocity(
                            velocity * (0.62 if i else 1.0), gain))
            return "note"

    def note_off(self, midi):
        """Key released: the sustain knob controls how long it keeps ringing."""
        with self.lock:
            self.held.discard(midi)
            outs = self.live.pop(midi, None)
            if outs is not None:
                for out in outs:
                    self.synth.stop_live(out)

    def release_note(self, midi):
        """Sustain pedal lifted: the note fades with the voice's own decay."""
        with self.lock:
            outs = self.live.pop(midi, None)
            if outs is not None:
                for out in outs:
                    self.synth.release_live(out)

    def panic_note(self, midi):
        """Stop a note immediately, ignoring the sustain knob."""
        with self.lock:
            outs = self.live.pop(midi, None)
            if outs is not None:
                for out in outs:
                    self.synth.panic_live(out)

    # ------------------------------------------------------------------
    def _start(self, intro):
        self.sec = "I" if intro else self.main
        self.intro_left = 2
        self.step = 0
        self.pend = None
        self.next_t = time.perf_counter() + 0.08
        self.running = True
        self.waiting = None
        self.beat = 0
        self.crash_next = True

    def start_button(self):
        with self.lock:
            if self.running:
                self._stop()
            elif self.waiting:
                self.waiting = None
            elif self.sync and self.acmp and not self.held:
                self.waiting = True
            else:
                self._start(False)

    def _stop(self):
        self.running = False
        self.waiting = None
        self.sec = self.main
        self.pend = None
        self.step = 0
        self.beat = -1
        self._silence_active()

    def stop(self):
        with self.lock:
            self._stop()

    def intro_button(self):
        with self.lock:
            if self.running:
                self.pend = "I"
            elif self.sync and self.acmp and not self.held:
                self.waiting = "I"
            else:
                self._start(True)

    def main_button(self, section):
        with self.lock:
            if self.running:
                self.pend = section
            else:
                self.main = section

    def ending_button(self):
        with self.lock:
            if self.running:
                self.pend = "E"

    def fill_button(self):
        with self.lock:
            if self.running:
                self.pend = self.sec if self.sec in ("A", "B") else self.main

    # ------------------------------------------------------------------
    def toggle_acmp(self):
        with self.lock:
            self.acmp = not self.acmp

    def toggle_sync(self):
        with self.lock:
            self.sync = not self.sync
            if not self.sync and not self.running:
                self.waiting = None

    def select_style(self, index):
        with self.lock:
            index = max(0, min(len(STYLES) - 1, int(index)))
            self._silence_active()
            self.style_index = index
            self._custom_style = None
            if self.running:
                self.crash_next = True
            self.tempo = STYLES[index]["bpm"]
            if self.step >= STYLES[index]["len"]:
                self.step = 0

    def select_style_dict(self, style_dict):
        with self.lock:
            self._silence_active()
            self._custom_style = style_dict
            self.style_index = 0
            if self.running:
                self.crash_next = True
            self.tempo = style_dict.get("bpm", 120)
            if self.step >= style_dict.get("len", 16):
                self.step = 0

    def restore(self, data):
        """Apply a saved song snapshot without starting playback."""
        with self.lock:
            self._stop()
            for outs in self.live.values():
                for out in outs:
                    self.synth.panic_live(out)
            self.live.clear()

            index = data.get("style_index", 0)
            name = data.get("style_name")
            if name:
                for i, style in enumerate(STYLES):
                    if style["name"] == name:
                        index = i
                        break
            try:
                index = int(index)
            except (TypeError, ValueError):
                index = 0
            self.style_index = max(0, min(len(STYLES) - 1, index))

            try:
                self.tempo = max(50, min(200, int(data.get("tempo",
                                                            self.tempo))))
            except (TypeError, ValueError):
                self.tempo = STYLES[self.style_index]["bpm"]

            voice = data.get("voice")
            if voice in self.voice_labels:
                self.melody_voice = voice

            try:
                self.transpose = max(-12, min(12, int(data.get("transpose", 0))))
            except (TypeError, ValueError):
                self.transpose = 0
            try:
                self.balance = max(0, min(100, int(data.get("balance", 50))))
            except (TypeError, ValueError):
                self.balance = 50

            self.mode = data.get("mode") if data.get("mode") in ("f", "s") else "f"
            self.acmp = bool(data.get("acmp", True))
            self.sync = bool(data.get("sync", False))
            self.harmony = bool(data.get("harmony", False))
            main = data.get("main")
            self.main = main if main in ("A", "B") else "A"
            self.sec = self.main
            self.chord = None
            chord = data.get("chord")
            if isinstance(chord, (list, tuple)) and len(chord) == 2:
                root, typ = chord
                try:
                    root = int(root) % 12
                except (TypeError, ValueError):
                    root = None
                if root is not None and typ in CHORD_TYPES:
                    self.chord = (root, typ)
            self.held.clear()
            self.step = 0
            self.beat = -1
            self.crash_next = True

    def set_tempo(self, value):
        with self.lock:
            self.tempo = max(50, min(200, int(round(value))))

    def set_voice(self, voice):
        with self.lock:
            if voice not in self.voice_labels:
                return
            self.melody_voice = voice
            ensure = getattr(self.synth, "ensure_instrument", None)
            if ensure is not None:
                ensure(voice)

    def set_harmony(self, on):
        with self.lock:
            self.harmony = bool(on)

    def set_transpose(self, value):
        with self.lock:
            self.transpose = max(-12, min(12, int(round(value))))

    def set_balance(self, value):
        with self.lock:
            self.balance = max(0, min(100, int(round(value))))

    def _voice_gain(self):
        return min(1.0, self.balance / 50.0)

    def _style_gain(self):
        return min(1.0, (100 - self.balance) / 50.0)

    @staticmethod
    def _gain_velocity(velocity, gain):
        if gain >= 0.999:
            return velocity
        return max(0.0, min(1.0, velocity * (gain ** (1.0 / 0.65))))

    def _drum(self, name, vel=1.0, open_=False):
        if name == "hat":
            vel *= 0.65
        elif name == "openhat":
            vel *= 0.5
        gain = self._style_gain()
        if gain > 0.0:
            self.synth.play_drum(
                name, self._gain_velocity(vel, gain), open_)

    def set_mode(self, mode):
        with self.lock:
            old = self.chord
            self.mode = mode
            if self.held:
                self.chord = self.detect(self.held)
                if old != self.chord:
                    self._revoice()

    # ------------------------------------------------------------------
    def state(self):
        with self.lock:
            return {
                "style": self.style_index,
                "style_name": self.style["name"],
                "tempo": self.tempo,
                "transpose": self.transpose,
                "running": self.running,
                "waiting": self.waiting,
                "main": self.main,
                "sec": self.sec,
                "pend": self.pend,
                "beat": self.beat if self.running else -1,
                "chord": self.chord,
                "chord_label": self.chord_label(),
                "acmp": self.acmp,
                "sync": self.sync,
                "mode": self.mode,
                "voice": self.melody_voice,
                "voice_label": self.voice_label(self.melody_voice),
                "harmony": self.harmony,
                "balance": self.balance,
                "held": set(self.held),
            }

    # ------------------------------------------------------------------
    def _loop(self):
        while self.alive:
            if not self.running:
                time.sleep(0.01)
                continue
            now = time.perf_counter()
            if now + 0.001 < self.next_t:
                time.sleep(min(0.004, max(0.001, self.next_t - now)))
                continue
            with self.lock:
                if not self.running:
                    continue
                s = self.step
                self._play_step(s)
                if s % 4 == 0:
                    self.beat = s // 4
                step_dur = 60.0 / self.tempo / 4.0
                self.next_t += step_dur
                self.step = s + 1
                if self.step >= self.style["len"]:
                    self.step = 0
                    self._bar_end()
                now2 = time.perf_counter()
                if self.next_t < now2 - 0.05:
                    self.next_t = now2 + step_dur

    def _bar_end(self):
        if self.sec == "E":
            self._stop()
            return
        if self.sec == "I":
            self.intro_left -= 1
            if self.intro_left <= 0:
                self.sec = self.main
                self.crash_next = True
        if self.pend:
            self.sec = self.pend
            if self.sec in ("A", "B"):
                self.main = self.sec
            if self.sec == "I":
                self.intro_left = 2
            self.pend = None
            self.crash_next = True

    # ------------------------------------------------------------------
    @staticmethod
    def _on(pattern, s):
        return bool(pattern) and s < len(pattern) and pattern[s] == "x"

    def _mark_active(self, part, voice, offset, vel, dur):
        self.active[part] = {
            "voice": voice, "offset": offset, "vel": vel,
            "end_t": time.perf_counter() + max(0.05, dur),
        }

    def _revoice(self):
        if not self.running or not self.acmp or not self.chord:
            return
        now = time.perf_counter()
        jobs = []
        for part in ("bass", "comp", "pad"):
            item = self.active.get(part)
            if not item:
                continue
            remaining = item["end_t"] - now
            if remaining <= 0.06:
                self.active.pop(part, None)
                continue
            jobs.append((part, dict(item), remaining))
        if not jobs:
            return
        stop_timed = getattr(self.synth, "stop_timed", None)
        if stop_timed is not None:
            for voice in {item["voice"] for _, item, _ in jobs}:
                stop_timed(voice)
        for part, item, remaining in jobs:
            if part == "bass":
                self._bass(self.chord, item["offset"], remaining,
                           item["vel"])
            else:
                self._comp(self.chord, remaining, item["voice"],
                           item["vel"])
            item["end_t"] = now + remaining
            self.active[part] = item

    def _silence_active(self):
        voices = {item["voice"] for item in self.active.values()}
        self.active.clear()
        stop_timed = getattr(self.synth, "stop_timed", None)
        if stop_timed is not None:
            for voice in voices:
                stop_timed(voice)

    def _play_step(self, s):
        S = self.style
        L = S["len"]
        sp = 60.0 / self.tempo / 4.0
        fill = (self.pend and self.pend != "I" and self.sec not in ("E", "I") and s >= L - 4)

        intro_pat = S.get("intro") or {}
        fill_pat = S.get("fill") or {}
        ending_pat = S.get("ending") or {}

        def has_pat(pat):
            return bool(pat and any(pat.get(k) for k in ["kick","snare","hat","open","crash","comp","bass"]))

        # Ending with pattern support
        if self.sec == "E":
            if has_pat(ending_pat):
                P = {**S, **ending_pat}
            else:
                if s == 0:
                    self._drum("kick", 1.0)
                    self._drum("snare", 1.0)
                    self._drum("crash", 0.9)
                    self._drum("openhat", 1.0, True)
                    if self.acmp and self.chord:
                        bdur = sp * L * 0.8
                        cdur = sp * L * 0.9
                        self._bass(self.chord, 0, bdur)
                        self._comp(self.chord, cdur)
                        self._mark_active("bass", "bass", 0, 0.5, bdur)
                        self._mark_active("comp", self.style["comp_voice"], 0, self.style.get("comp_vel", 0.14), cdur)
                return

        # Base pattern
        P = dict(S)
        if self.sec == "B" and "B" in S:
            P.update(S["B"])

        intro_generated = self.sec == "I" and not has_pat(intro_pat)
        fill_generated = fill and not has_pat(fill_pat)

        if self.sec == "I" and not intro_generated:
            P = {**S, **intro_pat}
        if fill and not fill_generated:
            P = {**S, **fill_pat}
        if self.sec == "E" and has_pat(ending_pat):
            P = {**S, **ending_pat}

        if s == 0:
            if self.crash_next:
                self._drum("crash", 1.0)
                self.crash_next = False

        if self.sec == "I" and intro_generated:
            if s % 4 == 0:
                self._drum("kick", 1.0)
            if s % 2 == 0:
                self._drum("openhat", 1.0 if s % 4 == 0 else 0.5, True)
        elif fill and fill_generated:
            if s == L - 4:
                self._drum("kick", 1.0)
            self._drum("snare", 0.5 + 0.12 * (s - (L - 4)))
            if s == L - 1:
                self._drum("openhat", 0.8, True)
        else:
            if self._on(P.get("kick"), s):
                self._drum("kick", 1.0)
            if self._on(P.get("snare"), s):
                self._drum("snare", S.get("snare_vel", 1.0))
            if self._on(P.get("hat"), s):
                self._drum("hat", 0.45 if s % 4 else 0.9, False)
            if self._on(P.get("open"), s):
                self._drum("openhat", 0.8, True)

        if self.acmp and self.chord and not fill:
            for pos, offset, dur in P.get("bass", ()):
                if pos == s:
                    bdur = dur * sp
                    self._bass(self.chord, offset, bdur)
                    self._mark_active("bass", "bass", offset, 0.5, bdur)
            if self._on(P.get("comp"), s):
                voice = P.get("comp_voice", S["comp_voice"])
                vel = P.get("comp_vel", S.get("comp_vel", 0.14))
                cdur = P.get("comp_len", S["comp_len"]) * sp
                self._comp(self.chord, cdur, voice, vel)
                self._mark_active("comp", voice, 0, vel, cdur)
                fade = P.get("comp_fade")
                if fade:
                    start, end = fade
                    self.synth.fade_voice(
                        voice, max(0.0, (start - s) * sp),
                        max(0.01, (end - start) * sp))
            if self._on(P.get("pad"), s):
                voice = P.get("pad_voice", S.get("pad_voice", "strings"))
                dur = P.get("pad_len", S.get("pad_len", S["comp_len"])) * sp
                vel = P.get("pad_vel", S.get("pad_vel", 0.09))
                self._comp(self.chord, dur, voice, vel)
                self._mark_active("pad", voice, 0, vel, dur)
            for pos, degree, dur in P.get("piano_notes", ()):
                if pos == s:
                    voice = P.get("note_voice", S.get("note_voice", "piano"))
                    vel = P.get("note_vel", S.get("note_vel", 0.22))
                    self._piano_note(self.chord, degree, dur * sp,
                                     voice, vel)

    def _bass(self, chord, offset, dur, vel=0.5):
        root, typ = chord
        iv = CHORD_TYPES[typ][1]
        off = iv[1] if offset == "T" else offset
        midi = 28 + ((root - 28) % 12) + off + self.transpose
        gain = self._style_gain()
        if gain > 0.0:
            self.synth.play(
                "bass", midi, max(0.05, dur),
                self._gain_velocity(vel, gain))

    def _piano_note(self, chord, degree, dur, voice="piano", vel=0.22):
        root, typ = chord
        iv = CHORD_TYPES[typ][1]
        octave, idx = divmod(degree, len(iv))
        midi = 60 + ((root - 60) % 12) + iv[idx] + 12 * octave
        midi += self.transpose
        gain = self._style_gain()
        if gain > 0.0:
            self.synth.play(
                voice, midi, max(0.05, dur),
                self._gain_velocity(vel, gain))

    def _comp(self, chord, dur, voice=None, vel=0.14):
        root, typ = chord
        gain = self._style_gain()
        if gain <= 0.0:
            return
        vel = self._gain_velocity(vel, gain)
        voice = voice or self.style["comp_voice"]
        for iv in CHORD_TYPES[typ][1]:
            midi = 55 + ((root + iv - 55) % 12) + self.transpose
            self.synth.play(voice, midi, max(0.05, dur), vel)

    def shutdown(self):
        self.alive = False
        self.running = False
        self.thread.join(timeout=0.5)
