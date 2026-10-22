<p align="center">
  <img src="https://img.shields.io/badge/language-Python%203.10-blue?style=flat-square&logo=python" alt="Language">
  <img src="https://img.shields.io/badge/mcu-STM32L151-blue?style=flat-square" alt="MCU">
  <img src="https://img.shields.io/badge/framework-Active%20Kernel%201.3-blue?style=flat-square" alt="Framework">
  <img src="https://img.shields.io/badge/platform-Linux-blue?style=flat-square&logo=linux" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License">
</p>

# ak-scope - Message trace and verify for AK firmware

Record, visualize and verify the task messages, signals and timers of firmware built on the
[AK Embedded Base Kit](https://github.com/the-ak-foundation/ak-base-kit-stm32l151).

<p align="center">
  <img src="docs/images/trace-dark.png" alt="Trace view: tasks, timeline and the detail of one handler run" width="900"/>
</p>
<p align="center"><strong><em>Figure 1:</em></strong> Trace view of the base kit application: a press of the MODE button and the runs it caused</p>

<hr>

## Documentation

<div align="center">

| File | Description |
| :---: | :--- |
| [README.md](README.md) | Overview, install, quick start and commands. |
| [docs/guide.md](docs/guide.md) | The page and its views, the desktop window, console rate, flashing, firmware without the trace. |
| [docs/verify-rules.md](docs/verify-rules.md) | The static and runtime rules, their limits, and the rules file. |
| [docs/trace-format.md](docs/trace-format.md) | The trace records on the wire and the `.akt` file format. |
| [akscope/firmware/akt/README.md](akscope/firmware/akt/README.md) | The firmware trace module: how it is linked in, cost, known issues. |
| [samples/README.md](samples/README.md) | The captures from a real kit that the tests use. |

</div>

## Introduction

An AK application is a set of tasks that talk through messages: a handler runs to completion
for each message, posts to other tasks and arms timers. When something is slow or out of order,
the console log says little about who posted what, how long a message waited, or which handler
held the CPU. ak-scope records that flow from a running kit and shows it on a timeline, the way
SEGGER SystemView does for an RTOS, and checks the firmware against the AK rules.

- **No change to the project:** `akscope build` links a small trace module into the firmware
  through a Makefile wrapper; the project folder is not written to.
- **Exact timing:** every post, handler run and timer, stamped with the CPU cycle counter and
  sent on the console UART (about 7 us per record at 32 MHz).
- **One page, two ways:** a local web page (`akscope serve`) or a desktop window (`akscope app`)
  with timeline, events, sequence flow, statistics and the console, dark and light.
- **Record and flash from the page:** live capture from the kit, or flash and record from boot
  with ak-flash, no ST-Link and no buttons.
- **Verify:** 13 static rules on the sources and 9 runtime rules on a trace, as text or JSON for
  CI.

Tested with three projects built from AK base kit 1.3: the base kit's own application and two
games.

<div align="center">

| Events | Verify |
| :---: | :---: |
| <img src="docs/images/events-dark.png" width="420"/> | <img src="docs/images/verify-dark.png" width="420"/> |
| **Stats** | **Light theme** |
| <img src="docs/images/stats-dark.png" width="420"/> | <img src="docs/images/trace-light.png" width="420"/> |

</div>

## Requirements

| | |
| --- | --- |
| Board | AK Embedded Base Kit (STM32L151), USB serial on the console UART |
| Firmware | a project built on the AK base kit, AK kernel 1.3 |
| Host | Linux (tested on Ubuntu 22.04), Python 3.10 or newer |
| Build and flash | `gcc-arm-none-eabi-10.3-2021.10`, [ak-flash](https://github.com/ak-embedded-software/ak-flash) on the `PATH` |
| Desktop window | `python3-gi`, `gir1.2-gtk-3.0`, `gir1.2-webkit2-4.0` (optional) |

## Install

```sh
git clone https://github.com/caotrongphuoc/ak-scope.git
cd ak-scope
python3 -m pip install --user -U pip        # the pip of Ubuntu 22.04 is too old
python3 -m pip install --user -e .
```

## Quick start

```sh
# 1. build the project with the trace linked in
akscope build ~/workspace/my-game --tools ~/workspace/tools --console-baud 460800

# 2. open the page, then Flash (writes the firmware, records from boot) or Record
akscope app --project ~/workspace/my-game --dev /dev/ttyUSB0

# 3. check the sources and a recorded trace against the AK rules
akscope verify ~/workspace/my-game --trace captures/run-20261022-101500.akt
```

`--tools` is the folder the AK Makefile calls `WORKSPACE_TOOLS_PATH`; it and the console rate
are remembered for the project. See [docs/guide.md](docs/guide.md) for the details.

## Commands

| Command | Does |
| --- | --- |
| `akscope build <project>` | Build the project with the trace linked in; objects go to `~/.cache/akscope/build/`. |
| `akscope flash --port <dev> <project>` | Flash with ak-flash, retrying and waking the bootloader as needed. |
| `akscope record --port <dev> -o run.akt [--flash <project>]` | Capture the UART to a `.akt` file; with `--flash`, from boot. |
| `akscope serve [run.akt] [--project <project>]` | The page in the browser, on `http://127.0.0.1:8730`. |
| `akscope app [run.akt] [--project <project>]` | The same page in a desktop window. |
| `akscope verify [<project>] [--trace run.akt]` | Static and runtime rule checks, `--format json` for scripts. |
| `akscope dump run.akt` | Print the records with task and signal names. |
| `akscope import-log`, `akscope stats` | Read the kernel task log of firmware without the trace. |
| `akscope install-desktop` | Add ak-scope to the desktop menu. |

`akscope <command> --help` lists every option.

## Verify

```sh
akscope verify ~/workspace/ak-base-kit-stm32l151 --trace samples/base-kit-boot-akt.bin
```

```
@ 130.985 ms: error R01 AC_TASK_DISPLAY_ID / AC_DISPLAY_INITIAL ran 154.7 ms (limit 50 ms), 1 run over 10 ms
@ 2130.991 ms: error R01 AC_TASK_DISPLAY_ID / AC_DISPLAY_SHOW_LOGO ran 338.6 ms (limit 50 ms), 1 run over 10 ms
@ 5031.042 ms: error R01 AC_TASK_FW_ID / FW_CHECKING_REQ ran 84.0 ms (limit 50 ms), 1 run over 10 ms
...
3 errors, 4 warnings
```

<div align="center">

| | Rules |
| :---: | :--- |
| Static (S01 - S13) | framework files unchanged, no blocking call in a handler, IO only in `io_cfg.c`, task and signal naming, task priorities, posted but not handled, dead signals, common message payload, globals shared between tasks, ISR rules, debug prints on a blocking console |
| Runtime (R01 - R09) | handler run time, queue wait, pool use, message storms, timer drift, unhandled messages, lost records, expected sequences, board restarts |

</div>

Code that comes unchanged from the base kit is left out when the kit is checked out next to the
project. Thresholds, ignores and expected sequences go in a rules file, see
[docs/verify-rules.md](docs/verify-rules.md).

## Development

```sh
python3 -m unittest
```

The tests use the captures in `samples/` and need no board. The page is plain JavaScript
modules under `akscope/web/`, no build step. Its components are compared with the Figma design
system pixel by pixel; the icons are drawn from the Figma file and not kept in the repository:

```sh
python3 tools/figma/export.py all "[WEB - 1] Design system VMS.fig"
AKSCOPE_FIG="[WEB - 1] Design system VMS.fig" python3 -m unittest tests.test_figma_gallery
```

## License

[MIT](LICENSE)
