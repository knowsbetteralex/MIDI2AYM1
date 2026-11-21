"""MIDI timing, three-voice allocation, AY registers, and strict AYM1/AYM2 codecs.

All MIDI interpretation happens here, never on the Z80 target.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from math import floor, log2, pi, sin
from pathlib import Path
import json
from .codec import (COMPRESSION_MODES, FORMATS, FPS, MAX_DICTIONARY, decode_aym,
                    decode_aym1, decode_aym2, decode_trace, encode_aym1, encode_aym2,
                    encode_stream, stream_from_frames)

DEFAULT_CLOCK = 2_000_000
PERCUSSION_MODES = ('noise', 'compact', 'pulse', 'off')

GM_DRUM_NAMES = {
    35: 'Acoustic Bass Drum', 36: 'Bass Drum 1', 37: 'Side Stick', 38: 'Acoustic Snare',
    39: 'Hand Clap', 40: 'Electric Snare', 41: 'Low Floor Tom', 42: 'Closed Hi-Hat',
    43: 'High Floor Tom', 44: 'Pedal Hi-Hat', 45: 'Low Tom', 46: 'Open Hi-Hat',
    47: 'Low-Mid Tom', 48: 'Hi-Mid Tom', 49: 'Crash Cymbal 1', 50: 'High Tom',
    51: 'Ride Cymbal 1', 52: 'Chinese Cymbal', 53: 'Ride Bell', 54: 'Tambourine',
    55: 'Splash Cymbal', 56: 'Cowbell', 57: 'Crash Cymbal 2', 58: 'Vibraslap',
    59: 'Ride Cymbal 2', 60: 'Hi Bongo', 61: 'Low Bongo', 62: 'Mute Hi Conga',
    63: 'Open Hi Conga', 64: 'Low Conga', 65: 'High Timbale', 66: 'Low Timbale',
    67: 'High Agogo', 68: 'Low Agogo', 69: 'Cabasa', 70: 'Maracas',
    71: 'Short Whistle', 72: 'Long Whistle', 73: 'Short Guiro', 74: 'Long Guiro',
    75: 'Claves', 76: 'Hi Wood Block', 77: 'Low Wood Block', 78: 'Mute Cuica',
    79: 'Open Cuica', 80: 'Mute Triangle', 81: 'Open Triangle',
}


@dataclass
class Voice:
    gain: float = 1.0
    attack_ms: int = 0
    decay_ms: int = 40
    sustain: float = 0.8
    release_ms: int = 100
    vibrato_cents: float = 0.0


@dataclass
class Drum:
    volume: int = 13
    duration_ms: int = 120
    noise_period: int = 12
    start_hz: float = 0.0
    end_hz: float = 0.0
    tone: bool = False
    noise: bool = True
    priority: int = 3


def default_drums() -> dict[int, Drum]:
    result = {}
    for note in GM_DRUM_NAMES:
        d = Drum()
        if note in (35, 36):  # short low falling thump, not just noise
            d = Drum(15, 160, 20, 130, 52, True, True, 5)
        elif note in (38, 40):
            d = Drum(14, 140, 5, 200, 110, True, True, 4)
        elif note in (37, 39):
            d = Drum(13, 90, 4, 0, 0, False, True, 3)
        elif note in (42, 44):
            d = Drum(11, 65, 2, 0, 0, False, True, 3)
        elif note == 46:
            d = Drum(12, 280, 2, 0, 0, False, True, 3)
        elif note in (41, 43, 45, 47, 48, 50, 60, 61, 62, 63, 64, 65, 66):
            hz = 80 + (note - 40) * 9
            d = Drum(13, 180, 15, hz * 1.5, hz * 0.7, True, note in (62, 63, 64), 3)
        elif note in (49, 51, 52, 53, 55, 57, 59):
            d = Drum(12, 450, 1, 0, 0, False, True, 3)
        elif note in (56, 67, 68, 71, 72, 75, 76, 77, 80, 81):
            d = Drum(13, 200, 6, 640 + (note % 10) * 45, 600, True, False, 3)
        result[note] = d
    return result


@dataclass
class Settings:
    clock: int = DEFAULT_CLOCK
    loop: bool = False
    overflow: str = 'steal'  # or drop
    percussion: str = 'noise'  # normal kit, compact kit, single-frame pulses, or off
    voices: list[Voice] = field(default_factory=lambda: [Voice() for _ in range(3)])
    drums: dict[int, Drum] = field(default_factory=default_drums)
    midi_priorities: list[int] = field(default_factory=lambda: [1] * 16)

    format: str = 'aym1'
    compression: str = 'dictionary'
    max_dictionary: int = MAX_DICTIONARY

    def validate(self):
        if self.format not in FORMATS or self.compression not in COMPRESSION_MODES:
            raise ValueError('invalid format or compression mode')
        if type(self.max_dictionary) is not int or not 0 <= self.max_dictionary <= MAX_DICTIONARY:
            raise ValueError('max_dictionary must be an integer in 0..96')
        if self.clock < 100_000 or self.clock > 20_000_000:
            raise ValueError('AY clock outside 100000..20000000 Hz')
        if self.overflow not in ('steal', 'drop'):
            raise ValueError('overflow must be steal or drop')
        if self.percussion not in PERCUSSION_MODES:
            raise ValueError('percussion must be noise, compact, pulse or off')
        if len(self.voices) != 3 or len(self.midi_priorities) != 16:
            raise ValueError('expected 3 AY voices and 16 MIDI priorities')
        for v in self.voices:
            if not (0 <= v.sustain <= 1 and 0 <= v.gain <= 3):
                raise ValueError('invalid voice sustain/gain')
            if min(v.attack_ms, v.decay_ms, v.release_ms) < 0:
                raise ValueError('negative ADSR duration')
        for d in self.drums.values():
            if not (0 <= d.volume <= 15 and 1 <= d.noise_period <= 31 and d.duration_ms >= 1):
                raise ValueError('invalid drum parameters')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        s = cls()
        for key in ('clock', 'loop', 'overflow', 'percussion', 'midi_priorities',
                    'format', 'compression', 'max_dictionary'):
            if key in data:
                setattr(s, key, data[key])
        if 'voices' in data:
            s.voices = [Voice(**entry) for entry in data['voices']]
        if 'drums' in data:
            for n, values in data['drums'].items():
                s.drums[int(n)] = Drum(**values)
        s.validate()
        return s


@dataclass
class Event:
    frame: int
    kind: str
    channel: int
    a: int
    b: int
    track: int
    index: int


@dataclass
class Song:
    events: list[Event]
    end_frame: int
    source_tracks: int
    tempo_changes: int
    warnings: list[str]


def read_midi(path: str | Path) -> Song:
    """Merge tempo events from all tracks before quantizing absolute time."""
    import mido

    mf = mido.MidiFile(str(path))
    if mf.type not in (0, 1) or mf.ticks_per_beat <= 0:
        raise ValueError('Only MIDI format 0/1 with PPQ timing is supported (no Type 2/SMPTE)')
    raw = []
    last_tick = 0
    for ti, track in enumerate(mf.tracks):
        tick = 0
        for ei, msg in enumerate(track):
            tick += msg.time
            raw.append((tick, ti, ei, msg))
        last_tick = max(last_tick, tick)
    raw.sort(key=lambda x: (x[0], x[1], x[2]))
    tempo = 500_000
    current_tick = 0
    seconds = 0.0
    timed = []
    tempo_changes = 0
    for tick, ti, ei, msg in raw:
        seconds += (tick - current_tick) * tempo / (mf.ticks_per_beat * 1_000_000)
        current_tick = tick
        if msg.type == 'set_tempo':
            tempo = msg.tempo
            tempo_changes += 1
        timed.append((int(floor(seconds * FPS + 0.5)), ti, ei, msg))
    end_frame = int(floor(seconds * FPS + 0.5))
    events = []
    warnings = []
    recognized = {'note_on', 'note_off', 'control_change', 'program_change', 'pitchwheel'}
    for frame, ti, ei, msg in timed:
        if msg.is_meta or msg.type == 'sysex':
            continue
        if msg.type not in recognized:
            if msg.type not in warnings:
                warnings.append(f'Ignored MIDI message: {msg.type}')
            continue
        ch = msg.channel
        if msg.type in ('note_on', 'note_off'):
            kind = 'off' if msg.type == 'note_off' or msg.velocity == 0 else 'on'
            events.append(Event(frame, kind, ch, msg.note, msg.velocity, ti, ei))
        elif msg.type == 'control_change':
            events.append(Event(frame, 'cc', ch, msg.control, msg.value, ti, ei))
        elif msg.type == 'program_change':
            events.append(Event(frame, 'program', ch, msg.program, 0, ti, ei))
        elif msg.type == 'pitchwheel':
            events.append(Event(frame, 'bend', ch, msg.pitch, 0, ti, ei))

    # Keep the absolute-tick/in-track order established above. Several MIDI ticks
    # can round to one AY frame: moving note_off before note_on would reverse a
    # short note's lifetime and leave it held until another off or end-of-MIDI.
    return Song(events, end_frame, len(mf.tracks), tempo_changes, warnings)


@dataclass
class Note:
    id: int
    pitch: int
    channel: int
    velocity: int
    track: int
    start: int
    key_down: bool = True
    released: int | None = None
    program: int = 0
    drum: bool = False


def _envelope(n: Note, frame: int, v: Voice):
    age = max(0, frame - n.start) * 1000 / FPS
    if v.attack_ms and age < v.attack_ms:
        level = age / v.attack_ms
    elif v.decay_ms and age < v.attack_ms + v.decay_ms:
        level = 1 - (1 - v.sustain) * (age - v.attack_ms) / v.decay_ms
    else:
        level = v.sustain
    if n.released is not None:
        dt = (frame - n.released) * 1000 / FPS
        if v.release_ms == 0 or dt >= v.release_ms:
            return 0.0
        # approximate level at key release, not a fresh attack
        before = max(0, n.released - n.start) * 1000 / FPS
        if v.attack_ms and before < v.attack_ms:
            at_release = before / v.attack_ms
        elif v.decay_ms and before < v.attack_ms + v.decay_ms:
            at_release = 1 - (1 - v.sustain) * (before - v.attack_ms) / v.decay_ms
        else:
            at_release = v.sustain
        level = at_release * (1 - dt / v.release_ms)
    return max(0.0, level)


def _period(hz: float, clock: int) -> int:
    return max(1, min(4095, int(floor(clock / (16 * hz) + 0.5))))


def _note_period(note: Note, frame: int, bend: list[int], cfg: Settings, slot: int):
    voice = cfg.voices[slot]
    cents = voice.vibrato_cents * sin(2 * pi * 5 * (frame - note.start) / FPS)
    semitones = (note.pitch - 69) + (bend[note.channel] / 8192 * 2) + cents / 100
    return _period(440 * 2 ** (semitones / 12), cfg.clock)


def _drum_duration(pitch: int, drum: Drum, mode: str):
    if mode == 'pulse':
        return min(drum.duration_ms, 1000 / FPS)
    if mode == 'compact':
        cap = 20 if pitch in (42, 44) else 60 if drum.tone else 40
        return min(drum.duration_ms, cap)
    return drum.duration_ms


def _drum_shape(pitch: int, drum: Drum, age_ms: float, mode: str):
    """Tone-sweep position and volume factor, shared by scoring and output."""
    duration = _drum_duration(pitch, drum, mode)
    if mode == 'pulse':
        return .5, 1.0  # one constant tone/noise impulse
    if mode == 'compact':
        return (0.0, 1.0) if age_ms < duration / 2 else (1.0, .5)
    x = min(1.0, age_ms / duration)
    return x, (1 - x) ** 1.3


def synthesize(song: Song, cfg: Settings):
    """Return 14-register frames and an explicit loss/voice-usage report."""
    cfg.validate()
    if song.end_frame > 1_000_000:
        raise ValueError('MIDI is too long (> 20000 seconds)')
    cc_volume, cc_expression = [100] * 16, [127] * 16
    pedal, bend, program = [False] * 16, [0] * 16, [0] * 16
    held = defaultdict(deque)  # FIFO by (channel, pitch) across tracks
    notes: list[Note] = []
    assigned: list[int | None] = [None] * 3
    last_regs = [0] * 14
    last_regs[7] = 0x3f
    result = []
    errors = list(song.warnings)
    losses = []
    lost_ids = set()
    lost_frames = 0
    over_frames = 0
    max_poly = 0
    poly_sum = 0
    eid = 0
    cursor = 0
    ignored_drums = 0
    unmatched_off = 0
    simple_drums = cfg.percussion in ('compact', 'pulse')
    drum_epoch = -1
    max_release = max(v.release_ms for v in cfg.voices)
    max_drum = max(_drum_duration(pitch, d, cfg.percussion) for pitch, d in cfg.drums.items())
    tail = max(1, int(floor(max(max_release, max_drum) * FPS / 1000 + 0.5))) + 2
    total_cap = song.end_frame + tail

    def release(note, frame):
        note.key_down = False
        if not pedal[note.channel]:
            note.released = frame

    def amp(note, frame, slot):
        v = cfg.voices[slot]
        return (_envelope(note, frame, v) * v.gain
                * (note.velocity / 127) ** .65
                * cc_volume[note.channel] / 127 * cc_expression[note.channel] / 127)

    finished = False
    for frame in range(total_cap + 1):
        while cursor < len(song.events) and song.events[cursor].frame <= frame:
            e = song.events[cursor]
            cursor += 1
            ch = e.channel
            if e.kind == 'cc':
                if e.a == 7:
                    cc_volume[ch] = e.b
                elif e.a == 11:
                    cc_expression[ch] = e.b
                elif e.a == 64:
                    previous = pedal[ch]
                    pedal[ch] = e.b >= 64
                    if previous and not pedal[ch]:
                        for n in notes:
                            if n.channel == ch and not n.key_down and n.released is None:
                                n.released = frame
                elif e.a == 120:
                    notes = [n for n in notes if n.channel != ch]
                    held = defaultdict(deque, {k: v for k, v in held.items() if k[0] != ch})
                elif e.a == 123:
                    for n in notes:
                        if n.channel == ch and n.key_down:
                            release(n, frame)
                    for k in list(held):
                        if k[0] == ch:
                            held.pop(k)
            elif e.kind == 'program':
                program[ch] = e.a
            elif e.kind == 'bend':
                bend[ch] = e.a
            elif e.kind == 'on':
                if ch == 9:
                    if cfg.percussion == 'off':
                        ignored_drums += 1
                        continue
                    if e.a not in cfg.drums:
                        errors.append(f'Unmapped GM drum note {e.a} (track {e.track + 1})')
                        continue
                    if simple_drums and cfg.drums[e.a].volume > 0:
                        drum_epoch = frame
                eid += 1
                n = Note(eid, e.a, ch, e.b, e.track, frame, program=program[ch], drum=ch == 9)
                notes.append(n)
                if not n.drum:
                    held[ch, e.a].append(n)
            elif e.kind == 'off' and ch != 9:
                q = held[ch, e.a]
                if q:
                    release(q.popleft(), frame)
                else:
                    unmatched_off += 1

        # End-of-MIDI is an implicit key release, including held sustain notes.
        if not finished and frame >= song.end_frame and cursor == len(song.events):
            finished = True
            for n in notes:
                if not n.drum and n.released is None:
                    n.key_down = False
                    n.released = frame
        # Discard sound only when all possible voice envelopes are exhausted.
        notes = [n for n in notes if n.drum and
                 (frame - n.start) * 1000 / FPS < _drum_duration(n.pitch, cfg.drums[n.pitch], cfg.percussion)] + [
                 n for n in notes if not n.drum and
                 (n.released is None or (frame - n.released) * 1000 / FPS < max_release)]
        by_id = {n.id: n for n in notes}
        prev = list(assigned)

        def score(n):
            if n.drum:
                d = cfg.drums[n.pitch]
                age = (frame - n.start) * 1000 / FPS
                _, shape = _drum_shape(n.pitch, d, age, cfg.percussion)
                level = d.volume / 15 * shape * n.velocity / 127
                return cfg.midi_priorities[9] * 40 + d.priority * 20 + level * 30 + (15 if n.start == frame else 0)
            slot = prev.index(n.id) if n.id in prev else 0
            level = amp(n, frame, slot)
            return cfg.midi_priorities[n.channel] * 40 + level * 30 + (8 if n.id in prev else 0) + (15 if n.start == frame else 0) - (20 if n.released is not None else 0)

        candidates = [n for n in notes if
                      ((n.drum and (not simple_drums or cfg.drums[n.pitch].volume > 0)) or
                       (not n.drum and max(amp(n, frame, slot) for slot in range(3)) > .006))]
        poly_sum += len(candidates)
        max_poly = max(max_poly, len(candidates))
        if len(candidates) > 3:
            over_frames += 1
        selectable = candidates
        if simple_drums:
            # A new hit chokes older tails. Choose simultaneous hits by kit priority;
            # keep rejected hits in candidates so the loss report remains explicit.
            drums = [n for n in candidates if n.drum and n.start == drum_epoch]
            chosen = max(drums, key=lambda n: (score(n), -n.id)) if drums else None
            selectable = [n for n in candidates if not n.drum or n is chosen]
        if cfg.overflow == 'drop':
            old = [by_id[x] for x in prev if x in by_id and by_id[x] in selectable]
            remaining = sorted((n for n in selectable if n not in old), key=lambda n: (-score(n), n.id))
            wanted = (old + remaining)[:3]
        else:
            wanted = sorted(selectable, key=lambda n: (-score(n), n.id))[:3]
        wanted_ids = {n.id for n in wanted}
        for n in candidates:
            if n.id not in wanted_ids:
                lost_frames += 1
                if n.id not in lost_ids:
                    lost_ids.add(n.id)
                    if len(losses) < 250:
                        losses.append({'frame': frame, 'midi_channel': n.channel + 1, 'track': n.track + 1,
                                       'note': n.pitch, 'reason': cfg.overflow})

        next_slots: list[Note | None] = [None, None, None]
        for slot, ident in enumerate(prev):
            if ident in wanted_ids:
                next_slots[slot] = by_id[ident]
        for n in wanted:
            if n.id not in [x.id for x in next_slots if x]:
                free_slots = [i for i in range(3) if next_slots[i] is None]
                free = free_slots[-1] if simple_drums and n.drum else free_slots[0]
                next_slots[free] = n
        assigned = [n.id if n else None for n in next_slots]
        regs = last_regs.copy()
        # A muted channel need not toggle its tone/noise gates. Keeping them saves
        # register writes between impulses without changing the audible output.
        mixer = last_regs[7] if simple_drums else 0x3f
        noise_requests = []
        for slot, n in enumerate(next_slots):
            regs[8 + slot] = 0
            if n is None:
                continue
            mixer |= (1 << slot) | (1 << (3 + slot))
            if n.drum:
                d = cfg.drums[n.pitch]
                age = (frame - n.start) * 1000 / FPS
                x, shape = _drum_shape(n.pitch, d, age, cfg.percussion)
                vol = int(floor(d.volume * n.velocity / 127 * shape + .5))
                regs[8 + slot] = max(0, min(15, vol))
                if d.tone and d.start_hz > 0 and d.end_hz > 0:
                    hz = d.start_hz * (d.end_hz / d.start_hz) ** x
                    p = _period(hz, cfg.clock)
                    regs[2 * slot], regs[2 * slot + 1] = p & 255, p >> 8
                    mixer &= ~(1 << slot)
                if d.noise:
                    mixer &= ~(1 << (3 + slot))
                    noise_requests.append((score(n), n.id, d.noise_period))
            else:
                p = _note_period(n, frame, bend, cfg, slot)
                regs[2 * slot], regs[2 * slot + 1] = p & 255, p >> 8
                regs[8 + slot] = max(0, min(15, int(floor(15 * min(1, amp(n, frame, slot)) + .5))))
                mixer &= ~(1 << slot)
        if noise_requests:
            regs[6] = sorted(noise_requests, reverse=True)[0][2]
        regs[7] = mixer
        result.append(regs)
        last_regs = regs
        if cursor == len(song.events) and frame >= song.end_frame and not candidates:
            break

    if unmatched_off:
        errors.append(f'{unmatched_off} note_off event(s) without matching note_on')
    if ignored_drums:
        errors.append(f'{ignored_drums} drum hit(s) disabled by percussion=off')
    report = {
        'fps': FPS, 'clock_hz': cfg.clock, 'frames': len(result), 'duration_seconds': round(len(result)/FPS, 3),
        'tracks': song.source_tracks,
        'note_tracks': len({e.track for e in song.events if e.kind == 'on'}),
        'midi_channels': sorted({e.channel + 1 for e in song.events if e.kind == 'on'}),
        'tempo_changes': song.tempo_changes, 'percussion': cfg.percussion,
        'peak_polyphony': max_poly, 'average_polyphony': round(poly_sum / max(1, len(result)), 3),
        'overflow_frames': over_frames, 'lost_note_instances': len(lost_ids),
        'lost_note_frames': lost_frames, 'losses': losses, 'warnings': errors,
    }
    return result, report


def convert(path: str | Path, cfg: Settings):
    cfg.validate()
    path = Path(path)
    with path.open('rb') as source:
        magic = source.read(4)
    if magic in (b'AYM1', b'AYM2'):
        trace = decode_trace(path.read_bytes())
        count = sum(1 if c[0] == 0x80 else c[0] for c in trace.commands)
        report = {
            'fps': FPS, 'clock_hz': trace.clock, 'frames': count,
            'duration_seconds': round(count / FPS, 3), 'source_format': magic.decode().lower(),
            'tracks': 0, 'note_tracks': 0, 'midi_channels': [], 'tempo_changes': 0,
            'percussion': cfg.percussion, 'peak_polyphony': 0, 'average_polyphony': 0,
            'overflow_frames': 0, 'lost_note_instances': 0, 'lost_note_frames': 0,
            'losses': [], 'warnings': [],
        }
    else:
        song = read_midi(path)
        frames, report = synthesize(song, cfg)
        trace = stream_from_frames(frames, cfg.clock, cfg.loop)
    blob, compression = encode_stream(trace, format=cfg.format, compression=cfg.compression,
                                      max_dictionary=cfg.max_dictionary)
    # All preview/export audio and CSV use the independently decoded result.
    decoded, _ = decode_aym(blob)
    if magic not in (b'AYM1', b'AYM2') and frames != decoded:
        raise AssertionError('codec frame roundtrip mismatch')
    report['format'] = compression['format']
    report['compression'] = compression
    report['bytes'] = len(blob)
    report['compression_bytes_per_frame'] = round(len(blob) / max(len(decoded), 1), 3)
    if compression['saved_bytes'] < 0:
        report['warnings'].append('AYM2 is larger than AYM1; format=auto selects the smaller file.')
    return blob, decoded, report


def write_outputs(path: Path, blob: bytes, frames, report, csv=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    path.with_suffix('.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    if csv:
        import csv as csvmod
        with path.with_suffix('.frames.csv').open('w', newline='', encoding='utf8') as fh:
            writer = csvmod.writer(fh)
            writer.writerow(['frame'] + [f'R{i}' for i in range(14)])
            writer.writerows([[i] + row for i, row in enumerate(frames)])
        with path.with_suffix('.writes.csv').open('w', newline='', encoding='utf8') as fh:
            writer = csvmod.writer(fh)
            writer.writerow(['frame', 'register', 'value'])
            for frame, writes in enumerate(decode_trace(blob).slots()):
                writer.writerows((frame, register, value) for register, value in writes)
