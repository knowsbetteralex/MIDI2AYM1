"""Run: python -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path

import mido

from midi2aym1.core import (Event, Settings, Song, convert, decode_aym1,
                            encode_aym1, synthesize)
from midi2aym1.audio import render_wav


def ev(f, kind, note, vel=100, channel=0, track=0, i=0):
    return Event(f, kind, channel, note, vel, track, i)


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()
        for voice in self.cfg.voices:
            voice.release_ms = 0
            voice.attack_ms = 0
            voice.decay_ms = 0
            voice.sustain = 1

    def test_chord_three_independent_tones(self):
        song = Song([ev(0, 'on', n) for n in (60, 64, 67)] +
                    [ev(50, 'off', n) for n in (60, 64, 67)], 50, 1, 0, [])
        frames, report = synthesize(song, self.cfg)
        self.assertEqual(report['peak_polyphony'], 3)
        self.assertEqual(report['lost_note_instances'], 0)
        self.assertTrue(all(v > 0 for v in frames[1][8:11]))
        self.assertEqual(frames[-1][8:11], [0, 0, 0])
        blob = encode_aym1(frames, self.cfg.clock)
        self.assertEqual(decode_aym1(blob)[0], frames)

    def test_overlapping_and_steal(self):
        seq = [ev(0, 'on', 60), ev(5, 'on', 64), ev(10, 'on', 67),
               ev(15, 'on', 71), ev(20, 'off', 60), ev(25, 'off', 64),
               ev(30, 'off', 67), ev(35, 'off', 71)]
        frames, report = synthesize(Song(seq, 35, 1, 0, []), self.cfg)
        self.assertTrue(all(v > 0 for v in frames[11][8:11]))
        self.assertGreater(report['lost_note_instances'], 0)
        self.assertGreater(report['overflow_frames'], 0)
        self.assertEqual(frames[-1][8:11], [0, 0, 0])

    def test_fifo_repeated_pitch(self):
        s = Song([ev(0, 'on', 60), ev(5, 'on', 60), ev(10, 'off', 60),
                  ev(20, 'off', 60)], 20, 1, 0, [])
        frames, report = synthesize(s, self.cfg)
        self.assertEqual(report['peak_polyphony'], 2)
        self.assertEqual(sum(a > 0 for a in frames[11][8:11]), 1)

    def test_sustain_pedal(self):
        seq = [ev(0, 'on', 60), ev(5, 'cc', 64, 127), ev(10, 'off', 60),
               ev(20, 'cc', 64, 0)]
        frames, _ = synthesize(Song(seq, 20, 1, 0, []), self.cfg)
        self.assertGreater(sum(frames[15][8:11]), 0)
        self.assertEqual(frames[-1][8:11], [0, 0, 0])

    def test_gm_drums_tone_noise_and_same_note(self):
        seq = [ev(0, 'on', 36, 127, 9), ev(5, 'on', 42, 100, 9)]
        frames, _ = synthesize(Song(seq, 20, 1, 0, []), self.cfg)
        self.assertGreater(frames[0][8], 0)
        self.assertNotEqual(frames[0][7] & 1, 1)  # kick has tone enabled
        self.assertGreaterEqual(frames[5][6], 1)

    def test_long_wait_and_loop(self):
        first = [0]*14
        first[7] = 63
        frames = [first[:] for _ in range(299)]
        data = encode_aym1(frames, 2_000_000, loop=True)
        decoded, h = decode_aym1(data)
        self.assertEqual(decoded, frames)
        self.assertTrue(h['loop'])
        self.assertEqual(data[5] & 1, 1)
        self.assertEqual(data[-1], 0)

    def test_midi_tempo_map_and_wav(self):
        mf = mido.MidiFile(ticks_per_beat=480, type=1)
        t = mido.MidiTrack()
        mf.tracks.append(t)
        t.append(mido.MetaMessage('set_tempo', tempo=500_000, time=0))
        t.append(mido.Message('note_on', note=60, velocity=100, time=0))
        t.append(mido.MetaMessage('set_tempo', tempo=1_000_000, time=480))
        t.append(mido.Message('note_off', note=60, velocity=0, time=480))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'demo.mid'
            mf.save(str(path))
            data, frames, report = convert(path, self.cfg)
            self.assertEqual(report['tempo_changes'], 2)
            self.assertGreaterEqual(len(frames), 75)
            self.assertTrue(render_wav(frames, self.cfg.clock).startswith(b'RIFF'))
            self.assertEqual(decode_aym1(data)[0], frames)

    def test_cli_preset_keeps_clock_and_loop(self):
        from midi2aym1.__main__ import main as cli_main
        import json
        mf = mido.MidiFile(ticks_per_beat=480, type=0)
        track = mido.MidiTrack()
        mf.tracks.append(track)
        track.extend([mido.Message('note_on', note=60, velocity=100, time=0),
                      mido.Message('note_off', note=60, velocity=0, time=480)])
        with tempfile.TemporaryDirectory() as tmp:
            src, preset, dest = (Path(tmp) / name for name in ('in.mid', 'preset.json', 'out.aym'))
            mf.save(str(src))
            cfg = Settings(clock=1_773_400, loop=True)
            preset.write_text(json.dumps(cfg.to_dict()), encoding='utf8')
            cli_main([str(src), '--preset', str(preset), '-o', str(dest)])
            _, hdr = decode_aym1(dest.read_bytes())
            self.assertEqual(hdr['clock_hz'], 1_773_400)
            self.assertTrue(hdr['loop'])
            cli_main([str(src), '--preset', str(preset), '--no-loop', '-o', str(dest)])
            _, hdr2 = decode_aym1(dest.read_bytes())
            self.assertFalse(hdr2['loop'])

    def test_reject_bad_stream(self):
        x = encode_aym1([[0,0,0,0,0,0,0,63,0,0,0,0,0,0]], 2000000)
        with self.assertRaises(ValueError):
            decode_aym1(x[:-1])


if __name__ == '__main__':
    unittest.main()
