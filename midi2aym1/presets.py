"""Built-in sound presets; loading them never depends on external JSON files."""
from __future__ import annotations

from dataclasses import dataclass

from .core import DEFAULT_CLOCK, Settings


@dataclass(frozen=True)
class BuiltinPreset:
    key: str
    name_key: str
    description_key: str
    percussion: str
    envelope: str


BUILTIN_PRESETS = (
    BuiltinPreset('default', 'builtin_default', 'builtin_default_hint', 'noise', 'default'),
    BuiltinPreset('tight-melody', 'builtin_tight', 'builtin_tight_hint', 'noise', 'tight'),
    BuiltinPreset('compact-drums', 'builtin_compact', 'builtin_compact_hint', 'compact', 'flat'),
    BuiltinPreset('pulse-drums', 'builtin_pulse', 'builtin_pulse_hint', 'pulse', 'flat'),
    BuiltinPreset('tones-only', 'builtin_tones', 'builtin_tones_hint', 'off', 'flat'),
)
BUILTIN_PRESET_IDS = tuple(preset.key for preset in BUILTIN_PRESETS)


def make_builtin_preset(key: str, *, clock: int = DEFAULT_CLOCK, loop: bool = False) -> Settings:
    """Return an independent settings snapshot, with explicit target options."""
    preset = next((preset for preset in BUILTIN_PRESETS if preset.key == key), None)
    if preset is None:
        raise ValueError(f'Unknown built-in preset: {key}')
    cfg = Settings(clock=clock, loop=loop, percussion=preset.percussion)
    if preset.envelope != 'default':
        cfg.midi_priorities[:2] = [6, 6]
        for voice in cfg.voices:
            voice.release_ms = 0
            if preset.envelope == 'flat':
                voice.attack_ms = voice.decay_ms = 0
                voice.sustain = .8
                voice.vibrato_cents = 0
    cfg.validate()
    return cfg
