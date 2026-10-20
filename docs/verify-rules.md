# akscope verify rules

`akscope verify` checks that firmware built on the AK base kit follows the event-driven rules
of the AK kernel. Static rules (S) read the project sources; runtime rules (R) read a trace
recorded with `akscope record`.

```sh
akscope verify ~/workspace/github/11m-game                      # static rules only
akscope verify --trace run.akt                                  # runtime rules only
akscope verify ~/workspace/github/11m-game --trace run.akt      # both, and R06 can run
```

Exit code: 0 when there are no errors, 1 when there are errors (or warnings with `--strict`),
2 when the check could not run. `--format json` gives the same findings for scripts and CI.

## How the static rules read the code

The sources are run through the project's own compiler with `-E` and the project's flags
(the same way `akscope build` gets them), so `#if`, `#ifdef` and macros resolve exactly as in
the firmware that gets built. Every finding points back to the original `file:line`.

From that output akscope builds a model: the functions defined in `sources/app`, `driver`,
`networks`, `common`, `sys` and `platform/stm32l/system.c`, which function calls which, the
task table and polling table from `task_list.cpp`, and the interrupt vector table from
`system.c`.

What runs inside a task: the task handler, every function it calls, and every message function
(`f(ak_msg_t*)`) whose name appears in it, such as the screen handlers handed to
`SCREEN_TRAN()`. Calls through function pointers are not followed beyond that.

**Base kit code is left out.** When the AK base kit is checked out next to the project
(`../ak-base-kit-stm32l151`) or given with `--base`, findings on lines that come unchanged from
the base kit are not reported: that code is the kit's, not the project's. A file the project
changed is diffed against the kit's copy and only the changed lines count. `--all` reports
everything.

## Static rules

| Rule | Severity | Checks | Why |
| --- | --- | --- | --- |
| S01 | error | files under `ak/`, `sys/`, `common/`, `networks/`, `platform/`, `libraries/` and `boot/` are the same as in the base kit. Allowed: `platform/stm32l/io_cfg.c/.h` and `ak/ak.cfg.mk` | framework code is shared by every task; a change there breaks guarantees the rest relies on |
| S02 | error / warning | no `sys_ctrl_delay_*`, `delay()` and similar (error), no `while (cond);` busy wait (warning) in code that runs in a task | a handler must run to completion; while it waits nothing else runs |
| S03 | error | no StdPeriph calls (`GPIO_*`, `TIM_*`...), Arduino pin calls or register access in `app/` and `driver/` | all board IO lives in `io_cfg.c` and is injected, so a port to another MCU only touches that file |
| S04 | warning | functions named `task_*` are tasks or polling tasks in `task_list.h` | `task_` means one thing: an AK task, which keeps the task list and logs unambiguous |
| S05 | warning | signals used by the app are at least `AK_USER_DEFINE_SIG` (10) | values below 10 are the kernel's |
| S06 | error | task priorities are `TASK_PRI_LEVEL_1` to `TASK_PRI_LEVEL_7` | level 0 is reserved |
| S07 | error | a signal posted to a task (or armed with `timer_set`) has a `case` in that task | an unhandled message is silently dropped |
| S08 | warning | every signal declared next to used signals is posted, armed or handled somewhere | dead signals hide removed features and confuse traces |
| S09 | error | `task_post_common_msg()` with a literal length over 64 | `AK_COMMON_MSG_DATA_SIZE` is 64; more is a FATAL at run time |
| S10 | warning | a file scope variable written from more than one task | tasks share data by messages; shared globals make the order of events matter |
| S11 | error | an ISR that reaches the kernel calls `task_entry_interrupt()` and `task_exit_interrupt()` | the kernel needs them to post safely from interrupt context |
| S12 | error | no `get_dynamic_msg()` or `task_post_dynamic_msg()` from an ISR | dynamic messages allocate from the heap |

Limits worth knowing:

- S07 and R06 need the handler to switch on `msg->sig`. Handlers that run code for every
  message (statements after the switch, a `default:` that does work) count as handling all
  signals. FSM and TSM style handlers (`fsm_dispatch`, `tsm_dispatch`) are skipped.
- S09 only sees literal lengths; `sizeof(...)` is not evaluated. The `len` field of every
  `POST` record in a trace has the real size.
- S10 finds writes by name (`x =`, `x++`, `x.f =`, `x[i] =`...). Writes through pointers or
  `memcpy()` are not seen.
- S02 busy waits include short hardware waits such as SPI flags. They are warnings for that
  reason.

## Runtime rules

Thresholds come from `akscope/rules/default.yml`; copy it, change it and pass it with
`--rules`. Findings are grouped per task and signal, with the count and the worst case.

| Rule | Severity | Checks | Default |
| --- | --- | --- | --- |
| R01 | warning / error | handler run time | warning from 10 ms, error from 50 ms |
| R02 | warning | time a message waited in the queue (exact when its POST is in the trace, else the kernel's ms figure) | 100 ms |
| R03 | warning / error | highest pool use in POOL records against the pool sizes in INFO | warning from 80 %, error when full |
| R04 | warning | posts of the same source, task and signal inside any 1 s window | 100 |
| R05 | warning | a periodic timer firing further than this from its period | 10 % |
| R06 | warning | a message delivered to a task that has no case for it; needs the project sources | |
| R07 | error / warning | records dropped on the board (error), corrupt frames (warning) | |
| R08 | error | expected sequences from the rules file | |

### Ignoring findings

```yaml
ignore:
  - rule: R01
    match: AC_DISPLAY_INITIAL     # the display init at boot is allowed to be slow
  - S04                           # a whole rule
```

`match` is looked for in the message and in the location.

### Expected sequences (R08)

```yaml
expect:
  - name: match starts after the logo
    after: {task: AC_TASK_DISPLAY_ID, sig: AC_DISPLAY_SHOW_LOGO}
    then: {task: EM_GAME_MATCH_ID, sig: EM_GAME_MATCH_INIT}
    within: 100ms
```

For every run of `after`, a run of `then` must start within `within`. `task` and `sig` take
the names from the project or plain numbers. A missing `after` in the trace is a warning.

## Results on the game projects

Measured on 2026-10-20 with the base kit next to the projects:

- `11m-game`: no errors in its own code; 7 helper functions named `task_display_*` (S04),
  `AC_DISPLAY_RENDER_SCREEN = 0` (S05), two unused Christmas signals (S08). On a boot trace:
  `AC_DISPLAY_INITIAL` 139 ms and `FW_CHECKING_REQ` 82.7 ms (R01 errors).
- `zomwar-game`: `ZW_GAME_DEFINE_SIG` added to the kernel's `ak.h` (S01), a new register access
  in the buzzer driver (S03), `zw_game_score` written by 3 tasks and `gunner` by 2 (S10).
