# Arranger Keyboard

A desktop arranger keyboard app for macOS built with Python + Pygame. Play styles in real time with chords and drum patterns, and edit styles with a lightweight Drums + Bass editor.

## What it does

* Play arranger styles: Main A / Main B switching, INTRO button, and automatic fill detection.
* Live chord accompaniment via two-key root + quality mapping, with selectable comp voice and length.
* Drum patterns: KICK, SNARE, HAT, OPEN, CRASH step sequencer per section.
* Bass editor: 16-step piano roll with MIDI note labels C2 – D#3, editable per section.
* Style editor: rename, delete, save user styles to `~/Library/Application Support/Arranger Keyboard/user_styles.json`.
* TEST / SAVE workflow in editor to preview changes without affecting the current set.
* Built-in styles: 8Beat Pop, Dance Pop, Rock, Bossa Nova, Waltz, Pop Ballad, Slow Go Go, Country.

## About

Arranger Keyboard is a compact, offline arranger workstation focused on quick song sketching and live play. It models the classic arranger workflow: select a style, play chords, trigger intro/fills, and switch between Main A and Main B variations. The style editor lets you tweak drum steps and bass lines directly, saving custom styles for reuse.

The current prototype focuses on a Drums + Bass editing experience. Comp, Pad, Piano tracks are intentionally left out of the prototype to keep the UI lean and fast.

## Build

Requires Python 3.9+ and PyInstaller.

```bash
bash build_macos.sh
```

Output: `dist/Arranger Keyboard.app`

Launch:

```bash
python desktop_launcher.py
```

## Project structure

* `arranger_keyboard/core.py` – styles, user styles path, load/save helpers
* `arranger_keyboard/engine.py` – playback engine, section switching, style selection
* `arranger_keyboard/pgui.py` – UI, style editor, drums + bass piano roll
* `desktop_launcher.py` – app entry point
* `assets/` – app icons

## License

Private / prototype.
