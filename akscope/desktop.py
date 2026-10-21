"""akscope install-desktop: a menu entry for the desktop window (akscope app).

Writes, for the current user only:
  ~/.local/share/applications/akscope.desktop   (akscope-<project>.desktop with --project)
  ~/.local/share/icons/hicolor/scalable/apps/akscope.svg

The entry starts this Python with -m akscope, so it works from a virtualenv or a source tree
as well as from an installed package. The icon is a plain placeholder until ak-scope has a logo.
"""

import os
import pathlib
import shlex
import shutil
import subprocess
import sys

ICON = """<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
<rect x="4" y="4" width="56" height="56" rx="14" fill="#131313"/>
<rect x="14" y="20" width="16" height="8" rx="2" fill="#2d7dff"/>
<rect x="34" y="20" width="10" height="8" rx="2" fill="#2d7dff"/>
<rect x="22" y="36" width="20" height="8" rx="2" fill="#029d3b"/>
<rect x="46" y="36" width="6" height="8" rx="2" fill="#029d3b"/>
</svg>
"""


def _data_home():
    return pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share")


def desktop_entry(project=None, python=None):
    cmd = [python or sys.executable, "-m", "akscope", "app"]
    name = "ak-scope"
    if project:
        project = str(pathlib.Path(project).expanduser().resolve())
        cmd += ["--project", project]
        name += " (%s)" % pathlib.Path(project).name
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=%s" % name,
        "Comment=Record, view and verify the message flow of AK firmware",
        "Exec=%s" % " ".join(shlex.quote(c) for c in cmd),
        "Icon=akscope",
        "Terminal=false",
        "Categories=Development;Electronics;",
        "StartupWMClass=akscope",
        "",
    ])


def install(project=None):
    """write the entry and the icon, return their paths"""
    data = _data_home()
    apps = data / "applications"
    icons = data / "icons" / "hicolor" / "scalable" / "apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    fname = "akscope.desktop"
    if project:
        fname = "akscope-%s.desktop" % pathlib.Path(project).expanduser().resolve().name
    entry = apps / fname
    entry.write_text(desktop_entry(project))
    icon = icons / "akscope.svg"
    icon.write_text(ICON)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return [entry, icon]
