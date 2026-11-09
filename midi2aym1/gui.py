"""Compact bilingual Tkinter workbench for MIDI -> AY AYM1."""
from __future__ import annotations

from pathlib import Path
from threading import Thread
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .audio import Player, render_pcm, render_wav
from .core import (Drum, Event, GM_DRUM_NAMES, Settings, Voice,
                   Song, convert, synthesize, write_outputs)

TEXT = {
    'ru': {
        'title': 'MIDI → AYM1 · SpaceCan', 'open': 'Открыть MIDI', 'convert': 'Конвертировать',
        'play': '▶ Слушать AY', 'stop': '■ Стоп', 'export': 'Сохранить AYM1',
        'help': 'Помощь', 'lang': 'Язык', 'clock': 'Частота AY', 'loop': 'Цикл',
        'overflow': 'Лишние ноты', 'percussion': 'Ударные', 'steal': 'Вытеснять',
        'drop': 'Пропускать', 'noise': 'Вкл.', 'off': 'Выкл.',
        'voices': 'Голоса AY — индивидуальные огибающие', 'gain': 'Громк.',
        'attack': 'Атака, мс', 'decay': 'Спад, мс', 'sustain': 'Удерж., 0–1',
        'release': 'Релиз, мс', 'vibrato': 'Вибр., центы',
        'priorities': 'Приоритеты MIDI каналов 1–16 (целые числа):',
        'drums': 'MIDI канал 10 · General MIDI (нота → звук)',
        'note': 'Нота', 'name': 'Инструмент', 'volume': 'Громк.', 'duration': 'Длит., мс',
        'noise_period': 'Шум 1–31', 'start_hz': 'Тон нач., Гц', 'end_hz': 'Тон кон., Гц',
        'tone': 'Тон', 'noise_enable': 'Шум', 'priority': 'Приоритет',
        'apply': 'Применить', 'drum_test': '▶ Удар', 'wav': 'Также WAV', 'csv': 'CSV кадров',
        'load_preset': 'Загрузить пресет', 'save_preset': 'Сохранить пресет',
        'ready': 'Откройте .mid. Предпрослушивание воспроизводит именно результат для AY.',
        'working': 'Преобразование и эмуляция AY…', 'done': 'Готово',
        'saved': 'Файлы сохранены', 'error': 'Ошибка',
        'help_text': ('1. Откройте .mid (SMF 0/1); настройте AY A/B/C и ударные GM 35–81.\n'
                      '2. ▶ Слушать AY — звук после ограничения до 3 голосов, не SoundFont.\n'
                      '3. На канале MIDI 10 каждая GM-нота имеет отдельный пресет.\n'
                      '4. Вытеснять — приоритет новых/громких нот; пропускать — держать старые.\n'
                      '5. Сохранить AYM1 создаёт .aym + .report.json (+ WAV/CSV по выбору).\n'
                      '6. Подключение к игре: INCBIN "track.aym"; AY clock должен совпасть с платой.'),
    },
    'en': {
        'title': 'MIDI → AYM1 · SpaceCan', 'open': 'Open MIDI', 'convert': 'Convert',
        'play': '▶ Listen to AY', 'stop': '■ Stop', 'export': 'Save AYM1',
        'help': 'Help', 'lang': 'Language', 'clock': 'AY clock', 'loop': 'Loop',
        'overflow': 'Overflow', 'percussion': 'Drums', 'steal': 'Steal',
        'drop': 'Drop', 'noise': 'On', 'off': 'Off',
        'voices': 'AY voices — individual envelopes', 'gain': 'Gain',
        'attack': 'Attack ms', 'decay': 'Decay ms', 'sustain': 'Sustain 0–1',
        'release': 'Release ms', 'vibrato': 'Vib. cents',
        'priorities': 'MIDI channel priorities 1–16 (integers):',
        'drums': 'MIDI channel 10 · General MIDI (note → sound)',
        'note': 'Note', 'name': 'Instrument', 'volume': 'Volume', 'duration': 'Length ms',
        'noise_period': 'Noise 1–31', 'start_hz': 'Tone start Hz', 'end_hz': 'Tone end Hz',
        'tone': 'Tone', 'noise_enable': 'Noise', 'priority': 'Priority',
        'apply': 'Apply', 'drum_test': '▶ Drum', 'wav': 'Also WAV', 'csv': 'Frame CSV',
        'load_preset': 'Load preset', 'save_preset': 'Save preset',
        'ready': 'Open a .mid. Preview plays the actual 3-voice AY conversion.',
        'working': 'Converting and rendering AY audio…', 'done': 'Ready',
        'saved': 'Files saved', 'error': 'Error',
        'help_text': ('1. Open .mid (SMF 0/1), adjust AY A/B/C and GM drums 35–81.\n'
                      '2. ▶ Listen to AY previews the 3-voice result, not a SoundFont.\n'
                      '3. Each MIDI channel 10 GM note has its own editable sound preset.\n'
                      '4. Steal prioritizes loud/new notes; Drop holds existing voices.\n'
                      '5. Save AYM1 writes .aym + .report.json (+ WAV/CSV if selected).\n'
                      '6. In game: INCBIN "track.aym"; match AY clock to your hardware.'),
    },
}


class Window:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = Settings()
        self.language = 'ru'
        self.path: Path | None = None
        self.player = Player()
        self.busy = False
        self.play_generation = 0
        self.status = tk.StringVar()
        self.csv_var = tk.BooleanVar(value=False)
        self.wav_var = tk.BooleanVar(value=True)
        self.clock_var = tk.StringVar(value='2000000')
        self.loop_var = tk.BooleanVar(value=False)
        self.overflow_var = tk.StringVar(value='steal')
        self.perc_var = tk.StringVar(value='noise')
        self.priority_var = tk.StringVar(value=','.join(str(x) for x in self.cfg.midi_priorities))
        self.build()

    def tr(self, key):
        return TEXT[self.language][key]

    def build(self):
        for item in self.root.winfo_children():
            item.destroy()
        self.root.title(self.tr('title'))
        self.root.geometry('1150x690')
        self.root.minsize(960, 570)
        root = ttk.Frame(self.root, padding=8)
        root.pack(fill='both', expand=True)

        toolbar = ttk.Frame(root)
        toolbar.pack(fill='x', pady=(0, 6))
        for key, cb in [('open', self.open), ('convert', lambda: self.run('convert')),
                        ('play', lambda: self.run('play')), ('stop', self.stop),
                        ('export', lambda: self.run('export')), ('help', self.help)]:
            ttk.Button(toolbar, text=self.tr(key), command=cb).pack(side='left', padx=(0, 5))
        ttk.Label(toolbar, text=self.tr('lang') + ':').pack(side='left', padx=(12, 3))
        self.lang_var = tk.StringVar(value=self.language.upper())
        lc = ttk.Combobox(toolbar, width=4, state='readonly', values=['RU', 'EN'], textvariable=self.lang_var)
        lc.pack(side='left')
        lc.bind('<<ComboboxSelected>>', self.change_language)

        opts = ttk.Frame(root)
        opts.pack(fill='x', pady=(0, 5))
        ttk.Label(opts, text=self.tr('clock')).pack(side='left')
        ttk.Combobox(opts, values=['2000000', '1773400'], width=9,
                     textvariable=self.clock_var).pack(side='left', padx=3)
        ttk.Checkbutton(opts, text=self.tr('loop'), variable=self.loop_var).pack(side='left', padx=8)
        ttk.Label(opts, text=self.tr('overflow')).pack(side='left')
        cb = ttk.Combobox(opts, state='readonly', width=11, values=[self.tr('steal'), self.tr('drop')])
        cb.set(self.tr(self.overflow_var.get()))
        cb.pack(side='left', padx=3)
        cb.bind('<<ComboboxSelected>>', lambda e: self.overflow_var.set(
            'steal' if cb.get() == self.tr('steal') else 'drop'))
        ttk.Label(opts, text=self.tr('percussion')).pack(side='left', padx=(8, 2))
        pc = ttk.Combobox(opts, state='readonly', width=6, values=[self.tr('noise'), self.tr('off')])
        pc.set(self.tr(self.perc_var.get()))
        pc.pack(side='left')
        pc.bind('<<ComboboxSelected>>', lambda e: self.perc_var.set(
            'noise' if pc.get() == self.tr('noise') else 'off'))
        ttk.Checkbutton(opts, text=self.tr('wav'), variable=self.wav_var).pack(side='right', padx=4)
        ttk.Checkbutton(opts, text=self.tr('csv'), variable=self.csv_var).pack(side='right', padx=4)

        pane = ttk.Panedwindow(root, orient='horizontal')
        pane.pack(fill='both', expand=True)
        left = ttk.LabelFrame(pane, text=self.tr('voices'), padding=8)
        right = ttk.LabelFrame(pane, text=self.tr('drums'), padding=8)
        pane.add(left, weight=4)
        pane.add(right, weight=6)

        cols = [('gain', 7), ('attack', 9), ('decay', 9), ('sustain', 10),
                ('release', 9), ('vibrato', 11)]
        ttk.Label(left, text='').grid(row=0, column=0)
        for c, (key, _) in enumerate(cols, 1):
            ttk.Label(left, text=self.tr(key)).grid(row=0, column=c, padx=2)
        self.voice_vars = []
        for r, voice in enumerate(self.cfg.voices, 1):
            ttk.Label(left, text='ABC'[r-1], font=('Arial', 13, 'bold')).grid(row=r, column=0, padx=4)
            vars_ = []
            for c, (key, width) in enumerate(cols, 1):
                value = getattr(voice, {'gain': 'gain', 'attack': 'attack_ms', 'decay': 'decay_ms',
                                        'sustain': 'sustain', 'release': 'release_ms',
                                        'vibrato': 'vibrato_cents'}[key])
                var = tk.StringVar(value=str(value))
                vars_.append(var)
                ttk.Entry(left, textvariable=var, width=width, justify='center').grid(
                    row=r, column=c, padx=2, pady=4, sticky='ew')
            self.voice_vars.append(vars_)
        ttk.Separator(left).grid(row=4, column=0, columnspan=7, sticky='ew', pady=12)
        ttk.Label(left, text=self.tr('priorities')).grid(
            row=5, column=0, columnspan=7, sticky='w', pady=(0, 4))
        ttk.Entry(left, textvariable=self.priority_var).grid(
            row=6, column=0, columnspan=7, sticky='ew')
        preset_buttons = ttk.Frame(left)
        preset_buttons.grid(row=7, column=0, columnspan=7, sticky='w', pady=8)
        ttk.Button(preset_buttons, text=self.tr('load_preset'), command=self.load_preset).pack(side='left')
        ttk.Button(preset_buttons, text=self.tr('save_preset'), command=self.save_preset).pack(side='left', padx=6)

        columns = ('note', 'name', 'volume', 'duration', 'noise')
        self.tree = ttk.Treeview(right, columns=columns, show='headings', height=13, selectmode='browse')
        for col, width, anchor in [('note', 48, 'center'), ('name', 170, 'w'),
                                   ('volume', 65, 'center'), ('duration', 78, 'center'), ('noise', 65, 'center')]:
            self.tree.heading(col, text=self.tr(col) if col in TEXT[self.language] else col)
            self.tree.column(col, width=width, anchor=anchor, stretch=col == 'name')
        scroller = ttk.Scrollbar(right, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroller.set)
        self.tree.pack(side='top', fill='both', expand=True)
        scroller.place(in_=self.tree, relx=1, rely=0, relheight=1, anchor='ne')
        self.tree.bind('<<TreeviewSelect>>', self.select_drum)
        self.drum_vars = {name: tk.StringVar() for name in
                          ('volume', 'duration_ms', 'noise_period', 'start_hz', 'end_hz', 'priority')}
        self.drum_tone = tk.BooleanVar()
        self.drum_noise = tk.BooleanVar()
        editor = ttk.Frame(right)
        editor.pack(fill='x', pady=(8, 0))
        fields = [('volume', 'volume'), ('duration_ms', 'duration'),
                  ('noise_period', 'noise_period'), ('start_hz', 'start_hz'),
                  ('end_hz', 'end_hz'), ('priority', 'priority')]
        for i, (name, label) in enumerate(fields):
            row, col = divmod(i, 3)
            wrapper = ttk.Frame(editor)
            wrapper.grid(row=row, column=col, sticky='ew', padx=4, pady=3)
            ttk.Label(wrapper, text=self.tr(label)).pack(anchor='w')
            ttk.Entry(wrapper, textvariable=self.drum_vars[name], width=12).pack(fill='x')
            editor.columnconfigure(col, weight=1)
        tools = ttk.Frame(right)
        tools.pack(fill='x', pady=6)
        ttk.Checkbutton(tools, text=self.tr('tone'), variable=self.drum_tone).pack(side='left')
        ttk.Checkbutton(tools, text=self.tr('noise_enable'), variable=self.drum_noise).pack(side='left', padx=7)
        ttk.Button(tools, text=self.tr('apply'), command=self.apply_drum).pack(side='right', padx=3)
        ttk.Button(tools, text=self.tr('drum_test'), command=self.test_drum).pack(side='right', padx=3)
        for n in GM_DRUM_NAMES:
            self.refresh_drum(n)
        self.tree.selection_set('35')
        self.tree.see('35')
        self.status.set(self.tr('ready'))
        ttk.Label(root, textvariable=self.status, anchor='w', wraplength=1100).pack(fill='x', pady=(8, 0))

    def change_language(self, _=None):
        self.capture_voices()
        self.language = self.lang_var.get().lower()
        self.build()

    def capture_voices(self):
        try:
            for i, row in enumerate(self.voice_vars):
                values = [float(v.get()) for v in row]
                self.cfg.voices[i] = Voice(
                    values[0], int(values[1]), int(values[2]), values[3], int(values[4]), values[5])
        except ValueError:
            raise ValueError('Invalid AY voice envelope setting / Неверные настройки голоса AY')

    def read_settings(self):
        self.capture_voices()
        self.cfg.clock = int(self.clock_var.get())
        self.cfg.loop = self.loop_var.get()
        self.cfg.overflow = self.overflow_var.get()
        self.cfg.percussion = self.perc_var.get()
        self.cfg.midi_priorities = [int(x.strip()) for x in self.priority_var.get().split(',')]
        self.cfg.validate()
        return Settings.from_dict(self.cfg.to_dict())

    def refresh_drum(self, note):
        d = self.cfg.drums[note]
        self.tree.insert('', 'end', iid=str(note), values=(note, GM_DRUM_NAMES[note],
                         d.volume, d.duration_ms, d.noise_period)) if not self.tree.exists(str(note)) else self.tree.item(
                             str(note), values=(note, GM_DRUM_NAMES[note], d.volume, d.duration_ms, d.noise_period))

    def select_drum(self, _=None):
        selected = self.tree.selection()
        if not selected:
            return
        n = int(selected[0])
        d = self.cfg.drums[n]
        for k, var in self.drum_vars.items():
            var.set(str(getattr(d, k)))
        self.drum_tone.set(d.tone)
        self.drum_noise.set(d.noise)

    def apply_drum(self):
        try:
            n = int(self.tree.selection()[0])
            d = Drum(volume=int(self.drum_vars['volume'].get()),
                     duration_ms=int(self.drum_vars['duration_ms'].get()),
                     noise_period=int(self.drum_vars['noise_period'].get()),
                     start_hz=float(self.drum_vars['start_hz'].get()),
                     end_hz=float(self.drum_vars['end_hz'].get()),
                     priority=int(self.drum_vars['priority'].get()),
                     tone=self.drum_tone.get(), noise=self.drum_noise.get())
            self.cfg.drums[n] = d
            self.cfg.validate()
            self.refresh_drum(n)
            return n
        except (ValueError, IndexError, KeyError) as exc:
            messagebox.showerror(self.tr('error'), str(exc))
            return None

    def stop(self):
        # A stop during lengthy conversion cancels the pending preview too.
        self.play_generation += 1
        self.player.stop()

    def open(self):
        filename = filedialog.askopenfilename(filetypes=[('MIDI', '*.mid *.midi'), ('All files', '*.*')])
        if filename:
            self.path = Path(filename)
            self.status.set(str(self.path))

    def run(self, action):
        if self.busy:
            return
        if self.path is None:
            self.open()
        if self.path is None:
            return
        try:
            cfg = self.read_settings()
        except (ValueError, TypeError) as exc:
            messagebox.showerror(self.tr('error'), str(exc))
            return
        output = None
        source = self.path
        token = self.play_generation
        if action == 'export':
            filename = filedialog.asksaveasfilename(defaultextension='.aym', initialfile=self.path.stem + '.aym',
                                                     filetypes=[('AYM1', '*.aym')])
            if not filename:
                return
            output = Path(filename)
        self.busy = True
        want_csv = self.csv_var.get()
        want_wav = self.wav_var.get()
        self.status.set(self.tr('working'))
        def work():
            try:
                blob, frames, report = convert(source, cfg)
                pcm = render_pcm(frames, cfg.clock) if action == 'play' else None
                wav = render_wav(frames, cfg.clock) if action == 'export' and want_wav else None
                if action == 'export':
                    write_outputs(output, blob, frames, report, csv=want_csv)
                    if wav is not None:
                        output.with_suffix('.wav').write_bytes(wav)
                self.root.after(0, lambda: self.completed(action, report, pcm, output, token))
            except Exception as exc:
                self.root.after(0, lambda message=str(exc): self.fail(message))
        Thread(target=work, daemon=True).start()

    def completed(self, action, report, pcm, output, token):
        self.busy = False
        if pcm is not None and token == self.play_generation:
            try:
                self.player.play(pcm)
            except Exception as exc:
                self.fail(str(exc))
                return
        desc = (f"{report['frames']} × 20 ms · {report['bytes']} B · "
                f"poly max {report['peak_polyphony']} · "
                f"lost {report['lost_note_instances']} / {report['lost_note_frames']} frames")
        prefix = self.tr('saved') + ': ' + str(output) if action == 'export' else self.tr('done')
        self.status.set(prefix + ' · ' + desc)

    def fail(self, message):
        self.busy = False
        self.status.set(self.tr('error') + ': ' + message)
        messagebox.showerror(self.tr('error'), message)

    def test_drum(self):
        n = self.apply_drum()
        if n is None:
            return
        try:
            cfg = self.read_settings()
            length = cfg.drums[n].duration_ms
            test = Song([Event(0, 'on', 9, n, 120, 0, 0)], max(1, int(length / 20) + 3), 1, 0, [])
            frames, _ = synthesize(test, cfg)
            self.player.play(render_pcm(frames, cfg.clock))
        except Exception as exc:
            self.fail(str(exc))

    def load_preset(self):
        filename = filedialog.askopenfilename(filetypes=[('JSON', '*.json')])
        if not filename:
            return
        try:
            self.cfg = Settings.from_dict(json.loads(Path(filename).read_text(encoding='utf8')))
            self.clock_var.set(str(self.cfg.clock))
            self.loop_var.set(self.cfg.loop)
            self.overflow_var.set(self.cfg.overflow)
            self.perc_var.set(self.cfg.percussion)
            self.priority_var.set(','.join(str(x) for x in self.cfg.midi_priorities))
            self.build()
        except (OSError, ValueError, TypeError) as exc:
            self.fail(str(exc))

    def save_preset(self):
        try:
            cfg = self.read_settings()
        except (ValueError, TypeError) as exc:
            self.fail(str(exc))
            return
        filename = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON', '*.json')])
        if filename:
            Path(filename).write_text(json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False), encoding='utf8')
            self.status.set(str(filename))

    def help(self):
        messagebox.showinfo(self.tr('title') + ' — ' + self.tr('help'), self.tr('help_text'))


def main():
    root = tk.Tk()
    ttk.Style().theme_use('clam')
    Window(root)
    root.mainloop()


if __name__ == '__main__':
    main()
