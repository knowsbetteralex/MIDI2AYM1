"""Software AY-3-8910 register preview. Approximate output DAC/noise, not SoundFont.

Preview reads the decoded AYM1 stream, i.e. the exact register states on Z80.
"""
from __future__ import annotations

from array import array
from io import BytesIO
import sys
import wave

from .core import FPS

# The AY volume DAC has strongly non-linear levels, unlike linear MIDI volume.
DAC = (0, .009, .013, .019, .027, .039, .057, .083,
       .12, .17, .25, .35, .48, .65, .83, 1.0)
SAMPLE_RATE = 22050  # divisible by 50 (441 samples per frame)


def render_pcm(frames: list[list[int]], clock: int, muted=(False, False, False)) -> bytes:
    if clock <= 0:
        raise ValueError('invalid AY clock')
    out = array('h')
    tone_phase = [0.0, 0.0, 0.0]
    noise_phase = 0.0
    lfsr = 0x1ffff
    noise_bit = 1
    dc = 0.0
    # The phase and noise LFSR continue between frames and register writes.
    for regs in frames:
        mixer = regs[7]
        noise_period = max(1, regs[6] & 31)
        noise_increment = clock / (16 * noise_period * SAMPLE_RATE)
        tone_inc = [clock / (16 * max(1, regs[2*i] | ((regs[2*i+1] & 15) << 8)) * SAMPLE_RATE)
                    for i in range(3)]
        for _ in range(SAMPLE_RATE // FPS):
            noise_phase += noise_increment
            while noise_phase >= 1:
                noise_phase -= 1
                feedback = (lfsr ^ (lfsr >> 3)) & 1
                lfsr = ((lfsr >> 1) | (feedback << 16)) & 0x1ffff
                noise_bit = lfsr & 1
            mixed = 0.0
            for ch in range(3):
                tone_phase[ch] = (tone_phase[ch] + tone_inc[ch]) % 1.0
                if muted[ch] or (regs[8+ch] & 15) == 0:
                    continue
                tone_ok = bool(mixer & (1 << ch)) or tone_phase[ch] < .5
                noise_ok = bool(mixer & (1 << (ch+3))) or bool(noise_bit)
                if tone_ok and noise_ok:
                    mixed += DAC[regs[8+ch] & 15]
            # Remove DC, preserving the characteristic chiptune square edge.
            dc += .0009 * (mixed - dc)
            value = int(max(-32767, min(32767, (mixed - dc) * 12000)))
            out.append(value)
    if sys.byteorder != 'little':
        out.byteswap()
    return out.tobytes()


def render_wav(frames: list[list[int]], clock: int, muted=(False, False, False)) -> bytes:
    buf = BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(render_pcm(frames, clock, muted))
    return buf.getvalue()


class Player:
    """pygame-ce mixer permits Start/Stop without launching an external program."""
    def __init__(self):
        self.sound = None
        self.channel = None

    def play(self, pcm: bytes):
        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError('Audio playback requires pygame-ce (pip install -r requirements.txt)') from exc
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=1024)
        self.stop()
        self.sound = pygame.mixer.Sound(buffer=pcm)
        self.channel = self.sound.play()

    def stop(self):
        if self.channel is not None:
            self.channel.stop()
            self.channel = None
        self.sound = None
