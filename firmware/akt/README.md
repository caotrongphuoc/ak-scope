# akt trace for AK firmware

`akt_trace` records message posts, handler runs and timers in an AK 1.3 project and sends
them on the console UART for `akscope`. It lives in the application layer and does not
change the kernel. Record format: [docs/trace-format.md](../../docs/trace-format.md).

Measured on `11m-game` (STM32L151, 32 MHz):

| | trace off | trace on |
| --- | --- | --- |
| Flash | 50728 B | 52856 B (+2128) |
| RAM | 13640 B | 14208 B (+568, of which 512 is the ring) |

With the trace off (`AKT_TRACE=0`, the default) the build is the same as without the module.

## Without changing the project: `akscope build`

This is the normal way. The project is built by its own Makefile with a wrapper around it;
objects go to `~/.cache/akscope/build/`, and no file in the project is written.

```sh
akscope build ~/workspace/github/11m-game --tools ~/workspace/tools
akscope record --port /dev/ttyUSB0 --flash ~/workspace/github/11m-game -o run.akt
akscope dump run.akt
```

What the wrapper does:

- reads which handlers `app_task_table` and `app_task_polling_table` point to, from the
  relocations of `task_list.o` (works for C and C++ handlers);
- generates `akt_auto.c` with a `__wrap_` function for each handler, a wrap of the first
  polling function that also calls `akt_trace_poll()`, and a wrap of `task_create()` that turns
  on the DWT cycle counter and calls `akt_trace_init()`;
- links with `--wrap` for those symbols and for `task_post_*`, `timer_set`,
  `timer_remove_attr`.

Options: `--tools` is the folder the AK Makefile calls `WORKSPACE_TOOLS_PATH`; `--gcc` sets
`GCC_PATH` directly, for Makefiles that hard-code it; `--ring` sets `AKT_RING_SIZE`
(default 512 bytes, use 256 when RAM is short).

Limits of this mode:

- a handler defined inside `task_list.cpp` itself cannot be wrapped and is reported;
- the app version in `INFO` comes from `APP_VER` in `app.h`; it is 0.0.0.0 when the project
  has no `sources/app/app.cpp` or no such macro;
- tested on `11m-game` and `zomwar-game` built from AK base kit 1.3.

`akscope build` also writes `symbols.json` next to the objects: task names from the task table,
and signal names per task, found from `case` labels in each handler's `switch (msg->sig)` and
from `task_post_*_msg()` / `timer_set()` calls. `akscope record --flash <project>` stores it in
the `.akt` file so `akscope dump` prints names. A name followed by `?` was matched only by value
and may be wrong.

Measured with `zomwar-game`: RAM 15024 of 16384 bytes with the default ring, 14768 with
`--ring 256`.

## Add it by hand

Only needed to keep the trace inside the project sources, for example to ship it in a
debug build. Paths are relative to `application/`.

1. Copy this folder to `sources/app/akt/` and include it from `sources/app/Makefile.mk`:

   ```make
   include sources/app/akt/Makefile.mk
   ```

2. Pass the wrap flags to the linker. In `application/Makefile`, add `$(AKT_LDFLAGS)` at the
   end of `LDFLAGS`. This is needed because `LDFLAGS` is assigned with `=` after the
   `Makefile.mk` includes, so the module cannot add to it by itself:

   ```make
   LDFLAGS	=	-Map=$(OBJECTS_DIR)/$(PROJECT).map	\
   		--gc-sections	\
   		$(LIB_PATH)	\
   		$(LIBC) $(LIBM) $(LIBSTDCPP_NANO) $(LIBGCC) $(LIBGCOV) $(LIBFPU) $(LIBRDPMON)	\
   		$(AKT_LDFLAGS)
   ```

3. Add the cycle counter to the board IO layer. `io_cfg.c` is the only place for register
   code in an AK project.

   `sources/platform/stm32l/io_cfg.h`:

   ```c
   /* DWT cycle counter, used by akt trace */
   extern void io_cycle_counter_init();
   extern uint32_t io_cycle_counter_get();
   ```

   `sources/platform/stm32l/io_cfg.c`:

   ```c
   void io_cycle_counter_init() {
   	CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
   	DWT->CYCCNT = 0;
   	DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
   }

   uint32_t io_cycle_counter_get() {
   	return DWT->CYCCNT;
   }
   ```

4. Wrap the task handlers in `sources/app/task_list.cpp`. Add one `AKT_WRAP()` line per
   handler before the table and use `AKT_TASK()` in the table. Leave `task_timer_tick` as it
   is: it runs every tick and its expiry posts are already seen as `EXEC` records.

   ```cpp
   #include "akt_trace.h"

   AKT_WRAP(task_system)
   AKT_WRAP(task_life)
   AKT_WRAP(em_game_match_handle)

   const task_t app_task_table[] = {
       {TASK_TIMER_TICK_ID, TASK_PRI_LEVEL_7, task_timer_tick},
       {AC_TASK_SYSTEM_ID, TASK_PRI_LEVEL_2, AKT_TASK(task_system)},
       {AC_TASK_LIFE_ID, TASK_PRI_LEVEL_6, AKT_TASK(task_life)},
       {EM_GAME_MATCH_ID, TASK_PRI_LEVEL_4, AKT_TASK(em_game_match_handle)},
       ...
   ```

   With the trace off, `AKT_WRAP()` is empty and `AKT_TASK(fn)` is `fn`.

5. Add the polling task. In `sources/app/task_list.h`, polling ids enum:

   ```c
   	AC_TASK_POLLING_CONSOLE_ID,
   #if defined(AKT_TRACE_EN)
   	AKT_TASK_POLLING_ID,
   #endif
   ```

   In `sources/app/task_list.cpp`, `app_task_polling_table`:

   ```cpp
       {AC_TASK_POLLING_CONSOLE_ID, AK_ENABLE, task_polling_console},
   #if defined(AKT_TRACE_EN)
       {AKT_TASK_POLLING_ID, AK_ENABLE, akt_trace_poll},
   #endif
   ```

6. Start it in `main_app()` (`sources/app/app.cpp`), right after `task_polling_create()`, so the
   posts and timers made during init are recorded:

   ```cpp
   #include "akt_trace.h"
   ...
   	task_polling_create((task_polling_t*)app_task_polling_table);
   	EXIT_CRITICAL();

   #if defined(AKT_TRACE_EN)
   	io_cycle_counter_init();
   	akt_port_t akt_port = {io_cycle_counter_get, SystemCoreClock, AK_TASK_EOT_ID, APP_VER, 0};
   	akt_trace_init(&akt_port);
   #endif
   ```

   `akt_trace_init()` keeps a copy of `akt_port`, so a local variable is fine.

## Build, flash, record

```sh
cd application
make clean && make AKT_TRACE=1
make flash dev=/dev/ttyUSB0
akscope record --port /dev/ttyUSB0 -o run.akt
akscope dump run.akt
```

The kernel console log must stay off (`LOG_AK_KERNEL_ENABLE = -UAK_TASK_LOG_CONSOLE_ENABLE`,
the default). `akt_cfg.h` stops the build if both are on.

Use `make clean` when switching `AKT_TRACE`: the object files do not depend on the flag.

## Marking your own events

```c
AKT_MARK(1, score);      /* USER record: id 1, value score */
```

`AKT_MARK()` costs nothing with the trace off.

## Known issues

- **`ak-flash` can fail while the trace is running.** Trace frames reach the PC in the middle
  of the `ak-flash` handshake and the first attempt may end with `checksum incorrectly`.
  After that the board waits in the bootloader; a retry right away answers `handshake
  faulted`, one 5 to 10 s later goes through. `akscope flash` retries up to 4 times, 5 s apart.
- **Console in blocking mode.** `main_app()` only switches the console to interrupt driven
  output (`sys_ctrl_shell_sw_to_nonblock()`) when `IF_LINK_UART_EN` is not set. With the
  link UART enabled, as in `11m-game`, each frame blocks the polling task for about 1.5 ms at
  115200 baud. This delays the next dispatch but is not counted in any handler's run time.
- **`INFO` arrives late if the PC attaches after boot.** It is sent at start-up and then after
  every 64 `POOL` records (64 s). Reset the board after starting `akscope record` to get it at
  once; without it `akscope dump` assumes 32 MHz.
- Messages posted inside the kernel (timer expiry) have no `POST` record. They appear as an
  `EXEC` without a matching post.
