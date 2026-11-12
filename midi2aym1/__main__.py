"""python -m midi2aym1 [--cli input.mid ...]"""
from __future__ import annotations

import argparse
from pathlib import Path
import json

from .core import PERCUSSION_MODES, Settings, convert, write_outputs
from .audio import render_wav
from .presets import BUILTIN_PRESET_IDS, make_builtin_preset


def main(argv=None):
    parser = argparse.ArgumentParser(description='Convert MIDI (SMF 0/1) to SpaceCan AYM1')
    parser.add_argument('input', nargs='?', type=Path, help='Input MIDI file (omit for GUI)')
    parser.add_argument('-o', '--output', type=Path, help='Destination .aym file')
    parser.add_argument('--clock', type=int, default=None, help='AY clock Hz (default 2000000)')
    parser.add_argument('--loop', action=argparse.BooleanOptionalAction, default=None, help='Loop from the beginning')
    parser.add_argument('--overflow', choices=['steal', 'drop'], default=None)
    parser.add_argument('--percussion', choices=PERCUSSION_MODES, default=None)
    presets = parser.add_mutually_exclusive_group()
    presets.add_argument('--preset', type=Path, help='JSON presets exported/edited by user')
    presets.add_argument('--builtin-preset', choices=BUILTIN_PRESET_IDS, help='Built-in sound preset')
    parser.add_argument('--wav', action='store_true', help='Write AY-emulated WAV')
    parser.add_argument('--csv', action='store_true', help='Write register frames CSV')
    args = parser.parse_args(argv)
    if not args.input:
        from .gui import main as gui_main
        gui_main()
        return
    try:
        if args.builtin_preset:
            cfg = make_builtin_preset(args.builtin_preset)
        else:
            cfg = Settings.from_dict(json.loads(args.preset.read_text(encoding='utf8'))) if args.preset else Settings()
        for key in ('clock', 'loop', 'overflow', 'percussion'):
            if getattr(args, key) is not None:
                setattr(cfg, key, getattr(args, key))
        blob, frames, report = convert(args.input, cfg)
        dest = args.output or args.input.with_suffix('.aym')
        write_outputs(dest, blob, frames, report, csv=args.csv)
        if args.wav:
            dest.with_suffix('.wav').write_bytes(render_wav(frames, cfg.clock))
        print(f"AYM1 {dest}: {len(blob)} bytes / {len(frames)} frames / "
              f"peak voices={report['peak_polyphony']} / lost={report['lost_note_instances']}")
        for warning in report['warnings']:
            print('WARNING:', warning)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
