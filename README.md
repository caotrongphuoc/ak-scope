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

# print the records
akscope dump run.akt
```

```
      31.143  EXEC         task 6  sig 10  pure  exe 0.071 ms  wait 0 ms
      31.146  POST         6 -> 17  sig 10  pure  len 0
      31.310  EXEC         task 17  sig 10  pure  exe 0.073 ms  wait 0 ms
     130.909  EXEC         task 9  sig 10  pure  exe 139.106 ms  wait 0 ms
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
