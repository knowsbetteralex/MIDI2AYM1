"""Strict AYM1/AYM2 containers and lossless, write-preserving compression.

RegisterStream stores commands rather than snapshots: a repeated R13 write is
an event and must survive even when the register state does not change.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import struct

FPS = 50
CODEC_VERSION = 'aym2-v1/2026-11-21'
FORMATS = ('aym1', 'aym2', 'auto')
COMPRESSION_MODES = ('none', 'short', 'dictionary')
MAX_DICTIONARY = 96
HEADER2 = struct.Struct('<4sBBBBHHIHHI')
MAX_FRAMES = 1_000_000
SHORT_MASKS = {
    **{0x81 + r: 1 << r for r in range(14)},
    **{0x92 + ch: (3 << (2 * ch)) | (1 << (8 + ch)) for ch in range(3)},
    **{0x95 + ch: 3 << (2 * ch) for ch in range(3)},
    **{0x98 + ch: 0xc0 | (1 << (8 + ch)) for ch in range(3)},
    0x9b: 0x700, 0x9c: 0x780,
}
MASK_TO_SHORT = {mask: opcode for opcode, mask in SHORT_MASKS.items()}


@dataclass(frozen=True)
class RegisterStream:
    commands: tuple[bytes, ...]  # AYM1 FRAME or one-byte WAIT, END excluded
    clock: int
    loop_index: int | None = None

    def slots(self):
        """Timed ordered writes; WAIT slots yield (), including after loop."""
        for command in self.commands:
            if command[0] != 0x80:
                for _ in range(command[0]):
                    yield ()
            else:
                mask = int.from_bytes(command[1:3], 'little')
                yield tuple(zip((r for r in range(14) if mask & (1 << r)), command[3:]))

    @property
    def loop_frame(self):
        if self.loop_index is None:
            return None
        return sum(1 if c[0] == 0x80 else c[0] for c in self.commands[:self.loop_index])


def _validate_values(mask, values):
    if mask & ~0x3fff or len(values) != mask.bit_count():
        raise ValueError('invalid FRAME mask/length')
    for r, value in zip((r for r in range(14) if mask & (1 << r)), values):
        limit = 15 if r in (1, 3, 5, 13) else 31 if r in (6, 8, 9, 10) else 63 if r == 7 else 255
        if not 0 <= value <= limit:
            raise ValueError(f'AY register R{r} out of bounds')


def _frame(data, offset, end):
    if offset + 3 > end or data[offset] != 0x80:
        raise ValueError('truncated FRAME or dictionary entry is not a full FRAME')
    mask = int.from_bytes(data[offset + 1:offset + 3], 'little')
    stop = offset + 3 + mask.bit_count()
    if stop > end:
        raise ValueError('truncated FRAME values')
    command = bytes(data[offset:stop])
    _validate_values(mask, command[3:])
    return command, stop


def decode_trace(data: bytes) -> RegisterStream:
    """Validate either format without discarding repeated writes or WAITs."""
    dictionary = []
    if data[:4] == b'AYM1':
        if len(data) < 17:
            raise ValueError('AYM1 too small')
        _, fps, flags, length, loop, reserved, clock = struct.unpack_from('<4sBBHHHI', data)
        if fps != FPS or flags & ~1 or reserved or length != len(data):
            raise ValueError('AYM1 header invalid')
        start, end = 16, length
        is_aym2 = False
    elif data[:4] == b'AYM2':
        if len(data) < HEADER2.size + 1:
            raise ValueError('AYM2 too small')
        (_, version, fps, flags, count, length, loop, clock,
         end, bodies, reserved) = HEADER2.unpack_from(data)
        if (version != 1 or fps != FPS or flags & ~1 or count > MAX_DICTIONARY
                or length != len(data) or reserved or not clock):
            raise ValueError('AYM2 header invalid')
        start = HEADER2.size
        if not start < end <= bodies <= length or bodies != end + 2 * count:
            raise ValueError('AYM2 stream/dictionary boundaries invalid')
        cursor = bodies
        for index in range(count):
            pointer = struct.unpack_from('<H', data, end + 2 * index)[0]
            if pointer != cursor:
                raise ValueError('dictionary pointers must address consecutive complete FRAMEs')
            command, cursor = _frame(data, pointer, length)
            dictionary.append(command)
        if cursor != length:
            raise ValueError('trailing bytes or missing dictionary entries')
        is_aym2 = True
    else:
        raise ValueError('expected AYM1 or AYM2 signature')
    if (flags & 1 and not start <= loop < end - 1) or (not flags & 1 and loop):
        raise ValueError('loop offset invalid')
    commands, boundaries = [], {}
    cursor, frames = start, 0
    while cursor < end:
        offset = cursor
        opcode = data[cursor]
        cursor += 1
        if opcode == 0:
            if cursor != end:
                raise ValueError('trailing bytes after END')
            if not commands:
                raise ValueError('empty stream')
            if flags & 1 and loop not in boundaries:
                raise ValueError('loop offset not on a timed command boundary')
            return RegisterStream(tuple(commands), clock, boundaries[loop] if flags & 1 else None)
        if opcode == 0x80:
            command, cursor = _frame(data, offset, end)
        elif 1 <= opcode <= 127:
            command = bytes((opcode,))
        elif is_aym2 and opcode >= 0xa0:
            index = opcode - 0xa0
            if index >= len(dictionary):
                raise ValueError('dictionary token index out of bounds')
            command = dictionary[index]
        elif is_aym2 and 0x8f <= opcode <= 0x91:
            command = b'\x80' + (1 << (8 + opcode - 0x8f)).to_bytes(2, 'little') + b'\0'
        elif is_aym2 and opcode in SHORT_MASKS:
            mask = SHORT_MASKS[opcode]
            stop = cursor + mask.bit_count()
            if stop > end:
                raise ValueError('truncated short command')
            values = bytes(data[cursor:stop])
            _validate_values(mask, values)
            command = b'\x80' + mask.to_bytes(2, 'little') + values
            cursor = stop
        else:
            raise ValueError('reserved opcode')
        if not commands and (command[0] != 0x80 or int.from_bytes(command[1:3], 'little') & 0x7bf != 0x7bf):
            raise ValueError('first FRAME fails to initialize voices')
        boundaries[offset] = len(commands)
        commands.append(command)
        frames += 1 if command[0] == 0x80 else command[0]
        if frames > MAX_FRAMES:
            raise ValueError('unreasonable AY duration')
    raise ValueError('missing END')


def _decode(data, magic):
    if data[:4] != magic:
        raise ValueError(f'expected {magic.decode()} signature')
    trace = decode_trace(data)
    regs = [0] * 14
    regs[7] = 63
    frames = []
    for writes in trace.slots():
        for register, value in writes:
            regs[register] = value
        frames.append(regs.copy())
    header = {'clock_hz': trace.clock, 'loop': trace.loop_index is not None,
              'frames': len(frames), 'loop_frame': trace.loop_frame}
    return frames, header


def decode_aym1(data: bytes):
    return _decode(data, b'AYM1')


def decode_aym2(data: bytes):
    return _decode(data, b'AYM2')


def decode_aym(data: bytes):
    return _decode(data, data[:4])


def stream_from_frames(frames, clock, loop=False):
    """Snapshots are for software envelopes; use decode_trace for R13 events."""
    if not isinstance(clock, int) or not 1 <= clock <= 0xffffffff:
        raise ValueError('invalid AY clock')
    if not frames:
        frames = [[0] * 7 + [63] + [0] * 6]
    if len(frames) > MAX_FRAMES:
        raise ValueError('unreasonable AY duration')
    previous = [0] * 7 + [63] + [0] * 6
    commands, wait = [], 0

    def flush_wait():
        nonlocal wait
        while wait:
            n = min(wait, 127)
            commands.append(bytes((n,)))
            wait -= n

    for i, regs in enumerate(frames):
        if len(regs) != 14 or any(not isinstance(v, int) for v in regs):
            raise ValueError('expected exactly 14 integer AY registers')
        _validate_values(0x3fff, regs)
        mask = sum(1 << r for r in range(14) if regs[r] != previous[r])
        if i == 0:
            mask |= 0x7ff
        if mask:
            flush_wait()
            commands.append(b'\x80' + mask.to_bytes(2, 'little') + bytes(regs[r] for r in range(14) if mask & (1 << r)))
        else:
            wait += 1
        previous = list(regs)
    flush_wait()
    return RegisterStream(tuple(commands), clock, 0 if loop else None)


def _short(command):
    if command[0] != 0x80:
        return command
    mask = int.from_bytes(command[1:3], 'little')
    if mask in (0x100, 0x200, 0x400) and command[3:] == b'\0':
        return bytes((0x8f + mask.bit_length() - 9,))
    opcode = MASK_TO_SHORT.get(mask)
    return bytes((opcode,)) + command[3:] if opcode is not None else command


def _encode1(trace):
    payload = b''.join(trace.commands) + b'\0'
    length = 16 + len(payload)
    loop = 16 + sum(map(len, trace.commands[:trace.loop_index])) if trace.loop_index is not None else 0
    if length > 65535:
        return None
    return struct.pack('<4sBBHHHI', b'AYM1', FPS, int(loop != 0), length, loop, 0, trace.clock) + payload


def _encode2(trace, compression, max_dictionary):
    _validate_options('aym2', compression, max_dictionary)
    counts = Counter(c for c in trace.commands if c[0] == 0x80)
    entries = []
    if compression == 'dictionary':
        gains = {c: n * (len(_short(c)) - 1) - len(c) - 2 for c, n in counts.items()}
        entries = sorted((c for c in counts if gains[c] > 0), key=lambda c: (-gains[c], c))[:max_dictionary]
    tokens = {c: bytes((0xa0 + i,)) for i, c in enumerate(entries)}
    encoded = [tokens.get(c, _short(c)) if compression != 'none' else c for c in trace.commands]
    stream = b''.join(encoded) + b'\0'
    end = HEADER2.size + len(stream)
    bodies = end + 2 * len(entries)
    length = bodies + sum(map(len, entries))
    if length > 65535:
        return None
    loop = HEADER2.size + sum(map(len, encoded[:trace.loop_index])) if trace.loop_index is not None else 0
    header = HEADER2.pack(b'AYM2', 1, FPS, int(loop != 0), len(entries), length,
                          loop, trace.clock, end, bodies, 0)
    offsets = bytearray()
    cursor = bodies
    for c in entries:
        offsets += struct.pack('<H', cursor)
        cursor += len(c)
    return header + stream + offsets + b''.join(entries)


def _validate_options(format, compression, max_dictionary):
    if format not in FORMATS or compression not in COMPRESSION_MODES:
        raise ValueError('invalid format or compression mode')
    if type(max_dictionary) is not int or not 0 <= max_dictionary <= MAX_DICTIONARY:
        raise ValueError('max_dictionary must be an integer in 0..96')


def encode_stream(trace, *, format='aym1', compression='dictionary', max_dictionary=MAX_DICTIONARY):
    """Return the selected container and reproducible size/command analytics."""
    _validate_options(format, compression, max_dictionary)
    aym1 = _encode1(trace)
    aym2 = _encode2(trace, compression, max_dictionary)
    if format == 'auto':
        candidates = [b for b in (aym1, aym2) if b is not None]
        blob = min(candidates, key=len) if candidates else None
    else:
        blob = aym1 if format == 'aym1' else aym2
    if blob is None:
        raise ValueError(f'{format.upper()} exceeds 65535 bytes; shorten/split the composition or choose compression')
    # Compare actual writes, WAIT grouping, loop command and timing, not snapshots.
    if decode_trace(blob) != trace:
        raise AssertionError('codec write/loop roundtrip mismatch')
    source_size = 17 + sum(map(len, trace.commands))
    selected_format = blob[:4].decode().lower()
    counts = Counter(c for c in trace.commands if c[0] == 0x80)
    mask_counts = Counter()
    for command, occurrences in counts.items():
        mask_counts[f'{int.from_bytes(command[1:3], "little"):04x}'] += occurrences
    count = blob[7] if selected_format == 'aym2' else 0
    end = struct.unpack_from('<H', blob, 16)[0] if selected_format == 'aym2' else len(blob)
    reason = 'explicit_format'
    if format == 'auto':
        reason = ('aym2_smaller_or_aym1_too_large' if selected_format == 'aym2' else
                  'aym2_too_large' if aym2 is None else 'aym1_smaller_or_equal')
    statistics = {
        'codec_version': CODEC_VERSION, 'requested_format': format, 'format': selected_format,
        'compression_mode': compression if selected_format == 'aym2' else 'none',
        'selection_reason': reason, 'lossless': True,
        'aym1_bytes': source_size, 'aym1_fits': aym1 is not None,
        'aym2_bytes': len(aym2) if aym2 is not None else None,
        'bytes': len(blob), 'saved_bytes': source_size - len(blob),
        'saved_percent': round(100 * (source_size - len(blob)) / source_size, 2),
        'header_bytes': 24 if selected_format == 'aym2' else 16,
        'stream_bytes': end - (24 if selected_format == 'aym2' else 16),
        'dictionary_entries': count, 'dictionary_pointer_bytes': 2 * count,
        'dictionary_body_bytes': len(blob) - end - 2 * count,
        'dictionary_limit': max_dictionary,
        'frame_commands': sum(counts.values()),
        'wait_commands': sum(c[0] != 0x80 for c in trace.commands),
        'wait_frames': sum(c[0] for c in trace.commands if c[0] != 0x80),
        'unique_frame_commands': len(counts), 'loop_frame': trace.loop_frame,
        'common_frames': [{'command_hex': c.hex(' '), 'count': n,
                           'short_bytes': len(_short(c))} for c, n in counts.most_common(12)],
        'register_masks': dict(sorted(mask_counts.items())),
    }
    return blob, statistics


def encode_aym1(frames, clock, loop=False):
    trace = stream_from_frames(frames, clock, loop)
    data = _encode1(trace)
    if data is None:
        raise ValueError('AYM1 exceeds 65535 bytes; shorten or split composition')
    decode_trace(data)
    return data


def encode_aym2(frames, clock, loop=False, *, compression='dictionary', max_dictionary=MAX_DICTIONARY):
    trace = stream_from_frames(frames, clock, loop)
    data = _encode2(trace, compression, max_dictionary)
    if data is None:
        raise ValueError('AYM2 exceeds 65535 bytes; shorten or split composition')
    decode_trace(data)
    return data


def transcode(data, *, format='aym2', compression='dictionary', max_dictionary=MAX_DICTIONARY):
    return encode_stream(decode_trace(data), format=format, compression=compression,
                         max_dictionary=max_dictionary)[0]
