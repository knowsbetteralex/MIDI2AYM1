"""Bilingual studio workbench for MIDI -> AY AYM1."""
from __future__ import annotations

from math import isfinite
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .audio import Player, render_pcm, render_wav
from .core import (Drum, Event, GM_DRUM_NAMES, PERCUSSION_MODES, Settings, Song, Voice,
                   convert, synthesize, write_outputs)
from .presets import BUILTIN_PRESETS, make_builtin_preset
from .ui_style import (AMBER, BG, FIELD, FG, MUTED, TEAL, VOICE_COLOURS,
                       ScrollFrame, configure_theme)
from .ui_text import TEXT

VOICE_FIELDS = ('gain', 'attack_ms', 'decay_ms', 'sustain', 'release_ms', 'vibrato_cents')
VOICE_LABELS = ('gain', 'attack', 'decay', 'sustain', 'release', 'vibrato')
DRUM_FIELDS = ('volume', 'duration_ms', 'noise_period', 'start_hz', 'end_hz', 'priority')
DRUM_LABELS = ('volume', 'duration', 'noise_period', 'start_hz', 'end_hz', 'priority')
CLOCK_PRESETS = {'Delta-C · 2 000 000 Hz': 2_000_000, 'ZX 128K · 1 773 400 Hz': 1_773_400}


class InputError(ValueError):
    def __init__(self, key, message):
        super().__init__(message)
        self.key = key


def size_label(size):
    return f'{size / 1024:.1f} KB' if size >= 1024 else f'{size} B'


class Window:
    def __init__(self, root: tk.Tk):
        self.root = root
        configure_theme(root)
        self.cfg = Settings()
        self.language = 'ru'
        self.path: Path | None = None
        self.preset_path: Path | None = None
        self.builtin_preset_id: str | None = 'default'
        self.preset_modified = False
        self.player = Player()
        self.busy = False
        self.playing = False
        self.active_action = None
        self.play_generation = 0
        self.revision = 0
        self.last_report = None
        self.report_revision = None
        self.selected_drum = 35
        self._guard = False
        self._closed = False
        self._reveal_after_id = None
        self._results = Queue()
        self.status = tk.StringVar(root)
        self._status_message = ('ready', {})
        self.csv_var = tk.BooleanVar(root, False)
        self.wav_var = tk.BooleanVar(root, True)
        self.clock_var = tk.StringVar(root, next(iter(CLOCK_PRESETS)))
        self.loop_var = tk.BooleanVar(root, False)
        self.overflow_var = tk.StringVar(root, 'steal')
        self.perc_var = tk.StringVar(root, 'noise')
        self.lang_var = tk.StringVar(root, 'RU')
        self.search_var = tk.StringVar(root)
        self.builtin_display = tk.StringVar(root)
        self.voice_vars = [[tk.StringVar(root, str(getattr(v, field))) for field in VOICE_FIELDS]
                           for v in self.cfg.voices]
        self.priority_vars = [tk.StringVar(root, str(x)) for x in self.cfg.midi_priorities]
        self.drum_vars = {field: tk.StringVar(root) for field in DRUM_FIELDS}
        self.drum_tone = tk.BooleanVar(root)
        self.drum_noise = tk.BooleanVar(root)
        self.error_vars = {key: tk.StringVar(root) for key in
                           ('target', 'voice0', 'voice1', 'voice2', 'drum', 'priorities')}
        self._load_drum(35)
        for var in [self.clock_var, self.loop_var, self.overflow_var, self.perc_var,
                    *self.priority_vars, *self.drum_vars.values(), self.drum_tone, self.drum_noise]:
            var.trace_add('write', self._settings_changed)
        for i, row in enumerate(self.voice_vars):
            for var in row:
                var.trace_add('write', lambda *_, i=i: self._voice_changed(i))
        self.search_var.trace_add('write', lambda *_: self.filter_drums())
        width = min(1440, root.winfo_screenwidth() - 80)
        height = min(940, root.winfo_screenheight() - 100)
        root.geometry(f'{width}x{height}')
        minimum_height = round(700 * max(1, float(root.tk.call('tk', 'scaling')) / (4 / 3)))
        root.minsize(min(1100, width), min(minimum_height, height))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<MouseWheel>', self._mousewheel)
        root.bind('<Button-4>', lambda e: self._mousewheel(e, -1))
        root.bind('<Button-5>', lambda e: self._mousewheel(e, 1))
        self.build()
        self._poll_id = root.after(60, self._poll)

    def tr(self, key, **values):
        return TEXT[self.language][key].format(**values)

    def set_status(self, key, **values):
        self._status_message = (key, values)
        self.status.set(self.tr(key, **values))

    def _card(self, parent, padding=16):
        return ttk.Frame(parent, style='Card.TFrame', padding=padding)

    def _label(self, parent, key, style='Card.TLabel', **kwargs):
        return ttk.Label(parent, text=self.tr(key), style=style, **kwargs)

    def _button(self, parent, key, command, style='TButton', mutable=True):
        button = ttk.Button(parent, text=self.tr(key), command=command, style=style)
        if mutable:
            self.mutable_controls.append(button)
        return button

    def _entry(self, parent, variable, key, width=8):
        entry = ttk.Entry(parent, textvariable=variable, width=width, font='TkFixedFont')
        self.entries[key] = entry
        self.mutable_controls.append(entry)
        return entry

    def _scroll_tab(self, key):
        tab = ScrollFrame(self.notebook)
        self.notebook.add(tab, text=self.tr(key))
        self.scroll_frames.append(tab)
        return tab

    def build(self):
        tab_index = self.notebook.index('current') if hasattr(self, 'notebook') else 0
        for item in self.root.winfo_children():
            item.destroy()
        self.root.title(self.tr('title'))
        self.mutable_controls = []
        self.entries = {}
        self.scroll_frames = []
        shell = ttk.Frame(self.root, padding=(20, 16, 20, 12))
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)
        self._build_header(shell)
        body = ttk.Frame(shell)
        body.grid(row=1, column=0, sticky='nsew')
        sidebar_width = max(294, round(252 * float(self.root.tk.call('tk', 'scaling')) / (4 / 3)) + 24)
        body.columnconfigure(0, minsize=sidebar_width)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_sidebar(body)
        main = ttk.Frame(body)
        main.grid(row=0, column=1, sticky='nsew')
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        heading = ttk.Frame(main)
        heading.grid(row=0, column=0, sticky='ew', pady=(0, 14))
        self._label(heading, 'workspace', 'Title.TLabel').pack(anchor='w')
        self._label(heading, 'workspace_hint', 'Muted.TLabel').pack(anchor='w', pady=(3, 0))
        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=1, column=0, sticky='nsew')
        self._build_voices()
        self._build_drums()
        self._build_priorities()
        self.notebook.select(tab_index)
        self._build_report(main)
        self._build_transport(shell)
        self._update_source()
        self._update_report()
        self._update_buttons()
        for control in self.mutable_controls:
            control.bind('<FocusIn>', lambda event: self._reveal_entry(event.widget), add='+')
        key, values = self._status_message
        self.set_status(key, **values)

    def _build_header(self, parent):
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 20))
        header.columnconfigure(1, weight=1)
        brand = ttk.Frame(header)
        brand.grid(row=0, column=0, sticky='w', padx=(0, 24))
        logo = tk.Canvas(brand, width=44, height=44, background=TEAL, highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        logo.create_line(9, 26, 9, 17, 17, 17, 17, 32, 25, 32, 25, 10, 33, 10, 33, 26,
                         fill='#10271f', width=3)
        ttk.Label(brand, text='MIDI2AYM1', style='Brand.TLabel').grid(row=0, column=1, sticky='w')
        self._label(brand, 'subtitle', 'Muted.TLabel').grid(row=1, column=1, sticky='w')
        tools = ttk.Frame(header)
        tools.grid(row=0, column=1, sticky='e')
        for i, (key, callback) in enumerate((('open', self.open), ('load_preset', self.load_preset),
                                             ('save_preset', self.save_preset))):
            self._button(tools, key, callback).grid(row=0, column=i, padx=(0, 8))
        lang = ttk.Combobox(tools, textvariable=self.lang_var, state='readonly',
                            values=('RU', 'EN'), width=4)
        lang.grid(row=0, column=3, padx=(0, 8))
        lang.bind('<<ComboboxSelected>>', self.change_language)
        self.mutable_controls.append(lang)
        self._button(tools, 'help', self.help, mutable=False).grid(row=0, column=4)
        self._header_compact = None

        def layout(event):
            compact = event.width < brand.winfo_reqwidth() + tools.winfo_reqwidth() + 24
            if compact != self._header_compact:
                self._header_compact = compact
                tools.grid(row=1 if compact else 0, column=0 if compact else 1,
                           columnspan=2 if compact else 1, sticky='w' if compact else 'e',
                           pady=(12, 0) if compact else 0)
        header.bind('<Configure>', layout)

    def _build_sidebar(self, parent):
        sidebar = ScrollFrame(parent)
        sidebar.grid(row=0, column=0, sticky='nsew', padx=(0, 24))
        self.scroll_frames.append(sidebar)
        presets = self._card(sidebar.content)
        presets.pack(fill='x', pady=(0, 16))
        self._label(presets, 'sound_preset', 'Section.TLabel').pack(anchor='w')
        self.builtin_combo = ttk.Combobox(presets, state='readonly', width=1,
            textvariable=self.builtin_display, values=[self.tr(p.name_key) for p in BUILTIN_PRESETS])
        self.builtin_combo.pack(fill='x', pady=(10, 8))
        self.builtin_combo.bind('<<ComboboxSelected>>', self.apply_builtin_preset)
        self.mutable_controls.append(self.builtin_combo)
        self.preset_description = ttk.Label(presets, style='CardMuted.TLabel', wraplength=210)
        self.preset_description.pack(anchor='w')
        self.preset_label = ttk.Label(presets, style='CardMuted.TLabel', wraplength=210)
        self.preset_label.pack(anchor='w', pady=(8, 0))
        source = self._card(sidebar.content)
        source.pack(fill='x', pady=(0, 16))
        self._label(source, 'source', 'Section.TLabel').pack(anchor='w')
        self.source_name = ttk.Label(source, style='CardTitle.TLabel', wraplength=210)
        self.source_name.pack(anchor='w', pady=(16, 5))
        self.source_info = ttk.Label(source, style='CardMuted.TLabel', wraplength=210)
        self.source_info.pack(anchor='w')
        ttk.Separator(source).pack(fill='x', pady=14)
        self.source_state = ttk.Label(source, style='Success.TLabel', wraplength=210)
        self.source_state.pack(anchor='w')
        ttk.Label(source, text='MIDI → 3 × AY → AYM1', style='CardMuted.TLabel').pack(anchor='w', pady=(6, 0))
        target = self._card(sidebar.content)
        target.pack(fill='x', pady=(0, 16))
        self._label(target, 'target', 'Section.TLabel').pack(anchor='w', pady=(0, 16))
        self._label(target, 'clock', 'CardMuted.TLabel').pack(anchor='w')
        clock = ttk.Combobox(target, textvariable=self.clock_var, values=tuple(CLOCK_PRESETS), width=1)
        clock.pack(fill='x', pady=(6, 16))
        self.entries['target:clock'] = clock
        self.mutable_controls.append(clock)
        self._label(target, 'overflow', 'CardMuted.TLabel').pack(anchor='w')
        self.overflow_display = tk.StringVar(self.root, self.tr(self.overflow_var.get()))
        overflow = ttk.Combobox(target, textvariable=self.overflow_display, state='readonly',
                               values=[self.tr('steal'), self.tr('drop')], width=1)
        overflow.pack(fill='x', pady=(6, 8))
        overflow.bind('<<ComboboxSelected>>', lambda _: self.overflow_var.set(
            'steal' if self.overflow_display.get() == self.tr('steal') else 'drop'))
        self.mutable_controls.append(overflow)
        self.overflow_hint = ttk.Label(target, style='CardMuted.TLabel', wraplength=210)
        self.overflow_hint.pack(anchor='w')
        ttk.Separator(target).pack(fill='x', pady=14)
        loop = ttk.Checkbutton(target, text=self.tr('loop'), variable=self.loop_var)
        loop.pack(anchor='w')
        self.mutable_controls.append(loop)
        self._label(target, 'timing', 'CardMuted.TLabel').pack(anchor='w', pady=(8, 0))
        ttk.Label(target, textvariable=self.error_vars['target'], style='Error.TLabel',
                  wraplength=210).pack(anchor='w', pady=(5, 0))
        formats = self._card(sidebar.content)
        formats.pack(fill='x')
        self._label(formats, 'format', 'Section.TLabel').pack(anchor='w')
        ttk.Label(formats, text='AYM1 v1  ·  ZX 48K', style='CardTitle.TLabel').pack(anchor='w', pady=(12, 8))
        self._label(formats, 'report_always', 'CardMuted.TLabel', wraplength=210).pack(anchor='w')

    def _build_voices(self):
        self.voices_tab = self._scroll_tab('voices')
        content = self.voices_tab.content
        self.voice_cards = []
        self.envelopes = []
        for i, row in enumerate(self.voice_vars):
            card = self._card(content)
            self.voice_cards.append(card)
            card.columnconfigure((0, 1), weight=1, uniform='fields')
            header = ttk.Frame(card, style='CardBody.TFrame')
            header.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 12))
            ttk.Label(header, text='ABC'[i], style=f'Voice{i}.TLabel').grid(row=0, column=0, rowspan=2, padx=(0, 16))
            ttk.Label(header, text=self.tr('voice', letter='ABC'[i]), style='CardTitle.TLabel').grid(row=0, column=1, sticky='w')
            self._label(header, 'envelope', 'CardMuted.TLabel').grid(row=1, column=1, sticky='w')
            canvas = tk.Canvas(card, background=FIELD, highlightthickness=0, height=80, width=1)
            canvas.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 8))
            canvas.bind('<Configure>', lambda _, i=i: self._draw_envelope(i))
            self.envelopes.append(canvas)
            for cell, field_index in enumerate((0, 3, 1, 2, 4, 5)):
                r, c = divmod(cell, 2)
                wrapper = ttk.Frame(card, style='CardBody.TFrame')
                wrapper.grid(row=r + 2, column=c, sticky='ew', padx=(0, 12) if c == 0 else 0, pady=(0, 8))
                self._label(wrapper, VOICE_LABELS[field_index], 'CardMuted.TLabel').pack(anchor='w', pady=(0, 4))
                self._entry(wrapper, row[field_index], f'voice{i}:{VOICE_FIELDS[field_index]}').pack(fill='x')
            ttk.Label(card, textvariable=self.error_vars[f'voice{i}'], style='Error.TLabel',
                      wraplength=220).grid(row=5, column=0, columnspan=2, sticky='ew')
        self._voice_columns = None

        def layout(event):
            available = self.voices_tab.canvas.winfo_width()
            required = max(c.winfo_reqwidth() for c in self.voice_cards)
            columns = 3 if available >= 3 * required + 48 else 2 if available >= 2 * required + 32 else 1
            if columns != self._voice_columns:
                self._voice_columns = columns
                for c in range(3):
                    content.columnconfigure(c, weight=1 if c < columns else 0, uniform='cards' if c < columns else '')
                for i, card in enumerate(self.voice_cards):
                    card.grid(row=i // columns, column=i % columns, sticky='new',
                              padx=(0, 16) if i % columns < columns - 1 else 0, pady=(0, 12))
        self.voices_tab.canvas.bind('<Configure>', layout, add='+')
        for i, card in enumerate(self.voice_cards):
            card.grid(row=0, column=i, sticky='new', padx=(0, 16) if i < 2 else 0)
            content.columnconfigure(i, weight=1, uniform='cards')

    def _build_drums(self):
        self.drums_tab = self._scroll_tab('drums')
        content = self.drums_tab.content
        table = self._card(content)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(2, weight=1)
        self._label(table, 'search', 'CardMuted.TLabel').grid(row=0, column=0, columnspan=2, sticky='w')
        search = ttk.Entry(table, textvariable=self.search_var, width=20)
        search.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 12))
        self.mutable_controls.append(search)
        columns = ('note', 'name', 'volume', 'duration', 'noise')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', height=10, selectmode='browse')
        for col, label, width in (('note', 'note', 46), ('name', 'name', 175),
                                  ('volume', 'volume_short', 62), ('duration', 'duration', 88),
                                  ('noise', 'noise_enable', 54)):
            self.tree.heading(col, text=self.tr(label))
            self.tree.column(col, width=width, minwidth=40 if col != 'name' else 100,
                             anchor='w' if col == 'name' else 'center', stretch=col == 'name')
        self.tree.grid(row=2, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=2, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_drum)
        self.drum_count_label = ttk.Label(table, style='CardMuted.TLabel')
        self.drum_count_label.grid(row=3, column=0, columnspan=2, sticky='w', pady=(12, 0))
        editor = self._card(content)
        editor.columnconfigure((0, 1), weight=1, uniform='drum_fields')
        self.drum_title = ttk.Label(editor, style='CardTitle.TLabel', wraplength=260)
        self.drum_title.grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        self.perc_display = tk.StringVar(self.root, self.tr(self.perc_var.get()))
        percussion = ttk.Combobox(editor, textvariable=self.perc_display,
                                 values=[self.tr(mode) for mode in PERCUSSION_MODES],
                                 state='readonly', width=11)
        self._label(editor, 'percussion', 'CardMuted.TLabel').grid(row=1, column=0, sticky='w')
        percussion.grid(row=1, column=1, sticky='ew', pady=(0, 12))
        self.percussion_combo = percussion
        percussion.bind('<<ComboboxSelected>>', lambda _: self.perc_var.set(
            PERCUSSION_MODES[percussion.current()]))
        self.mutable_controls.append(percussion)
        for i, (field, label) in enumerate(zip(DRUM_FIELDS, DRUM_LABELS)):
            row, col = divmod(i, 2)
            wrapper = ttk.Frame(editor, style='CardBody.TFrame')
            wrapper.grid(row=row + 2, column=col, sticky='ew', padx=(0, 12) if col == 0 else 0, pady=(0, 12))
            self._label(wrapper, label, 'CardMuted.TLabel').pack(anchor='w', pady=(0, 5))
            self._entry(wrapper, self.drum_vars[field], f'drum:{field}').pack(fill='x')
        toggles = ttk.Frame(editor, style='CardBody.TFrame')
        toggles.grid(row=5, column=0, columnspan=2, sticky='ew')
        for key, var in (('tone', self.drum_tone), ('noise_enable', self.drum_noise)):
            button = ttk.Checkbutton(toggles, text=self.tr(key), variable=var)
            button.pack(side='left', padx=(0, 16))
            self.mutable_controls.append(button)
        tools = ttk.Frame(editor, style='CardBody.TFrame')
        tools.grid(row=6, column=0, columnspan=2, sticky='ew', pady=(12, 0))
        self._button(tools, 'drum_test', self.test_drum, 'Listen.TButton').pack(side='left', padx=(0, 8))
        self._button(tools, 'apply', self.apply_drum).pack(side='left')
        ttk.Label(editor, textvariable=self.error_vars['drum'], style='Error.TLabel',
                  wraplength=260).grid(row=7, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        self.perc_hint = ttk.Label(editor, text=self.tr(self.perc_var.get() + '_hint'),
                                   style='CardMuted.TLabel', wraplength=260)
        self.perc_hint.grid(row=8, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        self._drums_stacked = None

        def layout(event):
            stacked = self.drums_tab.canvas.winfo_width() < table.winfo_reqwidth() + editor.winfo_reqwidth() + 16
            if stacked != self._drums_stacked:
                self._drums_stacked = stacked
                content.columnconfigure(0, weight=1 if stacked else 3)
                content.columnconfigure(1, weight=0 if stacked else 2)
                table.grid(row=0, column=0, sticky='nsew', padx=(0, 0 if stacked else 16), pady=(0, 12))
                editor.grid(row=1 if stacked else 0, column=0 if stacked else 1, sticky='new', pady=(0, 12))
        self.drums_tab.canvas.bind('<Configure>', layout, add='+')
        table.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        editor.grid(row=0, column=1, sticky='new')
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        self.filter_drums()
        self._update_drum_title()

    def _build_priorities(self):
        self.priorities_tab = self._scroll_tab('priorities')
        content = self.priorities_tab.content
        content.columnconfigure(0, weight=1)
        card = self._card(content)
        card.grid(row=0, column=0, sticky='ew')
        self._label(card, 'priorities_title', 'CardTitle.TLabel', wraplength=650).pack(anchor='w')
        self._label(card, 'priorities_hint', 'CardMuted.TLabel', wraplength=650).pack(anchor='w', pady=(8, 20))
        fields = ttk.Frame(card, style='CardBody.TFrame')
        fields.pack(fill='x')
        cells = []
        for i in range(4):
            fields.columnconfigure(i, weight=1, uniform='priorities')
        for i, var in enumerate(self.priority_vars):
            field = ttk.Frame(fields, style='CardBody.TFrame')
            cells.append(field)
            row, col = divmod(i, 4)
            field.grid(row=row, column=col, sticky='ew', padx=(0, 16) if col < 3 else 0, pady=(0, 16))
            label = self.tr('drum_channel') if i == 9 else self.tr('midi_channel', number=i + 1)
            ttk.Label(field, text=label, style='Warning.TLabel' if i == 9 else 'CardMuted.TLabel').pack(anchor='w', pady=(0, 5))
            self._entry(field, var, f'priorities:{i}').pack(fill='x')
        ttk.Label(card, textvariable=self.error_vars['priorities'], style='Error.TLabel',
                  wraplength=650).pack(anchor='w')
        self._priority_columns = None

        def layout(event):
            columns = 4 if event.width >= 4 * max(cell.winfo_reqwidth() for cell in cells) + 96 else 2
            if columns != self._priority_columns:
                self._priority_columns = columns
                for c in range(4):
                    fields.columnconfigure(c, weight=1 if c < columns else 0,
                                           uniform='priorities' if c < columns else '')
                for i, cell in enumerate(cells):
                    cell.grid(row=i // columns, column=i % columns,
                              padx=(0, 16) if i % columns < columns - 1 else 0)
        self.priorities_tab.canvas.bind('<Configure>', layout, add='+')

    def _build_report(self, parent):
        report = self._card(parent)
        report.grid(row=2, column=0, sticky='ew', pady=(16, 0))
        report.columnconfigure(0, weight=1)
        header = ttk.Frame(report, style='CardBody.TFrame')
        header.grid(row=0, column=0, sticky='ew')
        header.columnconfigure(0, weight=1)
        self._label(header, 'report', 'Section.TLabel').grid(row=0, column=0, sticky='w')
        self.report_state = ttk.Label(header, style='CardMuted.TLabel')
        self.report_state.grid(row=0, column=1, sticky='e')
        metrics = ttk.Frame(report, style='CardBody.TFrame')
        metrics.grid(row=1, column=0, sticky='ew', pady=(12, 10))
        self.metrics = []
        for i, key in enumerate(('duration_metric', 'bytes_metric', 'peak_metric', 'lost_metric')):
            metrics.columnconfigure(i, weight=1, uniform='metrics')
            cell = ttk.Frame(metrics, style='CardBody.TFrame')
            cell.grid(row=0, column=i, sticky='ew', padx=(0, 12))
            value = ttk.Label(cell, text='—', style='Metric.TLabel')
            value.pack(anchor='w')
            self._label(cell, key, 'CardMuted.TLabel').pack(anchor='w')
            self.metrics.append(value)
        separator = ttk.Separator(report)
        separator.grid(row=2, column=0, sticky='ew')
        bottom = ttk.Frame(report, style='CardBody.TFrame')
        bottom.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        self.report_message = ttk.Label(bottom, style='CardMuted.TLabel', wraplength=520)
        self.report_message.grid(row=0, column=0, sticky='w')
        self.report_button = self._button(bottom, 'show_report', self.show_report, 'Link.TButton', mutable=False)
        self.report_button.grid(row=0, column=1, sticky='e', padx=(8, 0))
        bottom.bind('<Configure>', lambda e: self.report_message.configure(
            wraplength=max(150, e.width - self.report_button.winfo_reqwidth() - 16)))
        self._report_compact = None

        def layout(event):
            compact = event.height < 500
            if compact != self._report_compact:
                self._report_compact = compact
                for widget in (metrics, separator, self.report_message):
                    widget.grid_remove() if compact else widget.grid()
        parent.bind('<Configure>', layout)

    def _build_transport(self, parent):
        transport = self._card(parent, padding=12)
        transport.grid(row=2, column=0, sticky='ew', pady=(16, 0))
        transport.columnconfigure(3, weight=1)
        self.action_buttons = {}
        for i, (key, style) in enumerate((('convert', 'TButton'), ('play', 'Listen.TButton'))):
            button = self._button(transport, key, lambda key=key: self.run(key), style, mutable=False)
            button.grid(row=0, column=i, padx=(0, 8))
            self.action_buttons[key] = button
        self.stop_button = self._button(transport, 'stop', self.stop, mutable=False)
        self.stop_button.grid(row=0, column=2, padx=(0, 12))
        extras = ttk.Frame(transport, style='CardBody.TFrame')
        extras.grid(row=0, column=3, sticky='w', padx=(8, 16))
        self._label(extras, 'additional', 'CardMuted.TLabel').pack(side='left', padx=(0, 12))
        for key, var in (('wav', self.wav_var), ('csv', self.csv_var)):
            check = ttk.Checkbutton(extras, text=self.tr(key), variable=var)
            check.pack(side='left', padx=(0, 12))
            self.mutable_controls.append(check)
        export = self._button(transport, 'export', lambda: self.run('export'), 'Primary.TButton', mutable=False)
        export.grid(row=0, column=4, sticky='e')
        self.action_buttons['export'] = export
        self._transport_compact = None

        def layout(event):
            controls = [*self.action_buttons.values(), self.stop_button, extras]
            compact = event.width < sum(control.winfo_reqwidth() for control in controls) + 88
            if compact != self._transport_compact:
                self._transport_compact = compact
                extras.grid(row=1 if compact else 0, column=0 if compact else 3,
                            columnspan=4 if compact else 1, sticky='w',
                            pady=(10, 0) if compact else 0)
        transport.bind('<Configure>', layout)
        footer = ttk.Frame(parent)
        footer.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        status = ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', wraplength=1000)
        status.grid(row=0, column=0, sticky='w')
        self.progress = ttk.Progressbar(footer, mode='indeterminate', length=120)
        self.progress.grid(row=0, column=1, sticky='e', padx=(16, 0))
        self._progress_running = False
        footer.bind('<Configure>', lambda e: status.configure(wraplength=max(200, e.width - 150)))

    def change_language(self, _=None):
        language = self.lang_var.get().lower()
        if language in TEXT and language != self.language:
            self.language = language
            self.build()

    def _settings_changed(self, *trace, preset_changed=True):
        if self._guard:
            return
        if preset_changed and (not trace or trace[0] not in (str(self.clock_var), str(self.loop_var))):
            self.preset_modified = True
        self.revision += 1
        self.play_generation += 1
        was_playing = self.playing
        self.player.stop()
        self.playing = False
        if hasattr(self, 'report_state'):
            self._update_report()
            self._update_buttons()
            self.overflow_hint.configure(text=self.tr(self.overflow_var.get() + '_hint'))
            self.perc_hint.configure(text=self.tr(self.perc_var.get() + '_hint'))
            self._sync_preset_ui()
            if not self.busy and (self.last_report is not None or was_playing):
                self.set_status('stale' if self.last_report is not None else 'stopped')
        for entry in getattr(self, 'entries', {}).values():
            entry.state(['!invalid'])
        for var in self.error_vars.values():
            var.set('')

    def _voice_changed(self, index):
        self._settings_changed()
        if not self._guard and hasattr(self, 'envelopes'):
            self._draw_envelope(index)

    def _draw_envelope(self, index):
        canvas = self.envelopes[index]
        canvas.delete('all')
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 30:
            return
        for fraction in (.25, .5, .75):
            canvas.create_line(12, 10 + fraction * (height - 30), width - 12,
                               10 + fraction * (height - 30), fill='#2c3742')
            canvas.create_line(12 + fraction * (width - 24), 10,
                               12 + fraction * (width - 24), height - 20, fill='#2c3742')
        try:
            _, attack, decay, sustain, release, _ = [float(v.get()) for v in self.voice_vars[index]]
            if not all(isfinite(x) for x in (attack, decay, sustain, release)):
                raise ValueError
            if min(attack, decay, release) < 0 or not 0 <= sustain <= 1:
                raise ValueError
            total = max(1, attack + decay + 180 + release)
            scale = (width - 24) / total
            base, top = height - 20, 12
            sustain_y = base - sustain * (base - top)
            points = (12, base, 12 + attack * scale, top,
                      12 + (attack + decay) * scale, sustain_y,
                      12 + (attack + decay + 180) * scale, sustain_y, width - 12, base)
            canvas.create_line(*points, fill=VOICE_COLOURS[index], width=2)
            canvas.create_text(width - 12, height - 7, text='A · D · S · R', anchor='e',
                               fill=MUTED, font=('Segoe UI', 8))
        except ValueError:
            canvas.create_text(width / 2, height / 2, text='…', fill=MUTED)

    def _number(self, variable, key, label, *, integer=False, low=None, high=None):
        field = self.tr(label)
        try:
            value = int(variable.get().strip()) if integer else float(variable.get())
            if not isfinite(value):
                raise ValueError
        except (ValueError, OverflowError):
            raise InputError(key, self.tr('integer_error' if integer else 'number_error', field=field)) from None
        if (low is not None and value < low) or (high is not None and value > high):
            message = (self.tr('minimum_error', field=field, low=low) if high is None else
                       self.tr('range_error', field=field, low=low, high=high))
            raise InputError(key, message)
        return value

    def _drum_from_editor(self):
        values = {}
        bounds = {'volume': (0, 15), 'duration_ms': (1, None), 'noise_period': (1, 31),
                  'start_hz': (0, None), 'end_hz': (0, None), 'priority': (None, None)}
        for field, label in zip(DRUM_FIELDS, DRUM_LABELS):
            low, high = bounds[field]
            values[field] = self._number(self.drum_vars[field], f'drum:{field}', label,
                                         integer=field not in ('start_hz', 'end_hz'), low=low, high=high)
        return Drum(**values, tone=self.drum_tone.get(), noise=self.drum_noise.get())

    def read_settings(self):
        # A failed edit must not corrupt the last good snapshot.
        cfg = Settings.from_dict(self.cfg.to_dict())
        voices = []
        for i, row in enumerate(self.voice_vars):
            values = []
            for field, label, var in zip(VOICE_FIELDS, VOICE_LABELS, row):
                low = None if field == 'vibrato_cents' else 0
                high = 3 if field == 'gain' else 1 if field == 'sustain' else None
                values.append(self._number(var, f'voice{i}:{field}', label,
                                            integer=field.endswith('_ms'), low=low, high=high))
            voices.append(Voice(*values))
        cfg.voices = voices
        cfg.clock = self._read_clock()
        cfg.loop = self.loop_var.get()
        cfg.overflow = self.overflow_var.get()
        cfg.percussion = self.perc_var.get()
        cfg.midi_priorities = [self._number(var, f'priorities:{i}', 'priority', integer=True)
                               for i, var in enumerate(self.priority_vars)]
        cfg.drums[self.selected_drum] = self._drum_from_editor()
        cfg.validate()
        self.cfg = cfg
        self.refresh_drum(self.selected_drum)
        return Settings.from_dict(cfg.to_dict())

    def _read_clock(self):
        raw_clock = self.clock_var.get().strip()
        try:
            clock = CLOCK_PRESETS[raw_clock] if raw_clock in CLOCK_PRESETS else int(raw_clock.replace(' ', '').replace('_', ''))
            if not 100_000 <= clock <= 20_000_000:
                raise ValueError
            return clock
        except ValueError:
            raise InputError('target:clock', self.tr('clock_error')) from None

    def _show_validation(self, exc):
        key = getattr(exc, 'key', 'target:clock')
        group = key.split(':')[0]
        self.error_vars[group].set(str(exc))
        entry = self.entries.get(key)
        if entry is not None:
            entry.state(['invalid'])
            if group.startswith('voice'):
                self.notebook.select(self.voices_tab)
            elif group == 'drum':
                self.notebook.select(self.drums_tab)
            elif group == 'priorities':
                self.notebook.select(self.priorities_tab)
            entry.focus_set()
            if self._reveal_after_id is not None:
                self.root.after_cancel(self._reveal_after_id)

            def reveal():
                self._reveal_after_id = None
                self._reveal_entry(entry)
            self._reveal_after_id = self.root.after_idle(reveal)
        self.set_status('invalid_settings')

    def _reveal_entry(self, entry):
        if not entry.winfo_exists():
            return
        ancestor = entry.master
        while ancestor is not None:
            if isinstance(ancestor, ScrollFrame):
                ancestor.reveal(entry)
                return
            ancestor = getattr(ancestor, 'master', None)

    def _load_drum(self, note):
        previous_guard, self._guard = self._guard, True
        try:
            self.selected_drum = note
            drum = self.cfg.drums[note]
            for field, var in self.drum_vars.items():
                var.set(str(getattr(drum, field)))
            self.drum_tone.set(drum.tone)
            self.drum_noise.set(drum.noise)
        finally:
            self._guard = previous_guard

    def _update_drum_title(self):
        self.drum_title.configure(text=self.tr('drum_selected', note=self.selected_drum,
                                               name=GM_DRUM_NAMES[self.selected_drum]))

    def refresh_drum(self, note):
        if self.tree.exists(str(note)):
            drum = self.cfg.drums[note]
            self.tree.item(str(note), values=(note, GM_DRUM_NAMES[note], drum.volume, drum.duration_ms, drum.noise_period))

    def filter_drums(self):
        if not hasattr(self, 'tree'):
            return
        query = self.search_var.get().strip().casefold()
        self.tree.delete(*self.tree.get_children())
        for note, name in GM_DRUM_NAMES.items():
            if not query or query in f'{note} {name}'.casefold():
                drum = self.cfg.drums[note]
                self.tree.insert('', 'end', iid=str(note), values=(note, name, drum.volume, drum.duration_ms, drum.noise_period))
        selected = str(self.selected_drum)
        if self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)
        self.drum_count_label.configure(text=self.tr('drum_count', count=len(self.tree.get_children())))

    def select_drum(self, _=None):
        selection = self.tree.selection()
        if not selection or int(selection[0]) == self.selected_drum:
            return
        if self.busy:
            if self.tree.exists(str(self.selected_drum)):
                self.tree.selection_set(str(self.selected_drum))
            return
        try:
            drum = self._drum_from_editor()
        except ValueError as exc:
            if self.tree.exists(str(self.selected_drum)):
                self.tree.selection_set(str(self.selected_drum))
            else:
                self.tree.selection_remove(*selection)
            self._show_validation(exc)
            return
        self.cfg.drums[self.selected_drum] = drum
        self.refresh_drum(self.selected_drum)
        self._load_drum(int(selection[0]))
        self.error_vars['drum'].set('')
        self._update_drum_title()

    def apply_drum(self):
        try:
            drum = self._drum_from_editor()
        except ValueError as exc:
            self._show_validation(exc)
            return None
        self.cfg.drums[self.selected_drum] = drum
        self.refresh_drum(self.selected_drum)
        self.set_status('drum_applied', note=self.selected_drum)
        return self.selected_drum

    def _update_source(self):
        self.source_name.configure(text=self.path.name if self.path else self.tr('no_file'))
        try:
            size = size_label(self.path.stat().st_size) if self.path else ''
        except OSError:
            size = ''
        info = self.tr('source_info', tracks=self.last_report['tracks'],
                       note_tracks=self.last_report['note_tracks'],
                       channels=', '.join(str(ch) for ch in self.last_report['midi_channels']),
                       size=size) if self.last_report else size
        self.source_info.configure(text=info or self.tr('source_hint'))
        self.source_state.configure(text=self.tr('file_selected') if self.path else self.tr('no_file'),
                                    style='Success.TLabel' if self.path else 'CardMuted.TLabel')
        self._sync_preset_ui()
        self.overflow_hint.configure(text=self.tr(self.overflow_var.get() + '_hint'))

    def set_source(self, path):
        self.stop()
        self.path = Path(path)
        self.last_report = None
        self.report_revision = None
        self.revision += 1
        self._update_source()
        self._update_report()
        self._update_buttons()
        self.set_status('source_opened', path=str(self.path))

    def open(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(parent=self.root,
                    filetypes=[('MIDI', '*.mid *.midi'), ('All files', '*.*')])
        if filename:
            self.set_source(filename)

    def _update_report(self):
        report = self.last_report
        if report is None:
            self.report_state.configure(text=self.tr('no_report'), style='CardMuted.TLabel')
            for metric in self.metrics:
                metric.configure(text='—', foreground=FG)
            self.report_message.configure(text=self.tr('report_hint'), style='CardMuted.TLabel')
        else:
            seconds = round(report['duration_seconds'])
            values = (f'{seconds // 60:02d}:{seconds % 60:02d}', size_label(report['bytes']),
                      f"{report['peak_polyphony']} → {min(3, report['peak_polyphony'])}", str(report['lost_note_instances']))
            for i, (metric, value) in enumerate(zip(self.metrics, values)):
                metric.configure(text=value, foreground=AMBER if i == 3 and report['lost_note_instances'] else FG)
            stale = self.report_revision != self.revision
            self.report_state.configure(text=self.tr('stale' if stale else 'current'),
                                        style='Warning.TLabel' if stale else 'Success.TLabel')
            if report['lost_note_instances']:
                text, style = self.tr('loss_warning'), 'Warning.TLabel'
            elif report['warnings']:
                text, style = self.tr('warnings', count=len(report['warnings'])), 'Warning.TLabel'
            else:
                text, style = self.tr('clean', frames=report['frames']), 'Success.TLabel'
            self.report_message.configure(text=text, style=style)
        self.report_button.state(['!disabled'] if report else ['disabled'])

    def _update_buttons(self):
        for control in self.mutable_controls:
            control.state(['disabled'] if self.busy else ['!disabled'])
        for button in self.action_buttons.values():
            button.state(['!disabled'] if self.path and not self.busy else ['disabled'])
        pending_preview = self.busy and self.active_action in ('play', 'drum')
        self.stop_button.state(['!disabled'] if self.playing or pending_preview else ['disabled'])
        if self.busy:
            self.progress.grid()
            if not self._progress_running:
                self.progress.start(12)
                self._progress_running = True
        else:
            self.progress.stop()
            self._progress_running = False
            self.progress.grid_remove()

    def stop(self):
        self.play_generation += 1
        self.player.stop()
        self.playing = False
        if hasattr(self, 'stop_button'):
            self._update_buttons()
            self.set_status('preview_cancelled' if self.busy and self.active_action in ('play', 'drum') else 'stopped')

    def run(self, action):
        if self.busy or self.path is None:
            return
        try:
            cfg = self.read_settings()
        except (ValueError, TypeError) as exc:
            self._show_validation(exc)
            return
        output = None
        if action == 'export':
            filename = filedialog.asksaveasfilename(parent=self.root, defaultextension='.aym',
                        initialfile=self.path.stem + '.aym', filetypes=[('AYM1', '*.aym')])
            if not filename:
                return
            output = Path(filename)
        source = self.path
        want_csv, want_wav = self.csv_var.get(), self.wav_var.get()

        def work():
            blob, frames, report = convert(source, cfg)
            pcm = render_pcm(frames, cfg.clock) if action == 'play' else None
            wav = render_wav(frames, cfg.clock) if action == 'export' and want_wav else None
            if action == 'export':
                write_outputs(output, blob, frames, report, csv=want_csv)
                if wav is not None:
                    output.with_suffix('.wav').write_bytes(wav)
            return report, pcm, output
        self._start_job(action, work)

    def test_drum(self):
        if self.busy:
            return
        try:
            cfg = self.read_settings()
        except (ValueError, TypeError) as exc:
            self._show_validation(exc)
            return
        note = self.selected_drum

        def work():
            length = cfg.drums[note].duration_ms
            song = Song([Event(0, 'on', 9, note, 120, 0, 0)], max(1, int(length / 20) + 3), 1, 0, [])
            frames, _ = synthesize(song, cfg)
            return None, render_pcm(frames, cfg.clock), None
        self._start_job('drum', work)

    def _start_job(self, action, work):
        self.player.stop()
        self.playing = False
        self.play_generation += 1
        token, revision = self.play_generation, self.revision
        self.busy, self.active_action = True, action
        self.set_status('drum_working' if action == 'drum' else 'working')
        self._update_buttons()

        def worker():
            try:
                self._results.put(('done', action, revision, token, work()))
            except Exception as exc:
                self._results.put(('error', str(exc)))
        Thread(target=worker, daemon=True).start()

    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                result = self._results.get_nowait()
                if result[0] == 'error':
                    self.fail(result[1])
                else:
                    _, action, revision, token, (report, pcm, output) = result
                    self.completed(action, report, pcm, output, token, revision)
        except Empty:
            pass
        if self.playing and self.player.channel is not None and not self.player.channel.get_busy():
            self.playing = False
            self._update_buttons()
            self.set_status('done')
        self._poll_id = self.root.after(60, self._poll)

    def completed(self, action, report, pcm, output, token, revision):
        self.busy, self.active_action = False, None
        if report is not None:
            self.last_report, self.report_revision = report, revision
            self._update_report()
            self._update_source()
        if output:
            self.set_status('saved', path=str(output))
        else:
            self.set_status('done')
        if pcm is not None:
            if token == self.play_generation and revision == self.revision:
                try:
                    self.player.play(pcm)
                    self.playing = True
                    self.set_status('playing')
                except Exception as exc:
                    self.fail(str(exc))
                    return
            else:
                self.set_status('stopped')
        self._update_buttons()

    def fail(self, message):
        self.busy, self.active_action = False, None
        self._update_buttons()
        self.set_status('error_status', message=message)
        messagebox.showerror(self.tr('error'), message, parent=self.root)

    def _sync_preset_ui(self):
        preset = next((p for p in BUILTIN_PRESETS if p.key == self.builtin_preset_id), None)
        if preset is not None:
            self.builtin_display.set(self.tr(preset.name_key))
            description = self.tr(preset.description_key)
            origin = self.tr('preset_builtin')
        else:
            self.builtin_display.set(self.tr('preset_from_json'))
            description = self.tr('preset_json_hint')
            origin = self.tr('preset_name', name=self.preset_path.name) if self.preset_path else self.tr('preset_custom')
        self.preset_description.configure(text=description)
        self.preset_label.configure(text=origin + (' · ' + self.tr('preset_modified') if self.preset_modified else ''),
                                    style='Warning.TLabel' if self.preset_modified else 'CardMuted.TLabel')

    def apply_builtin_preset(self, _=None):
        index = self.builtin_combo.current()
        if self.busy or index < 0:
            self._sync_preset_ui()
            return
        preset = BUILTIN_PRESETS[index]
        try:
            cfg = make_builtin_preset(preset.key, clock=self._read_clock(), loop=self.loop_var.get())
        except ValueError as exc:
            self._show_validation(exc)
            self._sync_preset_ui()
            return
        self._use_settings(cfg)
        self.builtin_preset_id = preset.key
        self.preset_path = None
        self.preset_modified = False
        self._update_source()
        self.set_status('builtin_applied')

    def load_preset(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(parent=self.root, filetypes=[('JSON', '*.json')])
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding='utf8'))
            if not isinstance(data, dict):
                raise ValueError(self.tr('preset_error'))
            cfg = Settings.from_dict(data)
            self._use_settings(cfg)
            self.preset_path = Path(filename)
            self.builtin_preset_id = None
            self.preset_modified = False
            self._update_source()
            self.set_status('preset_loaded', path=filename)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.fail(str(exc))

    def _use_settings(self, cfg):
        self.cfg = cfg
        self._guard = True
        try:
            self.clock_var.set(next((label for label, hz in CLOCK_PRESETS.items() if hz == cfg.clock), str(cfg.clock)))
            self.loop_var.set(cfg.loop)
            self.overflow_var.set(cfg.overflow)
            self.perc_var.set(cfg.percussion)
            for row, voice in zip(self.voice_vars, cfg.voices):
                for field, var in zip(VOICE_FIELDS, row):
                    var.set(str(getattr(voice, field)))
            for var, value in zip(self.priority_vars, cfg.midi_priorities):
                var.set(str(value))
            self._load_drum(self.selected_drum)
        finally:
            self._guard = False
        self.overflow_display.set(self.tr(cfg.overflow))
        self.perc_display.set(self.tr(cfg.percussion))
        self.filter_drums()
        for i in range(3):
            self._draw_envelope(i)
        self._settings_changed(preset_changed=False)

    def save_preset(self):
        if self.busy:
            return
        try:
            cfg = self.read_settings()
        except (ValueError, TypeError) as exc:
            self._show_validation(exc)
            return
        filename = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json',
                    initialfile=self.preset_path.name if self.preset_path else 'ay-preset.json',
                    filetypes=[('JSON', '*.json')])
        if filename:
            try:
                Path(filename).write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False), encoding='utf8')
                self.preset_path = Path(filename)
                self.builtin_preset_id = None
                self.preset_modified = False
                self._update_source()
                self.set_status('preset_saved', path=filename)
            except OSError as exc:
                self.fail(str(exc))

    def show_report(self):
        if self.last_report is None:
            return
        report = self.last_report
        dialog = tk.Toplevel(self.root)
        dialog.title(self.tr('report_summary') + ' · MIDI2AYM1')
        dialog.configure(background=BG)
        dialog.geometry('860x560')
        dialog.transient(self.root)
        notebook = ttk.Notebook(dialog)
        notebook.pack(fill='both', expand=True, padx=16, pady=16)
        summary = ttk.Frame(notebook, padding=12)
        notebook.add(summary, text=self.tr('report_summary'))
        text = tk.Text(summary, background=FIELD, foreground=FG, insertbackground=FG,
                        relief='flat', font='TkFixedFont', wrap='word', padx=12, pady=12)
        text.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(summary, command=text.yview)
        scrollbar.pack(side='right', fill='y')
        text.configure(yscrollcommand=scrollbar.set)
        if self.report_revision != self.revision:
            text.insert('end', self.tr('report_stale') + '\n\n')
        values = [('clock', f"{report['clock_hz']:,} Hz"),
                  ('duration_metric', f"{report['duration_seconds']:.2f} s"),
                  ('bytes_metric', size_label(report['bytes'])),
                  ('peak_metric', f"{report['peak_polyphony']} → {min(3, report['peak_polyphony'])}"),
                  ('lost_metric', report['lost_note_instances'])]
        values.extend((key, report[key]) for key in ('frames', 'tracks', 'note_tracks', 'tempo_changes',
                      'average_polyphony', 'overflow_frames', 'lost_note_frames'))
        values.append(('midi_channels', ', '.join(str(ch) for ch in report['midi_channels'])))
        values.append(('percussion', self.tr(report['percussion'])))
        values.append(('compression', report['compression_bytes_per_frame']))
        text.insert('end', '\n\n'.join(f'{self.tr(key)}:  {value}' for key, value in values))
        text.configure(state='disabled')
        losses = ttk.Frame(notebook, padding=12)
        notebook.add(losses, text=self.tr('report_losses'))
        losses.columnconfigure(0, weight=1)
        losses.rowconfigure(1, weight=1)
        ttk.Label(losses, text=self.tr('loss_limit', shown=len(report['losses']), total=report['lost_note_instances'])).grid(
            row=0, column=0, sticky='w', pady=(0, 12))
        columns = ('frame', 'midi_channel', 'track', 'note', 'reason')
        tree = ttk.Treeview(losses, columns=columns, show='headings')
        for col, key in zip(columns, ('frame', 'channel', 'track', 'note', 'reason')):
            tree.heading(col, text=self.tr(key))
            tree.column(col, width=120, anchor='center')
        for row in report['losses']:
            tree.insert('', 'end', values=[self.tr(row[c]) if c == 'reason' and row[c] in ('steal', 'drop') else row[c] for c in columns])
        tree.grid(row=1, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(losses, command=tree.yview)
        scrollbar.grid(row=1, column=1, sticky='ns')
        tree.configure(yscrollcommand=scrollbar.set)
        warnings = ttk.Frame(notebook, padding=16)
        notebook.add(warnings, text=self.tr('report_warnings'))
        warning_text = tk.Text(warnings, background=FIELD, foreground=AMBER, relief='flat',
                               font='TkDefaultFont', wrap='word', padx=12, pady=12)
        warning_text.pack(fill='both', expand=True)
        warning_text.insert('end', '\n\n'.join(report['warnings']) or self.tr('no_warnings'))
        warning_text.configure(state='disabled')
        if report['lost_note_instances']:
            ttk.Button(dialog, text=self.tr('to_priorities'), command=lambda: (
                self.notebook.select(self.priorities_tab), dialog.destroy())).pack(anchor='e', padx=16, pady=(0, 16))
        dialog.bind('<Escape>', lambda _: dialog.destroy())

    def _mousewheel(self, event, units=None):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, (ttk.Treeview, ttk.Combobox, tk.Text)):
            return
        if units is None:
            units = -int(event.delta / 120) if abs(event.delta) >= 120 else -1 if event.delta > 0 else 1
        while widget is not None:
            if widget in self.scroll_frames:
                widget.scroll(units)
                return 'break'
            widget = getattr(widget, 'master', None)

    def help(self):
        messagebox.showinfo(self.tr('help'), self.tr('help_text'), parent=self.root)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.player.stop()
        self.root.after_cancel(self._poll_id)
        if self._reveal_after_id is not None:
            self.root.after_cancel(self._reveal_after_id)
        self.root.destroy()


def main():
    root = tk.Tk()
    Window(root)
    root.mainloop()


if __name__ == '__main__':
    main()
