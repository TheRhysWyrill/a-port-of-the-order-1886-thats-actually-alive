#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Order: 1886 launcher (Tkinter).

The launcher window upstream is a Wails webview: HTML in WebKitGTK, which SteamOS does not ship
and cannot keep across updates (#114). This is the same launcher with no webview and no
dependency of its own: Tk widgets, the design the frontend used (charcoal #101315, brass
#d4bd75, Georgia headings, a 206px sidebar), driving the frozen Python worker the package
already ships over the same version-1 JSON-lines protocol.

Every page and every setting comes from the worker's own catalogue (`state.get` returns
`fields`, `pages` and the saved `settings`), so a change in `launcher/order1886_options.py`
appears here with no change to this file.

  order1886_launcher.py --project-root "<installation>" --state-dir "<state>"
  order1886_launcher.py --direct-play                    (Steam / Game Mode: no window)
  order1886_launcher.py --game-dir /path/to/CUSA00035 --direct-play
  order1886_launcher.py --self-test                      (build the window, report, exit)
"""
import argparse
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, font as tkfont, ttk

# ---------------------------------------------------------------------------------------------
# The look: the values launcher-frontend/assets/index-*.css used.

BG = '#101315'          # window ground
PANEL = '#171b1d'       # sidebar, bottom bar
CARD = '#202527'        # fields
EDGE = '#41484a'        # borders
TEXT = '#ece8dd'        # primary text
DIM = '#b9b9af'         # secondary text
BRASS = '#d4bd75'       # accents, selected page, the Play button
BRASS_LIGHT = '#f2d894'
WARN = '#e0731f'
SIDEBAR_WIDTH = 206

IMAGE = None            # kept alive by the Launcher instance; Tk drops unreferenced images

REQUEST_TIMEOUT = 45.0
START_TIMEOUT = 60.0


def family(*names):
    """The first of these font families this system has, else Tk's default."""
    available = set(tkfont.families())
    for name in names:
        if name in available:
            return name
    return tkfont.nametofont('TkDefaultFont').actual('family')


# ---------------------------------------------------------------------------------------------
# The worker: one JSON object per line in, one per line out, version 1, events interleaved.

class WorkerError(RuntimeError):
    pass


class _Slot:
    __slots__ = ('event', 'reply')

    def __init__(self):
        self.event = threading.Event()
        self.reply = None


class Worker:
    """The frozen worker (`bin/order1886-worker/order1886-worker`) as a request/reply client."""

    def __init__(self, root: Path, state: Path, on_event):
        self.root = root
        self.state = state
        self.binary = root / 'bin' / 'order1886-worker' / 'order1886-worker'
        self.on_event = on_event
        self.process = None
        self.failure = None
        self.closed = threading.Event()
        self._pending = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._next = 0
        self._tail = []

    def start(self):
        if not self.binary.is_file():
            raise WorkerError(f'the Order worker is missing ({self.binary}): this is not a '
                              'complete installation')
        self.process = subprocess.Popen(
            [str(self.binary), '--state-dir', str(self.state), '--installation-root', str(self.root)],
            cwd=str(self.root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', bufsize=1)
        threading.Thread(target=self._read, name='worker-read', daemon=True).start()
        threading.Thread(target=self._read_errors, name='worker-errors', daemon=True).start()

    def _read_errors(self):
        # Diagnostics never share the protocol pipe; keep the tail for the error message.
        for line in self.process.stderr:
            self._tail.append(line.rstrip('\n'))
            del self._tail[:-40]

    def _read(self):
        reason = None
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                reason = 'the worker returned a line that is not JSON'
                break
            if item.get('version') != 1:
                reason = 'the worker returned an unknown protocol version'
                break
            if item.get('event'):
                try:
                    self.on_event(item['event'], item.get('data'))
                except Exception:  # noqa: BLE001 - an event must never kill the reader
                    pass
                continue
            with self._lock:
                slot = self._pending.pop(item.get('id'), None)
            if slot is not None:
                slot.reply = item
                slot.event.set()
        code = self.process.wait()
        if reason is None:
            reason = f'the worker exited ({code})' if code else 'the worker closed'
        if self._tail:
            reason += ': ' + ' / '.join(self._tail[-3:])
        with self._lock:
            self.failure = reason
            waiting = list(self._pending.values())
            self._pending.clear()
        for slot in waiting:                     # unblock every request in flight
            slot.event.set()
        self.closed.set()
        try:
            self.on_event('disconnected', reason)
        except Exception:  # noqa: BLE001
            pass

    def request(self, method, params=None, timeout=REQUEST_TIMEOUT):
        if self.process is None:
            raise WorkerError('the worker is not running')
        with self._lock:
            if self.failure is not None:
                raise WorkerError(self.failure)
            self._next += 1
            identifier = self._next
            slot = _Slot()
            self._pending[identifier] = slot
        payload = json.dumps({'version': 1, 'id': identifier, 'method': method,
                              'params': params or {}}, ensure_ascii=False) + '\n'
        try:
            with self._write_lock:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            with self._lock:
                self._pending.pop(identifier, None)
            raise WorkerError(f'cannot send a request to the worker: {error}')
        if not slot.event.wait(timeout):
            with self._lock:
                self._pending.pop(identifier, None)
            raise WorkerError(f'the worker did not answer {method} in {timeout:.0f}s')
        if slot.reply is None:
            raise WorkerError(self.failure or 'the worker closed')
        error = slot.reply.get('error')
        if error:
            raise WorkerError(error.get('message') or 'the worker refused the request')
        return slot.reply.get('result')

    def close(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.stdin.close()
            except OSError:
                pass

    def wait(self, timeout=None):
        return self.closed.wait(timeout)


def read_snapshot(worker):
    result = worker.request('state.get')
    if not isinstance(result, dict):
        raise WorkerError('the worker did not return a state snapshot')
    return result


# ---------------------------------------------------------------------------------------------
# Direct play: the Steam / Game Mode path, no window at all.

def run_direct(root: Path, state: Path, game_dir: str) -> int:
    finished = queue.Queue()

    def event(name, data):
        if name == 'done':
            code = 1
            if isinstance(data, list) and data:
                try:
                    code = int(data[0])
                except (TypeError, ValueError):
                    code = 1
            finished.put(code)
        elif name == 'disconnected':
            finished.put(None)

    worker = Worker(root, state, event)
    try:
        worker.start()
    except WorkerError as error:
        print(error, file=sys.stderr)
        return 1
    previous = ''
    if game_dir:
        try:
            folder = str(Path(game_dir).expanduser().resolve())
        except OSError as error:
            print(f'cannot use --game-dir {game_dir}: {error}', file=sys.stderr)
            worker.close()
            return 1
        try:
            snapshot = read_snapshot(worker)
            settings = snapshot.get('settings') or {}
            previous = settings.get('game_dir') or ''
        except WorkerError:
            previous = ''
        try:
            worker.request('settings.set', {'values': {'game_dir': folder}})
        except WorkerError as error:
            print(f'cannot use --game-dir {folder}: {error}', file=sys.stderr)
            worker.close()
            return 1
    try:
        worker.request('session.start', {'mode': 'play'}, timeout=START_TIMEOUT)
    except WorkerError as error:
        # A folder the session refuses is not kept: put back what was saved before.
        if game_dir and previous:
            try:
                worker.request('settings.set', {'values': {'game_dir': previous}})
            except WorkerError:
                pass
        print(f'Direct play needs setup: {error}', file=sys.stderr)
        print('Set the game folder with --game-dir /path/to/your/game, or start without '
              '--direct-play for the launcher.', file=sys.stderr)
        worker.close()
        return 1
    code = finished.get()
    worker.close()
    worker.wait(15)
    if code is None:
        print('the worker closed during the session', file=sys.stderr)
        return 1
    return int(code)


# ---------------------------------------------------------------------------------------------
# The window.

class Launcher:
    def __init__(self, root: Path, state: Path):
        self.root_path = root
        self.state_path = state
        self.worker = None
        self.snapshot = {}
        self.loading = False
        self.built = False
        self.pages = []                 # catalogue order: [{'id','title'}]
        self.page_frames = {}           # page id -> scrollable body
        self.nav_items = {}             # page id -> (row frame, label, accent)
        self.chips = {}                 # home strip labels
        self.variables = []             # Tk variables: a local one is collected and unsets itself
        self.updates = []               # snapshot -> put its value in a widget
        self.current = None
        self.rows = 0
        self.note = ''                   # "Session finished.", kept in front of the status
        self.events = queue.Queue()      # worker events, from its reader thread
        self.jobs = queue.Queue()        # calls the window thread must make (Tk is not thread safe)

        self.window = tk.Tk()
        self.window.title('The Order: 1886')
        self.window.geometry('1280x820')
        self.window.minsize(940, 620)
        self.window.configure(bg=BG)
        self.serif = family('Georgia', 'DejaVu Serif', 'Liberation Serif', 'Noto Serif')
        self.sans = family('DejaVu Sans', 'Liberation Sans', 'Noto Sans', 'Segoe UI')
        self._style()
        self._layout()
        self.window.protocol('WM_DELETE_WINDOW', self.quit)
        self.window.after(60, self.poll)

    # -- styled ---------------------------------------------------------------------------------

    def _style(self):
        style = ttk.Style(self.window)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('TFrame', background=BG)
        style.configure('Panel.TFrame', background=PANEL)
        style.configure('Card.TFrame', background=CARD)
        style.configure('TLabel', background=BG, foreground=TEXT, font=(self.sans, 10))
        style.configure('Title.TLabel', background=BG, foreground=TEXT, font=(self.serif, 22))
        style.configure('Key.TLabel', background=BG, foreground=TEXT, font=(self.sans, 11, 'bold'))
        style.configure('KeyOff.TLabel', background=BG, foreground=DIM, font=(self.sans, 11, 'bold'))
        style.configure('Hint.TLabel', background=BG, foreground=DIM, font=(self.sans, 9))
        style.configure('Brand.TLabel', background=PANEL, foreground=BRASS,
                        font=(self.serif, 17, 'bold'))
        style.configure('BrandSub.TLabel', background=PANEL, foreground=DIM, font=(self.sans, 8))
        style.configure('Nav.TLabel', background=PANEL, foreground=DIM, font=(self.sans, 11))
        style.configure('NavOn.TLabel', background=PANEL, foreground=BRASS_LIGHT,
                        font=(self.sans, 11, 'bold'))
        style.configure('Chip.TLabel', background=CARD, foreground=DIM, font=(self.sans, 9))
        style.configure('Status.TLabel', background=PANEL, foreground=DIM, font=(self.sans, 9))
        style.configure('TEntry', fieldbackground=CARD, background=CARD, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                        padding=4)
        style.configure('TCombobox', fieldbackground=CARD, background=CARD, foreground=TEXT,
                        arrowcolor=BRASS, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                        padding=4)
        style.map('TCombobox', fieldbackground=[('readonly', CARD)],
                  foreground=[('readonly', TEXT)], selectbackground=[('readonly', CARD)],
                  selectforeground=[('readonly', BRASS_LIGHT)])
        style.configure('TSpinbox', fieldbackground=CARD, background=CARD, foreground=TEXT,
                        arrowcolor=BRASS, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                        padding=3)
        style.configure('TCheckbutton', background=BG, foreground=TEXT, font=(self.sans, 10))
        style.map('TCheckbutton', background=[('active', BG)],
                  foreground=[('selected', BRASS_LIGHT)])
        style.configure('TButton', background=CARD, foreground=TEXT, font=(self.sans, 9),
                        bordercolor=EDGE, focuscolor=BRASS, padding=(10, 5))
        style.map('TButton', background=[('active', EDGE)], foreground=[('active', TEXT)])
        style.configure('Brass.TButton', background=BRASS, foreground=BG,
                        font=(self.sans, 11, 'bold'), bordercolor=BRASS_LIGHT,
                        focuscolor=BRASS_LIGHT, padding=(26, 8))
        style.map('Brass.TButton',
                  background=[('disabled', EDGE), ('active', BRASS_LIGHT)],
                  foreground=[('disabled', DIM)])
        style.configure('Vertical.TScrollbar', background=CARD, troughcolor=BG,
                        bordercolor=BG, arrowcolor=DIM)

    def _layout(self):
        outer = tk.Frame(self.window, bg=BG)
        outer.pack(fill='both', expand=True)

        sidebar = ttk.Frame(outer, style='Panel.TFrame', width=SIDEBAR_WIDTH)
        sidebar.pack(side='left', fill='y')
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text='THE ORDER', bg=PANEL, fg=BRASS,
                 font=(self.serif, 17, 'bold')).pack(anchor='w', padx=20, pady=(22, 0))
        tk.Label(sidebar, text='1 8 8 6', bg=PANEL, fg=BRASS_LIGHT,
                 font=(self.serif, 17, 'bold')).pack(anchor='w', padx=20, pady=(0, 4))
        tk.Label(sidebar, text='Port by DivinusX', bg=PANEL, fg=DIM,
                 font=(self.sans, 8)).pack(anchor='w', padx=20, pady=(0, 18))
        self.nav_frame = tk.Frame(sidebar, bg=PANEL)
        self.nav_frame.pack(fill='x')

        column = tk.Frame(outer, bg=BG)
        column.pack(side='left', fill='both', expand=True)
        header = tk.Frame(column, bg=BG)
        header.pack(fill='x', padx=26, pady=(20, 6))
        self.page_title = tk.Label(header, text='', bg=BG, fg=TEXT, font=(self.serif, 22),
                                   anchor='w')
        self.page_title.pack(anchor='w')
        tk.Frame(header, bg=EDGE, height=1).pack(fill='x', pady=(10, 0))

        self.page_area = tk.Frame(column, bg=BG)
        self.page_area.pack(fill='both', expand=True, padx=(16, 8), pady=(6, 6))

        bar = tk.Frame(column, bg=PANEL)
        bar.pack(fill='x', side='bottom')
        self.status = tk.Label(bar, text='Starting…', bg=PANEL, fg=DIM, font=(self.sans, 9),
                               anchor='w', justify='left', wraplength=760)
        self.status.pack(side='left', fill='x', expand=True, padx=20, pady=12)
        self.stop_button = ttk.Button(bar, text='Stop', command=self.stop_session,
                                      style='TButton', state='disabled')
        self.stop_button.pack(side='right', padx=(0, 8), pady=10)
        self.play_button = ttk.Button(bar, text='PLAY', command=self.play, style='Brass.TButton')
        self.play_button.pack(side='right', padx=(0, 12), pady=10)

    # -- worker ---------------------------------------------------------------------------------

    def connect(self):
        if self.worker is not None:
            return None
        self.worker = Worker(self.root_path, self.state_path, self.event)
        try:
            self.worker.start()
        except WorkerError as error:
            self.worker = None
            return str(error)
        return None

    def event(self, name, data):
        """Called on the worker's reader thread: hand the event to the window thread."""
        self.events.put((name, data))

    def post(self, call, *args):
        """Queue a call for the window thread; Tk must never be touched from another one."""
        self.jobs.put((call, args))

    def poll(self):
        try:
            while True:
                name, data = self.events.get_nowait()
                self.handle_event(name, data)
        except queue.Empty:
            pass
        try:
            while True:
                call, args = self.jobs.get_nowait()
                try:
                    call(*args)
                except Exception:  # noqa: BLE001 - a failed update must not stop the window
                    pass
        except queue.Empty:
            pass
        self.window.after(80, self.poll)

    def handle_event(self, name, data):
        if name == 'started':
            self.note = ''
            self.set_status('Running…')
            self.busy(True)
        elif name == 'done':
            self.note = 'Session finished.'
            self.set_status(self.note)
            self.busy(False)
            self.reload()
        elif name == 'disconnected':
            self.note = ''
            self.busy(False)
            self.set_status(f'The worker closed. Restart the launcher. ({data})')

    def busy(self, running):
        self.play_button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')

    def load(self):
        error = self.connect()
        if error:
            self.set_status(error)
            return
        threading.Thread(target=self._load, name='load', daemon=True).start()

    def _load(self):
        try:
            snapshot = read_snapshot(self.worker)
        except WorkerError as error:
            self.post(self.set_status, f'Worker: {error}')
            return
        self.post(self.show, snapshot)

    def reload(self):
        if self.worker is not None:
            threading.Thread(target=self._load, name='reload', daemon=True).start()

    def save(self, key, value):
        if self.worker is None:
            self.set_status('The worker is not running; the change was not saved.')
            return
        threading.Thread(target=self._save, args=(key, value), name='save', daemon=True).start()

    def _save(self, key, value):
        try:
            snapshot = self.worker.request('settings.set', {'values': {key: value}})
        except WorkerError as error:
            self.post(self.set_status, f'{key} was not saved: {error}')
            self.post(self.reload)
            return
        if isinstance(snapshot, dict):
            self.post(self.apply, snapshot)

    def set_status(self, text):
        self.status.configure(text=text)

    # -- pages ----------------------------------------------------------------------------------

    def show(self, snapshot):
        if not self.built:
            self.snapshot = snapshot
            self.build(snapshot)
        self.apply(snapshot)

    def build(self, snapshot):
        self.pages = snapshot.get('pages') or []
        for item in self.pages:
            page_id, title = item.get('id'), item.get('title') or item.get('id')
            body = self.scrollable(self.page_area)
            self.page_frames[page_id] = body
            heading = tk.Frame(body, bg=BG)
            heading.pack(fill='x', padx=10, pady=(4, 10))
            tk.Label(heading, text=title, bg=BG, fg=TEXT, font=(self.serif, 18),
                     anchor='w').pack(anchor='w')
            tk.Frame(heading, bg=BRASS, height=2, width=64).pack(anchor='w', pady=(6, 0))
            if page_id == 'home':
                self.build_home(body)
            for field in snapshot.get('fields') or []:
                if field.get('page') == page_id:
                    self.build_row(body, field)
            self.add_nav(page_id, title)
        self.built = True
        # A field on a page the catalogue does not list would be invisible: give it one page.
        catalogued = {item.get('id') for item in self.pages}
        extras = [field for field in snapshot.get('fields') or []
                  if field.get('page') and field.get('page') not in catalogued]
        for page_id in dict.fromkeys(field['page'] for field in extras):
            body = self.scrollable(self.page_area)
            self.page_frames[page_id] = body
            self.add_nav(page_id, page_id.replace('_', ' ').title())
            heading = tk.Frame(body, bg=BG)
            heading.pack(fill='x', padx=10, pady=(4, 10))
            tk.Label(heading, text=page_id.replace('_', ' ').title(), bg=BG, fg=TEXT,
                     font=(self.serif, 18), anchor='w').pack(anchor='w')
        for field in extras:
            self.build_row(self.page_frames[field['page']], field)
        if self.pages:
            self.select_page(self.pages[0].get('id'))
        elif self.page_frames:
            self.select_page(next(iter(self.page_frames)))

    def scrollable(self, parent):
        holder = tk.Frame(parent, bg=BG)
        holder.pack_forget()
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(holder, orient='vertical', command=canvas.yview,
                            style='Vertical.TScrollbar')
        body = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=body, anchor='nw')
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        body.bind('<Configure>',
                  lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',
                    lambda event: canvas.itemconfigure(window, width=event.width))

        def wheel(event):
            if getattr(event, 'num', None) == 4:
                canvas.yview_scroll(-2, 'units')
            elif getattr(event, 'num', None) == 5:
                canvas.yview_scroll(2, 'units')
            else:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')

        for widget in (canvas, body):
            widget.bind('<Enter>', lambda _event, c=canvas: c.bind_all('<MouseWheel>', wheel))
            widget.bind('<Leave>', lambda _event, c=canvas: c.unbind_all('<MouseWheel>'))
        body.holder = holder
        return body

    def add_nav(self, page_id, title):
        row = tk.Frame(self.nav_frame, bg=PANEL)
        row.pack(fill='x')
        accent = tk.Frame(row, bg=PANEL, width=3)
        accent.pack(side='left', fill='y')
        label = tk.Label(row, text=title, bg=PANEL, fg=DIM, font=(self.sans, 11),
                         anchor='w', padx=14, pady=9)
        label.pack(side='left', fill='x', expand=True)

        def enter(_event):
            if self.current != page_id:
                row.configure(bg=CARD)
                label.configure(bg=CARD, fg=TEXT)

        def leave(_event):
            if self.current != page_id:
                row.configure(bg=PANEL)
                label.configure(bg=PANEL, fg=DIM)

        for widget in (row, label):
            widget.bind('<Button-1>', lambda _event, p=page_id: self.select_page(p))
            widget.bind('<Enter>', enter)
            widget.bind('<Leave>', leave)
        self.nav_items[page_id] = (row, label, accent)

    def select_page(self, page_id):
        if page_id not in self.page_frames:
            return
        for other, body in self.page_frames.items():
            if other == page_id:
                body.holder.pack(fill='both', expand=True)
            else:
                body.holder.pack_forget()
        for other, (row, label, accent) in self.nav_items.items():
            chosen = other == page_id
            row.configure(bg=CARD if chosen else PANEL)
            label.configure(bg=CARD if chosen else PANEL,
                            fg=BRASS_LIGHT if chosen else DIM,
                            font=(self.sans, 11, 'bold') if chosen else (self.sans, 11))
            accent.configure(bg=BRASS if chosen else PANEL)
        self.current = page_id
        title = next((p.get('title') for p in self.pages if p.get('id') == page_id), page_id)
        self.page_title.configure(text=title or page_id)

    def build_home(self, body):
        artwork = self.root_path / 'docs' / 'images' / 'launcher-home.png'
        if artwork.is_file():
            try:
                image = tk.PhotoImage(file=str(artwork))
                if image.width() > 900:
                    image = image.subsample(max(1, round(image.width() / 900)))
            except tk.TclError:
                image = None
            if image is not None:
                label = tk.Label(body, image=image, bg=BG, bd=0)
                label.image = image
                label.pack(anchor='w', padx=10, pady=(0, 12))
        strip = tk.Frame(body, bg=BG)
        strip.pack(fill='x', padx=10, pady=(0, 14))
        for key in ('display', 'upscaling', 'frame rate', 'framing', 'game'):
            chip = tk.Label(strip, text=key, bg=CARD, fg=DIM, font=(self.sans, 9),
                            padx=10, pady=5)
            chip.pack(side='left', padx=(0, 8))
            self.chips[key] = chip

        def update(snapshot):
            settings = snapshot.get('settings') or {}
            game = snapshot.get('game') or {}

            def text(key):
                value = settings.get(key)
                return '—' if value is None else str(value)

            self.chips['display'].configure(text=f'Display: {text("display_mode")}')
            self.chips['upscaling'].configure(text=f'Upscaling: {text("presentation_filter")}')
            self.chips['frame rate'].configure(text=f'Frame rate: {text("frame_rate")}')
            self.chips['framing'].configure(text=f'Framing: {text("aspect_ratio")}')
            where = 'ready' if game.get('ready') else 'not set'
            self.chips['game'].configure(text=f'Game folder: {where} — {game.get("message", "")}')

        self.updates.append(update)

    def build_row(self, body, field):
        key = field.get('key') or ''
        row = tk.Frame(body, bg=BG)
        row.pack(fill='x', padx=10, pady=3)
        left = tk.Frame(row, bg=BG)
        left.pack(side='left', fill='x', expand=True)
        ttk.Label(left, text=field.get('label') or key, style='Key.TLabel').pack(anchor='w')
        description = field.get('description') or ''
        unavailable = field.get('reason') or ''
        if description or unavailable:
            hint = description or ''
            if unavailable:
                hint = (hint + ' ' if hint else '') + f'({unavailable})'
            ttk.Label(left, text=hint, style='Hint.TLabel', wraplength=620,
                      justify='left').pack(anchor='w', pady=(1, 0))
        control, update = self.build_control(field, row)
        if control is not None:
            control.pack(side='right', padx=(16, 0))
        if update is not None:
            self.updates.append(update)
        self.rows += 1

    def build_control(self, field, master):
        """Returns (widget, snapshot -> value) for one catalogue field, packed into master."""
        kind = field.get('kind') or 'text'
        key = field.get('key') or ''
        if kind == 'boolean':
            variable = tk.BooleanVar(value=bool(field.get('default')))
            self.variables.append(variable)
            box = ttk.Checkbutton(master, variable=variable, style='TCheckbutton',
                                  command=lambda: None if self.loading else
                                  self.save(key, bool(variable.get())))
            return box, lambda snapshot: variable.set(bool((snapshot.get('settings') or {})
                                                           .get(key, field.get('default'))))
        if kind == 'select':
            values, labels = [], []
            for choice in field.get('choices') or []:
                values.append(choice.get('value'))
                label = choice.get('label') or str(choice.get('value'))
                if not choice.get('available', True):
                    label += '  (unavailable)'
                labels.append(label)
            box = ttk.Combobox(master, values=labels, state='readonly', width=34,
                               font=(self.sans, 9))

            def chosen(_event=None):
                if self.loading:
                    return
                index = box.current()
                if index < 0 or index >= len(values):
                    return
                choice = (field.get('choices') or [])[index]
                if not choice.get('available', True):
                    self.set_status(choice.get('reason') or
                                    'That option is not available on this system.')
                    self.apply(self.snapshot)
                    return
                self.save(key, values[index])

            box.bind('<<ComboboxSelected>>', chosen)

            def show(snapshot):
                current = str((snapshot.get('settings') or {}).get(key))
                for index, value in enumerate(values):
                    if str(value) == current:
                        box.current(index)
                        return
            return box, show
        if kind == 'number':
            minimum = field.get('minimum')
            maximum = field.get('maximum')
            step = field.get('multiple') or 1
            box = ttk.Spinbox(master, from_=0 if minimum is None else minimum,
                              to=1000 if maximum is None else maximum,
                              increment=step if step and step > 1 else 1, width=10,
                              font=(self.sans, 9),
                              command=lambda: None if self.loading else
                              self.save(key, number(box.get())))
            box.bind('<FocusOut>', lambda _event: None if self.loading else
                     self.save(key, number(box.get())))
            box.bind('<Return>', lambda _event: None if self.loading else
                     self.save(key, number(box.get())))

            def show(snapshot):
                value = (snapshot.get('settings') or {}).get(key)
                if isinstance(value, (int, float)):
                    box.set(str(int(value)) if float(value).is_integer() else str(value))
            return box, show
        if kind in ('text', 'secret', 'order'):
            variable = tk.StringVar()
            self.variables.append(variable)
            box = ttk.Entry(master, textvariable=variable, width=32,
                            font=(self.sans, 9),
                            show='•' if kind == 'secret' else '')
            if kind == 'order':
                box.configure(width=40)

            def typed(_event=None):
                if self.loading:
                    return
                text = variable.get()
                if kind == 'order':
                    order = [name.strip() for name in text.split(',') if name.strip()]
                    self.save(key, order)
                else:
                    self.save(key, text)
            box.bind('<Return>', typed)
            box.bind('<FocusOut>', typed)

            def show(snapshot):
                value = (snapshot.get('settings') or {}).get(key)
                if isinstance(value, list) and kind == 'order':
                    value = ', '.join(str(item) for item in value)
                text = '' if value is None else str(value)
                if variable.get() != text:
                    variable.set(text)
            return box, show
        if kind in ('directory', 'file'):
            holder = tk.Frame(master, bg=BG)
            variable = tk.StringVar()
            self.variables.append(variable)
            entry = ttk.Entry(holder, textvariable=variable, width=30, font=(self.sans, 9))
            entry.pack(side='left')
            browse = ttk.Button(holder, text='Browse…', style='TButton',
                                command=lambda: self.browse(kind, key, variable, field))
            browse.pack(side='left', padx=(6, 0))

            def typed(_event=None):
                if not self.loading:
                    self.save(key, variable.get().strip())
            entry.bind('<Return>', typed)
            entry.bind('<FocusOut>', typed)

            def show(snapshot):
                value = (snapshot.get('settings') or {}).get(key)
                text = '' if value is None else str(value)
                if variable.get() != text:
                    variable.set(text)
            return holder, show
        if kind == 'bindings':
            return self.build_bindings(field, master)
        note = ttk.Label(master, text='Managed by the worker; not editable in this window '
                                              'yet.', style='Hint.TLabel')
        return note, None

    def browse(self, kind, key, variable, field):
        if kind == 'directory':
            path = filedialog.askdirectory(parent=self.window, title=field.get('label') or key,
                                           initialdir=variable.get() or str(Path.home()))
        else:
            path = filedialog.askopenfilename(parent=self.window,
                                              title=field.get('label') or key,
                                              initialdir=str(Path.home()))
        if path:
            variable.set(path)
            self.save(key, path)

    def build_bindings(self, field, master):
        """One selector per button: a single source per button, as the value stores a list."""
        key = field.get('key') or ''
        holder = tk.Frame(master, bg=BG)
        choices = field.get('choices') or []
        labels = [choice.get('label') or str(choice.get('value')) for choice in choices]
        values = [choice.get('value') for choice in choices]
        selectors = {}
        current = {}

        for button in field.get('buttons') or []:
            line = tk.Frame(holder, bg=BG)
            line.pack(fill='x', pady=1)
            tk.Label(line, text=button.get('label') or button.get('id'), bg=BG, fg=DIM,
                     font=(self.sans, 9), width=16, anchor='w').pack(side='left')
            box = ttk.Combobox(line, values=labels, state='readonly', width=24,
                               font=(self.sans, 9))
            box.pack(side='left', padx=(6, 0))
            button_id = button.get('id')

            def chosen(_event=None, b=button_id, widget=box):
                if self.loading:
                    return
                index = widget.current()
                if index < 0 or index >= len(values):
                    return
                source = values[index]
                if source in (None, ''):
                    current.pop(b, None)
                else:
                    current[b] = [source]
                self.save(key, {name: list(items) for name, items in current.items()})

            box.bind('<<ComboboxSelected>>', chosen)
            selectors[button_id] = box

        def show(snapshot):
            if self.loading:
                return
            stored = (snapshot.get('settings') or {}).get(key) or {}
            current.clear()
            for name, raw in stored.items():
                if isinstance(raw, list):
                    current[name] = [str(item) for item in raw]
            for name, box in selectors.items():
                sources = current.get(name) or []
                target = str(sources[0]) if sources else ''
                box.set('')
                for index, value in enumerate(values):
                    if str(value) == target:
                        box.current(index)
                        break
        return holder, show

    # -- snapshot -------------------------------------------------------------------------------

    def apply(self, snapshot):
        self.snapshot = snapshot
        self.loading = True
        try:
            for update in self.updates:
                try:
                    update(snapshot)
                except Exception:  # noqa: BLE001 - one bad widget must not stop the rest
                    pass
        finally:
            self.loading = False
        game = snapshot.get('game') or {}
        if snapshot.get('busy'):
            self.busy(True)
            self.set_status('Running…')
            return
        self.busy(False)
        message = game.get('message') or ''
        if not game.get('ready'):
            message = 'Game folder: ' + (message or 'not set')
        last = snapshot.get('last_exit')
        if isinstance(last, int) and last:
            message += f' · last session exited {last}'
        if self.note:
            message = f'{self.note} · {message}' if message else self.note
        self.set_status(message or self.note or 'Ready.')

    # -- session --------------------------------------------------------------------------------

    def play(self):
        if self.worker is None:
            self.load()
            return
        self.busy(True)
        self.set_status('Starting…')
        threading.Thread(target=self._play, name='play', daemon=True).start()

    def _play(self):
        try:
            self.worker.request('session.start', {'mode': 'play'}, timeout=START_TIMEOUT)
        except WorkerError as error:
            self.post(self.set_status, f'Cannot start: {error}')
            self.post(self.busy, False)
            return
        self.post(self.set_status, 'Running…')

    def stop_session(self):
        if self.worker is None:
            return
        self.stop_button.configure(state='disabled')
        threading.Thread(target=self._stop, name='stop', daemon=True).start()

    def _stop(self):
        try:
            self.worker.request('session.stop')
            self.post(self.set_status, 'Stopping…')
        except WorkerError as error:
            self.post(self.set_status, f'Cannot stop: {error}')
        self.post(self.stop_button.configure, {'state': 'disabled'})

    # -- lifecycle ------------------------------------------------------------------------------

    def quit(self):
        if self.worker is not None:
            self.worker.close()
        self.window.destroy()

    def run(self):
        self.load()
        self.window.mainloop()


def number(text):
    """The spin box's text as the number the worker expects (whole numbers stay whole)."""
    try:
        value = float(str(text).strip())
    except ValueError:
        return 0
    return int(value) if value.is_integer() else value


def main(argv=None):
    parser = argparse.ArgumentParser(description='The Order: 1886 launcher')
    parser.add_argument('--project-root', default='')
    parser.add_argument('--state-dir', default='')
    parser.add_argument('--game-dir', default='')
    parser.add_argument('--direct-play', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)

    here = Path(__file__).resolve().parent.parent
    root = Path(args.project_root).expanduser().resolve() if args.project_root else here
    state = (Path(args.state_dir).expanduser().resolve() if args.state_dir
             else root / 'user' / 'order1886')
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f'cannot create the state folder {state}: {error}', file=sys.stderr)
        return 1

    if args.direct_play or args.game_dir:
        return run_direct(root, state, args.game_dir)

    try:
        launcher = Launcher(root, state)
    except tk.TclError as error:
        print(f'cannot open a window: {error}', file=sys.stderr)
        return 1

    if args.self_test:
        # No main loop: fetch and apply on this thread, then report what was built.
        error = launcher.connect()
        if error:
            print(f'self-test: {error}', file=sys.stderr)
            return 1
        try:
            snapshot = read_snapshot(launcher.worker)
        except WorkerError as error:
            print(f'self-test: {error}', file=sys.stderr)
            return 1
        launcher.show(snapshot)
        print(f'self-test: {len(launcher.pages)} pages, {launcher.rows} rows, '
              f'{len(snapshot.get("fields") or [])} fields, {len(launcher.chips)} chips')
        launcher.quit()
        if not launcher.rows or not launcher.pages:
            print('self-test: the window has no settings; the worker did not answer',
                  file=sys.stderr)
            return 1
        return 0

    launcher.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
