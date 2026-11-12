"""Built-in presets work without the repository's JSON examples."""
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import mido

from midi2aym1.__main__ import main as cli_main
from midi2aym1.core import Event, Settings, Song, convert, decode_aym1, synthesize
from midi2aym1.presets import BUILTIN_PRESETS, BUILTIN_PRESET_IDS, make_builtin_preset
from midi2aym1.ui_text import TEXT


class BuiltinPresetTests(unittest.TestCase):
    def test_presets_are_self_contained_and_localized(self):
        self.assertEqual(len(set(BUILTIN_PRESET_IDS)), len(BUILTIN_PRESETS))
        with patch('builtins.open', side_effect=AssertionError('Built-in presets must not read files')), \
             patch.object(Path, 'open', side_effect=AssertionError('Built-in presets must not read files')):
            for preset in BUILTIN_PRESETS:
                make_builtin_preset(preset.key).validate()
        for language in TEXT.values():
            names = [language[preset.name_key] for preset in BUILTIN_PRESETS]
            self.assertEqual(len(set(names)), len(names))
            for preset in BUILTIN_PRESETS:
                self.assertTrue(language[preset.description_key])

    def test_package_copy_works_without_the_presets_directory(self):
        source = Path(__file__).resolve().parents[1] / 'midi2aym1'
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / 'midi2aym1'
            package.mkdir()
            for module in source.glob('*.py'):
                shutil.copy2(module, package / module.name)
            code = ('from pathlib import Path\n'
                    'from midi2aym1 import presets\n'
                    'assert Path(presets.__file__).resolve().parent == Path.cwd() / "midi2aym1"\n'
                    'for key in presets.BUILTIN_PRESET_IDS:\n'
                    '    presets.make_builtin_preset(key).validate()\n')
            result = subprocess.run([sys.executable, '-c', code], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_preset_instances_are_independent(self):
        cfg = make_builtin_preset('compact-drums')
        cfg.voices[0].gain = .2
        cfg.drums[36].volume = 0
        cfg.midi_priorities[0] = 99
        fresh = make_builtin_preset('compact-drums')
        self.assertEqual(fresh.voices[0].gain, 1)
        self.assertEqual(fresh.drums[36].volume, 15)
        self.assertEqual(fresh.midi_priorities[0], 6)

    def test_default_and_target_options(self):
        self.assertEqual(make_builtin_preset('default').to_dict(), Settings().to_dict())
        for key in BUILTIN_PRESET_IDS:
            cfg = make_builtin_preset(key, clock=1_773_400, loop=True)
            self.assertEqual(cfg.clock, 1_773_400)
            self.assertTrue(cfg.loop)
        with self.assertRaises(ValueError):
            make_builtin_preset('unknown')

    def test_compact_tones_are_constant_and_stop_without_release_tail(self):
        cfg = make_builtin_preset('compact-drums')
        self.assertEqual(cfg.percussion, 'compact')
        self.assertTrue(all(voice.vibrato_cents == 0 for voice in cfg.voices))
        song = Song([Event(0, 'on', 0, 60, 100, 0, 0), Event(15, 'off', 0, 60, 0, 0, 1)],
                    20, 1, 0, [])
        frames, report = synthesize(song, cfg)
        self.assertGreater(frames[0][8], 0)
        self.assertEqual(len({frame[8] for frame in frames[:15]}), 1)
        self.assertEqual(len({tuple(frame[:2]) for frame in frames[:15]}), 1)
        self.assertTrue(all(not any(frame[8:11]) for frame in frames[15:]))
        self.assertEqual(report['lost_note_instances'], 0)

    def test_cli_builtin_preset_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, output = folder / 'input.mid', folder / 'output.aym'
            midi = mido.MidiFile(type=0)
            midi.tracks.append(mido.MidiTrack([
                mido.Message('note_on', note=60, velocity=100),
                mido.Message('note_on', channel=9, note=36, velocity=100),
                mido.Message('note_off', note=60, time=480)]))
            midi.save(source)
            cli_main([str(source), '--builtin-preset', 'compact-drums', '--clock', '1773400',
                      '--loop', '--percussion', 'pulse', '-o', str(output)])
            cfg = make_builtin_preset('compact-drums', clock=1_773_400, loop=True)
            cfg.percussion = 'pulse'
            expected, _, _ = convert(source, cfg)
            self.assertEqual(output.read_bytes(), expected)
            _, header = decode_aym1(output.read_bytes())
            self.assertEqual(header['clock_hz'], 1_773_400)
            self.assertTrue(header['loop'])
            report = json.loads(output.with_suffix('.report.json').read_text())
            self.assertEqual(report['percussion'], 'pulse')

    def test_cli_rejects_two_preset_sources(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            cli_main(['--preset', 'custom.json', '--builtin-preset', 'default'])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
