"""Compact percussion keeps timing and GM identities while reducing register writes."""
import json
from pathlib import Path
import tempfile
import unittest

import mido

from midi2aym1.__main__ import main as cli_main
from midi2aym1.core import (Event, PERCUSSION_MODES, Settings, Song, decode_aym1,
                            encode_aym1, synthesize)


def hit(frame, pitch, velocity=127):
    return Event(frame, 'on', 9, pitch, velocity, 0, 0)


class CompactDrumTests(unittest.TestCase):
    def render(self, events, mode, cfg=None, end=20):
        cfg = cfg or Settings()
        cfg.percussion = mode
        frames, report = synthesize(Song(events, end, 1, 0, []), cfg)
        blob = encode_aym1(frames, cfg.clock)
        self.assertEqual(decode_aym1(blob)[0], frames)
        return frames, report, blob

    def test_pulses_last_one_frame_with_no_tail(self):
        for pitch in (36, 38, 42, 49):
            with self.subTest(pitch=pitch):
                frames, report, _ = self.render([hit(0, pitch), hit(5, pitch)], 'pulse')
                audible = [i for i, regs in enumerate(frames) if any(regs[8:11])]
                self.assertEqual(audible, [0, 5])
                self.assertEqual(report['lost_note_instances'], 0)

    def test_compact_tone_uses_two_steps_and_short_duration(self):
        frames, _, compact = self.render([hit(0, 36)], 'compact')
        sounding = [row for row in frames if any(row[8:11])]
        self.assertEqual(len(sounding), 3)  # 60 ms at 50 Hz
        self.assertEqual(len({row[10] for row in sounding}), 2)
        self.assertEqual(len({tuple(row[4:6]) for row in sounding}), 2)
        _, _, full = self.render([hit(0, 36)], 'noise')
        self.assertLess(len(compact), len(full))

    def test_new_hit_chokes_older_tail_without_resuming_it(self):
        frames, report, _ = self.render([hit(0, 36), hit(1, 42)], 'compact')
        self.assertTrue(any(frames[0][8:11]))
        self.assertTrue(any(frames[1][8:11]))
        self.assertTrue(all(not any(row[8:11]) for row in frames[2:]))
        self.assertEqual(report['lost_note_instances'], 1)
        self.assertEqual(report['losses'][0]['note'], 36)

    def test_simultaneous_hits_choose_kit_priority_and_report_rejections(self):
        for policy in ('steal', 'drop'):
            with self.subTest(policy=policy):
                cfg = Settings(overflow=policy)
                frames, report, _ = self.render([hit(0, 42), hit(0, 38), hit(0, 36)], 'compact', cfg)
                self.assertEqual(sum(v > 0 for v in frames[0][8:11]), 1)
                self.assertEqual(frames[0][6], cfg.drums[36].noise_period)
                self.assertEqual({row['note'] for row in report['losses']}, {38, 42})

    def test_muted_hit_does_not_interrupt_active_drum(self):
        cfg = Settings()
        cfg.drums[42].volume = 0
        frames, report, _ = self.render([hit(0, 36), hit(1, 42)], 'compact', cfg)
        self.assertTrue(any(frames[2][8:11]))
        self.assertFalse(any(frames[3][8:11]))
        self.assertEqual(report['lost_note_instances'], 0)

    def test_shorter_user_duration_is_respected(self):
        cfg = Settings()
        cfg.drums[36].duration_ms = 10
        frames, _, _ = self.render([hit(0, 36)], 'compact', cfg)
        self.assertEqual([i for i, row in enumerate(frames) if any(row[8:11])], [0])

    def test_drum_pulses_keep_melody_slots_and_registers_stable(self):
        cfg = Settings()
        for voice in cfg.voices:
            voice.attack_ms = voice.decay_ms = voice.release_ms = 0
            voice.sustain = 1
        events = [Event(0, 'on', 0, 60, 100, 0, 0), Event(0, 'on', 1, 64, 100, 0, 1),
                  hit(0, 36), hit(5, 42), hit(10, 38),
                  Event(15, 'off', 0, 60, 0, 0, 2), Event(15, 'off', 1, 64, 0, 0, 3)]
        frames, report, _ = self.render(events, 'pulse', cfg)
        self.assertTrue(all(row[8] > 0 and row[9] > 0 for row in frames[:15]))
        self.assertTrue(all(row[:4] == frames[0][:4] for row in frames[:15]))
        self.assertEqual([i for i, row in enumerate(frames) if row[10]], [0, 5, 10])
        self.assertEqual(report['lost_note_instances'], 0)

    def test_modes_roundtrip_through_json_and_cli_override(self):
        for mode in PERCUSSION_MODES:
            cfg = Settings(percussion=mode)
            self.assertEqual(Settings.from_dict(json.loads(json.dumps(cfg.to_dict()))).percussion, mode)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source, preset, output = (folder / name for name in ('drum.mid', 'kit.json', 'drum.aym'))
            midi = mido.MidiFile(type=0, ticks_per_beat=96)
            midi.tracks.append(mido.MidiTrack([
                mido.Message('note_on', channel=9, note=36, velocity=127),
                mido.Message('note_off', channel=9, note=36, time=96)]))
            midi.save(source)
            preset.write_text(json.dumps(Settings(percussion='compact').to_dict()), encoding='utf8')
            cli_main([str(source), '--preset', str(preset), '--percussion', 'pulse', '-o', str(output)])
            frames, _ = decode_aym1(output.read_bytes())
            self.assertEqual([i for i, row in enumerate(frames) if any(row[8:11])], [0])
            report = json.loads(output.with_suffix('.report.json').read_text())
            self.assertEqual(report['percussion'], 'pulse')


if __name__ == '__main__':
    unittest.main()
