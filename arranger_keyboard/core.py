"""Musical constants, chord tables and style patterns."""

SR = 44100
SPLIT = 48
KEY_LOW = 29
KEY_HIGH = 84

NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()
BLACK_PCS = (1, 3, 6, 8, 10)

# name -> (display suffix, intervals from root)
CHORD_TYPES = {
    "M": ("", (0, 4, 7)),
    "m": ("m", (0, 3, 7)),
    "7": ("7", (0, 4, 7, 10)),
    "m7": ("m7", (0, 3, 7, 10)),
    "maj7": ("maj7", (0, 4, 7, 11)),
    "dim": ("dim", (0, 3, 6)),
    "aug": ("aug", (0, 4, 8)),
    "sus4": ("sus4", (0, 5, 7)),
    "sus2": ("sus2", (0, 2, 7)),
    "6": ("6", (0, 4, 7, 9)),
    "dim7": ("dim7", (0, 3, 6, 9)),
}

TWO_KEY = {1: "m", 2: "sus2", 4: "M", 5: "sus4", 6: "dim",
           7: "M", 8: "aug", 9: "6", 10: "7", 11: "maj7"}

STYLES = [
    {
        "name": "8Beat Pop", "bpm": 110, "len": 16, "comp_voice": "piano",
        "comp_len": 2,
        "kick": "x.......x.x.....",
        "snare": "....x.......x...",
        "hat": "x.x.x.x.x.x.x.x.",
        "bass": [[0, 0, 3], [4, 0, 1], [6, 7, 2], [8, 0, 3],
                 [12, 0, 2], [14, "T", 2]],
        "comp": "x.....x...x.x...",
        "B": {"hat": "xxxxxxxxxxxxxxxx",
              "kick": "x.....x.x.x...x.",
              "comp": "x..x..x.x.x.x.x."},
    },
    {
        "name": "Dance Pop", "bpm": 124, "len": 16, "comp_voice": "lead",
        "comp_len": 1,
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "hat": "x.x.x.x.x.x.x.x.",
        "open": "..x...x...x...x.",
        "bass": [[2, 0, 1], [6, 0, 1], [10, 0, 1], [14, 12, 1]],
        "comp": ".x....x...x...x.",
        "B": {"hat": "xxxxxxxxxxxxxxxx",
              "comp": ".x.x..x..x.x..x.",
              "bass": [[0, 0, 1], [2, 0, 1], [4, 12, 1], [6, 0, 1],
                       [8, 0, 1], [10, 0, 1], [12, 12, 1], [14, 7, 1]]},
    },
    {
        "name": "Rock", "bpm": 120, "len": 16, "comp_voice": "guitar",
        "comp_len": 8,
        "kick": "x.....x.x.......",
        "snare": "....x.......x...",
        "hat": "x.x.x.x.x.x.x.x.",
        "bass": [[0, 0, 2], [2, 0, 2], [4, 0, 2], [6, 0, 2],
                 [8, 0, 2], [10, 0, 2], [12, 0, 2], [14, 0, 2]],
        "comp": "x...............",
        "B": {"open": "x...............",
              "comp": "x...x...x...x...",
              "comp_voice": "palm", "comp_len": 2,
              "kick": "x.....x.x.x...x."},
    },
    {
        "name": "Bossa Nova", "bpm": 125, "len": 16, "comp_voice": "piano",
        "comp_len": 2, "snare_vel": 0.35,
        "kick": "x..x....x..x....",
        "snare": "..x..x...x..x...",
        "hat": "x.x.x.x.x.x.x.x.",
        "bass": [[0, 0, 3], [6, 7, 3], [8, 0, 3], [14, 7, 2]],
        "comp": "x..x..x...x..x..",
        "B": {"comp": "x..x..x.x.x..x..",
              "hat": "xxxxxxxxxxxxxxxx"},
    },
    {
        "name": "Waltz", "bpm": 100, "len": 12, "comp_voice": "strings",
        "comp_len": 2,
        "kick": "x...........",
        "snare": "....x...x...",
        "hat": "x.x.x.x.x.x.",
        "bass": [[0, 0, 4], [4, 7, 2], [8, "T", 2]],
        "comp": "....x...x...",
        "B": {"kick": "x.....x.....",
              "hat": "xxxxxxxxxxxx"},
    },
    {
        "name": "Pop Ballad", "bpm": 72, "len": 16, "comp_voice": "piano",
        "comp_len": 8, "snare_vel": 0.7,
        "kick": "x.......x.......",
        "snare": "........x.......",
        "hat": "x...x...x...x...",
        "bass": [[0, 0, 6], [8, 7, 6], [14, 0, 2]],
        "comp": "x.......x.......",
        "piano_notes": [[8, 0, 2], [10, 1, 2], [12, 2, 2], [14, 3, 2]],
        "B": {"hat": "x.x.x.x.x.x.x.x.",
              "kick": "x.......x.....x.",
              "comp": "x.......x...x..."},
    },
    {
        "name": "Slow Go Go", "bpm": 66, "len": 16,
        "comp_voice": "piano", "comp_len": 8, "comp_vel": 0.08,
        "snare_vel": 0.42,
        "kick": "x.......x.......",
        "snare": "....x.......x...",
        "hat": "x...x...x...x...",
        "bass": [[0, 0, 8], [8, 7, 8]],
        "comp": "x.......x.......",
        "pad": "x...............", "pad_voice": "strings",
        "pad_len": 16, "pad_vel": 0.09,
        "B": {"kick": "x.......x.....x.",
              "hat": "x.x.x.x.x.x.x.x.",
              "snare_vel": 0.5,
              "bass": [[0, 0, 4], [4, 7, 4], [8, 0, 4], [12, 7, 4]],
              "comp": "x.......x.......",
              "pad_vel": 0.10},
    },
    {
        "name": "Country", "bpm": 104, "len": 16,
        "comp_voice": "classgtr", "comp_len": 4, "comp_vel": 0.16,
        "snare_vel": 0.65,
        "kick": "x.......x.......",
        "snare": "....x.......x...",
        "hat": "x.x.x.x.x.x.x.x.",
        "bass": [[0, 0, 4], [4, 7, 2], [8, 0, 4], [12, 7, 2]],
        "comp": "x...x...x...x...",
        "B": {"kick": "x.....x.x.....x.",
              "hat": "xxxxxxxxxxxxxxxx",
              "snare": "....x.......x..x",
              "bass": [[0, 0, 2], [2, 7, 2], [4, 0, 2], [6, 7, 2],
                       [8, 0, 2], [10, 7, 2], [12, 0, 2], [14, 7, 2]],
              "comp": "x...x...x...x..."},
    },
]

VOICES = [("piano", "Piano"), ("ep", "E. Piano"), ("strings", "Strings"),
          ("organ", "Organ"), ("lead", "Synth Lead"),
          ("leadgtr", "Lead Guitar"),
          ("classgtr", "Classical Guitar")]

KEYMAP = {}
for _i, _k in enumerate("zsxdcvgbhnjm"):
    KEYMAP[_k] = 36 + _i
for _i, _k in enumerate("q2w3er5t6y7ui9o0p"):
    KEYMAP[_k] = 48 + _i

# ---- appearance -------------------------------------------------------
APP_W, APP_H = 1240, 770
COL_BG = "#15171c"
COL_PANEL_TOP = "#2b2f39"
COL_PANEL_BOT = "#1d2027"
COL_EDGE = "#3a3f4b"
COL_TX = "#e7ecf3"
COL_MUTED = "#9aa4b5"
COL_ACCENT = "#3fb6ff"
COL_LCD = "#0b1a24"
COL_LCD_TX = "#7fd8ff"
COL_ORANGE = "#ff9900"

# ---- user styles -------------------------------------------------------
from pathlib import Path
import json
USER_STYLES_PATH = Path.home() / "Library" / "Application Support" / "Arranger Keyboard" / "user_styles.json"

def load_user_styles():
    try:
        if USER_STYLES_PATH.exists():
            with open(USER_STYLES_PATH, "r") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []

def save_user_styles(styles):
    try:
        USER_STYLES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USER_STYLES_PATH, "w") as f:
            json.dump(styles, f, indent=2)
        return True
    except Exception:
        return False

# Normalized style schema: sections with instruments
# Sections: intro, main_a, main_b, fill, ending
# Instruments: drums.kick/snare/hat/openhat/crash, bass, comp, pad, piano
NORMALIZED_SCHEMA_VERSION = 1
