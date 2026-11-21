"""AYM2 normative vectors, exact timed writes, hostile containers, real music."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import json
import random
import struct
import tempfile
import unittest
from unittest.mock import patch

import mido

from midi2aym1.codec import (COMPRESSION_MODES, HEADER2, SHORT_MASKS, RegisterStream,
                            decode_aym, decode_aym1, decode_aym2, decode_trace,
                            encode_aym1, encode_aym2, encode_stream, transcode)
from midi2aym1.core import Settings, convert
from midi2aym1.audio import render_pcm
from midi2aym1.__main__ import main

FIXTURES = Path(__file__).parent / 'fixtures' / 'spacecan'
INITIAL = bytes.fromhex('80 ff 07 00 00 00 00 00 00 00 3f 00 00 00')


def frame(mask, *values):
    return b'\x80' + mask.to_bytes(2, 'little') + bytes(values)


def aym1(commands, loop_index=None, clock=2_000_000):
    payload = b''.join(commands) + b'\0'
    loop = 16 + sum(map(len, commands[:loop_index])) if loop_index is not None else 0
    return struct.pack('<4sBBHHHI', b'AYM1', 50, int(loop != 0), 16 + len(payload), loop, 0, clock) + payload


def header(blob, index, value):
    fields = list(HEADER2.unpack_from(blob))
    fields[index] = value
    return HEADER2.pack(*fields) + blob[24:]


class Aym2Tests(unittest.TestCase):
    def test_normative_short_vector(self):
        source = aym1([INITIAL, frame(0x400, 0), b'\x7f'])
        expected = bytes.fromhex(
            '41 59 4d 32 01 32 00 00 29 00 00 00 80 84 1e 00 '
            '29 00 29 00 00 00 00 00 '
            '80 ff 07 00 00 00 00 00 00 00 3f 00 00 00 91 7f 00')
        self.assertEqual(transcode(source, compression='short'), expected)
        self.assertEqual(decode_trace(expected), decode_trace(source))
        self.assertEqual(len(decode_aym2(expected)[0]), 129)

    def test_every_short_primitive_and_mute_preserves_writes(self):
        for opcode, mask in SHORT_MASKS.items():
            values = [1] * mask.bit_count()
            command = frame(mask, *values)
            with self.subTest(opcode=hex(opcode)):
                blob = transcode(aym1([INITIAL, command]), compression='short')
                self.assertEqual(blob[38:-1], bytes((opcode, *values)))
                self.assertEqual(decode_trace(blob).commands, (INITIAL, command))
        for ch in range(3):
            blob = transcode(aym1([INITIAL, frame(1 << (8 + ch), 0)]), compression='short')
            self.assertEqual(blob[-2], 0x8f + ch)

    def test_repeated_r13_and_hardware_envelope_bits_survive(self):
        commands = [INITIAL, frame(0x3900, 16, 0x34, 0x12, 9)]
        commands += [frame(0x2000, 9), b'\x01', frame(0x2000, 9)] * 15
        source = aym1(commands, loop_index=2, clock=1_773_400)
        trace = decode_trace(source)
        for mode in COMPRESSION_MODES:
            with self.subTest(mode=mode):
                blob = transcode(source, compression=mode)
                self.assertEqual(decode_trace(blob), trace)
                self.assertEqual(list(decode_trace(blob).slots()), list(trace.slots()))
                self.assertEqual(transcode(blob, format='aym1'), source)
                self.assertEqual(decode_aym2(blob)[1]['loop_frame'], 2)
        # Identical snapshots alone cannot detect the three retrigger writes.
        self.assertEqual(sum(r == 13 for slot in trace.slots() for r, _ in slot), 31)

    def test_dictionary_uses_net_gain_after_short_encoding(self):
        commands = [INITIAL] + [frame(0x400, 0), frame(0x1800, 50, 60)] * 24
        blob = transcode(aym1(commands))
        self.assertEqual(blob[7], 1)  # 1-byte MUTE needs no dictionary
        end, bodies = struct.unpack_from('<HH', blob, 16)
        self.assertEqual(blob[bodies:], frame(0x1800, 50, 60))
        self.assertEqual(struct.unpack_from('<H', blob, end)[0], bodies)
        self.assertEqual(decode_trace(blob).commands, tuple(commands))
        no_dict = transcode(aym1(commands), max_dictionary=0)
        self.assertEqual(no_dict, transcode(aym1(commands), compression='short'))
        self.assertLess(len(blob), len(no_dict))

    def test_dictionary_can_initialize_first_frame_and_loop_at_wait(self):
        commands = [INITIAL, b'\x01', b'\x7f', INITIAL] * 12
        source = aym1(commands, loop_index=2)
        blob = transcode(source)
        self.assertGreaterEqual(blob[24], 0xa0)
        trace = decode_trace(blob)
        self.assertEqual(trace, decode_trace(source))
        self.assertEqual(trace.loop_index, 2)
        self.assertEqual(trace.loop_frame, 2)
        # A second pass starts at the same command with no implicit wait/reset.
        looped = RegisterStream(trace.commands[trace.loop_index:], trace.clock)
        self.assertEqual(next(looped.slots()), ())

    def test_auto_honors_full_size_and_ties_and_limits(self):
        source = (FIXTURES / 'game_over.aym').read_bytes()
        blob, report = encode_stream(decode_trace(source), format='auto')
        self.assertEqual(blob, source)  # both are 56 bytes
        self.assertEqual(report['selection_reason'], 'aym1_smaller_or_equal')
        small = aym1([INITIAL])
        self.assertEqual(transcode(small, format='auto'), small)
        # Compressible input whose uncompressed AYM1 cannot fit a 16-bit length.
        trace = RegisterStream(tuple([INITIAL] * 5000), 2_000_000, 0)
        blob, report = encode_stream(trace, format='auto')
        self.assertTrue(blob.startswith(b'AYM2'))
        self.assertFalse(report['aym1_fits'])
        self.assertEqual(decode_trace(blob), trace)
        with self.assertRaises(ValueError):
            encode_stream(trace, format='aym1')
        with self.assertRaises(ValueError):
            encode_stream(trace, format='aym2', compression='none')

    def test_large_dictionary_is_limited_and_deterministic(self):
        commands = [INITIAL]
        for n in range(120):
            commands.extend([frame(0x1800, n, n)] * 15)
        trace = decode_trace(aym1(commands))
        blob, report = encode_stream(trace, format='aym2')
        self.assertEqual(blob[7], 96)
        self.assertEqual(sum(report['register_masks'].values()), report['frame_commands'])
        self.assertEqual(report['register_masks']['1800'], 1800)
        self.assertEqual(decode_trace(blob), trace)
        self.assertEqual(blob, encode_stream(trace, format='aym2')[0])
        for limit in (0, 1, 24, 95):
            limited = encode_stream(trace, format='aym2', max_dictionary=limit)[0]
            self.assertEqual(limited[7], limit)
        for limit in (-1, 97, 1.5, True):
            with self.assertRaises(ValueError):
                encode_stream(trace, max_dictionary=limit)

    def test_snapshot_api_matches_aym1_and_randomized_register_writes(self):
        rng = random.Random(20261121)
        limits = [255, 15, 255, 15, 255, 15, 31, 63, 31, 31, 31, 255, 255, 15]
        commands = [INITIAL]
        for _ in range(500):
            if rng.randrange(4) == 0:
                commands.append(bytes((rng.randint(1, 127),)))
            else:
                mask = rng.randrange(0x4000)
                commands.append(frame(mask, *(rng.randint(0, limits[r]) for r in range(14) if mask & (1 << r))))
        source = aym1(commands, loop_index=125)
        for mode in COMPRESSION_MODES:
            self.assertEqual(decode_trace(transcode(source, compression=mode)), decode_trace(source))
        frames = [[0] * 7 + [63] + [0] * 6] * 299
        for loop in (False, True):
            self.assertEqual(decode_aym2(encode_aym2(frames, 2_000_000, loop))[0], frames)
            self.assertEqual(decode_aym1(encode_aym1(frames, 2_000_000, loop))[0], frames)

    def test_malformed_headers(self):
        blob = transcode(aym1([INITIAL, b'\x01']))
        for index, value in ((0, b'AYM3'), (1, 2), (2, 60), (3, 2), (4, 97),
                             (5, len(blob) - 1), (6, 24), (7, 0), (8, 23),
                             (8, 65535), (9, 24), (10, 1)):
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                decode_aym2(header(blob, index, value))
        with self.assertRaises(ValueError):
            decode_aym1(blob)
        with self.assertRaises(ValueError):
            decode_aym2(aym1([INITIAL]))

    def test_malformed_commands_dictionary_and_loop(self):
        source = aym1([INITIAL] + [frame(0x1800, 2, 3)] * 20, loop_index=1)
        blob = transcode(source)
        end, bodies = struct.unpack_from('<HH', blob, 16)
        broken = []
        for offset, value in ((38, 0xff), (38, 0x9f), (bodies, 0xa0),
                              (bodies + 1, 0xff), (bodies + 2, 0xc0),
                              (end - 1, 0x8e)):
            x = bytearray(blob)
            x[offset] = value
            broken.append(bytes(x))
        for pointer in (24, bodies + 1, len(blob), 65535):
            x = bytearray(blob)
            struct.pack_into('<H', x, end, pointer)
            broken.append(bytes(x))
        for loop in (25, 37, end - 1, end, bodies):
            broken.append(header(blob, 6, loop))
        for x in broken:
            with self.subTest(data=x.hex()), self.assertRaises(ValueError):
                decode_trace(x)
        for payload in (b'\0', b'\x01\0', b'\x80\0', INITIAL + b'\x80\0',
                        INITIAL + b'\x92\x01\0', INITIAL + b'\0\0',
                        INITIAL + frame(0x2000, 16) + b'\0'):
            end = 24 + len(payload)
            x = HEADER2.pack(b'AYM2', 1, 50, 0, 0, end, 0, 2_000_000, end, end, 0) + payload
            with self.subTest(payload=payload.hex()), self.assertRaises(ValueError):
                decode_trace(x)
        for n in range(len(blob)):
            with self.subTest(truncate=n), self.assertRaises(ValueError):
                decode_aym2(blob[:n])
        with patch('midi2aym1.codec.MAX_FRAMES', 5), self.assertRaises(ValueError):
            decode_trace(blob)

    def test_spacecan_fixtures_exact_traces_sizes_and_audio(self):
        expected = {'midiver': 2608, 'main_menu': 1867, 'gol': 7644, 'victory': 114, 'game_over': 56}
        for name, size in expected.items():
            source = (FIXTURES / (name + '.aym')).read_bytes()
            original = decode_trace(source)
            original_frames, _ = decode_aym1(source)
            for mode in COMPRESSION_MODES:
                with self.subTest(name=name, mode=mode):
                    blob = transcode(source, compression=mode)
                    self.assertEqual(decode_trace(blob), original)
                    self.assertEqual(transcode(blob, format='aym1'), source)
                    frames, _ = decode_aym(blob)
                    self.assertEqual(frames, original_frames)
            self.assertEqual(len(transcode(source)), size)
            # Identical decoded states plus a representative PCM segment.
            self.assertEqual(render_pcm(frames[:50], original.clock), render_pcm(original_frames[:50], original.clock))

    def test_cli_midi_preview_and_binary_repack_csv_trace(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            folder = Path(tmp)
            midi = folder / 'notes.mid'
            mf = mido.MidiFile(type=0)
            mf.tracks.append(mido.MidiTrack([
                mido.Message('note_on', note=60),
                mido.Message('note_off', note=60, time=480)]))
            mf.save(midi)
            cfg = Settings(format='aym2', compression='short', max_dictionary=12)
            expected, frames, report = convert(midi, cfg)
            self.assertEqual(decode_aym2(expected)[0], frames)
            self.assertEqual(Settings.from_dict(cfg.to_dict()), cfg)
            output = folder / 'out.aym'
            main([str(midi), '-o', str(output), '--format', 'aym2', '--compression', 'short',
                  '--max-dictionary', '12', '--wav', '--csv'])
            self.assertEqual(output.read_bytes(), expected)
            self.assertTrue(output.with_suffix('.wav').exists())
            self.assertTrue(output.with_suffix('.writes.csv').read_text().startswith('frame,register,value'))
            saved = json.loads(output.with_suffix('.report.json').read_text())
            self.assertEqual(saved, report)
            source = FIXTURES / 'midiver.aym'
            main([str(source), '-o', str(output), '--format', 'auto', '--csv'])
            self.assertEqual(decode_trace(output.read_bytes()), decode_trace(source.read_bytes()))
            self.assertEqual(output.stat().st_size, 2608)
            for flags in (['--max-dictionary', '97'], ['--clock', '1773400'], ['-o', str(source)]):
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                    main([str(source)] + flags)
                self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
