"""Desktop workflow regression tests. Require Tk (and a display on Linux)."""
import json
from pathlib import Path
from threading import Event
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import mido

from midi2aym1.core import Settings, convert, decode_aym1
from midi2aym1.gui import InputError, Window
from midi2aym1.presets import BUILTIN_PRESET_IDS, make_builtin_preset


class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk display unavailable: {exc}')
        self.root.tk.call('tk', 'scaling', 4 / 3)
        self.root.withdraw()
        self.app = Window(self.root)
        self.app.player = Mock(channel=None)
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.source = self.folder / 'test song.mid'
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        track = mido.MidiTrack()
        midi.tracks.append(track)
        for note in (60, 64, 67, 71):
            track.append(mido.Message('note_on', note=note, velocity=100))
        for i, note in enumerate((60, 64, 67, 71)):
            track.append(mido.Message('note_off', note=note, time=120 if i == 0 else 0))
        midi.save(str(self.source))
        self.root.update()

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()
        self.assertEqual(self.callback_errors, [], 'Unhandled Tk callback error')

    def wait_for_job(self):
        deadline = time.monotonic() + 8
        while self.app.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.busy, 'Background job did not finish')

    def choose_builtin(self, key):
        self.app.builtin_combo.current(BUILTIN_PRESET_IDS.index(key))
        self.app.builtin_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()

    def test_language_keeps_drafts_selection_and_active_tab(self):
        self.app.voice_vars[0][0].set('1.75')
        self.app.voice_vars[1][1].set('')  # Even an incomplete edit must survive.
        self.app.priority_vars[9].set('7')
        self.app.drum_vars['duration_ms'].set('240')
        self.app.clock_var.set('1 773 400')
        self.app.loop_var.set(True)
        self.app.notebook.select(self.app.drums_tab)
        self.app.search_var.set('snare')
        revision = self.app.revision
        self.app.lang_var.set('EN')
        self.app.change_language()
        self.root.update()
        self.assertEqual(self.app.notebook.index('current'), 1)
        self.assertEqual(self.app.voice_vars[0][0].get(), '1.75')
        self.assertEqual(self.app.voice_vars[1][1].get(), '')
        self.assertEqual(self.app.drum_vars['duration_ms'].get(), '240')
        self.assertEqual(self.app.priority_vars[9].get(), '7')
        self.assertEqual(self.app.clock_var.get(), '1 773 400')
        self.assertTrue(self.app.loop_var.get())
        self.assertEqual(self.app.search_var.get(), 'snare')
        self.assertEqual(set(self.app.tree.get_children()), {'38', '40'})
        self.assertEqual(self.app.revision, revision)

    def test_invalid_edits_do_not_modify_settings_and_show_inline_error(self):
        before = self.app.cfg.to_dict()
        self.app.voice_vars[0][0].set('2')
        self.app.drum_vars['noise_period'].set('99')
        with self.assertRaises(InputError):
            self.app.read_settings()
        self.assertEqual(self.app.cfg.to_dict(), before)
        self.app.set_source(self.source)
        with patch('midi2aym1.gui.messagebox.showerror') as error:
            self.app.run('convert')
        error.assert_not_called()
        self.assertFalse(self.app.busy)
        self.assertTrue(self.app.entries['drum:noise_period'].instate(['invalid']))
        self.assertTrue(self.app.error_vars['drum'].get())
        self.app.drum_vars['noise_period'].set('12')
        self.assertEqual(self.app.read_settings().voices[0].gain, 2)

    def test_switching_drums_commits_valid_edits_and_preserves_invalid_draft(self):
        self.app.drum_vars['volume'].set('9')
        self.app.tree.selection_set('42')
        self.app.select_drum()
        self.assertEqual(self.app.cfg.drums[35].volume, 9)
        self.assertEqual(self.app.selected_drum, 42)
        self.app.drum_vars['duration_ms'].set('bad')
        self.app.tree.selection_set('38')
        self.app.select_drum()
        self.assertEqual(self.app.selected_drum, 42)
        self.assertEqual(self.app.drum_vars['duration_ms'].get(), 'bad')
        self.app.search_var.set('snare')
        self.app.tree.selection_set('38')
        self.app.select_drum()
        self.assertEqual(self.app.selected_drum, 42)
        self.assertEqual(self.app.tree.selection(), ())

    def test_preset_roundtrip_includes_pending_drum_and_channel_edits(self):
        preset = self.folder / 'kit.json'
        self.app.voice_vars[2][5].set('12.5')
        self.app.priority_vars[9].set('8')
        self.app.drum_vars['volume'].set('6')
        self.app.clock_var.set('1773400')
        self.app.loop_var.set(True)
        with patch('midi2aym1.gui.filedialog.asksaveasfilename', return_value=str(preset)):
            self.app.save_preset()
        saved = Settings.from_dict(json.loads(preset.read_text(encoding='utf8')))
        self.assertEqual(saved.drums[35].volume, 6)
        self.assertEqual(saved.midi_priorities[9], 8)
        self.assertEqual(saved.voices[2].vibrato_cents, 12.5)
        self.app.voice_vars[2][5].set('0')
        with patch('midi2aym1.gui.filedialog.askopenfilename', return_value=str(preset)):
            self.app.load_preset()
        self.assertEqual(self.app.read_settings().to_dict(), saved.to_dict())

    def test_conversion_report_becomes_stale_after_edit(self):
        self.assertTrue(self.app.action_buttons['convert'].instate(['disabled']))
        self.app.set_source(self.source)
        self.app.run('convert')
        self.assertTrue(self.app.action_buttons['export'].instate(['disabled']))
        self.wait_for_job()
        self.assertGreater(self.app.last_report['lost_note_instances'], 0)
        self.assertEqual(self.app.report_revision, self.app.revision)
        report = self.app.last_report
        self.app.priority_vars[0].set('3')
        self.assertIs(self.app.last_report, report)
        self.assertNotEqual(self.app.report_revision, self.app.revision)
        self.assertEqual(self.app.report_state.cget('text'), self.app.tr('stale'))
        self.app.show_report()
        self.root.update()

    def test_export_matches_core_and_writes_optional_outputs(self):
        dest = self.folder / 'output song.aym'
        self.app.set_source(self.source)
        self.app.clock_var.set('1773400')
        self.app.loop_var.set(True)
        self.app.csv_var.set(True)
        self.app.wav_var.set(True)
        expected, _, _ = convert(self.source, self.app.read_settings())
        with patch('midi2aym1.gui.filedialog.asksaveasfilename', return_value=str(dest)):
            self.app.run('export')
        self.wait_for_job()
        self.assertEqual(dest.read_bytes(), expected)
        _, header = decode_aym1(dest.read_bytes())
        self.assertTrue(header['loop'])
        self.assertEqual(header['clock_hz'], 1_773_400)
        self.assertEqual(json.loads(dest.with_suffix('.report.json').read_text())['bytes'], len(expected))
        self.assertTrue(dest.with_suffix('.wav').read_bytes().startswith(b'RIFF'))
        self.assertTrue(dest.with_suffix('.frames.csv').read_text().startswith('frame,R0'))

    def test_stop_cancels_pending_audio_without_discarding_conversion_report(self):
        started, release = Event(), Event()

        def render(*_):
            started.set()
            release.wait(5)
            return b'pcm'

        self.app.set_source(self.source)
        with patch('midi2aym1.gui.render_pcm', side_effect=render):
            self.app.run('play')
            self.assertTrue(started.wait(3))
            self.assertTrue(self.app.stop_button.instate(['!disabled']))
            self.app.stop()
            release.set()
            self.wait_for_job()
        self.app.player.play.assert_not_called()
        self.assertIsNotNone(self.app.last_report)
        self.assertFalse(self.app.playing)

    def test_worker_error_recovers_controls(self):
        self.app.set_source(self.folder / 'missing.mid')
        with patch('midi2aym1.gui.messagebox.showerror') as error:
            self.app.run('convert')
            self.wait_for_job()
        error.assert_called_once()
        self.assertTrue(self.app.action_buttons['convert'].instate(['!disabled']))
        self.assertIsNone(self.app.last_report)

    def test_small_window_reveals_invalid_field_and_keeps_actions_visible(self):
        self.root.geometry('1100x700+20+20')
        self.root.deiconify()
        self.root.update()
        self.app.set_source(self.source)
        self.app.voice_vars[2][0].set('nan')
        self.app.run('convert')
        self.root.update()
        entry = self.app.entries['voice2:gain']
        viewport = self.app.voices_tab.canvas
        offset = entry.winfo_rooty() - viewport.winfo_rooty()
        self.assertGreaterEqual(offset, 0)
        self.assertLessEqual(offset + entry.winfo_height(), viewport.winfo_height())
        self.assertGreater(viewport.yview()[0], 0)
        for button in self.app.action_buttons.values():
            right = button.winfo_rootx() + button.winfo_width()
            bottom = button.winfo_rooty() + button.winfo_height()
            self.assertLessEqual(right, self.root.winfo_rootx() + self.root.winfo_width())
            self.assertLessEqual(bottom, self.root.winfo_rooty() + self.root.winfo_height())

    def test_drum_preview_without_source_and_natural_playback_end(self):
        self.app.player.channel = Mock()
        self.app.player.channel.get_busy.return_value = True
        self.app.test_drum()
        self.wait_for_job()
        self.app.player.play.assert_called_once()
        self.assertGreater(len(self.app.player.play.call_args.args[0]), 0)
        self.assertTrue(self.app.playing)
        self.assertIsNone(self.app.last_report)
        self.app.player.channel.get_busy.return_value = False
        deadline = time.monotonic() + 2
        while self.app.playing and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app.playing)
        self.assertTrue(self.app.stop_button.instate(['disabled']))

    def test_percussion_mode_control_survives_language_and_preset_changes(self):
        self.app.percussion_combo.current(1)
        self.app.percussion_combo.event_generate('<<ComboboxSelected>>')
        self.assertEqual(self.app.read_settings().percussion, 'compact')
        self.app.lang_var.set('EN')
        self.app.change_language()
        self.assertEqual(self.app.percussion_combo.get(), 'Compact')
        self.assertEqual(self.app.perc_hint.cget('text'), self.app.tr('compact_hint'))
        self.app.percussion_combo.current(2)
        self.app.percussion_combo.event_generate('<<ComboboxSelected>>')
        preset = self.folder / 'pulse.json'
        with patch('midi2aym1.gui.filedialog.asksaveasfilename', return_value=str(preset)):
            self.app.save_preset()
        self.assertEqual(json.loads(preset.read_text())['percussion'], 'pulse')
        self.app.perc_var.set('off')
        with patch('midi2aym1.gui.filedialog.askopenfilename', return_value=str(preset)):
            self.app.load_preset()
        self.assertEqual(self.app.percussion_combo.get(), 'Pulses')
        self.assertEqual(self.app.perc_hint.cget('text'), self.app.tr('pulse_hint'))

    def test_builtin_restores_sound_settings_but_preserves_clock_and_loop(self):
        self.app.set_source(self.source)
        self.app.run('convert')
        self.wait_for_job()
        self.app.clock_var.set('1773400')
        self.app.loop_var.set(True)
        self.app.voice_vars[0][0].set('unfinished')
        self.app.drum_vars['volume'].set('bad')
        with patch('midi2aym1.gui.filedialog.askopenfilename') as open_file:
            self.choose_builtin('compact-drums')
        open_file.assert_not_called()
        cfg = self.app.read_settings()
        expected = make_builtin_preset('compact-drums', clock=1_773_400, loop=True)
        self.assertEqual(cfg.to_dict(), expected.to_dict())
        self.assertEqual(self.app.builtin_preset_id, 'compact-drums')
        self.assertIsNone(self.app.preset_path)
        self.assertFalse(self.app.preset_modified)
        self.assertNotEqual(self.app.revision, self.app.report_revision)

    def test_builtin_modified_marker_and_language_changes(self):
        self.app.clock_var.set('1773400')
        self.app.loop_var.set(True)
        self.assertFalse(self.app.preset_modified)
        self.choose_builtin('compact-drums')
        self.app.voice_vars[0][4].set('20')
        self.assertTrue(self.app.preset_modified)
        self.app.lang_var.set('EN')
        self.app.change_language()
        self.assertEqual(self.app.builtin_preset_id, 'compact-drums')
        self.assertEqual(self.app.builtin_combo.get(), self.app.tr('builtin_compact'))
        self.assertIn(self.app.tr('preset_modified'), self.app.preset_label.cget('text'))
        self.assertEqual(self.app.voice_vars[0][4].get(), '20')
        self.choose_builtin('compact-drums')
        self.assertFalse(self.app.preset_modified)
        self.assertEqual(self.app.read_settings().voices[0].release_ms, 0)
        self.assertEqual(self.app.status.get(), self.app.tr('builtin_applied'))

    def test_saving_builtin_edits_and_loading_json_updates_preset_origin(self):
        self.choose_builtin('compact-drums')
        self.app.voice_vars[0][0].set('.5')
        preset = self.folder / 'custom.json'
        with patch('midi2aym1.gui.filedialog.asksaveasfilename', return_value=str(preset)):
            self.app.save_preset()
        self.assertIsNone(self.app.builtin_preset_id)
        self.assertFalse(self.app.preset_modified)
        self.assertEqual(self.app.builtin_combo.get(), self.app.tr('preset_from_json'))
        self.choose_builtin('default')
        with patch('midi2aym1.gui.filedialog.askopenfilename', return_value=str(preset)):
            self.app.load_preset()
        self.assertEqual(self.app.read_settings().voices[0].gain, .5)
        self.assertIsNone(self.app.builtin_preset_id)
        self.assertIn(preset.name, self.app.preset_label.cget('text'))

    def test_invalid_clock_prevents_partial_preset_application(self):
        before = self.app.cfg.to_dict()
        self.app.clock_var.set('bad clock')
        self.choose_builtin('compact-drums')
        self.assertEqual(self.app.cfg.to_dict(), before)
        self.assertEqual(self.app.builtin_preset_id, 'default')
        self.assertEqual(self.app.builtin_combo.get(), self.app.tr('builtin_default'))
        self.assertTrue(self.app.entries['target:clock'].instate(['invalid']))


if __name__ == '__main__':
    unittest.main()
