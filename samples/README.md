# samples

Raw captures from real boards, kept byte for byte (CR/LF and shell echo included) so the
parsers are tested against what the UART actually sends.

| File | Firmware | How it was captured |
| --- | --- | --- |
| `11m-game-boot-console.log` | same build as below | 120 s on the kit UART, starting just before a board reset: boot banner, task start-up, `-LINK_PRINT-` text mixed with kernel log lines, then the game idle. Display task 9 takes 139 ms on its first signal. |
| `11m-game-boot-akt.bin` | `11m-game` c6c9b29 with `firmware/akt` added, `make AKT_TRACE=1` | raw bytes from the UART for 20 s right after `ak-flash` finished: boot text, then 68 akt frames (INFO, init posts and timers, display init 139 ms, POOL every second). No `.akt` header. |
| `11m-game-symbols.json` | `11m-game` c6c9b29 | written by `akscope build`, local `project` path removed. Names for the records in `11m-game-boot-akt.bin`. |
| `11m-game-idle-console.log` | `11m-game` c6c9b29, built with `LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE` | 115200 8N1 on the kit UART, game idle for 15 s, then shell commands `ver`, `fatal l`, `fatal m`. This build disables all shell prints, so only the kernel log lines show up. |
