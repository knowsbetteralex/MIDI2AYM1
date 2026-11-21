"""python -m midi2aym1 [--cli input.mid ...]"""
from __future__ import annotations

import argparse
from pathlib import Path
import json

from .core import PERCUSSION_MODES, Settings, convert, write_outputs
from .audio import render_wav
from .presets import BUILTIN_PRESET_IDS, make_builtin_preset
from .codec import COMPRESSION_MODES, FORMATS


def main(argv=None):
    parser = argparse.ArgumentParser(description='Convert MIDI (SMF 0/1) or repack AY music to AYM1/AYM2')
    parser.add_argument('input', nargs='?', type=Path, help='Input MIDI or AYM1/AYM2 file (omit for GUI)')
    parser.add_argument('-o', '--output', type=Path, help='Destination .aym file')
    parser.add_argument('--clock', type=int, default=None, help='AY clock Hz (default 2000000)')
    parser.add_argument('--loop', action=argparse.BooleanOptionalAction, default=None, help='Loop from the beginning')
    parser.add_argument('--overflow', choices=['steal', 'drop'], default=None)
    parser.add_argument('--percussion', choices=PERCUSSION_MODES, default=None)
    parser.add_argument('--format', choices=FORMATS, default=None, help='Output format (default aym1; auto chooses smaller)')
    parser.add_argument('--compression', choices=COMPRESSION_MODES, default=None, help='AYM2 compression (default dictionary)')
    parser.add_argument('--max-dictionary', type=int, default=None, metavar='0..96', help='Maximum AYM2 dictionary entries')
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
        for key in ('clock', 'loop', 'overflow', 'percussion', 'format', 'compression', 'max_dictionary'):
            if getattr(args, key) is not None:
                setattr(cfg, key, getattr(args, key))
        blob, frames, report = convert(args.input, cfg)
        is_binary = 'source_format' in report
        if is_binary and (args.preset or args.builtin_preset or any(getattr(args, key) is not None
                          for key in ('clock', 'loop', 'overflow', 'percussion'))):
            raise ValueError('AYM repacking preserves source clock, loop and writes; sound options apply to MIDI only')
        dest = args.output or (args.input.with_name(args.input.stem + '.repacked.aym') if is_binary
                               else args.input.with_suffix('.aym'))
        if dest.resolve() == args.input.resolve():
            raise ValueError('output must differ from the source file')
        write_outputs(dest, blob, frames, report, csv=args.csv)
        if args.wav:
            dest.with_suffix('.wav').write_bytes(render_wav(frames, report['clock_hz']))
        print(f"{report['format'].upper()} {dest}: {len(blob)} bytes / {len(frames)} frames / "
              f"peak voices={report['peak_polyphony']} / lost={report['lost_note_instances']}")
        c = report['compression']
        print(f"AYM1={c['aym1_bytes']} B, AYM2={c['aym2_bytes']} B, "
              f"saved={c['saved_bytes']} B ({c['saved_percent']}%), dictionary={c['dictionary_entries']}")
        for warning in report['warnings']:
            print('WARNING:', warning)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
