"""Studio colours, ttk styles and a scrolling editor surface. No extra dependencies."""
from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

BG = '#141a21'
CARD = '#1b222b'
FIELD = '#141b23'
BORDER = '#34414e'
FG = '#edf2f5'
MUTED = '#9faebd'
TEAL = '#6ce5c1'
AMBER = '#f4c17d'
RED = '#f39b95'
VOICE_COLOURS = (TEAL, '#b7a3ff', AMBER)


def configure_theme(root: tk.Tk) -> ttk.Style:
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
        font.nametofont(name, root).configure(family='Segoe UI', size=10)
    font.nametofont('TkFixedFont', root).configure(family='Consolas', size=11)
    root.configure(background=BG)
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', FG)
    root.option_add('*TCombobox*Listbox.selectBackground', '#2b5147')
    root.option_add('*TCombobox*Listbox.selectForeground', FG)
    root.option_add('*TCombobox*Listbox.font', 'TkDefaultFont')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=BG, foreground=FG, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, font='TkDefaultFont')
    style.configure('TFrame', background=BG)
    style.configure('Card.TFrame', background=CARD, borderwidth=1, relief='solid')
    style.configure('CardBody.TFrame', background=CARD)
    style.configure('TLabel', background=BG, foreground=FG)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Title.TLabel', font=('Segoe UI', 20, 'bold'))
    style.configure('Brand.TLabel', font=('Segoe UI', 15, 'bold'))
    style.configure('Card.TLabel', background=CARD)
    style.configure('CardMuted.TLabel', background=CARD, foreground=MUTED)
    style.configure('CardTitle.TLabel', background=CARD, font=('Segoe UI', 11, 'bold'))
    style.configure('Section.TLabel', background=CARD, foreground=MUTED,
                    font=('Segoe UI', 9, 'bold'))
    style.configure('Metric.TLabel', background=CARD, font=('Consolas', 20))
    style.configure('Warning.TLabel', background=CARD, foreground=AMBER)
    style.configure('Success.TLabel', background=CARD, foreground=TEAL)
    style.configure('Error.TLabel', background=CARD, foreground=RED, font=('Segoe UI', 9))
    style.configure('TButton', background=FIELD, foreground=FG, padding=(12, 9),
                    borderwidth=1, focusthickness=2, focuscolor=TEAL)
    style.map('TButton', background=[('disabled', '#202830'), ('pressed', '#345148'),
                                   ('active', '#293b45')],
              foreground=[('disabled', '#73818e')], bordercolor=[('focus', TEAL)])
    style.configure('Primary.TButton', background=TEAL, foreground='#10271f',
                    font=('Segoe UI', 10, 'bold'))
    style.map('Primary.TButton', background=[('disabled', '#304b43'), ('pressed', '#48b99a'),
                                           ('active', '#8cf3d3')],
              foreground=[('disabled', '#8ca49c'), ('!disabled', '#10271f')])
    style.configure('Listen.TButton', background='#293f39', foreground=TEAL)
    style.map('Listen.TButton', background=[('disabled', '#202830'), ('active', '#34594c')],
              foreground=[('disabled', '#73818e'), ('!disabled', TEAL)])
    style.configure('Link.TButton', borderwidth=0, background=CARD, foreground=TEAL,
                    padding=(6, 4))
    style.map('Link.TButton', background=[('active', '#293d38'), ('!active', CARD)],
              foreground=[('disabled', '#73818e'), ('!disabled', TEAL)])
    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, fieldbackground=FIELD, foreground=FG, padding=(8, 6),
                        insertcolor=FG, arrowcolor=MUTED, borderwidth=1)
        style.map(name, fieldbackground=[('disabled', '#202830'), ('readonly', FIELD)],
                  foreground=[('disabled', '#73818e')],
                  bordercolor=[('invalid', RED), ('focus', TEAL)],
                  lightcolor=[('invalid', RED), ('focus', TEAL)],
                  darkcolor=[('invalid', RED), ('focus', TEAL)],
                  selectbackground=[('!disabled', '#2b5147')],
                  selectforeground=[('!disabled', FG)])
    style.configure('TCheckbutton', background=CARD, foreground=FG, padding=(0, 5),
                    indicatorbackground=FIELD, indicatorforeground=TEAL,
                    indicatormargin=(0, 0, 8, 0), focuscolor=TEAL)
    style.map('TCheckbutton', background=[('active', CARD)],
              foreground=[('disabled', '#73818e')],
              indicatorbackground=[('selected', '#294e42'), ('!selected', FIELD)])
    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(0, 0, 0, 10))
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(16, 10))
    style.map('TNotebook.Tab', background=[('selected', CARD), ('active', '#202b33')],
              foreground=[('selected', TEAL)], lightcolor=[('selected', TEAL)])
    style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=FG,
                    borderwidth=0, rowheight=32)
    style.configure('Treeview.Heading', background=CARD, foreground=MUTED,
                    padding=(8, 8), relief='flat')
    style.map('Treeview', background=[('selected', '#2b5147')],
              foreground=[('selected', FG)])
    style.map('Treeview.Heading', background=[('active', '#293b45')])
    style.configure('Vertical.TScrollbar', background='#3b4956', troughcolor=BG,
                    arrowcolor=MUTED, borderwidth=0)
    style.map('Vertical.TScrollbar', background=[('active', '#566c7d')])
    style.configure('TSeparator', background=BORDER)
    style.configure('Horizontal.TProgressbar', background=TEAL, troughcolor=FIELD,
                    borderwidth=0, lightcolor=TEAL, darkcolor=TEAL)
    for i, colour in enumerate(VOICE_COLOURS):
        style.configure(f'Voice{i}.TLabel', background=CARD, foreground=colour,
                        font=('Consolas', 20, 'bold'))
    return style


class ScrollFrame(ttk.Frame):
    """Resize an editor to the viewport and scroll vertically when it is too tall."""
    def __init__(self, parent):
        super().__init__(parent)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=BG, highlightthickness=0, width=1, height=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.canvas.bind('<Configure>', self._resize)
        self.content.bind('<Configure>', self._content_resize)

    def _resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)
        self._content_resize()

    def _content_resize(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        if self.content.winfo_reqheight() > self.canvas.winfo_height():
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def scroll(self, units):
        if self.content.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, 'units')

    def reveal(self, widget):
        """Bring a focused field into view, including on a vertically stacked tab."""
        top = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        total = max(1, self.content.winfo_height())
        if top < visible_top:
            self.canvas.yview_moveto(max(0, top - 8) / total)
        elif bottom > visible_top + height:
            self.canvas.yview_moveto(max(0, bottom - height + 8) / total)
