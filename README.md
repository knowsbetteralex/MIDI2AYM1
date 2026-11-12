# MIDI2AYM1 — SpaceCan AY music converter

Desktop **MIDI → AYM1 v1** converter for the ZX Spectrum 48K **SpaceCan** AY player. It runs on a PC, not on the Spectrum. The 3-channel preview synthesizes audio from the actual 50 Hz AY register stream, **not MIDI instruments or a SoundFont**. It approximates AY square/noise generators and nonlinear output levels; real hardware may sound different.

Source specification: [SpaceCan AYM1 format](https://github.com/knowsbetteralex/SpaceCan/blob/main/docs/aym1_format.md) and [MIDI conversion spec](https://github.com/knowsbetteralex/SpaceCan/blob/main/docs/midi_to_aym1_spec.md).

## Start

Install Python 3.10+ (Tkinter included with Windows Python; on Linux also install `python3-tk`), then:

```bash
python -m pip install -r requirements.txt
python -m midi2aym1
```

### VS Code tasks

Open the project folder in VS Code, then use **Terminal → Run Task**:

- **MIDI2AYM1: Setup** — first-time setup: create `.venv` with Python 3 and install dependencies. On Windows this uses the Python launcher (`py`); on Linux/macOS it uses `python3`.
- **MIDI2AYM1: Run** — launch the desktop app with the project's `.venv`. This is also the default build task: **Ctrl+Shift+B** on Windows/Linux, **Cmd+Shift+B** on macOS.
- **MIDI2AYM1: Test** — run the regression suite in the same environment.
- **MIDI2AYM1: Build EXE** — build the standalone Windows desktop executable; see below.

Python 3.10+ must already be installed. On Linux, install `python3-venv` and `python3-tk` if your distribution packages them separately. No Python extension or environment activation is required. Run Setup again when dependencies change; normal launches do not install packages.

Task definitions: [`.vscode/tasks.json`](.vscode/tasks.json). See also the [VS Code task documentation](https://code.visualstudio.com/docs/debugtest/tasks).

### Windows EXE

On Windows, run **Terminal → Run Task → MIDI2AYM1: Build EXE**. Run **Setup** first if `.venv` has not been created. The build task automatically installs `requirements-build.txt`, then packages the application with PyInstaller.

The result is **`dist/MIDI2AYM1.exe`**: one GUI executable with Python, Tkinter, pygame audio libraries and all built-in presets included. Copy that EXE to another Windows PC to run the converter without installing Python or copying the source/preset directories. The build targets the architecture of the Python used to build it (normally Windows x64).

`build/` contains generated specifications and intermediate files; `dist/` contains the distributable. Both directories are ignored by Git. Close any running copy of `MIDI2AYM1.exe` before rebuilding it. The normal **Ctrl+Shift+B** shortcut still launches the Python app; select **Build EXE** from Run Task to package it.

This is the desktop build without a console window. For terminal output in batch conversions, use `python -m midi2aym1` as described below. Packaging options are documented in the [PyInstaller manual](https://pyinstaller.org/en/stable/usage.html).

### Desktop studio

The desktop app implements the [studio concept](docs/ui-concept.svg) with a dark Tkinter/ttk theme, three colour-coded AY voice cards with live ADSR diagrams, tabs for voices/drums/MIDI priorities, and a conversion report. Editors scroll and rearrange as the window narrows; the action bar stays visible. No new dependencies are required. The [original PNG mockup](docs/ui-concept.png) and [design notes](docs/ui-concept.md) are included as references.

Search GM drums by note or name, adjust all 16 MIDI channel priorities individually, and inspect lost notes and warnings with **Open report**. Field errors appear inline. Edits survive switching RU/EN, mark previous reports as stale and stop the current preview. Listen/export always convert the current settings; **Stop** also cancels a pending preview while processing finishes.

The source summary distinguishes **MIDI channels**, **tracks containing notes**, and **total SMF tracks**. Tempo/time-signature tracks are included in the total but add no voices. Three MIDI channels can still contain chords, overlapping drum hits and AY release tails that exceed the chip's three simultaneous voices.

Choose a built-in preset from **Sound preset** at the top of the left sidebar. Presets are embedded in the Python package and work without external JSON files:

| Preset | CLI ID | Sound |
| --- | --- | --- |
| Default | `default` | Full drums and the original melodic envelopes. |
| Melody first | `tight-melody` | Zero release, 40 ms decay; MIDI channels 1/2 have priority over drums. |
| Compact drums + flat tones | `compact-drums` | Compact drums; all three tonal voices have zero attack/decay/release/vibrato and constant 0.8 sustain. |
| Pulses + flat tones | `pulse-drums` | Single-frame drum pulses and the same flat tonal voices. |
| Flat tones only | `tones-only` | Drums off and flat tonal voices. |

Built-in selection preserves the current AY clock and loop option. Sound edits mark the preset as modified; selecting it again restores its settings. **Load preset / Save preset** still import/export user JSON settings, including clock and loop. The JSON files in [`presets/`](presets/) are optional examples.

GUI features:

- Open MIDI type 0/1, convert to AYM1, ▶ listen / ■ stop, export `.aym`, `.report.json`, optional `.wav` and `.frames.csv`.
- AY target clock presets **2,000,000 Hz** (Delta-C) / **1,773,400 Hz** (ZX 128K); loop and overflow policy.
- **Three independent voice settings** (gain, attack/decay/sustain/release, vibrato) and source MIDI channel priorities.
- **MIDI channel 10 General MIDI drum map**: GM notes **35–81** keep their note identities (kick on 35/36, snare 38/40, hats 42/44/46, etc.). Customize each sound's noise period, tone sweep, duration, level, and priority. Select a drum and press ▶ Drum.
- On the **GM drums** tab, choose **Full / Compact / Pulses / Off**. Full uses the original kit. Compact limits closed hats to 20 ms, other noise hits to 40 ms and tonal hits to 60 ms, with up to two volume/tone steps. Pulses uses one 20 ms frame per hit with constant level and tone. Both simplified modes play one drum hit at a time: a new hit chokes the previous tail, and simultaneous hits use kit priorities. Rejected or interrupted hits appear in the loss report. Shorter user-defined durations are respected at the 20 ms frame resolution.
- Load/save all settings and drum kits as JSON presets. Russian and English interface; Help explains the essentials in six lines.

The built-in **Compact drums + flat tones** preset combines Compact percussion with flat melodic envelopes and priorities for MIDI channels 1/2. As a measured example, the tested 154 BPM `midiver.mid` arrangement with the same flat melodic settings produced 10,504 bytes in Full, 5,656 in Compact, 4,638 in Pulses and 1,851 with drums Off. These are sound simplifications; the tempo and MIDI note positions are retained, while drum tails and simultaneous hits are reduced.

The AY has **only three sound channels**; drums must share those channels and the noise generator R6. When 4+ notes overlap, `steal` selects louder/higher-priority notes or `drop` preserves currently allocated voices. The JSON report counts losses. The converter does **not** promise to reproduce unrestricted MIDI orchestration, but its preview is based on its export.

## CLI / batch conversion

```bash
python -m midi2aym1 input.mid -o track.aym --clock 2000000 --loop --overflow steal --percussion noise --wav --csv
python -m midi2aym1 input.mid --preset my_kit.json --wav
python -m midi2aym1 input.mid --builtin-preset compact-drums --wav
python -m midi2aym1 input.mid --builtin-preset pulse-drums --clock 1773400 --loop
```

Configuration: `--clock`, `--loop`/`--no-loop`, `--overflow {steal,drop}`, `--percussion {noise,compact,pulse,off}`, `--preset file.json` or `--builtin-preset ID`, `--wav`, `--csv`. `noise` is the original Full kit. CLI options override matching preset settings; choose one preset source. A full AYM1 blob is emitted, ready for:

```asm
AY_MUSIC_GAME:
    INCBIN "assets/music/track.aym"
```

## Format / conversion notes

- Time map handles `set_tempo` changes; notes are quantized **once** to 50 Hz, not by rounding each MIDI delta.
- MIDI event order is preserved inside each quantized frame. A short note's `note_on` and `note_off` can land in the same 20 ms frame; reversing them would leave the note stuck.
- Matching repeated notes use FIFO by MIDI channel/pitch. Overlapping notes, CC64 sustain, CC7 volume, CC11 expression, CC120/123, program changes and pitch bend (±2 semitones) are parsed. Software ADSR avoids hardware envelope sharing problems.
- GM drums have tone/noise presets rather than an impossible independent fourth AY voice; kick is a short falling tone, hats are sharp noise, snares use tone plus noise.
- `AYM1`: 16-byte LE header; FRAME `0x80 + mask16 + changed R0..R13 bytes`, WAIT 1..127, END 0. R7 GPIO bits cleared; hardware R13 unused by default. Full size ≤65535 bytes. Loop offset points to first payload command.
- Actual output WAV uses the *decoded* output frames and a software AY square/noise emulation. It is indicative, not bit-accurate AY/YM hardware emulation. Timing resolution remains 20 ms.
- Preview playback keeps the rendered PCM format fixed at **22,050 Hz, signed 16-bit mono**. SDL converts for the audio device internally; automatic changes to the mixer's rate/channel count would otherwise change the speed of a raw PCM buffer.

## Validation

```bash
python -m unittest discover -s tests -v
```

Covers C/E/G chord, 4-note overflow, staggered overlaps, repeated-note FIFO, sustain pedal, GM drums, tempo map, WAV, AYM1 roundtrip/WAIT/loop. For final validation, play the exported binary with `ay_player.asm` on a ZX 48K emulator and Delta-C hardware. A valid AYM1 file does not guarantee it will fit beside all game assets in the machine's 64K address space.

GUI regression tests also cover language changes with unfinished edits, preset roundtrips, field validation, drum selection, stale reports, asynchronous export, playback cancellation and small-window scrolling. They require Tk and a display; on headless Linux run under a virtual display (for example `xvfb-run -a python -m unittest discover -s tests -v`). Audio playback is mocked in these tests.

Separate playback tests use a real pygame mixer with SDL's silent `dummy` driver to check that PCM, WAV and preview durations agree, including when the mixer was previously initialized with a different sample rate or channel count. MIDI timing tests cover 60/90/120/240 BPM and different PPQ resolutions.

Compact percussion tests cover one-frame pulses, two-step tones, simultaneous-hit priorities, choking without tail resumption, muted hits, user durations, stable melodic voices, codec roundtrips and GUI/CLI/preset mode selection.

Built-in preset tests check independence from external JSON files, isolated settings snapshots, flat tonal output, CLI overrides, GUI target preservation, modified markers, localization and continued JSON import/export.
