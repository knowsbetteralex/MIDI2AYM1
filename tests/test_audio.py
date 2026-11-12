"""Check preview timing with a real pygame mixer and SDL's silent audio driver."""
from io import BytesIO
import os
import unittest
from unittest.mock import patch
import wave

import pygame

from midi2aym1.audio import Player, SAMPLE_RATE, render_pcm, render_wav
from midi2aym1.core import FPS


class PlaybackTimingTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'SDL_AUDIODRIVER': 'dummy'})
        self.environment.start()
        pygame.mixer.quit()
        self.player = Player()

    def tearDown(self):
        self.player.stop()
        pygame.mixer.quit()
        self.environment.stop()

    def test_pcm_wav_and_playback_have_the_same_duration(self):
        registers = [0, 0, 0, 0, 0, 0, 1, 63, 0, 0, 0, 0, 0, 0]
        frames = [registers[:] for _ in range(75)]
        pcm = render_pcm(frames, 2_000_000)
        with wave.open(BytesIO(render_wav(frames, 2_000_000))) as wav:
            wav_seconds = wav.getnframes() / wav.getframerate()
        self.player.play(pcm)
        self.assertEqual(wav_seconds, len(frames) / FPS)
        self.assertAlmostEqual(self.player.sound.get_length(), wav_seconds, places=4)
        self.assertIsNotNone(self.player.channel)
        self.player.stop()
        self.assertIsNone(self.player.channel)

    def test_preinitialized_mixer_does_not_change_preview_speed(self):
        one_second_pcm = b'\0' * (SAMPLE_RATE * 2)
        # A backend can negotiate stereo; another caller can also initialize pygame first.
        for rate, bits, channels in ((22050, -16, 2), (44100, -16, 2),
                                     (48000, -16, 1), (22050, 16, 1)):
            with self.subTest(rate=rate, bits=bits, channels=channels):
                pygame.mixer.init(frequency=rate, size=bits, channels=channels, allowedchanges=0)
                try:
                    self.player.play(one_second_pcm)
                    self.assertAlmostEqual(self.player.sound.get_length(), 1.0, places=4)
                finally:
                    self.player.stop()
                    pygame.mixer.quit()


if __name__ == '__main__':
    unittest.main()
