# ak-scope guide

How to use ak-scope day to day: the page and its views, the desktop window, the console rate,
flashing, and what works without the trace. The README has the short version.

## Install notes

- **Ubuntu 22.04:** the system pip (22.0) cannot read this package and installs it as
  `UNKNOWN`. Update pip for your user first, then install ak-scope:

  ```sh
  python3 -m pip install --user -U pip
  python3 -m pip install --user -e .      # editable: the akscope command runs this checkout
  ```

- **Toolchain:** `--tools` is the folder the AK Makefile calls `WORKSPACE_TOOLS_PATH` (it holds
  `gcc-arm-none-eabi-10.3-2021.10-x86_64-linux`); `--gcc` points at the toolchain itself for
  projects that hard code `GCC_PATH`. Either one is remembered for the project.
- **Desktop window:** needs GTK 3 and WebKit2GTK 4.0 for Python
  (`sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0`).

Tested on Ubuntu 22.04 with the AK kit on a CH340 USB serial adapter. Windows is not tested yet.

## Building

`akscope build <project>` builds the project with its own Makefile through a wrapper. Objects go
to `~/.cache/akscope/build/`; no file in the project is written. It also writes the task and
signal names (`symbols.json`), which `record --flash` stores in the `.akt` file. Details and
limits: [akscope/firmware/akt/README.md](../akscope/firmware/akt/README.md).

### Console rate

The trace shares the console UART. For a game that sends many messages, build with
`--console-baud 460800`: at 115200 a busy game lost 43 % of its records while being played, at
460800 it lost 0.2 %. `record`, `serve` and `app` pick up the rate of the project. The
bootloader keeps 115200, so its banner reads as noise at the faster rate and is ignored.

### Flashing

ak-flash asks the running firmware for its bootloader at 115200 baud. A build with another
console rate does not understand that, so ak-scope first types the base kit shell command `fwu`
at the console rate, which restarts the kit into the bootloader. A firmware without that
command needs the boot mode buttons: hold MODE, press and release RESET, keep MODE held until
the LIFE LED blinks fast. The error message says so. A port that another program holds is not
flashed.

## The page

`akscope serve` opens it in the browser on `http://127.0.0.1:8730`, `akscope app` in its own
window. The top bar holds the trace facts, the serial port, Record, Flash and Open; the side bar
switches between the views. The look follows the design system file in Figma, dark and light.

| View | Shows |
| --- | --- |
| Trace | The tasks, the timeline and the detail of the selected run. The timeline has one row per task, higher priority first; a block is one handler run, an arrow goes from a post to the run it caused, a tick marks a run started by a timer, a red line a board restart. Wheel to zoom, drag to pan, shift and drag to measure, `f` to fit. |
| Detail | For the selected run: run time, the exact queue wait, who posted it and what it posted, each a link to follow the chain. With the base kit's debug prints on (`APP_DBG_SIG_EN`), the console lines the run printed and how long sending them held the handler. |
| Events | Every record, with a filter; the filtered rows export as CSV or JSON. |
| Flow | The messages in the timeline view as a sequence diagram. |
| Stats | Run time per task and per signal, CPU share, longest handler. |
| Verify | The rule checks for this trace, and for the project sources when `--project` is given. A runtime finding opens its run. |
| Console | The text the firmware printed. |

Record writes the live capture to `captures/` in the current folder (`--captures` for another)
and the view follows the newest records.

A decoded capture is kept in `~/.cache/akscope/decoded/` (the last 20), so a large trace opens
at once the second time. The first time, 100 000 handler runs take about 12 s.

### Desktop window

`akscope app` runs the server in its own process on a free port. It asks where to save an
export, keeps its size per project in `~/.config/akscope/window.json`, and raises the open
window when started again for the same project. `akscope install-desktop` writes a menu entry
to `~/.local/share/applications/`.

### Security

The server only listens on 127.0.0.1, only opens serial ports the machine lists, only flashes
the `--project` firmware, and refuses requests that do not come from its own page.

## Firmware without the trace

Firmware that was not built with `akscope build` sends no message records; ak-scope only shows
its console text. If it was built with the kernel task log
(`LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE`), `akscope import-log` and `akscope stats`
give run and wait times per task from it, in milliseconds and without the sender.
