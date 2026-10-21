"""ak-scope in a desktop window: GTK 3 and WebKit2GTK 4.0 around the page of akscope serve.

The HTTP server of serve runs in this process, on a free port of 127.0.0.1, and the window
shows it. What a browser would do differently is done here:

- a file the page saves (Export CSV, JSON) asks where to put it in a GTK save dialog
- links to other sites open in the default browser, the window only shows ak-scope
- size, position and maximized state are kept per project in ~/.config/akscope/window.json
- one window per project: starting it again for the same project raises that window
- the page's localStorage (theme, chosen port, last page) lives in ~/.local/share/akscope/webkit

System packages on Ubuntu 22.04: python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0
"""

import hashlib
import json
import os
import pathlib
import signal
import sys
import threading
import urllib.parse

try:
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2
except (ImportError, ValueError) as e:
    raise ImportError("the desktop window needs GTK 3 and WebKit2GTK 4.0 for Python "
                      "(Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0): %s" % e)

APP_ID = "io.github.caotrongphuoc.akscope"
DEFAULT_SIZE = (1440, 900)


def _dir(env, fallback, *parts):
    base = pathlib.Path(os.environ.get(env) or pathlib.Path.home() / fallback)
    return base.joinpath("akscope", *parts)


STATE_FILE = _dir("XDG_CONFIG_HOME", ".config", "window.json")


def app_id(project):
    """the GApplication id; a project gets its own, so each project has one window"""
    if not project:
        return APP_ID
    key = hashlib.sha1(str(pathlib.Path(project).expanduser().resolve()).encode()).hexdigest()[:12]
    return "%s.p%s" % (APP_ID, key)


def load_state(key):
    try:
        return json.loads(STATE_FILE.read_text()).get(key) or {}
    except (OSError, ValueError):
        return {}


def save_state(key, value):
    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data[key] = value
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=1) + "\n")
    except OSError:
        pass


class Window(Gtk.ApplicationWindow):
    def __init__(self, application, url, title, key, inspector):
        super().__init__(application=application, title=title)
        self.url = url
        self.key = key
        st = load_state(key)
        self.set_default_size(*st.get("size", DEFAULT_SIZE))
        if "position" in st:
            self.move(*st["position"])
        if st.get("maximized"):
            self.maximize()
        self.maximized = bool(st.get("maximized"))
        self.connect("window-state-event", self.on_state)
        self.connect("delete-event", self.on_delete)

        manager = WebKit2.WebsiteDataManager(base_data_directory=str(_dir("XDG_DATA_HOME", ".local/share", "webkit")),
                                             base_cache_directory=str(_dir("XDG_CACHE_HOME", ".cache", "webkit")))
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
        context.connect("download-started", self.on_download)
        self.view = WebKit2.WebView.new_with_context(context)
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(inspector)
        settings.set_javascript_can_access_clipboard(True)
        if not inspector:
            self.view.connect("context-menu", lambda *a: True)
        self.view.connect("decide-policy", self.on_policy)
        self.add(self.view)
        self.view.load_uri(url)

    def on_state(self, _win, event):
        self.maximized = bool(event.new_window_state & Gdk.WindowState.MAXIMIZED)
        return False

    def on_delete(self, *_):
        self.save()
        return False

    def save(self):
        st = {"maximized": self.maximized}
        if not self.maximized:
            st["size"] = list(self.get_size())
            st["position"] = list(self.get_position())
        else:
            old = load_state(self.key)
            for k in ("size", "position"):
                if k in old:
                    st[k] = old[k]
        save_state(self.key, st)

    def own(self, uri):
        a, b = urllib.parse.urlsplit(uri), urllib.parse.urlsplit(self.url)
        return a.scheme in ("blob", "data", "about") or (a.scheme, a.netloc) == (b.scheme, b.netloc)

    def on_policy(self, _view, decision, kind):
        if kind not in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if self.own(uri) and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        decision.ignore()
        if not self.own(uri):
            Gtk.show_uri_on_window(self, uri, Gtk.get_current_event_time())
        return True

    def on_download(self, _context, download):
        download.connect("decide-destination", self.on_destination)

    def on_destination(self, download, suggested):
        dialog = Gtk.FileChooserNative.new("Save", self, Gtk.FileChooserAction.SAVE, "_Save", "_Cancel")
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(suggested or "akscope-export")
        last = load_state("downloads").get("folder")
        if last and os.path.isdir(last):
            dialog.set_current_folder(last)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = dialog.get_filename()
            save_state("downloads", {"folder": os.path.dirname(path)})
            download.set_destination(GLib.filename_to_uri(path))
        else:
            download.cancel()
        dialog.destroy()
        return True


def run(httpd, live, args):
    """show the window until it is closed; httpd is served from a thread meanwhile"""
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    project = getattr(args, "project", None)
    title = "ak-scope"
    if project:
        title += " - " + pathlib.Path(project).expanduser().resolve().name
    elif getattr(args, "file", None):
        title += " - " + pathlib.Path(args.file).name
    key = str(pathlib.Path(project).expanduser().resolve()) if project else ""

    # the window class the menu entry (StartupWMClass=akscope) matches
    GLib.set_prgname("akscope")
    application = Gtk.Application(application_id=app_id(project), flags=Gio.ApplicationFlags.FLAGS_NONE)

    def activate(app):
        win = app.get_active_window()
        if win is None:
            win = Window(app, url, title, key, getattr(args, "inspector", False))
            win.show_all()
        win.present()

    def quit_(*_):
        win = application.get_active_window()
        if win is not None:
            win.save()
        application.quit()
        return GLib.SOURCE_REMOVE

    application.connect("activate", activate)
    # Ctrl-C in the terminal, or a kill, closes the window as its close button does
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, quit_)
    try:
        code = application.run([sys.argv[0]])
    finally:
        httpd.shutdown()
    return code
