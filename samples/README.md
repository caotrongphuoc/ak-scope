# samples

Raw captures from real boards, kept byte for byte (CR/LF and shell echo included) so the
parsers are tested against what the UART actually sends.

| File | Firmware | How it was captured |
| --- | --- | --- |
| `11m-game-idle-console.log` | `11m-game` c6c9b29, built with `LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE` | 115200 8N1 on the kit UART, game idle for 15 s, then shell commands `ver`, `fatal l`, `fatal m`. This build disables all shell prints, so only the kernel log lines show up. |
