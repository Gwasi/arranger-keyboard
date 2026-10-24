"""Pygame interface: LCD, controls, digital readouts and the F1-B5 keybed."""

import json
import math
import os
import sys
import time
import uuid
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame

from core import (APP_H, APP_W, BLACK_PCS, COL_ACCENT, COL_EDGE, COL_LCD_TX,
                  COL_MUTED, COL_ORANGE, COL_PANEL_BOT, COL_PANEL_TOP,
                  COL_TX, KEY_HIGH, KEY_LOW, KEYMAP, NOTE_NAMES, SPLIT,
                  STYLES, load_user_styles, save_user_styles)
from engine import Engine
from midi_input import MidiInput
from synth import Synth as SynthFallback

try:
    from soundbank import BankSynth
except Exception:
    BankSynth = None

SONG_FILE = (Path.home() / "Library" / "Application Support"
             / "Arranger Keyboard" / "songs.json")
SONG_ROWS = 12
SONG_ROW_H = 40
SONG_ROW_GAP = 4
SIDEBAR_X = 1000
SIDEBAR_W = 224
SIDEBAR_Y = 16
SIDEBAR_H = 724
KEY_X = 40
KEY_W = 940
KEY_W_HIDDEN = APP_W - 36 - KEY_X
KEY_Y = 326

def note_label(midi):
    return NOTE_NAMES[midi % 12] + str(midi // 12 - 1)


BTN_COLORS = {
    "normal": ("#3a404c", "#2f343e", "#4a5160", "#e7ecf3"),
    "hover": ("#454c5a", "#373d49", "#5b6474", "#ffffff"),
    "press": ("#2b303a", "#252932", "#4a5160", "#cfd6e0"),
    "on": ("#17617f", "#10465f", "#3fb6ff", "#ffffff"),
    "on_hover": ("#1c7093", "#12506f", "#3fb6ff", "#ffffff"),
    "on_press": ("#12506f", "#0d3a50", "#3fb6ff", "#e6f7ff"),
}


def rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    ca, cb = rgb(a), rgb(b)
    return tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3))


def grad_surface(w, h, top, bottom):
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        pygame.draw.line(surf, (*mix(top, bottom, y / max(1, h - 1)), 255),
                         (0, y), (w - 1, y))
    return surf


def rounded_surface(w, h, r, top, bottom, border=None, bw=1, square_top=False):
    surf = grad_surface(w, h, top, bottom)
    mask = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, h), border_radius=r)
    if square_top:
        pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, w, min(r, 8)))
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    if border:
        pygame.draw.rect(surf, border, (0, 0, w, h), bw, border_radius=r)
        if square_top:
            pygame.draw.line(surf, border, (1, 0), (w - 2, 0))
    return surf


class Button:
    def __init__(self, app, rect, text, command, on=False, bold=False, size=13,
                 radius=7):
        self.app = app
        self.rect = pygame.Rect(rect)
        self.text = text
        self.command = command
        self.on = on
        self.bold = bold
        self.size = size
        self.radius = radius
        self.hover = False
        self.pressed = False

    def state(self):
        if self.on:
            return ("on_press" if self.pressed else
                    "on_hover" if self.hover else "on")
        return ("press" if self.pressed else
                "hover" if self.hover else "normal")

    def draw(self, surf):
        state = self.state()
        key = ("button", self.rect.w, self.rect.h, state, self.radius)
        img = self.app.image_cache.get(key)
        if img is None:
            top, bot, border, _ = BTN_COLORS[state]
            img = rounded_surface(self.rect.w, self.rect.h, self.radius,
                                  top, bot, border)
            self.app.image_cache[key] = img
        surf.blit(img, self.rect.topleft)
        color = BTN_COLORS[state][3]
        self.app.text(self.text, self.size, color, self.bold,
                      center=self.rect.center)

    def set_on(self, value):
        self.on = bool(value)

    def set_text(self, text):
        self.text = text


class NumericField:
    """Holds one editable numeric setting and its display formatter."""

    def __init__(self, label, lo, hi, value, fmt=None):
        self.label = label
        self.lo, self.hi = lo, hi
        self.value = value
        self.fmt = fmt or (lambda v: str(int(round(v))))

    def set_value(self, value):
        value = int(round(value))
        self.value = max(self.lo, min(self.hi, value))


class Knob:
    """A rotary control driven by vertical dragging or the mouse wheel."""

    def __init__(self, app, center, label, lo, hi, value, command,
                 fmt=None):
        self.app = app
        self.cx, self.cy = center
        self.label = label
        self.lo, self.hi = lo, hi
        self.value = int(round(value))
        self.command = command
        self.fmt = fmt or (lambda v: str(int(v)))
        self.rect = pygame.Rect(self.cx - 30, self.cy - 20, 60, 58)
        self.hover = False
        self.dragging = False
        self.start_y = 0
        self.start_value = self.value

    def set_value(self, value, notify=False):
        value = max(self.lo, min(self.hi, int(round(value))))
        changed = value != self.value
        self.value = value
        if notify and changed:
            self.command(value)
        return changed

    def begin_drag(self, pos):
        self.dragging = True
        self.start_y = pos[1]
        self.start_value = self.value

    def drag_to(self, pos):
        span = self.hi - self.lo
        delta = (self.start_y - pos[1]) / 170.0 * span
        if pygame.key.get_mods() & pygame.KMOD_SHIFT:
            delta *= 0.25
        self.set_value(self.start_value + delta, notify=True)

    def nudge(self, direction):
        step = max(1, int(round((self.hi - self.lo) / 40.0)))
        self.set_value(self.value + direction * step, notify=True)

    def draw(self, sc):
        t = (self.value - self.lo) / float(self.hi - self.lo)
        active = self.hover or self.dragging
        cx, cy = self.cx, self.cy
        ticks = 13
        for i in range(ticks):
            f = i / float(ticks - 1)
            ang = math.radians(135.0 + 270.0 * f)
            inner = 20.0
            outer = 24.0 if f <= t + 0.001 else 22.0
            x1 = cx + math.cos(ang) * inner
            y1 = cy + math.sin(ang) * inner
            x2 = cx + math.cos(ang) * outer
            y2 = cy + math.sin(ang) * outer
            color = ("#7fe4ff" if f <= t + 0.001 else "#2b3a48")
            if active and f > t:
                color = "#3b4d5f"
            pygame.draw.line(sc, color, (x1, y1), (x2, y2), 2)

        pygame.draw.circle(sc, "#070b10", (cx, cy), 19)
        pygame.draw.circle(sc, "#5b6678" if active else "#39424f",
                           (cx, cy), 18, 1)
        pygame.draw.circle(sc, "#2d3542", (cx, cy), 16)
        pygame.draw.circle(sc, "#1b222c", (cx, cy), 14)
        pygame.draw.circle(sc, "#343d4b", (cx, cy), 10, 1)

        ang = math.radians(135.0 + 270.0 * t)
        x1 = cx + math.cos(ang) * 4
        y1 = cy + math.sin(ang) * 4
        x2 = cx + math.cos(ang) * 14
        y2 = cy + math.sin(ang) * 14
        pygame.draw.line(sc, "#8be9ff", (x1, y1), (x2, y2), 3)
        pygame.draw.circle(sc, "#dff9ff", (int(x2), int(y2)), 2)

        label_color = "#8fb8ca" if active else "#5f8fa5"
        value_color = "#8be9ff" if active else "#6fc9e6"
        self.app.text(self.label, 8, label_color, True, (cx, cy + 22),
                      center=True)
        self.app.text(self.fmt(self.value), 10, value_color, True,
                      (cx, cy + 34), center=True)


class App:
    def __init__(self):
        self.synth = None
        if BankSynth is not None:
            try:
                self.synth = BankSynth(0.6)
            except Exception as exc:
                print("Sound bank unavailable (%s); using synthesis." % exc)
        if self.synth is None:
            self.synth = SynthFallback(0.6)
        self.engine = Engine(self.synth)
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((APP_W, APP_H))
        pygame.display.set_caption("Arranger Keyboard")
        self.clock = pygame.time.Clock()
        self.image_cache = {}
        self.text_cache = {}
        self.font_cache = {}
        self.pressed = set()
        self.kb_keys = set()
        self.mouse_note = None
        self.active_button = None
        self.active_knob = None
        self.param_drag = None
        self.edit_param = None
        self.edit_buffer = ""
        self.edit_select_all = False
        self.list_overlay = None
        self.list_hits = []
        self.list_scroll = 0
        self.list_rows = 0
        self.list_visible_rows = 0
        self.plugin_manager_open = False
        self.plugin_search = ""
        self.plugin_search_focus = False
        self.plugin_scroll = 0
        self.plugin_hits = []
        self.plugin_editor_open = False
        self.plugin_editor_key = None
        self.plugin_editor_label = ""
        self.plugin_editor_params = []
        self.plugin_editor_rows = []
        self.plugin_editor_scroll = 0
        self.plugin_editor_drag = None
        self.name_hits = {}
        self.running = True
        self.state = self.engine.state()
        self.view = {}
        self.last_ui = 0.0
        self.sustain = False
        self.sustained = set()
        self.harmony = False
        self.song_path = SONG_FILE
        self.sidebar_open = True
        self.key_w = KEY_W
        self.songs = self._read_songs()
        self.song_scroll = 0
        self.song_active_at = None
        self.song_msg = ""
        self.song_msg_until = 0
        self.dialog = None
        self.style_editor_open = False
        self.style_editor_data = None
        self.style_editor_section = "main_a"
        self.style_editor_instrument = "kick"
        self.style_editor_instrument_tab = "drums"
        self.style_editor_load_mode = False
        self.song_sidebar = pygame.Rect(SIDEBAR_X, SIDEBAR_Y,
                                        SIDEBAR_W, SIDEBAR_H)
        self.song_panel_img = rounded_surface(
            SIDEBAR_W, SIDEBAR_H, 10, "#111b24", "#080d13", "#2b4150")
        self.harmony_toggle = pygame.Rect(598, 106, 64, 18)
        self.midi_notes = set()
        self.midi = MidiInput(self._midi_note_on, self._midi_note_off,
                              self._midi_sustain, self._midi_all_off)

        self.bg = grad_surface(APP_W, APP_H, "#1a1e27", "#0c0e12")
        self.scrim = pygame.Surface((APP_W, APP_H), pygame.SRCALPHA)
        self.scrim.fill((0, 0, 0, 175))
        self.panel = rounded_surface(1208, 724, 14,
                                     COL_PANEL_TOP, COL_PANEL_BOT, COL_EDGE)
        pygame.draw.line(self.panel, "#3d434f", (16, 2), (1191, 2))
        self.lcd_img = self._lcd_surface()
        self._build_controls()
        self._build_keys()
        self._update_ui()

    # ------------------------------------------------------------------
    def _lcd_surface(self):
        w, h = 640, 232
        surf = rounded_surface(w, h, 8, "#0e2431", "#07141b", "#000000", 2)
        lines = pygame.Surface((w, h), pygame.SRCALPHA)
        for y in range(4, h - 4, 4):
            pygame.draw.line(lines, (30, 62, 78, 90), (4, y), (w - 5, y))
        surf.blit(lines, (0, 0))
        return surf

    def _build_controls(self):
        x0 = 700
        self.buttons = []
        self.param_fields = []
        self.param_hitboxes = {}
        for key, x, label in (("tempo", 58, "TEMPO"),
                              ("volume", 222, "VOLUME"),
                              ("transpose", 386, "TRANSPOSE")):
            label_w = self.text(label, 9, "#5f8fa5", True).get_width()
            value_x = x + label_w + 9
            self.param_fields.append((key, x, value_x, label))
            self.param_hitboxes[key] = pygame.Rect(
                x - 8, 206, value_x + 76 - (x - 8), 34)
        prefix = "CHORD CONTROL :"
        prefix_x = 58
        prefix_w = self.text(prefix, 9, "#5f8fa5", True).get_width()
        two_x = prefix_x + prefix_w + 8
        two_w = self.text("TWO-KEY", 9, "#ffffff", True).get_width()
        sep_x = two_x + two_w + 8
        sep_w = self.text("|", 9, "#5f8fa5", True).get_width()
        single_x = sep_x + sep_w + 8
        single_w = self.text("SINGLE FINGER", 9, "#ffffff", True).get_width()
        self.mode_line = (prefix_x, two_x, sep_x, single_x)
        self.mode_hits = {
            "f": pygame.Rect(two_x - 4, 254, two_w + 8, 16),
            "s": pygame.Rect(single_x - 4, 254, single_w + 8, 16),
        }
        self.btn_acmp = Button(self, (x0, 144, 88, 48), "ACMP",
                               self._acmp, on=True, bold=True, size=11)
        self.btn_sync = Button(self, (794, 144, 88, 48), "SYNC START",
                               self._sync, bold=True, size=11)
        self.btn_start = Button(self, (888, 144, 88, 48), "START",
                                self._start, bold=True, size=11)
        self.btn_intro = Button(self, (x0, 218, 54, 48), "INTRO",
                                self._intro, bold=True, size=9)
        self.btn_a = Button(self, (759, 218, 54, 48), "MAIN A",
                            lambda: self._main("A"), on=True,
                            bold=True, size=9)
        self.btn_b = Button(self, (818, 218, 54, 48), "MAIN B",
                            lambda: self._main("B"), bold=True, size=9)
        self.btn_fill = Button(self, (877, 218, 54, 48), "FILL",
                               self._fill, bold=True, size=9)
        self.btn_ending = Button(self, (936, 218, 54, 48), "ENDING",
                                 self._ending, bold=True, size=9)
        self.btn_save_song = Button(self, (1014, 72, 196, 38),
                                    "SAVE CURRENT SONG",
                                    self._open_save_dialog,
                                    bold=True, size=11)
        plugin_btn_w = 142
        plugins_x = (APP_W - plugin_btn_w) // 2
        self.btn_plugins = Button(self, (plugins_x, 28,
                                         plugin_btn_w, 28),
                                  "PLUGIN MANAGER",
                                  self._open_plugin_manager, bold=True, size=9)
        self.btn_style_editor = Button(self, (plugins_x + plugin_btn_w + 16, 28, 140, 28),
                                        "STYLE EDITOR",
                                        self._open_style_editor, bold=True, size=9)
        self.buttons += [self.btn_acmp, self.btn_sync, self.btn_start,
                         self.btn_intro, self.btn_a, self.btn_b,
                         self.btn_fill, self.btn_ending, self.btn_save_song,
                         self.btn_plugins, self.btn_style_editor]

        self.knobs = [
            Knob(self, (740, 80), "REVERB", 0, 100, 20,
                 self._reverb, fmt=lambda v: "%d%%" % v),
            Knob(self, (848, 80), "CHORUS", 0, 100, 18,
                 self._chorus, fmt=lambda v: "%d%%" % v),
            Knob(self, (956, 80), "SUSTAIN", 0, 100, 0,
                 self._sustain, fmt=lambda v: "%d%%" % v),
        ]
        self.reverb_knob, self.chorus_knob = self.knobs[0], self.knobs[1]
        self.sustain_knob = self.knobs[2]
        self.synth.set_reverb(self.reverb_knob.value)
        self.synth.set_chorus(self.chorus_knob.value)
        self.synth.set_sustain(self.sustain_knob.value)

        self.tempo_ctl = NumericField("TEMPO", 50, 200, 110)
        self.volume_ctl = NumericField("VOLUME", 0, 100, 60,
                                       fmt=lambda v: "%d%%" % v)
        self.transpose_ctl = NumericField("TRANSPOSE", -12, 12, 0,
                                          fmt=lambda v: "%+d" % v)

    # ------------------------------------------------------------------
    def key_surface(self, midi, black, on, w, h, label=None, split=False):
        key = (midi, on)
        surf = self.image_cache.get(key)
        if surf is not None:
            return surf
        if black:
            top, bot, border = (("#2d9fd1", "#0d5a84", "#0a4566") if on
                                else ("#3b3b3b", "#050505", "#101010"))
        else:
            top, bot, border = (("#a6e4ff", "#2fa8ee", "#1a7fb5") if on
                                else ("#fcfcfc", "#d8d8d8", "#8a8a8a"))
        surf = rounded_surface(w, h, 5, top, bot, border, 1, square_top=True)
        if black:
            pygame.draw.line(surf, "#707070", (2, 1), (w - 3, 1))
        if split:
            pygame.draw.rect(surf, COL_ORANGE, (2, 2, 3, h - 4))
        if label:
            color = "#ffb347" if split else ("#0b3a52" if on else "#8b93a0")
            self.text(label, 9, color, split, (w // 2, h - 14), center=True,
                      target=surf)
        self.image_cache[key] = surf
        return surf

    def _build_keys(self):
        white_count = sum(
            1 for m in range(KEY_LOW, KEY_HIGH) if m % 12 not in BLACK_PCS)
        self.ww = self.key_w / float(white_count)
        self.bw = 18
        self.bh = int(224 * 0.62)
        self.white_notes = []
        self.black_notes = []
        wi = 0
        for m in range(KEY_LOW, KEY_HIGH):
            if m % 12 in BLACK_PCS:
                self.black_notes.append((m, wi))
            else:
                self.white_notes.append((m, wi))
                wi += 1
        self.key_iw = int(round(self.ww)) - 1
        self.split_slot = sum(
            1 for m in range(KEY_LOW, SPLIT) if m % 12 not in BLACK_PCS)
        self.key_surfs = {}
        for m, slot in self.white_notes:
            label = None
            split = (m == SPLIT)
            if m == KEY_LOW or m % 12 == 0:
                label = note_label(m)
            self.key_surfs[m] = (
                self.key_surface(m, False, False, self.key_iw, 224, label, split),
                self.key_surface(m, False, True, self.key_iw, 224, label, split))
        for m, _ in self.black_notes:
            self.key_surfs[m] = (
                self.key_surface(m, True, False, self.bw, self.bh),
                self.key_surface(m, True, True, self.bw, self.bh))

    def hit_key(self, pos):
        px, py = pos
        x, y = KEY_X, KEY_Y
        if not (x <= px < x + self.key_w and y <= py < y + 224):
            return None
        if py < y + self.bh:
            for m, wi in self.black_notes:
                left = x + wi * self.ww - self.bw / 2.0
                if left <= px <= left + self.bw:
                    return m
        slot = int((px - x) / self.ww)
        if 0 <= slot < len(self.white_notes):
            for m, s in self.white_notes:
                if s == slot:
                    return m
        return None

    # ------------------------------------------------------------------
    def text(self, s, size, color, bold=False, pos=None, center=False,
             right=False, target=None, mono=False):
        key = (s, size, color, bold, mono)
        surf = self.text_cache.get(key)
        if surf is None:
            fkey = (size, bold, mono)
            font = self.font_cache.get(fkey)
            if font is None:
                font = pygame.font.SysFont("Menlo" if mono else "Arial",
                                           size, bold=bold)
                self.font_cache[fkey] = font
            surf = font.render(s, True, color)
            self.text_cache[key] = surf
        if target is None:
            target = self.screen
        if pos is None and center not in (False, None, True):
            pos, center = center, True
        if pos is not None:
            r = surf.get_rect()
            if center:
                r.center = pos
            elif right:
                r.midright = pos
            else:
                r.topleft = pos
            target.blit(surf, r)
        return surf

    # ------------------------------------------------------------------
    def _read_songs(self):
        try:
            raw = json.loads(self.song_path.read_text(encoding="utf-8"))
            songs = raw.get("songs", []) if isinstance(raw, dict) else raw
            if isinstance(songs, list):
                return [s for s in songs if isinstance(s, dict)]
        except FileNotFoundError:
            pass
        except Exception as exc:
            print("Song library unavailable (%s)" % exc)
        return []

    def _write_songs(self, songs):
        self.song_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.song_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "songs": songs},
                                  indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, self.song_path)

    @staticmethod
    def _song_key(song):
        return song.get("id") or song.get("saved_at") or song.get("name")

    def _song_snapshot(self):
        s = self.engine.state()
        now = time.time()
        return {
            "version": 1,
            "id": uuid.uuid4().hex,
            "name": "",
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                      time.localtime(now)),
            "style_name": s["style_name"],
            "style_index": s["style"],
            "voice": s["voice"],
            "tempo": s["tempo"],
            "transpose": s["transpose"],
            "mode": s["mode"],
            "chord": list(s["chord"]) if s["chord"] else None,
            "sustain": self.sustain,
            "harmony": s["harmony"],
            "reverb": self.reverb_knob.value,
            "chorus": self.chorus_knob.value,
            "sustain_level": self.sustain_knob.value,
            "volume": self.volume_ctl.value,
            "balance": s["balance"],
            "acmp": s["acmp"],
            "sync": s["sync"],
            "main": s["main"],
        }

    def _song_notice(self, text):
        self.song_msg = text
        self.song_msg_until = pygame.time.get_ticks() + 2500

    def _name_used(self, name, ignore=None):
        lower = name.casefold()
        for i, song in enumerate(self.songs):
            if i == ignore:
                continue
            if (song.get("name") or "").strip().casefold() == lower:
                return True
        return False

    def _open_save_dialog(self):
        self._commit_edit()
        self._close_list()
        self.dialog = {"kind": "save", "index": None, "text": "",
                       "select_all": False, "error": ""}

    def _open_rename_dialog(self, index):
        if not 0 <= index < len(self.songs):
            return
        song = self.songs[index]
        name = song.get("name") or song.get("style_name") or "Song"
        self.dialog = {"kind": "rename", "index": index, "text": name,
                       "select_all": True, "error": ""}

    def _open_delete_dialog(self, index):
        if not 0 <= index < len(self.songs):
            return
        self.dialog = {"kind": "delete", "index": index, "text": "",
                       "select_all": False, "error": ""}

    def _close_dialog(self):
        self.dialog = None

    def _confirm_dialog(self):
        d = self.dialog
        if d is None:
            return
        kind = d["kind"]
        index = d.get("index")
        if kind == "style_rename":
            name = d.get("text","").strip()
            if not name:
                d["error"] = "ENTER A NAME"
                return
            if self.style_editor_data is not None:
                self.style_editor_data["name"] = name
                self._update_ui()
            self._close_dialog()
            return
        if kind == "delete":
            if not 0 <= index < len(self.songs):
                self._close_dialog()
                return
            song = self.songs[index]
            songs = self.songs[:index] + self.songs[index + 1:]
            try:
                self._write_songs(songs)
            except Exception as exc:
                print("Could not delete song (%s)" % exc)
                d["error"] = "COULD NOT SAVE THE LIBRARY"
                return
            self.songs = songs
            self.song_scroll = min(
                self.song_scroll, max(0, len(songs) - SONG_ROWS))
            if self.song_active_at == self._song_key(song):
                self.song_active_at = None
            self._close_dialog()
            self._song_notice("DELETED")
            return

        name = d["text"].strip()
        if not name:
            d["error"] = "ENTER A NAME BEFORE SAVING"
            d["select_all"] = False
            return
        if self._name_used(name, None if kind == "save" else index):
            d["error"] = "THAT NAME IS ALREADY USED"
            d["select_all"] = True
            return
        if kind == "save":
            song = self._song_snapshot()
            song["name"] = name
            songs = [song] + self.songs
            try:
                self._write_songs(songs)
            except Exception as exc:
                print("Could not save song (%s)" % exc)
                d["error"] = "COULD NOT SAVE THE LIBRARY"
                return
            self.songs = songs
            self.song_scroll = 0
            self.song_active_at = self._song_key(song)
            self._close_dialog()
            self._song_notice("SAVED")
        else:
            if not 0 <= index < len(self.songs):
                self._close_dialog()
                return
            songs = [dict(song) for song in self.songs]
            songs[index]["name"] = name
            try:
                self._write_songs(songs)
            except Exception as exc:
                print("Could not rename song (%s)" % exc)
                d["error"] = "COULD NOT SAVE THE LIBRARY"
                return
            self.songs = songs
            self._close_dialog()
            self._song_notice("RENAMED")

    def _dialog_key(self, e):
        d = self.dialog
        if d is None:
            return
        if e.key == pygame.K_ESCAPE:
            self._close_dialog()
            return
        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._confirm_dialog()
            return
        if d["kind"] == "delete":
            return
        if e.key == pygame.K_BACKSPACE:
            if d["select_all"]:
                d["text"] = ""
                d["select_all"] = False
            else:
                d["text"] = d["text"][:-1]
            d["error"] = ""
            return
        if e.key == pygame.K_DELETE:
            d["text"] = ""
            d["select_all"] = False
            d["error"] = ""
            return
        ch = e.unicode
        if not ch or not ch.isprintable():
            return
        if d["select_all"]:
            d["text"] = ""
            d["select_all"] = False
        if len(d["text"]) < 40:
            d["text"] += ch
        d["error"] = ""

    def _dialog_rects(self):
        d = self.dialog
        if d is None:
            return None
        if d["kind"] == "delete":
            box = pygame.Rect((APP_W - 440) // 2, (APP_H - 206) // 2,
                              440, 206)
            return {
                "box": box,
                "input": None,
                "cancel": pygame.Rect(box.right - 268, box.y + 142,
                                      110, 36),
                "confirm": pygame.Rect(box.right - 150, box.y + 142,
                                       150, 36),
            }
        box = pygame.Rect((APP_W - 460) // 2, (APP_H - 232) // 2,
                          460, 232)
        return {
            "box": box,
            "input": pygame.Rect(box.x + 28, box.y + 83,
                                 box.w - 76, 40),
            "cancel": pygame.Rect(box.right - 268, box.y + 173, 110, 36),
            "confirm": pygame.Rect(box.right - 150, box.y + 173, 150, 36),
        }

    def _dialog_mouse_down(self, pos):
        d = self.dialog
        if d is None:
            return
        r = self._dialog_rects()
        if r["cancel"].collidepoint(pos):
            self._close_dialog()
            return
        if r["confirm"].collidepoint(pos):
            self._confirm_dialog()
            return
        if r["input"] is not None and r["input"].collidepoint(pos):
            d["select_all"] = False

    def _draw_modal_button(self, sc, rect, label, kind="normal",
                           enabled=True):
        mouse = pygame.mouse.get_pos()
        hover = enabled and rect.collidepoint(mouse)
        if not enabled:
            top, bot, border, ink = ("#202630", "#1a1f27",
                                     "#343c47", "#56606e")
        elif kind == "primary":
            top, bot, border, ink = (("#1c7093", "#12506f", "#3fb6ff",
                                      "#ffffff") if hover else
                                     ("#17617f", "#10465f", "#3fb6ff",
                                      "#ffffff"))
        elif kind == "danger":
            top, bot, border, ink = (("#8b2f2f", "#5c1b1b", "#ff7b72",
                                      "#ffffff") if hover else
                                     ("#6f2424", "#451515", "#b84a44",
                                      "#ffe7e5"))
        else:
            top, bot, border, ink = (("#454c5a", "#373d49", "#5b6474",
                                      "#ffffff") if hover else
                                     ("#3a404c", "#2f343e", "#4a5160",
                                      "#e7ecf3"))
        img = rounded_surface(rect.w, rect.h, 7, top, bot, border)
        sc.blit(img, rect.topleft)
        self.text(label, 11, ink, True, rect.center, center=True)

    def _draw_dialog(self, sc):
        d = self.dialog
        if d is None:
            return
        sc.blit(self.scrim, (0, 0))
        r = self._dialog_rects()
        box = r["box"]
        key = ("dialog", box.w, box.h)
        img = self.image_cache.get(key)
        if img is None:
            img = rounded_surface(box.w, box.h, 12,
                                  "#1a2631", "#0c1218", "#3c5566")
            self.image_cache[key] = img
        sc.blit(img, box.topleft)

        kind = d["kind"]
        title = {"save": "SAVE SONG", "rename": "RENAME SONG",
                 "delete": "DELETE SONG", "style_rename": "RENAME STYLE"}[kind]
        self.text(title, 16, "#dff9ff", True, (box.x + 28, box.y + 18))
        if kind == "save":
            self.text("Give this setup a name before saving.",
                      9, "#8b95a6", False, (box.x + 28, box.y + 44))
        elif kind == "rename":
            self.text("Choose a new name for this setup.",
                      9, "#8b95a6", False, (box.x + 28, box.y + 44))
        elif kind == "style_rename":
            self.text("Choose a new name for this style.",
                      9, "#8b95a6", False, (box.x + 28, box.y + 44))

        if kind in ("save", "rename", "style_rename"):
            label = "STYLE NAME" if kind == "style_rename" else "SONG NAME"
            self.text(label, 8, "#5f8fa5", True,
                      (box.x + 28, box.y + 69))
            field = r["input"]
            error = bool(d.get("error",""))
            fill = "#071820"
            edge = "#ff7b72" if error else "#3fb6ff"
            pygame.draw.rect(sc, fill, field, border_radius=5)
            pygame.draw.rect(sc, edge, field, 1, border_radius=5)
            text = d["text"]
            if text:
                surf = self.text(text, 14, "#dff9ff", False,
                                 (field.x + 12, field.y + 11))
                cx = field.x + 12 + surf.get_width() + 2
            else:
                self.text("Type a song name", 14, "#4f7385", False,
                          (field.x + 12, field.y + 11))
                cx = field.x + 14
            if (pygame.time.get_ticks() // 450) % 2 == 0:
                pygame.draw.line(sc, "#8be9ff",
                                 (cx, field.y + 10),
                                 (cx, field.y + 30), 2)
            confirm = r["confirm"]
            enabled = bool(d["text"].strip())
            self._draw_modal_button(sc, r["cancel"], "CANCEL")
            self._draw_modal_button(
                sc, confirm, "SAVE" if kind == "save" else "RENAME",
                "primary", enabled)
        else:
            index = d.get("index")
            name = "this song"
            if 0 <= index < len(self.songs):
                name = self.songs[index].get("name") or "this song"
            shown = self._clip_text(name, 14, box.w - 56, True)
            self.text(shown, 14, "#ffffff", True,
                      (box.x + 28, box.y + 58))
            self.text("This song will be removed permanently.",
                      9, "#9aa4b3", False, (box.x + 28, box.y + 86))
            self._draw_modal_button(sc, r["cancel"], "CANCEL")
            self._draw_modal_button(sc, r["confirm"], "DELETE",
                                    "danger", True)

        if d["error"]:
            self.text(d["error"], 9, "#ff7b72", True,
                      (box.x + 28, box.y + 129))

    def _load_song(self, song):
        self._commit_edit()
        self._close_list()
        self._midi_all_off()
        self.engine.restore(song)
        self.sustain = bool(song.get("sustain", False))
        self.harmony = bool(song.get("harmony", False))
        try:
            volume = max(0, min(100, int(song.get("volume", 60))))
        except (TypeError, ValueError):
            volume = 60
        self.volume_ctl.set_value(volume)
        self._volume(volume)
        try:
            self.reverb_knob.set_value(int(song.get("reverb", 20)),
                                       notify=True)
            self.chorus_knob.set_value(int(song.get("chorus", 18)),
                                       notify=True)
            self.sustain_knob.set_value(int(song.get("sustain_level", 0)),
                                        notify=True)
        except (TypeError, ValueError):
            pass
        self.song_active_at = self._song_key(song)
        self._song_notice("LOADED")
        self._update_ui()

    def _sidebar_toggle_rect(self):
        if self.sidebar_open:
            return pygame.Rect(SIDEBAR_X + SIDEBAR_W - 66,
                               SIDEBAR_Y + 12, 52, 22)
        return pygame.Rect(APP_W - 126, 54, 104, 26)

    def _toggle_sidebar(self):
        self.sidebar_open = not self.sidebar_open
        self.key_w = KEY_W if self.sidebar_open else KEY_W_HIDDEN
        self.image_cache.clear()
        self._build_keys()

    def _draw_sidebar_toggle(self, sc):
        rect = self._sidebar_toggle_rect()
        label = "HIDE" if self.sidebar_open else "SHOW SONGS"
        hover = rect.collidepoint(pygame.mouse.get_pos())
        fill = "#123442" if hover else "#0a202b"
        border = "#3fb6ff" if hover else "#2a647d"
        ink = "#dff9ff" if hover else "#8fb8ca"
        pygame.draw.rect(sc, fill, rect, border_radius=6)
        pygame.draw.rect(sc, border, rect, 1, border_radius=6)
        self.text(label, 8, ink, True, rect.center, center=True)

    def _song_rows(self):
        rows = []
        start = self.song_scroll
        songs = self.songs[start:start + SONG_ROWS]
        for i, song in enumerate(songs):
            y = 154 + i * (SONG_ROW_H + SONG_ROW_GAP)
            rows.append((pygame.Rect(1014, y, 196, SONG_ROW_H),
                         song, start + i))
        return rows

    @staticmethod
    def _song_icon_rects(row):
        rename = pygame.Rect(row.right - 44, row.y + 12, 18, 16)
        delete = pygame.Rect(row.right - 22, row.y + 12, 18, 16)
        return rename, delete

    def _clip_text(self, text, size, maxw, bold=False):
        if not text:
            return ""
        if self.text(text, size, "#ffffff", bold).get_width() <= maxw:
            return text
        out = text
        while out and self.text(out + "...", size, "#ffffff",
                                bold).get_width() > maxw:
            out = out[:-1]
        return out.rstrip() + "..."

    def _draw_song_icon(self, sc, rect, kind, color):
        cx, cy = rect.center
        if kind == "rename":
            pygame.draw.line(sc, color, (cx - 4, cy + 4),
                             (cx + 4, cy - 4), 2)
            pygame.draw.line(sc, color, (cx - 5, cy + 5),
                             (cx - 3, cy + 5), 2)
            pygame.draw.line(sc, color, (cx + 4, cy - 4),
                             (cx + 5, cy - 3), 2)
        else:
            pygame.draw.line(sc, color, (cx - 5, cy - 5),
                             (cx + 5, cy - 5), 2)
            pygame.draw.line(sc, color, (cx - 2, cy - 7),
                             (cx + 2, cy - 7), 2)
            pygame.draw.rect(sc, color, (cx - 4, cy - 3, 8, 9), 1,
                             border_radius=1)
            pygame.draw.line(sc, color, (cx - 1, cy),
                             (cx - 1, cy + 4), 1)
            pygame.draw.line(sc, color, (cx + 1, cy),
                             (cx + 1, cy + 4), 1)

    def _draw_song_sidebar(self, sc):
        if not self.sidebar_open:
            return
        x, y = SIDEBAR_X, SIDEBAR_Y
        pygame.draw.rect(sc, "#05080b", (x - 4, y + 4,
                                         SIDEBAR_W, SIDEBAR_H),
                         border_radius=12)
        sc.blit(self.song_panel_img, (x, y))
        pygame.draw.line(sc, "#3fb6ff", (x + 1, y + 12),
                         (x + 1, y + SIDEBAR_H - 12), 1)
        self.text("SONG LIBRARY", 13, "#dff9ff", True, (x + 14, y + 16))
        self.text("SAVE \u00b7 LOAD \u00b7 RENAME \u00b7 DELETE", 8,
                  "#5f8fa5", True, (x + 14, y + 36))
        self.text("SAVED SONGS", 8, "#7f8a9d", True, (1014, 134))
        self.text(str(len(self.songs)), 8, "#6fc9e6", True,
                  (1210, 134), right=True)
        pygame.draw.line(sc, "#173646", (1014, 122), (1210, 122), 1)

        mouse = pygame.mouse.get_pos()
        rows = self._song_rows()
        for rect, song, index in rows:
            active = self._song_key(song) == self.song_active_at
            hover = rect.collidepoint(mouse)
            rename, delete = self._song_icon_rects(rect)
            if active:
                fill, border = "#0c2d3b", "#3fb6ff"
            elif hover:
                fill, border = "#123442", "#2a647d"
            else:
                fill, border = "#0a202b", "#173646"
            pygame.draw.rect(sc, fill, rect, border_radius=5)
            pygame.draw.rect(sc, border, rect, 1, border_radius=5)
            if active:
                pygame.draw.rect(sc, "#3fb6ff",
                                 (rect.x + 1, rect.y + 7, 3,
                                  rect.h - 14), border_radius=1)
            name = (song.get("name") or song.get("style_name") or
                    "Untitled song")
            shown = self._clip_text(name, 10, rect.w - 62, True)
            color = "#dff9ff" if active else "#b9e6ff"
            self.text(shown, 10, color, True, (rect.x + 10, rect.y + 7))
            style = song.get("style_name") or "Unknown style"
            stamp = str(song.get("saved_at", ""))[11:16]
            sub = style + ("  \u00b7  " + stamp if stamp else "")
            self.text(self._clip_text(sub, 8, rect.w - 62), 8,
                      "#5f8fa5", True, (rect.x + 10, rect.y + 24))

            for box, kind in ((rename, "rename"), (delete, "delete")):
                icon_hover = box.collidepoint(mouse)
                if icon_hover:
                    pygame.draw.rect(sc, "#123442", box,
                                     border_radius=4)
                    pygame.draw.rect(sc, "#2a647d", box, 1,
                                     border_radius=4)
                ink = ("#ff9e96" if kind == "delete" and icon_hover
                       else "#dff9ff" if icon_hover else "#5f8fa5")
                self._draw_song_icon(sc, box, kind, ink)

        if not rows:
            self.text("NO SAVED SONGS", 10, "#4f7385", True,
                      (1112, 390), center=True)
            self.text("CLICK SAVE CURRENT SONG", 8, "#35576a", True,
                      (1112, 412), center=True)

        if len(self.songs) > SONG_ROWS:
            track = pygame.Rect(1216, 154, 3, 528)
            pygame.draw.rect(sc, "#12303d", track, border_radius=2)
            frac = SONG_ROWS / float(len(self.songs))
            thumb_h = max(16, int(track.h * frac))
            span = max(1, len(self.songs) - SONG_ROWS)
            top = track.y + int((track.h - thumb_h) *
                                (self.song_scroll / float(span)))
            pygame.draw.rect(sc, "#2a647d",
                             (track.x, top, track.w, thumb_h),
                             border_radius=2)

        now = pygame.time.get_ticks()
        if self.song_msg and now < self.song_msg_until:
            msg = self.song_msg
            color = "#45e07d" if "FAILED" not in msg else "#ff7b72"
        else:
            msg = "CLICK A SONG TO LOAD"
            color = "#35576a"
        self.text(msg, 8, color, True, (1112, 702), center=True)
        hint = ("SCROLL FOR MORE" if len(self.songs) > SONG_ROWS
                else "RENAME OR DELETE WITH THE ICONS")
        self.text(hint, 7, "#2f4c5c", True, (1112, 720), center=True)

    # ------------------------------------------------------------------
    def _update_ui(self):
        s = self.engine.state()
        self.state = s
        status = "ACMP ON" if s["acmp"] else "ACMP OFF"
        if s["waiting"]:
            section = "SYNC START"
            hint = "SYNC START \u2014 PLAY A CHORD"
        elif s["running"]:
            section = ("INTRO" if s["sec"] == "I" else
                       "ENDING" if s["sec"] == "E" else "MAIN " + s["sec"])
            if s["pend"] in ("A", "B"):
                hint = "FILL \u2192 MAIN " + s["pend"]
            elif s["pend"] == "E":
                hint = "ENDING"
            elif s["pend"] == "I":
                hint = "INTRO"
            else:
                hint = ""
        else:
            section = "MAIN " + s["main"]
            hint = ""
        self.view = {
            "style": s["style_name"], "status": status, "chord": s["chord_label"],
            "hint": hint, "tempo": self.tempo_ctl.fmt(s["tempo"]),
            "volume": self.volume_ctl.fmt(self.volume_ctl.value),
            "transpose": self.transpose_ctl.fmt(s["transpose"]),
            "section": section,
            "voice": s.get("voice_label", self.engine.voice_label(s["voice"])),
        }
        style_w = self.text(self.view["style"], 17, COL_LCD_TX, True).get_width()
        voice_w = self.text(self.view["voice"], 17, COL_LCD_TX, True).get_width()
        self.name_hits = {
            "style": pygame.Rect(54, 84, max(70, style_w) + 12, 24),
            "voice": pygame.Rect(662 - max(70, voice_w) - 12, 84,
                                 max(70, voice_w) + 12, 24),
        }
        self.btn_acmp.set_on(s["acmp"])
        self.btn_sync.set_on(s["sync"])
        c = s["pend"] or (s["main"] if s["sec"] == "I" else s["sec"])
        self.btn_a.set_on(c == "A")
        self.btn_b.set_on(c == "B")
        self.btn_intro.set_on(s["sec"] == "I" or s["pend"] == "I")
        self.btn_ending.set_on(c == "E")
        self.btn_fill.set_on(s["pend"] in ("A", "B") and s["running"])
        self.btn_start.set_on(s["running"])
        if s["waiting"]:
            self.btn_start.set_text("SYNC...")
        elif s["running"]:
            self.btn_start.set_text("STOP")
        else:
            self.btn_start.set_text("START")
        self.tempo_ctl.set_value(s["tempo"])
        self.transpose_ctl.set_value(s["transpose"])

    # ------------------------------------------------------------------
    def _style(self, idx):
        self.engine.select_style(idx)
        self._update_ui()

    def _voice(self, voice):
        self.engine.set_voice(voice)
        self._update_ui()

    @staticmethod
    def _list_layout(kind):
        if kind == "style":
            return (pygame.Rect(48, 68, 624, 216), 60, 110,
                    4, 146, 74, 6, 12, 2)
        return (pygame.Rect(48, 68, 624, 500), 60, 108,
                3, 190, 60, 6, 8, 6)

    def _list_items(self):
        if self.list_overlay == "style":
            items = []
            # Built-in styles
            for i, style in enumerate(STYLES):
                items.append((('builtin', i), style["name"]))
            # User styles
            try:
                user_styles = load_user_styles()
                for i, style in enumerate(user_styles):
                    name = style.get("name", f"User {i+1}")
                    items.append((('user', i), name))
            except Exception:
                pass
            return items
        return list(self.engine.voice_choices)

    def _refresh_list_hits(self):
        self.list_hits = []
        items = self._list_items()
        _, _, _, cols, _, _, _, _, visible_rows = self._list_layout(
            self.list_overlay)
        self.list_rows = max(1, int(math.ceil(len(items) / float(cols))))
        self.list_visible_rows = visible_rows
        self.list_scroll = max(0, min(self.list_scroll,
                                      self.list_rows - visible_rows))
        box, left, top, cols, cw, ch, gx, gy, _ = self._list_layout(
            self.list_overlay)
        first = self.list_scroll * cols
        last = min(len(items), first + visible_rows * cols)
        for i in range(first, last):
            row, col = divmod(i, cols)
            visible_row = row - self.list_scroll
            rect = pygame.Rect(left + col * (cw + gx),
                               top + visible_row * (ch + gy), cw, ch)
            value, label = items[i]
            self.list_hits.append((rect, value, label))

    def _open_list(self, kind):
        self._close_list()
        self.list_overlay = kind
        self.list_scroll = 0
        if kind == "voice":
            self.engine.refresh_voices()
        self._refresh_list_hits()

    def _close_list(self):
        self.list_overlay = None
        self.list_hits = []
        self.list_scroll = 0

    def _choose_list(self, value):
        kind = self.list_overlay
        self._close_list()
        if kind == "style":
            # value may be tuple ('builtin'/'user', idx) or legacy int
            kind_val = None
            idx = None
            if isinstance(value, tuple) and len(value) == 2:
                kind_val, idx = value
            elif isinstance(value, int):
                kind_val, idx = 'builtin', value
            else:
                kind_val, idx = 'builtin', 0
            if getattr(self, "style_editor_load_mode", False):
                self.style_editor_load_mode = False
                self.style_editor_open = True
                if kind_val == 'builtin':
                    style = STYLES[idx] if 0 <= idx < len(STYLES) else STYLES[0]
                else:
                    try:
                        user_styles = load_user_styles()
                        style = user_styles[idx] if 0 <= idx < len(user_styles) else STYLES[0]
                    except Exception:
                        style = STYLES[0]
                self.style_editor_data = self._style_to_editor_data(style)
                self.style_editor_section = "main_a"
                self.style_editor_instrument = "kick"
                self._update_ui()
            else:
                if kind_val == 'builtin':
                    self._style(idx)
                else:
                    try:
                        user_styles = load_user_styles()
                        style = user_styles[idx] if 0 <= idx < len(user_styles) else None
                        if style:
                            # Convert normalized user style to engine style if needed
                            # Assume user style is already in engine format
                            self.engine.select_style_dict(style)
                            self._update_ui()
                            return
                    except Exception:
                        pass
                    # fallback to built-in
                    self._style(0)
        else:
            self._voice(value)

    @staticmethod
    def _plugin_box():
        return pygame.Rect(48, 62, 888, 620)

    @staticmethod
    def _plugin_search_rect():
        return pygame.Rect(64, 106, 620, 38)

    @staticmethod
    def _plugin_close_rect():
        return pygame.Rect(824, 76, 96, 26)

    def _open_plugin_manager(self):
        self._close_list()
        self.plugin_manager_open = True
        self.plugin_search = ""
        self.plugin_search_focus = True
        self.plugin_scroll = 0
        self.engine.refresh_voices()
        self._refresh_plugin_hits()
        self._update_ui()

    def _close_plugin_manager(self):
        self.plugin_manager_open = False
        self.plugin_search_focus = False
        self.plugin_hits = []
        self._update_ui()

    def _style_to_editor_data(self, style):
        name = style.get("name", "Unnamed")
        bpm = style.get("bpm", 120)
        length = style.get("len", 16)
        comp_voice = style.get("comp_voice", "piano")
        comp_len = style.get("comp_len", 2)
        def pad_pat(pat, length):
            if not pat:
                return "." * length
            return (pat[:length]).ljust(length, ".")
        sections = {}
        for sec in ["intro", "main_a", "main_b", "fill", "ending"]:
            sections[sec] = {
                "drums": {"kick": "", "snare": "", "hat": "", "open": "", "crash": ""},
                "bass": [],
                "comp": ""
            }
        def normalize_bass(bass):
            # migrate old 0-15 index to MIDI 36+
            if not bass:
                return []
            if not isinstance(bass[0], (list, tuple)):
                return bass
            try:
                first_note = bass[0][1]
                if isinstance(first_note, int) and first_note < 32:
                    return [[e[0], 36 + e[1] if isinstance(e[1], int) else e[1], e[2] if len(e) > 2 else 1] for e in bass]
            except Exception:
                pass
            return bass
        # main_a from base
        main_a = sections["main_a"]
        main_a["drums"]["kick"] = pad_pat(style.get("kick", ""), length)
        main_a["drums"]["snare"] = pad_pat(style.get("snare", ""), length)
        main_a["drums"]["hat"] = pad_pat(style.get("hat", ""), length)
        main_a["drums"]["open"] = pad_pat(style.get("open", ""), length)
        main_a["drums"]["crash"] = pad_pat(style.get("crash", ""), length)
        main_a["bass"] = normalize_bass(style.get("bass", []))
        main_a["comp"] = pad_pat(style.get("comp", ""), length)
        # main_b from B section if present
        bsec = style.get("B", {})
        main_b = sections["main_b"]
        main_b["drums"]["kick"] = pad_pat(bsec.get("kick", style.get("kick", "")), length)
        main_b["drums"]["snare"] = pad_pat(bsec.get("snare", style.get("snare", "")), length)
        main_b["drums"]["hat"] = pad_pat(bsec.get("hat", style.get("hat", "")), length)
        main_b["drums"]["open"] = pad_pat(bsec.get("open", style.get("open", "")), length)
        main_b["drums"]["crash"] = pad_pat(bsec.get("crash", ""), length)
        main_b["bass"] = normalize_bass(bsec.get("bass", style.get("bass", [])))
        main_b["comp"] = pad_pat(bsec.get("comp", style.get("comp", "")), length)
        # intro / fill / ending from style if present
        for sec in ["intro", "fill", "ending"]:
            sec_pat = style.get(sec, {})
            if isinstance(sec_pat, dict):
                sec_sec = sections[sec]
                sec_sec["drums"]["kick"] = pad_pat(sec_pat.get("kick", ""), length)
                sec_sec["drums"]["snare"] = pad_pat(sec_pat.get("snare", ""), length)
                sec_sec["drums"]["hat"] = pad_pat(sec_pat.get("hat", ""), length)
                sec_sec["drums"]["open"] = pad_pat(sec_pat.get("open", ""), length)
                sec_sec["drums"]["crash"] = pad_pat(sec_pat.get("crash", ""), length)
                sec_sec["bass"] = normalize_bass(sec_pat.get("bass", []))
                sec_sec["comp"] = pad_pat(sec_pat.get("comp", ""), length)
        return {
            "name": name,
            "bpm": bpm,
            "len": length,
            "comp_voice": comp_voice,
            "comp_len": comp_len,
            "sections": sections
        }

    def _editor_data_to_style(self, ed):
        name = ed.get("name", "Untitled")
        bpm = ed.get("bpm", 120)
        length = ed.get("len", 16)
        comp_voice = ed.get("comp_voice", "piano")
        comp_len = ed.get("comp_len", 2)
        sections = ed.get("sections", {})
        def get_sec(key):
            sec = sections.get(key, {})
            drums = sec.get("drums", {})
            return drums, sec.get("bass", []), sec.get("comp", "")
        main_a_drums, main_a_bass, main_a_comp = get_sec("main_a")
        main_b_drums, main_b_bass, main_b_comp = get_sec("main_b")
        def clean(pat):
            return pat if pat else ""
        style = {
            "name": name,
            "bpm": bpm,
            "len": length,
            "comp_voice": comp_voice,
            "comp_len": comp_len,
            "kick": clean(main_a_drums.get("kick", "")),
            "snare": clean(main_a_drums.get("snare", "")),
            "hat": clean(main_a_drums.get("hat", "")),
            "open": clean(main_a_drums.get("open", "")),
            "crash": clean(main_a_drums.get("crash", "")),
            "comp": clean(main_a_comp),
            "bass": main_a_bass,
        }
        bsec = {}
        changed = False
        for k in ["kick","snare","hat","open","crash","comp"]:
            val = clean(main_b_drums.get(k, "")) if k!="comp" else clean(main_b_comp)
            base = style[k] if k!="comp" else style["comp"]
            if val != base:
                changed = True
                bsec[k] = val
        if main_b_bass != main_a_bass:
            changed = True
            bsec["bass"] = main_b_bass
        if changed:
            style["B"] = bsec
        # export intro / fill / ending patterns
        for sec in ["intro", "fill", "ending"]:
            drums, bass, comp = get_sec(sec)
            sec_dict = {
                "kick": clean(drums.get("kick","")),
                "snare": clean(drums.get("snare","")),
                "hat": clean(drums.get("hat","")),
                "open": clean(drums.get("open","")),
                "crash": clean(drums.get("crash","")),
                "bass": bass,
                "comp": clean(comp),
            }
            # prune empty sections to keep file small
            if any(sec_dict[k] for k in ["kick","snare","hat","open","crash","comp"]) or sec_dict["bass"]:
                style[sec] = sec_dict
        return style

    def _open_style_editor(self):
        self._close_list()
        self.plugin_manager_open = False
        self.style_editor_load_mode = True
        self._open_list("style")

    def _style_editor_mouse_down(self, pos):
        if hasattr(self, "_style_editor_name_rect") and self._style_editor_name_rect.collidepoint(pos):
            self.dialog = {"kind": "style_rename", "text": self.style_editor_data.get("name","Untitled"), "select_all": True, "error": ""}
            return
        if hasattr(self, "_style_editor_test_rect") and self._style_editor_test_rect.collidepoint(pos):
            style_dict = self._editor_data_to_style(self.style_editor_data)
            self.engine.select_style_dict(style_dict)
            self._update_ui()
            return
        if hasattr(self, "_style_editor_save_rect") and self._style_editor_save_rect.collidepoint(pos):
            # Save with user chosen name, ensure unique
            # Convert editor data to engine style format
            style_dict = self._editor_data_to_style(self.style_editor_data)
            styles = load_user_styles()
            base_name = style_dict.get("name", "Untitled")
            name = base_name
            counter = 1
            existing_names = {s.get("name") for s in styles}
            while name in existing_names:
                name = f"{base_name} copy {counter}"
                counter += 1
            style_dict["name"] = name
            styles.append(style_dict)
            save_user_styles(styles)
            # Select the newly saved style
            self.engine.select_style_dict(style_dict)
            self._close_style_editor()
            return
        if hasattr(self, "_style_editor_delete_rect") and self._style_editor_delete_rect.collidepoint(pos):
            # Delete user style with matching name
            name = self.style_editor_data.get("name")
            if name:
                styles = load_user_styles()
                new_styles = [s for s in styles if s.get("name") != name]
                if len(new_styles) != len(styles):
                    save_user_styles(new_styles)
            self._close_style_editor()
            return
        if hasattr(self, "_style_editor_cancel_rect") and self._style_editor_cancel_rect.collidepoint(pos):
            self._close_style_editor()
            return
        # instrument tab buttons
        if hasattr(self, "_style_editor_instrument_tab_rects"):
            for rect, tab in self._style_editor_instrument_tab_rects:
                if rect.collidepoint(pos):
                    self.style_editor_instrument_tab = tab
                    self._update_ui()
                    return
        # section buttons
        if hasattr(self, "_style_editor_section_rects"):
            for rect, sec in self._style_editor_section_rects:
                if rect.collidepoint(pos):
                    self.style_editor_section = sec
                    self._update_ui()
                    return
        # handle step grid clicks
        if hasattr(self, "_style_editor_grid_rect") and self._style_editor_grid_rect.collidepoint(pos):
            if self.style_editor_instrument_tab == "drums":
                grid_x, grid_y, step_w, step_h = self._style_editor_grid_origin
                rel_x = pos[0] - grid_x
                rel_y = pos[1] - grid_y
                idx = int(rel_x // step_w)
                ti = int(rel_y // (step_h + 4))
                steps = self.style_editor_data["len"]
                tracks = [
                    ("KICK","drums","kick"),
                    ("SNARE","drums","snare"),
                    ("HAT","drums","hat"),
                    ("OPEN","drums","open"),
                    ("CRASH","drums","crash"),
                ]
                if 0 <= idx < steps and 0 <= ti < len(tracks):
                    sec = self.style_editor_section
                    instr = tracks[ti][2]
                    sec_data = self.style_editor_data["sections"][sec]
                    if instr in ("kick","snare","hat","open","crash"):
                        pat = list(sec_data["drums"].get(instr, "." * steps))
                    else:
                        return
                    if len(pat) < steps:
                        pat = list("".join(pat).ljust(steps, "."))
                    cur = pat[idx] if idx < len(pat) else "."
                    pat[idx] = "x" if cur != "x" else "."
                    new_pat = "".join(pat[:steps])
                    sec_data["drums"][instr] = new_pat
                    self._update_ui()
            elif self.style_editor_instrument_tab == "bass":
                grid_x, grid_y, step_w, note_h = self._style_editor_grid_origin
                rel_x = pos[0] - grid_x
                rel_y = pos[1] - grid_y
                idx = int(rel_x // step_w)
                note_idx = int(rel_y // note_h)
                steps = self.style_editor_data["len"]
                note_count = 16
                if 0 <= idx < steps and 0 <= note_idx < note_count:
                    sec = self.style_editor_section
                    sec_data = self.style_editor_data["sections"][sec]
                    bass_list = sec_data.get("bass", [])
                    midi_note = 36 + note_idx
                    # toggle note at position
                    existing = [e for e in bass_list if e[0] == idx and e[1] == midi_note]
                    if existing:
                        sec_data["bass"] = [e for e in bass_list if not (e[0] == idx and e[1] == midi_note)]
                    else:
                        sec_data.setdefault("bass", []).append([idx, midi_note, 1])
                    self._update_ui()

    def _close_style_editor(self):
        self.style_editor_open = False
        self.style_editor_data = None
        self._update_ui()

    def _commit_style_rename(self):
        if self.style_editor_data is not None and self.dialog and "edit_buffer" in self.dialog:
            new_name = self.dialog["edit_buffer"].strip() or self.style_editor_data.get("name","Untitled")
            self.style_editor_data["name"] = new_name
            self.dialog = None
            self._update_ui()


    def _refresh_plugin_hits(self):
        entries = self.synth.plugin_entries(self.plugin_search)
        all_entries = self.synth.plugin_entries()
        self.plugin_total = len(all_entries)
        self.plugin_added = sum(1 for entry in all_entries if entry["added"])
        self.plugin_rows = len(entries)
        self.plugin_scroll = max(0, min(self.plugin_scroll,
                                        self.plugin_rows - 8))
        self.plugin_hits = []
        for row_index, entry in enumerate(entries[
                self.plugin_scroll:self.plugin_scroll + 8]):
            rect = pygame.Rect(64, 156 + row_index * 62, 856, 54)
            action = pygame.Rect(rect.right - 104, rect.y + 12, 92, 30)
            edit = pygame.Rect(rect.right - 210, rect.y + 12, 92, 30)
            self.plugin_hits.append((rect, edit, action, entry))

    def _plugin_toggle(self, key, added):
        if added:
            self.synth.remove_instrument(key)
        else:
            self.synth.add_instrument(key)
        self.engine.refresh_voices()
        self._refresh_plugin_hits()
        self._update_ui()

    def _plugin_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self._close_plugin_manager()
            return
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.plugin_search_focus = False
            return
        if event.key == pygame.K_DOWN:
            self.plugin_scroll += 1
            self._refresh_plugin_hits()
            return
        if event.key == pygame.K_UP:
            self.plugin_scroll -= 1
            self._refresh_plugin_hits()
            return
        if event.key == pygame.K_PAGEDOWN:
            self.plugin_scroll += 8
            self._refresh_plugin_hits()
            return
        if event.key == pygame.K_PAGEUP:
            self.plugin_scroll -= 8
            self._refresh_plugin_hits()
            return
        if event.key == pygame.K_BACKSPACE:
            self.plugin_search = self.plugin_search[:-1]
        elif event.key == pygame.K_DELETE:
            self.plugin_search = ""
        else:
            ch = event.unicode
            if not ch or len(ch) != 1 or not ch.isprintable():
                return
            self.plugin_search_focus = True
            self.plugin_search += ch
        self.plugin_scroll = 0
        self._refresh_plugin_hits()

    def _plugin_mouse_down(self, pos):
        if self._plugin_close_rect().collidepoint(pos):
            self._close_plugin_manager()
            return
        self.plugin_search_focus = self._plugin_search_rect().collidepoint(pos)
        for _, edit, action, entry in self.plugin_hits:
            if entry["added"] and edit.collidepoint(pos):
                self._open_plugin_editor(entry["key"], entry["label"])
                return
            if action.collidepoint(pos):
                self._plugin_toggle(entry["key"], entry["added"])
                return
        if not self._plugin_box().collidepoint(pos):
            self._close_plugin_manager()

    # ------------------------------------------------------------------
    def _plugin_editor_box(self):
        return pygame.Rect(120, 96, 1000, 580)

    def _plugin_editor_close_rect(self):
        return pygame.Rect(1004, 110, 96, 26)

    def _open_plugin_editor(self, key, label):
        self.plugin_editor_open = True
        self.plugin_editor_key = key
        self.plugin_editor_label = label
        self.plugin_editor_scroll = 0
        self.plugin_editor_drag = None
        self.plugin_editor_refresh = 0.0
        self.synth.ensure_instrument(key)
        self.plugin_editor_params = self.synth.plugin_parameters(key)
        self._refresh_plugin_editor_rows()
        self._update_ui()

    def _refresh_plugin_editor_values(self):
        if not self.plugin_editor_open or self.plugin_editor_key is None:
            return
        now = time.time()
        if now - self.plugin_editor_refresh < 0.4:
            return
        self.plugin_editor_refresh = now
        for param in self.plugin_editor_params:
            value = self.synth.plugin_parameter_value(self.plugin_editor_key,
                                                      param["id"])
            if value is not None:
                param["value"] = value

    def _close_plugin_editor(self):
        self.plugin_editor_open = False
        self.plugin_editor_key = None
        self.plugin_editor_label = ""
        self.plugin_editor_params = []
        self.plugin_editor_rows = []
        self.plugin_editor_drag = None
        self.plugin_editor_refresh = 0.0
        self._update_ui()

    def _refresh_plugin_editor_rows(self):
        self.plugin_editor_rows = []
        visible = 9
        self.plugin_editor_scroll = max(0, min(
            self.plugin_editor_scroll,
            max(0, len(self.plugin_editor_params) - visible)))
        for index in range(visible):
            position = self.plugin_editor_scroll + index
            if position >= len(self.plugin_editor_params):
                break
            rect = pygame.Rect(148, 182 + index * 52, 944, 44)
            track = pygame.Rect(rect.x + 250, rect.y + 20, 520, 6)
            self.plugin_editor_rows.append((rect, track, position))

    def _plugin_editor_value_at(self, param, ratio):
        low = param["min"]
        high = param["max"]
        span = float(high - low) or 1.0
        return low + max(0.0, min(1.0, float(ratio))) * span

    def _plugin_editor_set(self, position, value):
        if position >= len(self.plugin_editor_params):
            return
        param = self.plugin_editor_params[position]
        value = max(param["min"], min(param["max"], float(value)))
        param["value"] = value
        self.synth.set_plugin_parameter(self.plugin_editor_key, param["id"],
                                        value)

    def _plugin_editor_mouse_down(self, pos):
        if self._plugin_editor_close_rect().collidepoint(pos):
            self._close_plugin_editor()
            return
        for rect, track, position in self.plugin_editor_rows:
            if track.inflate(0, 16).collidepoint(pos):
                self.plugin_editor_drag = (position, track)
                self._plugin_editor_set(position, self._plugin_editor_value_at(
                    self.plugin_editor_params[position],
                    (pos[0] - track.x) / float(max(1, track.w))))
                return
        if not self._plugin_editor_box().collidepoint(pos):
            self._close_plugin_editor()

    def _plugin_editor_mouse_move(self, pos):
        if self.plugin_editor_drag is None:
            return
        position, track = self.plugin_editor_drag
        self._plugin_editor_set(position, self._plugin_editor_value_at(
            self.plugin_editor_params[position],
            (pos[0] - track.x) / float(max(1, track.w))))

    def _plugin_editor_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self._close_plugin_editor()
            return
        if event.key == pygame.K_DOWN:
            self.plugin_editor_scroll += 1
        elif event.key == pygame.K_UP:
            self.plugin_editor_scroll -= 1
        elif event.key == pygame.K_PAGEDOWN:
            self.plugin_editor_scroll += 9
        elif event.key == pygame.K_PAGEUP:
            self.plugin_editor_scroll -= 9
        self._refresh_plugin_editor_rows()

    def _plugin_editor_wheel(self, direction):
        self.plugin_editor_scroll -= direction
        self._refresh_plugin_editor_rows()

    def _plugin_wheel(self, direction):
        self.plugin_scroll -= direction
        self._refresh_plugin_hits()

    def _mode(self, mode):
        self.engine.set_mode(mode)
        self._update_ui()

    def _tempo(self, value):
        self.engine.set_tempo(value)

    def _volume(self, value):
        self.synth.set_master(value / 100.0)

    def _reverb(self, value):
        self.synth.set_reverb(value)

    def _chorus(self, value):
        self.synth.set_chorus(value)

    def _sustain(self, value):
        self.synth.set_sustain(value)

    def _transpose(self, value):
        self.engine.set_transpose(value)
        self._update_ui()

    def _acmp(self):
        self.engine.toggle_acmp()
        self._update_ui()

    def _sync(self):
        self.engine.toggle_sync()
        self._update_ui()

    def _intro(self):
        self.engine.intro_button()
        self._update_ui()

    def _ending(self):
        self.engine.ending_button()
        self._update_ui()

    def _start(self):
        self.engine.start_button()
        self._update_ui()

    def _main(self, section):
        self.engine.main_button(section)
        self._update_ui()

    def _fill(self):
        self.engine.fill_button()
        self._update_ui()

    # ------------------------------------------------------------------
    def _key_press(self, midi, velocity=0.5):
        if midi in self.pressed:
            return
        self.sustained.discard(midi)
        self.pressed.add(midi)
        self.engine.note_on(midi, velocity)
        self._update_ui()

    def _key_release(self, midi):
        if midi not in self.pressed:
            return
        self.pressed.discard(midi)
        if self.sustain:
            self.sustained.add(midi)
        else:
            self.engine.note_off(midi)
        self._update_ui()

    def _midi_note_on(self, midi, velocity):
        self.midi_notes.add(midi)
        if midi in self.sustained:
            self.sustained.discard(midi)
            if midi in self.pressed:
                self.pressed.discard(midi)
                self.engine.note_off(midi)
        self._key_press(midi, velocity)

    def _midi_note_off(self, midi):
        self.midi_notes.discard(midi)
        self._key_release(midi)

    def _release_sustained(self):
        for midi in list(self.sustained):
            self.engine.release_note(midi)
        self.sustained.clear()

    def _midi_sustain(self, down):
        self.sustain = bool(down)
        if not self.sustain:
            self._release_sustained()
        self._update_ui()

    def _midi_all_off(self):
        self.sustain = False
        notes = self.midi_notes | self.sustained | self.pressed
        self.midi_notes.clear()
        self.sustained.clear()
        self.pressed.clear()
        for midi in notes:
            self.engine.panic_note(midi)
        self._update_ui()

    def _toggle_harmony(self):
        self.harmony = not self.harmony
        self.engine.set_harmony(self.harmony)
        self._update_ui()

    def _param_at(self, pos):
        for key, rect in self.param_hitboxes.items():
            if rect.collidepoint(pos):
                return key
        return None

    def _param_value(self, key):
        return {"tempo": self.tempo_ctl.value,
                "volume": self.volume_ctl.value,
                "transpose": self.transpose_ctl.value}[key]

    def _set_param_value(self, key, value):
        value = int(round(value))
        if key == "tempo":
            value = max(50, min(200, value))
            self.tempo_ctl.set_value(value)
            self.engine.set_tempo(value)
        elif key == "volume":
            value = max(0, min(100, value))
            self.volume_ctl.set_value(value)
            self._volume(value)
        elif key == "transpose":
            value = max(-12, min(12, value))
            self.transpose_ctl.set_value(value)
            self.engine.set_transpose(value)
        self._update_ui()

    def _begin_edit(self, key):
        value = self._param_value(key)
        self.edit_param = key
        self.edit_buffer = str(int(value))
        self.edit_select_all = True

    def _cancel_edit(self):
        self.edit_param = None
        self.edit_buffer = ""
        self.edit_select_all = False

    def _commit_edit(self):
        key = self.edit_param
        if key is None:
            return
        raw = self.edit_buffer.strip()
        self._cancel_edit()
        try:
            value = int(raw)
        except ValueError:
            self._update_ui()
            return
        self._set_param_value(key, value)

    def _edit_key(self, e):
        if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_TAB):
            self._commit_edit()
            return
        if e.key == pygame.K_ESCAPE:
            self._cancel_edit()
            return
        if e.key == pygame.K_BACKSPACE:
            if self.edit_select_all:
                self.edit_buffer = ""
                self.edit_select_all = False
            else:
                self.edit_buffer = self.edit_buffer[:-1]
            return
        if e.key == pygame.K_DELETE:
            self.edit_buffer = ""
            self.edit_select_all = False
            return
        ch = e.unicode
        if not ch or len(ch) != 1:
            return
        if ch.isdigit():
            if self.edit_select_all:
                self.edit_buffer = ""
                self.edit_select_all = False
            if len(self.edit_buffer) < 4:
                self.edit_buffer += ch
        elif ch in "+-" and self.edit_param == "transpose":
            if self.edit_select_all:
                self.edit_buffer = ""
                self.edit_select_all = False
            if not self.edit_buffer:
                self.edit_buffer = ch

    def _mouse_down(self, pos):
        if self.list_overlay is not None:
            if self._list_back_rect().collidepoint(pos):
                self._close_list()
                return
            for rect, value, _ in self.list_hits:
                if rect.collidepoint(pos):
                    self._choose_list(value)
                    return
            if not self._list_layout(self.list_overlay)[0].collidepoint(pos):
                self._close_list()
            return
        if self._sidebar_toggle_rect().collidepoint(pos):
            self._toggle_sidebar()
            return
        key = self._param_at(pos)
        if key is not None:
            if self.edit_param == key:
                return
            self._commit_edit()
            self.param_drag = {"key": key, "start_y": pos[1],
                               "start_value": self._param_value(key),
                               "moved": False}
            return
        if self.edit_param is not None:
            self._commit_edit()
        if self.sidebar_open:
            for rect, song, index in self._song_rows():
                rename, delete = self._song_icon_rects(rect)
                if rename.collidepoint(pos):
                    self._open_rename_dialog(index)
                    return
                if delete.collidepoint(pos):
                    self._open_delete_dialog(index)
                    return
                if rect.collidepoint(pos):
                    self._load_song(song)
                    return
        if self.harmony_toggle.collidepoint(pos):
            self._toggle_harmony()
            return
        for knob in self.knobs:
            if knob.rect.collidepoint(pos):
                self.active_knob = knob
                knob.begin_drag(pos)
                return
        for kind, rect in self.name_hits.items():
            if rect.collidepoint(pos):
                self._open_list(kind)
                return
        for mode, rect in self.mode_hits.items():
            if rect.collidepoint(pos):
                self._mode(mode)
                return
        for b in self.buttons:
            if b is self.btn_save_song and not self.sidebar_open:
                continue
            if b.rect.collidepoint(pos):
                self.active_button = b
                b.pressed = True
                return
        m = self.hit_key(pos)
        if m is not None:
            self.mouse_note = m
            self._key_press(m)

    def _mouse_up(self, pos):
        if self.active_knob is not None:
            self.active_knob.dragging = False
            self.active_knob = None
            return
        if self.param_drag is not None:
            drag = self.param_drag
            self.param_drag = None
            if drag["moved"]:
                self._update_ui()
            elif self._param_at(pos) == drag["key"]:
                self._begin_edit(drag["key"])
            return
        if self.active_button:
            b = self.active_button
            b.pressed = False
            self.active_button = None
            if b.rect.collidepoint(pos):
                b.command()
                self._update_ui()
        if self.mouse_note is not None:
            self._key_release(self.mouse_note)
            self.mouse_note = None

    def _mouse_move(self, pos):
        if self.active_knob is not None:
            self.active_knob.drag_to(pos)
            return
        if self.param_drag is not None:
            drag = self.param_drag
            dy = drag["start_y"] - pos[1]
            if abs(dy) >= 3:
                drag["moved"] = True
            if drag["moved"]:
                step = {"tempo": 2.0, "volume": 2.0,
                        "transpose": 8.0}[drag["key"]]
                self._set_param_value(drag["key"],
                                      drag["start_value"] + dy / step)
            return
        if self.mouse_note is not None:
            m = self.hit_key(pos)
            if m != self.mouse_note:
                self._key_release(self.mouse_note)
                self.mouse_note = m
                if m is not None:
                    self._key_press(m)

    def _mouse_wheel(self, direction):
        if self.dialog is not None or self.edit_param is not None:
            return
        if self.list_overlay is not None:
            self.list_scroll -= direction
            self._refresh_list_hits()
            return
        pos = pygame.mouse.get_pos()
        if self.sidebar_open and self.song_sidebar.collidepoint(pos):
            span = max(0, len(self.songs) - SONG_ROWS)
            self.song_scroll = max(0, min(span,
                                           self.song_scroll - direction))
            return
        for knob in self.knobs:
            if knob.rect.collidepoint(pos):
                knob.nudge(direction)
                return

    def _event(self, e):
        if e.type == pygame.QUIT:
            self.running = False
        elif self.dialog is not None:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self._dialog_mouse_down(e.pos)
            elif e.type == pygame.KEYDOWN:
                self._dialog_key(e)
            return
        elif self.style_editor_open:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self._style_editor_mouse_down(e.pos)
            return
        elif self.plugin_editor_open:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self._plugin_editor_mouse_down(e.pos)
            elif e.type == pygame.MOUSEMOTION:
                self._plugin_editor_mouse_move(e.pos)
            elif e.type == pygame.MOUSEWHEEL:
                self._plugin_editor_wheel(e.y)
            elif e.type == pygame.KEYDOWN:
                self._plugin_editor_key(e)
            return
        elif self.plugin_manager_open:
            if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                self._plugin_mouse_down(e.pos)
            elif e.type == pygame.MOUSEWHEEL:
                self._plugin_wheel(e.y)
            elif e.type == pygame.KEYDOWN:
                self._plugin_key(e)
            return
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self._mouse_down(e.pos)
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._mouse_up(e.pos)
        elif e.type == pygame.MOUSEMOTION:
            self._mouse_move(e.pos)
        elif e.type == pygame.MOUSEWHEEL:
            self._mouse_wheel(e.y)
        elif e.type == pygame.KEYDOWN:
            if self.edit_param is not None:
                self._edit_key(e)
                return
            if self.list_overlay is not None:
                if e.key == pygame.K_ESCAPE:
                    self._close_list()
                return
            k = e.key
            if k == pygame.K_SPACE:
                self.engine.start_button()
                self._update_ui()
            elif k == pygame.K_ESCAPE:
                self.running = False
            else:
                ch = self._key_char(e)
                midi = KEYMAP.get(ch)
                if midi is not None and midi not in self.kb_keys:
                    self.kb_keys.add(midi)
                    self._key_press(midi)
        elif e.type == pygame.KEYUP:
            ch = self._key_char(e)
            midi = KEYMAP.get(ch)
            if midi is not None:
                self.kb_keys.discard(midi)
                self._key_release(midi)

    @staticmethod
    def _key_char(e):
        try:
            ch = e.unicode.lower()
        except Exception:
            ch = ""
        if len(ch) == 1:
            return ch
        names = {
            pygame.K_a: "a", pygame.K_b: "b", pygame.K_c: "c", pygame.K_d: "d",
            pygame.K_e: "e", pygame.K_f: "f", pygame.K_g: "g", pygame.K_h: "h",
            pygame.K_i: "i", pygame.K_j: "j", pygame.K_k: "k", pygame.K_l: "l",
            pygame.K_m: "m", pygame.K_n: "n", pygame.K_o: "o", pygame.K_p: "p",
            pygame.K_q: "q", pygame.K_r: "r", pygame.K_s: "s", pygame.K_t: "t",
            pygame.K_u: "u", pygame.K_v: "v", pygame.K_w: "w", pygame.K_x: "x",
            pygame.K_y: "y", pygame.K_z: "z",
            pygame.K_0: "0", pygame.K_1: "1", pygame.K_2: "2", pygame.K_3: "3",
            pygame.K_4: "4", pygame.K_5: "5", pygame.K_6: "6", pygame.K_7: "7",
            pygame.K_8: "8", pygame.K_9: "9",
        }
        return names.get(e.key, "")

    # ------------------------------------------------------------------
    def _draw_lcd_toggles(self, sc):
        mouse = pygame.mouse.get_pos()
        for rect, label, on in (
                (self.harmony_toggle, "HARMONY", self.harmony),):
            hover = rect.collidepoint(mouse)
            fill = "#0c2d3b" if on else (
                "#123442" if hover else "#0a202b")
            edge = "#2f657b" if on else (
                "#3fb6ff" if hover else "#173646")
            pygame.draw.rect(sc, fill, rect, border_radius=9)
            pygame.draw.rect(sc, edge, rect, 1, border_radius=9)
            led = (rect.x + 10, rect.centery)
            if on:
                pygame.draw.circle(sc, "#1d5c3e", led, 6)
                pygame.draw.circle(sc, "#45e07d", led, 4)
            else:
                pygame.draw.circle(sc, "#2a4a5a", led, 4, 1)
            color = "#dff9ff" if on else (
                "#9fc7d8" if hover else "#5f8fa5")
            self.text(label, 8, color, True, (rect.x + 19, rect.y + 4))

    def _draw_lcd_params(self, sc):
        mouse = pygame.mouse.get_pos()
        for key, x, value_x, label in self.param_fields:
            rect = self.param_hitboxes[key]
            dragging = (self.param_drag is not None and
                        self.param_drag["key"] == key)
            hover = rect.collidepoint(mouse) and self.edit_param != key
            if dragging or hover:
                pygame.draw.rect(sc, "#0c2d3b", rect, border_radius=5)
                pygame.draw.rect(sc, "#3fb6ff" if dragging else "#1d4a60",
                                 rect, 1, border_radius=5)
            self.text(label, 9, "#5f8fa5", True, (x, 217))
            if self.edit_param == key:
                display = self.edit_buffer
                if key == "volume" and display:
                    display += "%"
                surf = self.text(display or " ", 18, "#dff9ff", True,
                                 mono=True)
                rect = pygame.Rect(value_x - 5, 209,
                                   max(44, surf.get_width() + 10), 27)
                pygame.draw.rect(sc, "#0c2d3b", rect, border_radius=5)
                pygame.draw.rect(sc, "#3fb6ff", rect, 1, border_radius=5)
                self.text(display, 18, "#dff9ff", True, (value_x, 212),
                          mono=True)
                if (pygame.time.get_ticks() // 450) % 2 == 0:
                    cx = value_x + surf.get_width() + 2
                    pygame.draw.line(sc, "#8be9ff", (cx, 214), (cx, 229), 2)
            else:
                self.text(self.view.get(key, ""), 18, "#8be9ff", True,
                          (value_x, 212), mono=True)

    # ------------------------------------------------------------------
    def _draw_mode_choices(self, sc):
        mode = self.state["mode"]
        mouse = pygame.mouse.get_pos()
        prefix_x, two_x, sep_x, single_x = self.mode_line
        y = 258
        self.text("CHORD CONTROL :", 9, "#5f8fa5", True, (prefix_x, y))
        self.text("|", 9, "#35576a", True, (sep_x, y))
        for key, label, x in (("f", "TWO-KEY", two_x),
                              ("s", "SINGLE FINGER", single_x)):
            active = mode == key
            hover = self.mode_hits[key].collidepoint(mouse)
            color = "#8be9ff" if active else (
                "#9fc7d8" if hover else "#5f8fa5")
            surf = self.text(label, 9, color, True, (x, y))
            if active:
                pygame.draw.line(sc, "#3fb6ff",
                                 (x, y + 12),
                                 (x + surf.get_width(), y + 12), 1)

    # ------------------------------------------------------------------
    @staticmethod
    def _list_back_rect():
        return pygame.Rect(573, 69, 22, 22)

    def _draw_list_overlay(self, sc):
        kind = self.list_overlay
        layout = self._list_layout(kind)
        box = layout[0]
        pygame.draw.rect(sc, "#071820", box, border_radius=6)
        pygame.draw.rect(sc, "#2f657b", box, 1, border_radius=6)
        title = "SELECT STYLE" if kind == "style" else "SELECT VOICE OR PLUGIN"
        self.text(title, 12, "#dff9ff", True, (62, 78))
        mouse = pygame.mouse.get_pos()
        back = self._list_back_rect()
        back_hot = back.collidepoint(mouse)
        back_fill = "#123442" if back_hot else "#0a202b"
        back_edge = "#3fb6ff" if back_hot else "#2f657b"
        back_ink = "#dff9ff" if back_hot else "#89b4c4"
        cx, cy = 584, 80
        pygame.draw.circle(sc, back_fill, (cx, cy), 8)
        pygame.draw.circle(sc, back_edge, (cx, cy), 8, 1)
        pygame.draw.line(sc, back_ink, (cx + 3, cy), (cx - 3, cy), 2)
        pygame.draw.line(sc, back_ink, (cx - 3, cy), (cx, cy - 3), 2)
        pygame.draw.line(sc, back_ink, (cx - 3, cy), (cx, cy + 3), 2)
        self.text("ESC TO CLOSE", 8, "#4f7385", True, (658, 80), right=True)
        pygame.draw.line(sc, "#1d4354", (60, 96), (660, 96), 1)
        current = (self.engine.style_index if kind == "style"
                   else self.engine.melody_voice)
        for rect, value, label in self.list_hits:
            active = value == current
            hover = rect.collidepoint(mouse)
            if active:
                fill, border = "#0c2d3b", "#3fb6ff"
            elif hover:
                fill, border = "#123442", "#2a647d"
            else:
                fill, border = "#0a202b", "#12303d"
            pygame.draw.rect(sc, fill, rect, border_radius=4)
            pygame.draw.rect(sc, border, rect, 1, border_radius=4)
            if active:
                pygame.draw.rect(sc, "#3fb6ff",
                                 (rect.x + 7, rect.y + 10, 3, rect.h - 20),
                                 border_radius=1)
            color = "#dff9ff" if active else (
                "#b9e6ff" if hover else "#89b4c4")
            size = 12 if kind == "style" else 13
            self.text(label, size, color, True,
                      (rect.x + 16, rect.y + (rect.h - 15) // 2))

        if self.list_rows > self.list_visible_rows:
            top = layout[2]
            card_h = layout[5]
            gap = layout[7]
            track_h = self.list_visible_rows * card_h + (
                self.list_visible_rows - 1) * gap
            track = pygame.Rect(box.right - 14, top, 4, track_h)
            pygame.draw.rect(sc, "#173340", track, border_radius=2)
            thumb_h = max(28, int(track_h * self.list_visible_rows
                                  / float(self.list_rows)))
            span = self.list_rows - self.list_visible_rows
            thumb_y = top + int((track_h - thumb_h) * self.list_scroll
                                / float(max(1, span)))
            pygame.draw.rect(sc, "#3fb6ff",
                             pygame.Rect(track.x, thumb_y, track.w, thumb_h),
                             border_radius=2)
            self.text("SCROLL", 7, "#4f7385", True,
                      (box.right - 20, box.bottom - 12), right=True)

    def _draw_plugin_manager(self, sc):
        box = self._plugin_box()
        sc.blit(self.scrim, (0, 0))
        pygame.draw.rect(sc, "#071820", box, border_radius=8)
        pygame.draw.rect(sc, "#2f657b", box, 1, border_radius=8)
        self.text("PLUGIN MANAGER", 14, "#dff9ff", True, (64, 76))
        mouse = pygame.mouse.get_pos()
        close = self._plugin_close_rect()
        close_hover = close.collidepoint(mouse)
        pygame.draw.rect(sc, "#123442" if close_hover else "#0a202b",
                         close, border_radius=4)
        pygame.draw.rect(sc, "#3fb6ff" if close_hover else "#2f657b",
                         close, 1, border_radius=4)
        self.text("CLOSE", 9, "#dff9ff", True, center=close.center)

        search = self._plugin_search_rect()
        pygame.draw.rect(sc, "#0b2430", search, border_radius=5)
        pygame.draw.rect(sc,
                         "#3fb6ff" if self.plugin_search_focus else "#2f657b",
                         search, 1, border_radius=5)
        if self.plugin_search:
            self.text(self.plugin_search, 13, "#dff9ff", False,
                      (search.x + 12, search.y + 10))
        else:
            self.text("Search installed Audio Unit instruments", 12,
                      "#5f8293", False, (search.x + 12, search.y + 11))
        if self.plugin_search_focus and int(time.time() * 2) % 2 == 0:
            text_width = self.text(self.plugin_search, 13, "#dff9ff").get_width()
            cursor_x = search.x + 12 + min(search.w - 20, text_width + 2)
            pygame.draw.line(sc, "#3fb6ff", (cursor_x, search.y + 8),
                             (cursor_x, search.bottom - 8), 1)
        self.text("%d INSTALLED  ·  %d ADDED" %
                  (self.plugin_total, self.plugin_added),
                  9, "#6f9aaa", True, (box.right - 16, 119), right=True)

        if not self.plugin_hits:
            message = ("No matching plugins" if self.plugin_search
                       else "No Audio Unit instruments found")
            self.text(message, 14, "#6f8b9b", True, (76, 180))

        for rect, edit, action, entry in self.plugin_hits:
            added = entry["added"]
            hover = rect.collidepoint(mouse)
            fill = "#0c2d3b" if added else (
                "#123442" if hover else "#0a202b")
            border = "#2f657b" if added else "#12303d"
            pygame.draw.rect(sc, fill, rect, border_radius=5)
            pygame.draw.rect(sc, border, rect, 1, border_radius=5)
            if added:
                pygame.draw.rect(sc, "#3fb6ff",
                                 (rect.x + 8, rect.y + 10, 3, rect.h - 20),
                                 border_radius=1)
            label = entry["label"]
            if len(label) > 44:
                label = label[:41] + "..."
            self.text(label, 13, "#dff9ff" if added else "#b9e6ff",
                      True, (rect.x + 20, rect.y + 18))
            self.text("ADDED" if added else "AVAILABLE", 8,
                      "#3fb6ff" if added else "#5f8293", True,
                      (rect.x + 380, rect.y + 22))
            if added:
                edit_hover = edit.collidepoint(mouse)
                pygame.draw.rect(sc, "#10465f" if not edit_hover else "#17617f",
                                 edit, border_radius=4)
                pygame.draw.rect(sc, "#3fb6ff", edit, 1, border_radius=4)
                self.text("EDIT", 9, "#ffffff", True, center=edit.center)
            action_hover = action.collidepoint(mouse)
            action_fill = ("#4a2d28" if added else "#10465f")
            if action_hover:
                action_fill = "#64382e" if added else "#17617f"
            pygame.draw.rect(sc, action_fill, action, border_radius=4)
            pygame.draw.rect(sc,
                             "#c86b52" if added else "#3fb6ff",
                             action, 1, border_radius=4)
            self.text("REMOVE" if added else "ADD", 9, "#ffffff", True,
                      center=action.center)

        if self.plugin_rows > 8:
            track = pygame.Rect(box.right - 10, 156, 4, 488)
            pygame.draw.rect(sc, "#173340", track, border_radius=2)
            thumb_h = max(30, int(track.h * 8 / float(self.plugin_rows)))
            span = self.plugin_rows - 8
            thumb_y = track.y + int((track.h - thumb_h) * self.plugin_scroll
                                    / float(max(1, span)))
            pygame.draw.rect(sc, "#3fb6ff",
                             pygame.Rect(track.x, thumb_y, track.w, thumb_h),
                             border_radius=2)
        self.text("Only added plugins appear in Voices.  Remove detaches a plugin from this app.",
                  10, "#5f8293", False, (64, box.bottom - 22))

    def _draw_plugin_editor(self, sc):
        self._refresh_plugin_editor_values()
        box = self._plugin_editor_box()
        pygame.draw.rect(sc, "#06141b", box, border_radius=10)
        pygame.draw.rect(sc, "#3fb6ff", box, 1, border_radius=10)
        title = self.plugin_editor_label or "PLUGIN"
        self.text("PLUGIN EDITOR", 14, "#dff9ff", True, (box.x + 24, box.y + 16))
        self.text(title, 12, "#3fb6ff", True, (box.x + 24, box.y + 40))
        self.text("THE PLUGIN'S OWN AUDIO UNIT PARAMETERS", 9, "#4f7385", True,
                  (box.right - 130, box.y + 30), right=True)
        mouse = pygame.mouse.get_pos()
        close = self._plugin_editor_close_rect()
        close_hover = close.collidepoint(mouse)
        pygame.draw.rect(sc, "#123442" if close_hover else "#0a202b",
                         close, border_radius=4)
        pygame.draw.rect(sc, "#3fb6ff" if close_hover else "#2f657b",
                         close, 1, border_radius=4)
        self.text("CLOSE", 9, "#dff9ff", True, center=close.center)
        pygame.draw.line(sc, "#123441",
                         (box.x + 24, box.y + 66), (box.right - 24, box.y + 66), 1)

        if not self.plugin_editor_params:
            self.text("This plugin exposes no automatable parameters.",
                      13, "#6f8b9b", True, (box.x + 24, box.y + 96))
            self.text("Its vendor interface has to be opened in the plugin's "
                      "own host application.", 11, "#5f8293", False,
                      (box.x + 24, box.y + 122))
            return

        for rect, track, position in self.plugin_editor_rows:
            param = self.plugin_editor_params[position]
            span = float(param["max"] - param["min"]) or 1.0
            ratio = (float(param["value"]) - param["min"]) / span
            ratio = max(0.0, min(1.0, ratio))
            self.text(param["name"], 12, "#dff9ff", True, (rect.x, rect.y + 6))
            self.text("#%d" % param["id"], 9, "#4f7385", False,
                      (rect.x, rect.y + 26))
            pygame.draw.rect(sc, "#123441", track, border_radius=3)
            filled = pygame.Rect(track.x, track.y, int(track.w * ratio),
                                 track.h)
            pygame.draw.rect(sc, "#3fb6ff", filled, border_radius=3)
            knob_x = track.x + int(track.w * ratio)
            pygame.draw.circle(sc, "#dff9ff", (knob_x, track.centery), 7)
            pygame.draw.circle(sc, "#3fb6ff", (knob_x, track.centery), 7, 2)
            self.text("%.2f" % param["value"], 13, "#dff9ff", True,
                      (track.right + 34, rect.y + 11))
            self.text("%.2f" % param["min"], 9, "#4f7385", False,
                      (track.x, rect.y + 28))
            self.text("%.2f" % param["max"], 9, "#4f7385", False,
                      (track.right, rect.y + 28), right=True)

        total = len(self.plugin_editor_params)
        if total > 9:
            self.text("%d PARAMETERS  ·  %d-%d" %
                      (total, self.plugin_editor_scroll + 1,
                       min(total, self.plugin_editor_scroll + 9)),
                      9, "#6f9aaa", True, (box.x + 24, box.bottom - 26))
        else:
            self.text("%d PARAMETERS" % total, 9, "#6f9aaa", True,
                      (box.x + 24, box.bottom - 26))
        self.text("Drag a slider to change the live plugin.  ESC closes.",
                  10, "#5f8293", False, (box.right - 24, box.bottom - 26),
                  right=True)

    # ------------------------------------------------------------------
    def draw(self):
        sc = self.screen
        sc.blit(self.bg, (0, 0))
        sc.blit(self.panel, (16, 16))
        content_right = KEY_X + self.key_w
        self.text("ARRANGER KEYBOARD", 15, "#d7dfeb", True, (40, 28))
        self.text("%d KEYS  \u00b7  %s \u2013 %s" % (
                      KEY_HIGH - KEY_LOW, note_label(KEY_LOW),
                      note_label(KEY_HIGH - 1)),
                  10, "#6d788b", True, (content_right, 33), right=True)

        sc.blit(self.lcd_img, (40, 62))
        self.text("STYLE", 9, "#5f8fa5", True, (58, 74))
        self.text(self.view.get("style", ""), 17, COL_LCD_TX, True, (58, 88))
        self.text("VOICES", 9, "#5f8fa5", True, (662, 74), right=True)
        self.text(self.view.get("voice", ""), 17, COL_LCD_TX, True,
                  (662, 88), right=True)
        self._draw_lcd_toggles(sc)
        self.text(self.view.get("chord", "\u2013"), 52, "#ffffff", True,
                  (58, 112))
        self.text(self.view.get("hint", ""), 10, COL_ACCENT, True, (58, 198))
        self._draw_lcd_params(sc)
        self.text(self.view.get("status", ""), 9, "#5f8fa5", True, (58, 244))
        self._draw_mode_choices(sc)
        self.text(self.view.get("section", "MAIN A"), 11, COL_LCD_TX, True,
                  (662, 244), right=True)
        for i in range(4):
            x = 448 + i * 20
            on = self.state["running"] and self.state["beat"] == i
            pygame.draw.circle(sc, "#7fd8ff" if on else "#18394d", (x, 262), 4)
        self.text("EFFECTS & MIXING", 10, "#7f8a9d", True, (700, 48))
        for knob in self.knobs:
            knob.draw(sc)
        self.text("ACCOMPANIMENT", 10, "#7f8a9d", True, (700, 130))
        self.text("SECTIONS", 10, "#7f8a9d", True, (700, 204))
        self._draw_song_sidebar(sc)
        self._draw_sidebar_toggle(sc)
        for b in self.buttons:
            if b is self.btn_save_song and not self.sidebar_open:
                continue
            b.draw(sc)

        self.draw_keys(sc)

        split_x = int(round(KEY_X + self.ww * self.split_slot))
        pygame.draw.line(sc, COL_ORANGE, (split_x, 550), (split_x, 556), 2)
        self.text("CHORD ZONE  \u00b7  %s \u2013 %s" % (
                      note_label(KEY_LOW), note_label(SPLIT - 1)),
                  10, "#ffb347", True, ((KEY_X + split_x) // 2, 558),
                  center=True)
        self.text("MELODY  \u00b7  %s \u2013 %s" % (
                      note_label(SPLIT), note_label(KEY_HIGH - 1)),
                  10, "#8b95a6", True,
                  ((split_x + KEY_X + self.key_w) // 2, 558), center=True)

        self.text("Left hand %s\u2013%s plays chords; right hand %s\u2013%s plays the melody.  "
                  "Two-key: root + next key = minor, +4 major, +10 7th, +11 maj7, +5 sus4, +9 6th."
                  % (note_label(KEY_LOW), note_label(SPLIT - 1),
                     note_label(SPLIT), note_label(KEY_HIGH - 1)),
                  12, "#98a2b3", False, (42, 582))
        self.text("Single Finger: root with a black key at its left = minor, a white key = 7th, "
                  "both = m7.  Three or more keys are read as a fingered chord.",
                  12, "#98a2b3", False, (42, 607))
        self.text("Computer keys: Z\u2013M chord zone \u00b7 Q\u2013P melody \u00b7 Space start/stop.  "
                  "Switch MAIN A/B while playing to trigger a fill.",
                  12, "#98a2b3", False, (42, 632))
        self.text("Knobs: drag vertically or scroll.  Tempo, volume and transpose: "
                  "drag the label up or down.",
                  11, "#98a2b3", False, (42, 656))
        self.text("Sustain knob: 0% cuts the note when you release a key, 100% rings "
                  "it out for eight seconds.",
                  11, "#98a2b3", False, (42, 672))

        pygame.draw.line(sc, "#2f3540", (40, 688), (content_right, 688))
        self.text("ARRANGER KEYBOARD  \u00b7  PYTHON EDITION", 9, "#525b6b",
                  True, (42, 700))
        self.text("SPLIT AT %s" % note_label(SPLIT), 9, "#525b6b", True,
                  (content_right - 2, 700), right=True)
        midi_surf = self.text(self.midi.status, 9, "#525b6b", True,
                              (510, 700), center=True)
        led = (510 - midi_surf.get_width() // 2 - 12, 705)
        if self.midi.device is not None:
            pygame.draw.circle(sc, "#1d5c3e", led, 5)
            pygame.draw.circle(sc, "#45e07d", led, 3)
        else:
            pygame.draw.circle(sc, "#2a4a5a", led, 3, 1)
        if self.list_overlay is not None:
            self._draw_list_overlay(sc)
        if self.plugin_manager_open:
            self._draw_plugin_manager(sc)
        if self.plugin_editor_open:
            self._draw_plugin_editor(sc)
        if self.style_editor_open:
            self._draw_style_editor(sc)
        if self.dialog is not None:
            self._draw_dialog(sc)

    def _draw_style_editor(self, sc):
        self.scrim.blit(self.bg, (0,0))
        sc.blit(self.scrim, (0,0))
        w, h = APP_W, APP_H
        x, y = 0, 0
        rect = pygame.Rect(x, y, w, h)
        pygame.draw.rect(sc, "#0f131a", rect)
        pygame.draw.rect(sc, "#3a3f4b", rect, 2)
        self.text("STYLE EDITOR", 20, COL_TX, True, (x+24, y+24))
        self.text("Name:", 14, COL_MUTED, True, (x+40, y+72))
        name_rect = pygame.Rect(x+120, y+60, 320, 28)
        pygame.draw.rect(sc, "#1b2028", name_rect, border_radius=4)
        pygame.draw.rect(sc, "#3a3f4b", name_rect, 1, border_radius=4)
        self.text(self.style_editor_data["name"], 16, COL_TX, True, (name_rect.x+8, name_rect.centery))
        self._style_editor_name_rect = name_rect
        # section buttons
        sections = ["intro","main_a","main_b","fill","ending"]
        sec_btns = []
        for i, sec in enumerate(sections):
            btn = pygame.Rect(x+40 + i*110, y+180, 100, 28)
            color = COL_ACCENT if sec == self.style_editor_section else "#2b2f39"
            pygame.draw.rect(sc, color, btn)
            pygame.draw.rect(sc, "#3a3f4b", btn, 1)
            self.text(sec.upper(), 12, "#000000" if sec == self.style_editor_section else COL_TX, True, (btn.centerx, btn.centery))
            sec_btns.append((btn, sec))
        self._style_editor_section_rects = sec_btns
        # instrument tabs
        instr_tabs = ["drums","bass"]
        instr_btns = []
        for i, tab in enumerate(instr_tabs):
            btn = pygame.Rect(x+40 + i*110, y+140, 100, 28)
            color = COL_ACCENT if tab == self.style_editor_instrument_tab else "#2b2f39"
            pygame.draw.rect(sc, color, btn)
            pygame.draw.rect(sc, "#3a3f4b", btn, 1)
            self.text(tab.upper(), 12, "#000000" if tab == self.style_editor_instrument_tab else COL_TX, True, (btn.centerx, btn.centery))
            instr_btns.append((btn, tab))
        self._style_editor_instrument_tab_rects = instr_btns
        # DAW-style multi-track grid
        grid_y_offset = 80
        grid_x = x+160
        grid_y = y+220 + grid_y_offset
        step_w = 36
        step_h = 28
        steps = self.style_editor_data["len"]
        sec_data = self.style_editor_data["sections"][self.style_editor_section]
        if self.style_editor_instrument_tab == "drums":
            # define tracks in DAW order
            tracks = [
                ("KICK","drums","kick"),
                ("SNARE","drums","snare"),
                ("HAT","drums","hat"),
                ("OPEN","drums","open"),
                ("CRASH","drums","crash"),
            ]
            # draw track labels
            for ti, (label, _, _) in enumerate(tracks):
                ty = grid_y + ti * (step_h + 4)
                self.text(label, 12, COL_TX, True, (x+40, ty + step_h//2))
            # draw grid for each track
            for ti, (label, group, instr) in enumerate(tracks):
                ty = grid_y + ti * (step_h + 4)
                # get pattern
                if group == "drums":
                    pat = sec_data.get("drums", {}).get(instr, "")
                elif instr == "bass":
                    bass_list = sec_data.get("bass", [])
                    pat_chars = ["." for _ in range(steps)]
                    for pos, note, length in bass_list:
                        for s in range(int(length)):
                            idx = (pos + s) % steps
                            pat_chars[idx] = "x"
                    pat = "".join(pat_chars)
                else:
                    pat = sec_data.get(instr, "")
                if len(pat) < steps:
                    pat = pat.ljust(steps, ".")
                for i in range(steps):
                    r = pygame.Rect(grid_x + i*step_w, ty, step_w-4, step_h-4)
                    active = i < len(pat) and pat[i] == "x"
                    is_selected = instr == self.style_editor_instrument
                    fill = "#3fb6ff" if active else "#2b2f39"
                    if is_selected:
                        pygame.draw.rect(sc, "#5a6a80", r, 2)
                    pygame.draw.rect(sc, fill, r)
                    pygame.draw.rect(sc, "#3a3f4b", r, 1)
                    if ti == 0:
                        self.text(str(i+1), 9, COL_MUTED, True, (r.centerx, r.centery))
            num_tracks = len(tracks)
            grid_h = num_tracks * (step_h + 4) - 4
            self._style_editor_grid_rect = pygame.Rect(grid_x, grid_y, step_w*steps, grid_h)
            self._style_editor_grid_origin = (grid_x, grid_y, step_w, step_h)
            # visual playback indicator
            if self.style_editor_open:
                ticks = pygame.time.get_ticks()
                play_step = (ticks // 400) % steps
                line_x = grid_x + play_step * step_w + step_w // 2
                for ti in range(len(tracks)):
                    ty = grid_y + ti * (step_h + 4)
                    pygame.draw.line(sc, COL_ACCENT, (line_x, ty-4), (line_x, ty+step_h+4), 2)
        elif self.style_editor_instrument_tab == "bass":
            # piano roll for bass
            note_count = 16
            note_h = 24
            step_w = 36
            grid_w = step_w * steps
            grid_h = note_h * note_count
            # note names starting C2 = MIDI 36
            note_names = ["C2","C#2","D2","D#2","E2","F2","F#2","G2","G#2","A2","A#2","B2","C3","C#3","D3","D#3"]
            # draw note labels
            for n in range(note_count):
                ny = grid_y + n * note_h
                name = note_names[n] if n < len(note_names) else str(n)
                self.text(name, 10, COL_MUTED, True, (x+40, ny + note_h//2))
            # draw grid
            for i in range(steps):
                for n in range(note_count):
                    r = pygame.Rect(grid_x + i*step_w, grid_y + n*note_h, step_w-4, note_h-4)
                    pygame.draw.rect(sc, "#2b2f39", r)
                    pygame.draw.rect(sc, "#3a3f4b", r, 1)
                    # check note presence
                    bass_list = sec_data.get("bass", [])
                    midi_note = 36 + n
                    # simple toggle by pos/note
                    active = any(pos == i and note == midi_note for pos, note, length in bass_list if length > 0)
                    if active:
                        pygame.draw.rect(sc, "#3fb6ff", r)
            self._style_editor_grid_rect = pygame.Rect(grid_x, grid_y, grid_w, grid_h)
            self._style_editor_grid_origin = (grid_x, grid_y, step_w, note_h)
            # playback line
            if self.style_editor_open:
                ticks = pygame.time.get_ticks()
                play_step = (ticks // 400) % steps
                line_x = grid_x + play_step * step_w + step_w // 2
                pygame.draw.line(sc, COL_ACCENT, (line_x, grid_y-4), (line_x, grid_y+grid_h+4), 2)
        self.text("Click steps to toggle. SAVE stores to user styles library.", 12, COL_MUTED, False, (x+40, y+260))
        test_rect = pygame.Rect(x+320, y+h-64, 110, 36)
        save_rect = pygame.Rect(x+560, y+h-64, 110, 36)
        cancel_rect = pygame.Rect(x+680, y+h-64, 110, 36)
        delete_rect = pygame.Rect(x+440, y+h-64, 110, 36)
        pygame.draw.rect(sc, "#2b6a5a", test_rect)
        pygame.draw.rect(sc, COL_ACCENT, save_rect)
        pygame.draw.rect(sc, "#3a3f4b", cancel_rect)
        pygame.draw.rect(sc, "#5a1a1a", delete_rect)
        self.text("TEST", 14, "#cfffec", True, (test_rect.centerx, test_rect.centery))
        self.text("SAVE", 14, "#000000", True, (save_rect.centerx, save_rect.centery))
        self.text("CANCEL", 14, COL_TX, True, (cancel_rect.centerx, cancel_rect.centery))
        self.text("DELETE", 14, "#ff6b6b", True, (delete_rect.centerx, delete_rect.centery))
        self._style_editor_test_rect = test_rect
        self._style_editor_save_rect = save_rect
        self._style_editor_cancel_rect = cancel_rect
        self._style_editor_delete_rect = delete_rect

    def draw_keys(self, sc):
        x, y = KEY_X, KEY_Y
        for m, slot in self.white_notes:
            left = int(round(x + slot * self.ww))
            surf = self.key_surfs[m][1 if m in self.pressed else 0]
            sc.blit(surf, (left, y))
        for m, wi in self.black_notes:
            left = int(round(x + wi * self.ww - self.bw / 2.0))
            surf = self.key_surfs[m][1 if m in self.pressed else 0]
            sc.blit(surf, (left, y))

    # ------------------------------------------------------------------
    def loop(self):
        while self.running:
            now = pygame.time.get_ticks() / 1000.0
            for e in pygame.event.get():
                self._event(e)
            self.midi.poll()
            pos = pygame.mouse.get_pos()
            for b in self.buttons:
                b.hover = b.rect.collidepoint(pos)
            for knob in self.knobs:
                knob.hover = knob.rect.collidepoint(pos)
            if now - self.last_ui >= 0.03:
                self._update_ui()
                self.last_ui = now
            self.draw()
            pygame.display.flip()
            self.clock.tick(60)
        self.close()

    def close(self):
        try:
            self.midi.close()
            self.engine.shutdown()
            self.synth.all_notes_off()
            self.synth.shutdown()
        finally:
            pygame.quit()


def main():
    shot = None
    for arg in sys.argv[1:]:
        if arg.startswith("--screenshot"):
            if "=" in arg:
                shot = arg.split("=", 1)[1]
            else:
                shot = "/tmp/arranger_preview.png"
    plugin_list = "--plugins" in sys.argv
    plugin_smoke = "--plugin-smoke" in sys.argv
    plugin_editor = "--plugin-editor" in sys.argv
    plugin_manager = "--plugin-manager" in sys.argv
    if shot or plugin_list or plugin_smoke or plugin_editor or plugin_manager:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    app = App()
    if plugin_manager:
        added_here = False
        entries = [entry for entry in app.synth.plugin_entries()
                   if entry["added"]]
        if not entries:
            entries = app.synth.plugin_entries()
            if entries:
                app.synth.add_instrument(entries[0]["key"])
                app.engine.refresh_voices()
                added_here = True
        app._open_plugin_manager()
        app.draw()
        pygame.display.flip()
        target = shot or "/tmp/arranger_plugin_manager.png"
        pygame.image.save(app.screen, target)
        print("saved", target)
        if added_here:
            app.synth.remove_instrument(entries[0]["key"])
        app.close()
        return
    if plugin_editor:
        entries = [entry for entry in app.synth.plugin_entries()
                   if entry["added"]]
        added_here = False
        if not entries:
            entries = app.synth.plugin_entries()
            if entries:
                app.synth.add_instrument(entries[0]["key"])
                app.engine.refresh_voices()
                entries = [entries[0]]
                added_here = True
        if entries:
            entry = entries[0]
            key = entry["key"]
            app.synth.ensure_instrument(key)
            deadline = time.time() + 8.0
            while (time.time() < deadline
                   and not app.synth.instrument_ready(key)):
                time.sleep(0.05)
            app._open_plugin_editor(key, entry["label"])
            params = app.plugin_editor_params
            print("PLUGIN_EDITOR %s | %d parameters" % (key, len(params)))
            for param in params[:6]:
                before = param["value"]
                app.synth.set_plugin_parameter(key, param["id"],
                                               param["min"] +
                                               (param["max"] - param["min"]) * 0.5)
                after = app.synth.plugin_parameter_value(key, param["id"])
                print("  PARAM %s id=%s range=%s..%s before=%s after=%s" %
                      (param["name"], param["id"], param["min"], param["max"],
                       before, after))
            app.draw()
            pygame.display.flip()
            target = shot or "/tmp/arranger_plugin_editor.png"
            pygame.image.save(app.screen, target)
            print("saved", target)
            if added_here:
                app.synth.remove_instrument(key)
        app.close()
        return
    if plugin_list or plugin_smoke:
        entries = app.synth.plugin_entries()
        for entry in entries:
            print("PLUGIN %s | %s | %s" %
                  (entry["key"], entry["label"],
                   "added" if entry["added"] else "available"))
        if plugin_smoke and entries:
            entry = next((entry for entry in entries
                          if entry["label"].startswith("FL Studio")),
                         entries[0])
            key = entry["key"]
            was_added = entry["added"]
            if not was_added:
                app.synth.add_instrument(key)
                app.engine.refresh_voices()
            try:
                app.engine.set_voice(key)
                deadline = time.time() + 8.0
                while (time.time() < deadline
                       and not app.synth.instrument_ready(key)):
                    time.sleep(0.05)
                ready = app.synth.instrument_ready(key)
                if ready:
                    app.synth.play_live(key, 60, 0.7)
                    time.sleep(0.2)
                    app.synth.stop_live(60)
                print("PLUGIN_SMOKE %s %s" %
                      (key, "ready" if ready else "failed"))
            finally:
                if not was_added:
                    app.synth.remove_instrument(key)
        app.close()
        return
    if shot:
        app.draw()
        pygame.display.flip()
        pygame.image.save(app.screen, shot)
        print("saved", shot)
        app.close()
        return
    app.loop()


if __name__ == "__main__":
    main()
