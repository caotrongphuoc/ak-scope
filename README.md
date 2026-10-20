# ak-scope
Record, visualize and verify task messages, signals and timers in AK event-driven firmware.

ak-scope works with projects built on the
[AK base kit](https://github.com/the-ak-foundation/ak-base-kit-stm32l151) (AK kernel 1.3,
STM32L151). It links a small trace module into the firmware without changing the project,
flashes it with `ak-flash`, reads the trace from the console UART, shows it in a local web page,
and checks the firmware against the AK rules.

## Install

Needs Python 3.10 or newer.

```sh
pip install .
```

For building and flashing the firmware you also need what the AK base kit uses:

- the ARM toolchain `gcc-arm-none-eabi-10.3-2021.10`
- [ak-flash](https://github.com/ak-embedded-software/ak-flash) on the `PATH`

Tested on Ubuntu 22.04 with the AK kit on a CH340 USB serial adapter. Windows is not tested yet.

## Quick start

```sh
# 1. build the game with the trace linked in; the project folder is not written to
akscope build ~/workspace/github/11m-game --tools ~/workspace/tools

# 2. open the page, then Flash (writes the firmware, records from boot) or Record
akscope serve --project ~/workspace/github/11m-game --dev /dev/ttyUSB0

# 3. check the sources and a recorded trace against the AK rules
akscope verify ~/workspace/github/11m-game --trace captures/run-20261020-161828.akt
```

`--tools` is the folder the AK Makefile calls `WORKSPACE_TOOLS_PATH` (it holds
`gcc-arm-none-eabi-10.3-2021.10-x86_64-linux`). It is remembered for the project, so later
commands do not need it.

## Commands

| Command | Does |
| --- | --- |
| `akscope build <project>` | builds the project with the trace linked in, objects in `~/.cache/akscope/build/`; also writes the task and signal names |
| `akscope flash --port <dev> <project or .bin>` | flashes with `ak-flash`, retrying when the running trace breaks the handshake |
| `akscope record --port <dev> -o run.akt [--flash <project>]` | captures the UART to a `.akt` file; with `--flash`, flashes first and records from boot |
| `akscope dump run.akt` | prints the records with task and signal names |
| `akscope serve [run.akt] [--project <project>] [--dev <dev>]` | web page on `http://127.0.0.1:8730` |
| `akscope verify [<project>] [--trace run.akt]` | static and runtime rule checks, text or `--format json` |
| `akscope import-log console.txt`, `akscope stats console.txt` | read the kernel's own task log (`LOG_AK_KERNEL_ENABLE`) from firmware without the trace |

`akscope <command> --help` lists every option.

## The web page

```
akscope serve --project ~/workspace/github/11m-game --dev /dev/ttyUSB0
```

- **Timeline**: one row per task, higher priority first. A block is one handler run; an arrow
  goes from the post to the run it caused; a tick marks a run started by a timer. Wheel to
  zoom, drag to pan, shift and drag to measure, `f` to fit.
- **Detail**: for the selected run, its run time, its exact queue wait, who posted it, and what
  it posted, each a link to follow the chain.
- **Events**: every record, with a filter; export the filtered rows as CSV or JSON.
- **Flow**: the messages in the timeline view as a sequence diagram.
- **Stats**: run time per task and per signal, CPU share, longest handler.
- **Verify**: the rule checks for this trace, and for the project sources when `--project` is
  given. Click a runtime finding to jump to the run.
- **Record / Flash**: live capture into `captures/`, the view follows the newest records.

The server only listens on 127.0.0.1, only opens serial ports the machine lists, only flashes
the `--project` firmware, and refuses requests that do not come from its own page.

## Verify

```sh
akscope verify ~/workspace/github/zomwar-game
```

```
application/sources/ak/inc/ak.h:43: error S01 framework file changed from the AK base kit
sources/driver/buzzer/buzzer.c:189: error S03 register access CR1 outside io_cfg.c, move it to platform/stm32l/io_cfg.c and inject it
sources/app/game/game_zomwar/zw_game_border.cpp:3: warning S10 zw_game_score is written by 3 tasks: ...
2 errors, 4 warnings
```

Twelve static rules (blocking calls in handlers, IO outside `io_cfg.c`, signals posted but not
handled, globals shared between tasks, ISR rules...) and eight runtime rules (handler run
time, queue wait, pool use, message storms, timer drift, expected sequences...). Code that
comes unchanged from the base kit is left out when the kit is checked out next to the project.
See [docs/verify-rules.md](docs/verify-rules.md) for every rule, its limits, and the rules
file (thresholds, ignores, expected sequences).

## Firmware without the trace

Firmware that was not built with `akscope build` sends no message records; ak-scope can only
show its console text. If it was built with the kernel task log
(`LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE`), `akscope stats` gives run and wait
times per task from it, in milliseconds and without the sender.

## Documents

- [docs/trace-format.md](docs/trace-format.md): the bytes on the wire and in `.akt` files
- [docs/verify-rules.md](docs/verify-rules.md): the static and runtime rules
- [akscope/firmware/akt/README.md](akscope/firmware/akt/README.md): how the trace is linked in,
  adding it by hand, known issues
- [samples/](samples/): captures from a real kit, used by the tests

## Development

```sh
python3 -m unittest
```

The tests use the captures in `samples/` and need no board. The web page is plain JavaScript
modules under `akscope/web/`, no build step.
