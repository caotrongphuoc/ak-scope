# ak-scope
Record, visualize and verify task messages, signals and timers in AK event-driven firmware.

ak-scope works with projects built on the AK base kit (AK kernel 1.3, STM32L151). It links a
small trace module into the firmware without changing the project, flashes it with
`ak-flash`, and reads the trace from the console UART.

## Requirements

- Python 3.10 or newer, `pip install .`
- the ARM toolchain the AK Makefile uses (`gcc-arm-none-eabi-10.3-2021.10`)
- [ak-flash](https://github.com/ak-embedded-software/ak-flash) for flashing over UART

## Usage

```sh
# build the game with the trace linked in; the project folder is not written to
akscope build ~/workspace/github/11m-game --tools ~/workspace/tools

# flash it and record from boot until Ctrl-C
akscope record --port /dev/ttyUSB0 --flash ~/workspace/github/11m-game -o run.akt

# print the records, with task and signal names read from the project sources
akscope dump run.akt

# or look at it in the browser: timeline, events, stats
akscope serve run.akt
```

`akscope serve` listens on `http://127.0.0.1:8730` only. A trace can also be opened from the
page (Open, or drop the file on it).

To record and flash from the page, start it with the built project:

```sh
akscope serve --project ~/workspace/github/11m-game --dev /dev/ttyUSB0
```

Record captures the port into `captures/run-<date>-<time>.akt` and shows the records as they
arrive (Follow keeps the newest ones in view). Flash stops the capture, writes the firmware made
by `akscope build` with `ak-flash`, retrying when the trace of the running firmware breaks the
handshake, and starts a new capture so the boot is recorded. The page can only open serial
ports the machine lists and only flash the `--project` firmware.

```
      31.141  EXEC         task AC_TASK_UART_IF_ID  sig AC_UART_IF_INIT  pure  exe 0.071 ms  wait 0 ms
      31.144  POST         AC_TASK_UART_IF_ID -> AC_LINK_ID  sig AC_LINK_INIT  pure  len 0
      31.309  EXEC         task AC_LINK_ID  sig AC_LINK_INIT  pure  exe 0.073 ms  wait 0 ms
     130.921  EXEC         task AC_TASK_DISPLAY_ID  sig AC_DISPLAY_INITIAL  pure  exe 139.099 ms  wait 0 ms
    2168.281  TIMER_SET    -> AC_TASK_DISPLAY_ID  sig AC_DISPLAY_RENDER_SCREEN  3 ms  one shot
```

Firmware that was built without the trace sends no message records. For it, `akscope record`
still captures the console text, and `akscope stats` reads the kernel task log if the
firmware was built with `LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE`.

## Documents

- [docs/trace-format.md](docs/trace-format.md): bytes on the wire
- [firmware/akt/README.md](firmware/akt/README.md): how the trace is linked in, limits, known issues
- [samples/](samples/): captures from a real kit, used by the tests

## Tests

```sh
python3 -m unittest
```
