# akt trace format, version 1

This is the byte format that `akt_trace` (firmware) sends and `akscope` (host) reads.
All multi-byte fields are little endian.

## Transport

Trace frames share the console UART with normal text (shell echo, `APP_DBG`, boot banner).
The firmware writes frames through `xfunc_output()`, the console output function of
`xprintf`, so they go into the same TX ring (`ring_buffer_char_shell_send`, 512 bytes, drained
by the USART TXE interrupt) as every other console byte. No UART register is touched by
`akt_trace`. `xputc()` is not used because it turns every 0x0A into 0x0D 0x0A, and frame
bytes can be 0x0A.

Recording an event only copies the raw record into the akt ring (512 bytes by default) with
interrupts off. The polling task takes records out, adds the CRC and the COBS encoding, and
writes the frame. That keeps the cost inside handlers and ISRs to 219 cycles (6.8 us) per
record on the AK kit.

The console TX ring overwrites its oldest bytes when full, so `akt_trace` never pushes faster
than the line can drain. It keeps a byte budget refilled from the elapsed time and the configured baud
rate (`AKT_BAUD`), and only moves a whole frame into the TX ring when the budget covers it.
Records that do not fit stay in the akt ring; if the akt ring itself is full, new records are
dropped and counted (see `OVERFLOW`).

The kernel task log (`AK_TASK_LOG_CONSOLE_ENABLE`) must be off while the trace is on.
It prints about 70 bytes per message and would take the whole budget. `akt_cfg.h` stops the
build with `#error` if both are enabled.

## Framing

```
0x00 | COBS( record | crc8 ) | 0x00
```

- `record` is one of the records below. Byte 0 is the record id.
- `crc8` is CRC-8 with polynomial 0x07, init 0x00, no reflection, no final xor
  (CRC-8/SMBUS), computed over `record`.
- COBS removes every 0x00 from the frame body, so 0x00 only appears as a delimiter.
  Console text never contains 0x00.
- Both delimiters are sent. The leading one lets the host find the start of a frame right
  after text. Two frames back to back look like `00 <frame> 00 00 <frame> 00`.

Host rules:

1. Split the byte stream on 0x00.
2. A segment that COBS-decodes and has a valid CRC is a frame. A frame whose id is known
   must also have the exact size from the table below, otherwise it is corrupt. A frame with
   an unknown id is skipped (see Versioning). Anything else is console text and goes to the
   text view unchanged.
3. A frame broken by text printed from an interrupt fails the CRC. It is counted as a
   corrupt frame and shown as text, never decoded.

## Timestamp

`t` is the Cortex-M3 DWT cycle counter (`DWT->CYCCNT`), 32 bits, at the CPU clock
(`cpu_hz` in `INFO`, 32 MHz on the AK kit, so 31.25 ns per count). It wraps every 134 s.

Records are not sorted by `t`: `EXEC` is sent when the handler returns but carries the time
the handler started, so it arrives after the `POST`s made inside that handler. The host
unwraps by taking each step as a signed 32-bit difference from the previous record. That is
correct while two consecutive records are less than half a wrap (67 s) apart, and the firmware
sends a `POOL` record every second to keep it that way. A handler that runs longer than 67 s
would break this, but the watchdog resets the board long before.

## Records

| Id | Name | Size | Sent when |
| --- | --- | --- | --- |
| 0x01 | `INFO` | 20 | at `akt_trace_init()`, and again after every 64 `POOL` records |
| 0x02 | `POST` | 11 | a task or ISR posts a message (`task_post_*_msg`) |
| 0x03 | `EXEC` | 14 | a task handler returns |
| 0x04 | `TIMER_SET` | 12 | `timer_set()` |
| 0x05 | `TIMER_REMOVE` | 7 | `timer_remove_attr()` |
| 0x06 | `POOL` | 9 | every 1000 ms |
| 0x07 | `OVERFLOW` | 7 | the first record that fits after one or more drops |
| 0x08 | `USER` | 10 | `AKT_MARK(id, value)` in application code |

Size includes the id byte, excludes the crc.

### 0x01 INFO

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x01 |
| 1 | u32 | t |
| 5 | u8 | format version = 1 |
| 6 | u32 | cpu_hz |
| 10 | u8 | task_count (rows in the task table, without the EOT row) |
| 11 | u8 | pure pool size |
| 12 | u8 | common pool size |
| 13 | u8 | dynamic pool size |
| 14 | u8 | timer pool size |
| 15 | u8[4] | app version (`APP_VER`) |
| 19 | u8 | flags, bit 0 = RELEASE build |

### 0x02 POST

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x02 |
| 1 | u32 | t |
| 5 | u8 | src task id, see below |
| 6 | u8 | des task id |
| 7 | u8 | sig |
| 8 | u8 | msg type (0x80 pure, 0xC0 common, 0x40 dynamic) |
| 9 | u16 | payload length (0 for pure, saturates at 0xFFFF) |

src is tracked by `akt_trace`, not read from the message. In AK 1.3 the kernel sets
`current_task_id = t_msg->if_des_task_id` while a handler runs (`task.c`), and
`if_des_task_id` is only filled for interface messages, so `src_task_id` of a message posted
from a task handler is 0, the same value as `TASK_TIMER_TICK_ID`. `akt_trace` stores the id of
the handler it is running and uses:

| Context | src |
| --- | --- |
| inside a traced task handler | that task id |
| inside an ISR | `AK_TASK_INTERRUPT_ID` (0xEE) |
| anywhere else (init code, polling tasks) | `AK_TASK_IDLE_ID` (0xEF) |

Messages posted by the kernel itself are not seen here. The main case is a timer expiry:
the timer task posts it inside `timer.c`. It still shows up as an `EXEC` with no matching
`POST`, and the `TIMER_SET` for the same task and sig tells which timer it was.

### 0x03 EXEC

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x03 |
| 1 | u32 | t_begin, just before the handler is called |
| 5 | u32 | duration in cycles |
| 9 | u8 | task id |
| 10 | u8 | sig |
| 11 | u8 | msg type |
| 12 | u16 | wait_ms from the kernel (`start_exe - start_post`), saturates at 0xFFFF; 0xFFFF also when the kernel does not keep these times (`AK_TASK_OBJ_LOG_ENABLE` off) |

One record per handled message, written after the handler returns, so a handler that never
returns leaves no `EXEC`. That case is covered by the watchdog and `fatal m`.
Task id, sig and type are taken before the handler runs, because a handler that forwards the
same message with `task_post()` changes its `des_task_id`.

There is no src field: the kernel value is wrong (see `POST`). The host gets src and the exact
wait time by pairing each `EXEC` with the oldest unmatched `POST` to the same task with the
same sig. This works because AK keeps FIFO order per priority queue, so order per destination
task is kept as well. `wait_ms` is the kernel's own millisecond figure, kept as a cross check
and for messages with no `POST` (timer expiry).

### 0x04 TIMER_SET

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x04 |
| 1 | u32 | t |
| 5 | u8 | des task id |
| 6 | u8 | sig |
| 7 | u8 | 0 = one shot, 1 = periodic |
| 8 | u32 | duty in ms |

### 0x05 TIMER_REMOVE

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x05 |
| 1 | u32 | t |
| 5 | u8 | des task id |
| 6 | u8 | sig |

### 0x06 POOL

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x06 |
| 1 | u32 | t |
| 5 | u8 | pure messages in use, highest since boot |
| 6 | u8 | common messages in use, highest since boot |
| 7 | u8 | dynamic messages in use, highest since boot |
| 8 | u8 | timers in use, highest since boot |

Values come from `get_*_pool_used_max()`.

### 0x07 OVERFLOW

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x07 |
| 1 | u32 | t |
| 5 | u16 | records dropped since the last `OVERFLOW`, saturates at 0xFFFF |

### 0x08 USER

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u8 | id = 0x08 |
| 1 | u32 | t |
| 5 | u8 | user id |
| 6 | u32 | value |

## Bandwidth

On the wire (record + crc + COBS overhead + 2 delimiters):

| Record | Bytes |
| --- | --- |
| `POST` | 15 |
| `EXEC` | 18 |
| `TIMER_SET` | 16 |

A message posted by a task and then handled costs 33 bytes. At 115200 baud (11520 bytes/s)
that is about 350 messages per second with no other console output, and about 1390 per
second at 460800 baud.

## Versioning

The format version in `INFO` changes when an existing record changes layout. New record ids
can be added without a version change; the host skips ids it does not know, using the CRC to
confirm the frame and ignoring its body.
