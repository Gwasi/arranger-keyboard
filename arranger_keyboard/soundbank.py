"""Sample-based sounds: Apple's General MIDI DLS bank via AVAudioUnitSampler."""

import math
import os
import threading
import time

import AVFoundation as av
from Foundation import NSURL

from auparams import ParameterCatalog, read_parameter, write_parameter
from plugins import PluginCatalog

BANK_PATH = ("/System/Library/Components/CoreAudio.component/Contents/"
             "Resources/gs_instruments.dls")

MELODIC_BANK = 121
PERCUSSION_BANK = 120

# voice -> General MIDI program
PROGRAMS = {
    "piano": 0,      # Acoustic Grand Piano
    "ep": 4,         # Electric Piano 1
    "strings": 48,   # String Ensemble 1
    "organ": 16,     # Drawbar Organ
    "lead": 81,      # Lead 2 (sawtooth)
    "guitar": 30,    # Distortion Guitar
    "palm": 28,      # Electric Guitar (muted)
    "leadgtr": 29,   # Overdriven Guitar
    "classgtr": 24,  # Acoustic Guitar (nylon)
    "bass": 33,      # Electric Bass (finger)
}

# drum name -> General MIDI percussion note
DRUM_NOTES = {
    "kick": 36,
    "snare": 38,
    "hat": 42,
    "openhat": 46,
    "clap": 39,
    "crash": 49,
    "tom1": 45,
    "tom2": 47,
    "tom3": 50,
}

# Rough release time per voice, in seconds.
RELEASE = {"piano": 0.12, "ep": 0.10, "strings": 0.30,
           "organ": 0.06, "lead": 0.08, "guitar": 0.10,
           "palm": 0.04, "leadgtr": 0.10, "classgtr": 0.18,
           "bass": 0.10}

# Seconds a released key keeps ringing at 100 percent sustain.
MAX_SUSTAIN = 8.0

LIVE_CH = 0
TIMED_CH = 1
DRUM_CH = 9


def _velocity(v):
    v = max(0.0, min(1.0, float(v)))
    return max(1, min(127, int(round(127.0 * (v ** 0.65)))))


class BankSynth:
    """Plays real sampled instruments from the macOS GM sound bank."""

    def __init__(self, master=0.6):
        if not os.path.exists(BANK_PATH):
            raise RuntimeError("GM sound bank not found at " + BANK_PATH)
        self._url = NSURL.fileURLWithPath_(BANK_PATH)
        self._lock = threading.RLock()
        self._timers = set()
        self._live = {}
        self._live_tails = {}
        self._tokens = {}
        self._seq = 0
        self._fade_seq = 0
        self._fade_tokens = {}
        self._closed = False
        self.master = float(master)
        self._plugin_catalog = PluginCatalog()
        self._param_catalog = ParameterCatalog()
        self._plugin_units = {}
        self._plugin_pending = set()
        self._plugin_queue = {}

        self.engine = av.AVAudioEngine.alloc().init()
        self._mixer = self.engine.mainMixerNode()
        self._mixer.setOutputVolume_(self.master)

        self.reverb = 20.0
        self.chorus = 18.0
        self.sustain = 0.0
        self._fx_mix = None
        self._reverb = None
        self._chorus = None
        self._fx_ok = False
        self._fx_loop_thread = None
        try:
            self._build_fx()
            self._fx_ok = True
        except Exception as exc:
            print("Effects unavailable (%s); using dry output." % exc)
            self._fx_mix = None

        out_node = self._fx_mix if self._fx_ok else self._mixer
        self._destination = out_node

        self._voices = {}
        self._samplers = {}
        for voice, program in PROGRAMS.items():
            sampler = av.AVAudioUnitSampler.alloc().init()
            self.engine.attachNode_(sampler)
            self.engine.connect_to_fromBus_toBus_format_(
                sampler, out_node, 0,
                out_node.nextAvailableInputBus(), None)
            ok, err = sampler.loadSoundBankInstrumentAtURL_program_bankMSB_bankLSB_error_(
                self._url, program, MELODIC_BANK, 0, None)
            if not ok:
                raise RuntimeError("could not load %s: %s" % (voice, err))
            self._voices[voice] = sampler
            self._samplers[id(sampler)] = sampler

        self._drums = av.AVAudioUnitSampler.alloc().init()
        self.engine.attachNode_(self._drums)
        self.engine.connect_to_fromBus_toBus_format_(
            self._drums, out_node, 0,
            out_node.nextAvailableInputBus(), None)
        ok, err = self._drums.loadSoundBankInstrumentAtURL_program_bankMSB_bankLSB_error_(
            self._url, 0, PERCUSSION_BANK, 0, None)
        if not ok:
            raise RuntimeError("could not load drum kit: %s" % err)

        ok, err = self.engine.startAndReturnError_(None)
        if not ok:
            try:
                self.engine.stop()
            except Exception:
                pass
            raise RuntimeError("audio engine failed to start: %s" % err)

        if self._fx_ok:
            self.set_reverb(self.reverb)
            self.set_chorus(self.chorus)
            self._fx_loop_thread = threading.Thread(
                target=self._fx_loop, daemon=True)
            self._fx_loop_thread.start()

    # ------------------------------------------------------------------
    def _build_fx(self):
        self._fx_mix = av.AVAudioMixerNode.alloc().init()
        self._chorus = av.AVAudioUnitDelay.alloc().init()
        self._reverb = av.AVAudioUnitReverb.alloc().init()
        for node in (self._fx_mix, self._chorus, self._reverb):
            self.engine.attachNode_(node)
        self.engine.connect_to_fromBus_toBus_format_(
            self._fx_mix, self._chorus, 0, 0, None)
        self.engine.connect_to_fromBus_toBus_format_(
            self._chorus, self._reverb, 0, 0, None)
        self.engine.connect_to_fromBus_toBus_format_(
            self._reverb, self._mixer, 0,
            self._mixer.nextAvailableInputBus(), None)
        self._chorus.setDelayTime_(0.011)
        self._chorus.setFeedback_(6.0)
        self._chorus.setLowPassCutoff_(7000.0)
        self._chorus.setWetDryMix_(0.0)
        self._reverb.loadFactoryPreset_(av.AVAudioUnitReverbPresetMediumHall)
        self._reverb.setWetDryMix_(0.0)

    def _fx_loop(self):
        phase = 0.0
        while not self._closed:
            try:
                if self.chorus > 0.0:
                    phase += 0.04 * 2.0 * math.pi * 0.7
                    self._chorus.setDelayTime_(0.011 + 0.0025 * math.sin(phase))
            except Exception:
                return
            time.sleep(0.04)

    # ------------------------------------------------------------------
    def _later(self, delay, func, *args):
        if self._closed:
            return

        def run():
            with self._lock:
                self._timers.discard(timer)
            if self._closed:
                return
            try:
                func(*args)
            except Exception:
                pass

        timer = threading.Timer(max(0.01, delay), run)
        timer.daemon = True
        self._timers.add(timer)
        timer.start()

    @staticmethod
    def _start(sampler, midi, vel, channel):
        sampler.startNote_withVelocity_onChannel_(
            int(midi), _velocity(vel), channel)

    @staticmethod
    def _stop(sampler, midi, channel):
        try:
            sampler.stopNote_onChannel_(int(midi), channel)
        except Exception:
            pass

    def _play(self, sampler, voice, midi, dur, vel):
        with self._lock:
            self._seq += 1
            token = self._seq
            key = (id(sampler), TIMED_CH, int(midi))
            self._tokens[key] = token
            self._start(sampler, midi, vel, TIMED_CH)
        self._later(dur + self._release_tail(voice),
                    self._timed_stop, sampler, midi, key, token, TIMED_CH)

    def _timed_stop(self, sampler, midi, key, token, channel=TIMED_CH):
        with self._lock:
            if self._tokens.get(key) != token:
                return
            del self._tokens[key]
            if channel == LIVE_CH:
                self._live_tails.pop(int(midi), None)
            self._stop(sampler, midi, channel)

    def available_instruments(self):
        return self._plugin_catalog.instruments()

    def refresh_instruments(self):
        self._plugin_catalog.discover()
        return self.available_instruments()

    def instrument_ready(self, voice):
        with self._lock:
            return voice in self._plugin_units

    def plugin_entries(self, query=""):
        return self._plugin_catalog.manager_entries(query)

    def plugin_parameters(self, voice):
        """Return the editable parameters of an added plugin.

        Each entry carries the parameter identifier, label, range and the
        current value of the instrument that is playing inside the app.
        """
        description = self._plugin_catalog.description(voice)
        if description is None:
            return []
        specs = self._param_catalog.parameters(voice, description)
        with self._lock:
            unit = self._plugin_units.get(voice)
        params = []
        for spec in specs:
            value = read_parameter(unit, spec["id"]) if unit is not None else None
            if value is None:
                value = spec["default"]
            if value is None:
                value = spec["min"]
            params.append({
                "id": spec["id"],
                "name": spec["name"],
                "min": spec["min"],
                "max": spec["max"],
                "value": value,
            })
        return params

    def plugin_presets(self, voice):
        """Return the factory presets of an added plugin."""
        description = self._plugin_catalog.description(voice)
        if description is None:
            return []
        return self._param_catalog.presets(voice, description)

    def set_plugin_parameter(self, voice, parameter_id, value):
        """Change one parameter of a playing plugin."""
        with self._lock:
            unit = self._plugin_units.get(voice)
        if unit is None:
            return False
        return write_parameter(unit, parameter_id, value)

    def plugin_parameter_value(self, voice, parameter_id):
        """Read one parameter of a playing plugin."""
        with self._lock:
            unit = self._plugin_units.get(voice)
        if unit is None:
            return None
        return read_parameter(unit, parameter_id)

    def add_instrument(self, key):
        return self._plugin_catalog.add(key)

    def remove_instrument(self, key):
        removed = self._plugin_catalog.remove(key)
        with self._lock:
            for midi, (voice, _) in list(self._live.items()):
                if voice == key:
                    self.panic_live(midi)
            self.stop_timed(key)
            self._plugin_queue.pop(key, None)
            unit = self._plugin_units.pop(key, None)
            self._param_catalog.release(key)
            if unit is not None:
                self._samplers.pop(id(unit), None)
                try:
                    self.engine.detachNode_(unit)
                except Exception as exc:
                    print("Audio Unit %s could not detach (%s)." % (key, exc))
        return removed

    def ensure_instrument(self, voice):
        with self._lock:
            if voice in self._plugin_units:
                return True
            if voice in self._plugin_pending:
                return False
            description = self._plugin_catalog.description(voice)
            if description is None:
                return False
            self._plugin_pending.add(voice)

        def completed(unit, error):
            with self._lock:
                self._plugin_pending.discard(voice)
                if error is not None or unit is None:
                    print("Audio Unit %s could not load." % voice)
                    return
                if not self._plugin_catalog.is_added(voice):
                    return
                try:
                    self.engine.attachNode_(unit)
                    self.engine.connect_to_fromBus_toBus_format_(
                        unit,
                        self._destination,
                        0,
                        self._destination.nextAvailableInputBus(),
                        None,
                    )
                except Exception as exc:
                    print("Audio Unit %s could not connect (%s)." %
                          (voice, exc))
                    return
                if not self._plugin_catalog.is_added(voice):
                    try:
                        self.engine.detachNode_(unit)
                    except Exception:
                        pass
                    return
                self._plugin_units[voice] = unit
                self._samplers[id(unit)] = unit
                queued = self._plugin_queue.pop(voice, [])
            for kind, midi, dur, vel in queued:
                if kind == "live":
                    self.play_live(voice, midi, vel)
                else:
                    self.play(voice, midi, dur, vel)

        try:
            av.AVAudioUnit.instantiateWithComponentDescription_options_completionHandler_(
                description,
                0,
                completed,
            )
        except Exception as exc:
            with self._lock:
                self._plugin_pending.discard(voice)
            print("Audio Unit %s could not start (%s)." % (voice, exc))
            return False
        return False

    def _queue_plugin_event(self, voice, kind, midi, dur=0.0, vel=0.5):
        with self._lock:
            events = self._plugin_queue.setdefault(voice, [])
            if kind == "live":
                events[:] = [event for event in events
                             if not (event[0] == "live" and event[1] == midi)]
            events.append((kind, int(midi), float(dur), float(vel)))

    # ------------------------------------------------------------------
    def play(self, voice, midi, dur, vel=0.4, live=False):
        sampler = self._voices.get(voice)
        if sampler is None:
            if self._plugin_catalog.description(voice) is None:
                return
            if not self.ensure_instrument(voice):
                self._queue_plugin_event(
                    voice,
                    "live" if live else "timed",
                    midi,
                    dur,
                    vel,
                )
                return
            sampler = self._plugin_units.get(voice)
            if sampler is None:
                return
        if live:
            self.play_live(voice, midi, vel)
            return
        self._play(sampler, voice, midi, dur, vel)

    def stop_timed(self, voice=None):
        """Stop all scheduled notes, optionally for one instrument voice."""
        with self._lock:
            target = self._voices.get(voice) if voice is not None else None
            if target is None and voice is not None:
                target = self._plugin_units.get(voice)
            if voice is not None and target is None:
                return
            target_id = id(target) if target is not None else None
            for key in list(self._tokens):
                sid, channel, midi = key
                if target_id is not None and sid != target_id:
                    continue
                sampler = (target if target is not None
                           else self._samplers.get(sid))
                self._tokens.pop(key, None)
                if sampler is not None:
                    self._stop(sampler, midi, channel)

    def play_live(self, voice, midi, vel=0.5):
        sampler = self._voices.get(voice)
        if sampler is None:
            if self._plugin_catalog.description(voice) is None:
                return
            if not self.ensure_instrument(voice):
                self._queue_plugin_event(voice, "live", midi, 0.0, vel)
                return
            sampler = self._plugin_units.get(voice)
            if sampler is None:
                return
        with self._lock:
            if midi in self._live or int(midi) in self._live_tails:
                self.panic_live(midi)
            self._start(sampler, midi, vel, LIVE_CH)
            self._live[midi] = (voice, sampler)
            self._seq += 1
            self._tokens[(id(sampler), LIVE_CH, int(midi))] = self._seq

    def stop_live(self, midi, fade_ms=150):
        """Release a played note; the sustain knob decides how long it rings."""
        self._stop_live_with_tail(midi, self._sustain_tail())

    def release_live(self, midi):
        """Release a note with the voice's natural decay (sustain pedal up)."""
        with self._lock:
            item = self._live.get(midi)
            voice = item[0] if item is not None else None
        self._stop_live_with_tail(midi, self._release_tail(voice))

    def panic_live(self, midi):
        """Stop a note right away, ignoring the sustain knob."""
        self._stop_live_with_tail(midi, 0.0)

    def _stop_live_with_tail(self, midi, tail):
        with self._lock:
            for events in self._plugin_queue.values():
                events[:] = [event for event in events
                             if not (event[0] == "live" and event[1] == midi)]
            pending = self._live_tails.pop(int(midi), None)
            item = self._live.pop(midi, None)
            if pending is not None:
                self._tokens.pop(pending[1], None)
            if item is None and pending is None:
                return
            if item is not None:
                voice, sampler = item
                key = (id(sampler), LIVE_CH, int(midi))
            else:
                sampler, key, _token = pending
            if tail <= 0.02:
                self._tokens.pop(key, None)
                self._stop(sampler, midi, LIVE_CH)
                return
            self._seq += 1
            token = self._seq
            self._tokens[key] = token
            self._live_tails[int(midi)] = (sampler, key, token)
        self._later(tail, self._timed_stop, sampler, midi, key, token, LIVE_CH)

    def play_drum(self, name, vel=1.0, open_=False):
        note = DRUM_NOTES.get("openhat" if open_ else name)
        if note is None:
            return
        with self._lock:
            self._start(self._drums, note, vel, DRUM_CH)

    # ------------------------------------------------------------------
    def set_master(self, value):
        value = max(0.0, min(1.0, float(value)))
        self.master = value
        try:
            self._mixer.setOutputVolume_(value)
        except Exception:
            pass

    def set_reverb(self, value):
        value = max(0.0, min(100.0, float(value)))
        self.reverb = value
        if self._reverb is not None:
            try:
                self._reverb.setWetDryMix_(value * 0.8)
            except Exception:
                pass

    def set_chorus(self, value):
        value = max(0.0, min(100.0, float(value)))
        self.chorus = value
        if self._chorus is not None:
            try:
                self._chorus.setWetDryMix_(value * 0.6)
            except Exception:
                pass

    def set_sustain(self, value):
        """Set the voice sustain level, 0-100 percent."""
        self.sustain = max(0.0, min(1.0, float(value) / 100.0))

    def _sustain_tail(self):
        """Seconds a released key keeps ringing; 0 means no sustain at all."""
        return self.sustain * MAX_SUSTAIN

    def _release_tail(self, voice=None):
        """Natural release of a voice, without the sustain knob."""
        return RELEASE.get(voice, 0.1)

    def fade_voice(self, voice, delay, duration,
                   start=1.0, end=0.0):
        with self._lock:
            sampler = self._voices.get(voice)
            if sampler is None:
                sampler = self._plugin_units.get(voice)
            if sampler is None:
                return
            self._fade_seq += 1
            token = self._fade_seq
            self._fade_tokens[voice] = token
            try:
                sampler.setVolume_(max(0.0, min(1.0, float(start))))
            except Exception:
                return
        steps = max(2, int(max(0.01, duration) * 30))
        self._later(max(0.0, delay), self._fade_step,
                    voice, token, 0, steps, start, end,
                    max(0.01, duration))

    def _fade_step(self, voice, token, i, steps, start, end, duration):
        with self._lock:
            if self._fade_tokens.get(voice) != token:
                return
            sampler = self._voices.get(voice)
            if sampler is None:
                sampler = self._plugin_units.get(voice)
            if sampler is None:
                return
            value = start + (end - start) * (i / float(steps))
            try:
                sampler.setVolume_(max(0.0, min(1.0, value)))
            except Exception:
                return
        if i < steps:
            self._later(duration / steps, self._fade_step,
                        voice, token, i + 1, steps, start, end, duration)

    def all_notes_off(self):
        with self._lock:
            live = list(self._live.items())
            self._live.clear()
            for events in self._plugin_queue.values():
                events.clear()
            for midi, (_, sampler) in live:
                self._stop(sampler, midi, LIVE_CH)
            samplers = (list(self._voices.values())
                        + list(self._plugin_units.values())
                        + [self._drums])
            for sampler in samplers:
                for ch in (LIVE_CH, TIMED_CH, DRUM_CH):
                    try:
                        sampler.sendController_withValue_onChannel_(123, 0, ch)
                    except Exception:
                        pass

    def shutdown(self):
        with self._lock:
            self._closed = True
            if self._fx_loop_thread is not None:
                self._fx_loop_thread.join(timeout=0.2)
            for timer in list(self._timers):
                timer.cancel()
            self._timers.clear()
            self.all_notes_off()
            try:
                self.engine.stop()
            except Exception:
                pass
