# samples

Raw captures from the AK kit, kept byte for byte (CR/LF and shell echo included) so the
parsers are tested against what the UART actually sends. The firmware is the application of
[ak-base-kit-stm32l151](https://github.com/the-ak-foundation/ak-base-kit-stm32l151) 57e480e,
unchanged, on the kit's STM32L151 at 32 MHz.

| File | Firmware | How it was captured |
| --- | --- | --- |
| `base-kit-boot-akt.bin` | built by `akscope build --console-baud 460800` | raw bytes from the UART for 30 s right after flashing: the bootloader banner (115200, so noise at 460800), then 153 akt frames: INFO, init posts and timers, `AC_DISPLAY_INITIAL` 154.7 ms, the logo 338.6 ms, the idle screen animation, and one press of the MODE button (a post from the interrupt). No `.akt` header. |
| `base-kit-symbols.json` | same build | written by `akscope build`, local `project` path removed. Names for the records in `base-kit-boot-akt.bin`. |
| `base-kit-boot-console.log` | built with `make LOG_AK_KERNEL_ENABLE=-DAK_TASK_LOG_CONSOLE_ENABLE`, no trace, 115200 | 30 s from the end of the flash: boot banner, task start-up, `-LINK_PRINT-` and `-SIG->` text between 71 kernel log lines. |
| `base-kit-idle-console.log` | same build | the next 15 s on the idle screen, then the shell commands `ver`, `fatal l`, `fatal m`: 168 kernel log lines and the 18 messages `fatal m` lists. |
