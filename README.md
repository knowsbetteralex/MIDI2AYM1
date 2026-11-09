# MIDI2AYM1 — SpaceCan AY music converter

Desktop **MIDI → AYM1 v1** converter for the ZX Spectrum 48K **SpaceCan** AY player. It runs on a PC, not on the Spectrum. The 3-channel preview synthesizes audio from the actual 50 Hz AY register stream, **not MIDI instruments or a SoundFont**. It approximates AY square/noise generators and nonlinear output levels; real hardware may sound different.

Source specification: [SpaceCan AYM1 format](https://github.com/knowsbetteralex/SpaceCan/blob/main/docs/aym1_format.md) and [MIDI conversion spec](https://github.com/knowsbetteralex/SpaceCan/blob/main/docs/midi_to_aym1_spec.md).

## Start

Install Python 3.10+ (Tkinter included with Windows Python; on Linux also install `python3-tk`), then:

```bash
python -m pip install -r requirements.txt
python -m midi2aym1
```

GUI features:

- Open MIDI type 0/1, convert to AYM1, ▶ listen / ■ stop, export `.aym`, `.report.json`, optional `.wav` and `.frames.csv`.
- AY target clock presets **2,000,000 Hz** (Delta-C) / **1,773,400 Hz** (ZX 128K); loop and overflow policy.
- **Three independent voice settings** (gain, attack/decay/sustain/release, vibrato) and source MIDI channel priorities.
- **MIDI channel 10 General MIDI drum map**: GM notes **35–81** keep their note identities (kick on 35/36, snare 38/40, hats 42/44/46, etc.). Customize each sound's noise period, tone sweep, duration, level, and priority. Select a drum and press ▶ Drum.
- Load/save all settings and drum kits as JSON presets. Russian and English interface; Help explains the essentials in six lines.

The AY has **only three sound channels**; drums must share those channels and the noise generator R6. When 4+ notes overlap, `steal` selects louder/higher-priority notes or `drop` preserves currently allocated voices. The JSON report counts losses. The converter does **not** promise to reproduce unrestricted MIDI orchestration, but its preview is based on its export.

## CLI / batch conversion

```bash
python -m midi2aym1 input.mid -o track.aym --clock 2000000 --loop --overflow steal --percussion noise --wav --csv
python -m midi2aym1 input.mid --preset my_kit.json --wav
```

Configuration: `--clock`, `--loop`, `--overflow {steal,drop}`, `--percussion {noise,off}`, `--preset file.json`, `--wav`, `--csv`. CLI options override matching preset settings. A full AYM1 blob is emitted, ready for:

```asm
AY_MUSIC_GAME:
    INCBIN "assets/music/track.aym"
```

## Format / conversion notes

- Time map handles `set_tempo` changes; notes are quantized **once** to 50 Hz, not by rounding each MIDI delta.
- Matching repeated notes use FIFO by MIDI channel/pitch. Overlapping notes, CC64 sustain, CC7 volume, CC11 expression, CC120/123, program changes and pitch bend (±2 semitones) are parsed. Software ADSR avoids hardware envelope sharing problems.
- GM drums have tone/noise presets rather than an impossible independent fourth AY voice; kick is a short falling tone, hats are sharp noise, snares use tone plus noise.
- `AYM1`: 16-byte LE header; FRAME `0x80 + mask16 + changed R0..R13 bytes`, WAIT 1..127, END 0. R7 GPIO bits cleared; hardware R13 unused by default. Full size ≤65535 bytes. Loop offset points to first payload command.
- Actual output WAV uses the *decoded* output frames and a software AY square/noise emulation. It is indicative, not bit-accurate AY/YM hardware emulation. Timing resolution remains 20 ms.

## Validation

```bash
python -m unittest discover -s tests -v
```

Covers C/E/G chord, 4-note overflow, staggered overlaps, repeated-note FIFO, sustain pedal, GM drums, tempo map, WAV, AYM1 roundtrip/WAIT/loop. For final validation, play the exported binary with `ay_player.asm` on a ZX 48K emulator and Delta-C hardware. A valid AYM1 file does not guarantee it will fit beside all game assets in the machine's 64K address space.
