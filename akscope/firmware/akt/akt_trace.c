/**
 ******************************************************************************
 * @file    akt_trace.c
 * @brief   record AK message flow and send it on the console UART
 *
 * Record layout and framing: docs/trace-format.md in ak-scope.
 ******************************************************************************
 */
#if defined(AKT_TRACE_EN)

#include <string.h>

#include "ak.h"
#include "task.h"
#include "timer.h"
#include "message.h"

#include "xprintf.h"

#include "akt_trace.h"

/* cycle counter: the port function by default. A build that knows where the counter is can
 * define AKT_CYCLE_NOW() to read it inline; akscope build reads DWT->CYCCNT that way. */
#if defined(AKT_CYCLE_NOW)
#define akt_now()					AKT_CYCLE_NOW()
#else
#define akt_now()					akt_port.cycle_now()
#endif

/* the longest record is INFO (20 bytes); a frame adds the crc and one COBS code byte */
#define AKT_RECORD_MAX				(20)
#define AKT_FRAME_MAX				(AKT_RECORD_MAX + 2)
/* bytes on the line for a record of n bytes: frame plus the two 0x00 delimiters */
#define AKT_WIRE_SIZE(n)			((n) + 4)

typedef struct {
	uint8_t task_id;
	uint8_t sig;
	uint8_t type;
	uint16_t wait_ms;
} akt_exec_t;

static akt_port_t akt_port;
static uint8_t akt_ready;

/* records waiting for the line, stored raw as [len][len bytes]. CRC and COBS are done by
 * akt_trace_poll() when it sends them, so recording from a handler or an ISR is only a copy. */
static uint8_t akt_ring[AKT_RING_SIZE];
static uint16_t akt_ring_head;
static uint16_t akt_ring_used;
static uint16_t akt_dropped;

/* id of the traced handler that is running, AK_TASK_IDLE_ID outside of handlers */
static uint8_t akt_running = AK_TASK_IDLE_ID;
static akt_exec_t akt_exec;

static uint32_t akt_cycles_per_byte;
static uint32_t akt_last_tx;
static uint32_t akt_tx_cycles;
static uint16_t akt_tx_budget;

static uint32_t akt_cycles_per_pool;
static uint32_t akt_last_pool;
static uint8_t akt_pool_count;

static void akt_info();

/* -Os does not inline these by itself; each call costs more than the body on the record path */
#define AKT_INLINE					static inline __attribute__((always_inline))

AKT_INLINE void put_u16(uint8_t* p, uint16_t v) {
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
}

AKT_INLINE void put_u32(uint8_t* p, uint32_t v) {
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
	p[2] = (uint8_t)(v >> 16);
	p[3] = (uint8_t)(v >> 24);
}

AKT_INLINE uint16_t sat_u16(uint32_t v) {
	return (v > 0xFFFF) ? 0xFFFF : (uint16_t)v;
}

/* CRC-8/SMBUS: poly 0x07, init 0, no reflection, no final xor.
 * Table driven: the bitwise loop took most of the ~1700 cycles a record cost at 32 MHz. */
static const uint8_t akt_crc8_table[256] = {
	0x00, 0x07, 0x0E, 0x09, 0x1C, 0x1B, 0x12, 0x15, 0x38, 0x3F, 0x36, 0x31, 0x24, 0x23, 0x2A, 0x2D,
	0x70, 0x77, 0x7E, 0x79, 0x6C, 0x6B, 0x62, 0x65, 0x48, 0x4F, 0x46, 0x41, 0x54, 0x53, 0x5A, 0x5D,
	0xE0, 0xE7, 0xEE, 0xE9, 0xFC, 0xFB, 0xF2, 0xF5, 0xD8, 0xDF, 0xD6, 0xD1, 0xC4, 0xC3, 0xCA, 0xCD,
	0x90, 0x97, 0x9E, 0x99, 0x8C, 0x8B, 0x82, 0x85, 0xA8, 0xAF, 0xA6, 0xA1, 0xB4, 0xB3, 0xBA, 0xBD,
	0xC7, 0xC0, 0xC9, 0xCE, 0xDB, 0xDC, 0xD5, 0xD2, 0xFF, 0xF8, 0xF1, 0xF6, 0xE3, 0xE4, 0xED, 0xEA,
	0xB7, 0xB0, 0xB9, 0xBE, 0xAB, 0xAC, 0xA5, 0xA2, 0x8F, 0x88, 0x81, 0x86, 0x93, 0x94, 0x9D, 0x9A,
	0x27, 0x20, 0x29, 0x2E, 0x3B, 0x3C, 0x35, 0x32, 0x1F, 0x18, 0x11, 0x16, 0x03, 0x04, 0x0D, 0x0A,
	0x57, 0x50, 0x59, 0x5E, 0x4B, 0x4C, 0x45, 0x42, 0x6F, 0x68, 0x61, 0x66, 0x73, 0x74, 0x7D, 0x7A,
	0x89, 0x8E, 0x87, 0x80, 0x95, 0x92, 0x9B, 0x9C, 0xB1, 0xB6, 0xBF, 0xB8, 0xAD, 0xAA, 0xA3, 0xA4,
	0xF9, 0xFE, 0xF7, 0xF0, 0xE5, 0xE2, 0xEB, 0xEC, 0xC1, 0xC6, 0xCF, 0xC8, 0xDD, 0xDA, 0xD3, 0xD4,
	0x69, 0x6E, 0x67, 0x60, 0x75, 0x72, 0x7B, 0x7C, 0x51, 0x56, 0x5F, 0x58, 0x4D, 0x4A, 0x43, 0x44,
	0x19, 0x1E, 0x17, 0x10, 0x05, 0x02, 0x0B, 0x0C, 0x21, 0x26, 0x2F, 0x28, 0x3D, 0x3A, 0x33, 0x34,
	0x4E, 0x49, 0x40, 0x47, 0x52, 0x55, 0x5C, 0x5B, 0x76, 0x71, 0x78, 0x7F, 0x6A, 0x6D, 0x64, 0x63,
	0x3E, 0x39, 0x30, 0x37, 0x22, 0x25, 0x2C, 0x2B, 0x06, 0x01, 0x08, 0x0F, 0x1A, 0x1D, 0x14, 0x13,
	0xAE, 0xA9, 0xA0, 0xA7, 0xB2, 0xB5, 0xBC, 0xBB, 0x96, 0x91, 0x98, 0x9F, 0x8A, 0x8D, 0x84, 0x83,
	0xDE, 0xD9, 0xD0, 0xD7, 0xC2, 0xC5, 0xCC, 0xCB, 0xE6, 0xE1, 0xE8, 0xEF, 0xFA, 0xFD, 0xF4, 0xF3,
};

static uint8_t akt_crc8(const uint8_t* data, uint8_t len) {
	uint8_t crc = 0;

	while (len--) {
		crc = akt_crc8_table[crc ^ *data++];
	}

	return crc;
}

/* COBS without the trailing delimiter, input <= 253 bytes so one block is enough */
static uint8_t akt_cobs(const uint8_t* in, uint8_t len, uint8_t* out) {
	uint8_t code_index = 0;
	uint8_t out_index = 1;
	uint8_t code = 1;

	for (uint8_t i = 0; i < len; i++) {
		if (in[i] == 0) {
			out[code_index] = code;
			code_index = out_index++;
			code = 1;
		}
		else {
			out[out_index++] = in[i];
			code++;
		}
	}
	out[code_index] = code;

	return out_index;
}

static uint8_t akt_encode(uint8_t* record, uint8_t len, uint8_t* frame) {
	record[len] = akt_crc8(record, len);
	return akt_cobs(record, len + 1, frame);
}

/* the record path saves and restores PRIMASK instead of calling entry_critical(): it is
 * cheaper, and it nests inside AK critical sections and ISRs (Cortex-M) */
AKT_INLINE uint32_t akt_lock() {
	uint32_t primask;
	__asm volatile ("mrs %0, primask\n\tcpsid i" : "=r" (primask) :: "memory");
	return primask;
}

AKT_INLINE void akt_unlock(uint32_t primask) {
	__asm volatile ("msr primask, %0" :: "r" (primask) : "memory");
}

/* called with interrupts off */
AKT_INLINE void akt_ring_put(const uint8_t* record, uint8_t len) {
	uint16_t i = akt_ring_head + akt_ring_used;
	uint8_t* dst;

	if (i >= AKT_RING_SIZE) {
		i -= AKT_RING_SIZE;
	}
	akt_ring_used += len + 1;

	if (i + len < AKT_RING_SIZE) {
		/* the usual case: room up to the end of the ring, no wrap check per byte */
		dst = &akt_ring[i];
		*dst++ = len;
		while (len--) {
			*dst++ = *record++;
		}
		return;
	}

	akt_ring[i] = len;
	while (len--) {
		if (++i == AKT_RING_SIZE) {
			i = 0;
		}
		akt_ring[i] = *record++;
	}
}

/* called from tasks and ISRs */
static void akt_push(const uint8_t* record, uint8_t len) {
	uint32_t primask;

	if (!akt_ready) {
		return;
	}

	primask = akt_lock();

	if (akt_dropped) {
		uint8_t ovf[7];

		if (akt_ring_used + sizeof(ovf) + 1 + len + 1 > AKT_RING_SIZE) {
			akt_dropped = sat_u16((uint32_t)akt_dropped + 1);
			akt_unlock(primask);
			return;
		}

		ovf[0] = AKT_ID_OVERFLOW;
		put_u32(&ovf[1], akt_now());
		put_u16(&ovf[5], akt_dropped);
		akt_ring_put(ovf, sizeof(ovf));
		akt_dropped = 0;
	}

	if (akt_ring_used + len + 1 > AKT_RING_SIZE) {
		akt_dropped = sat_u16((uint32_t)akt_dropped + 1);
	}
	else {
		akt_ring_put(record, len);
	}

	akt_unlock(primask);
}

static uint8_t akt_src() {
	/* the kernel src_task_id is 0 for every message posted from a handler, see trace-format.md */
	if (get_current_task_id() == AK_TASK_INTERRUPT_ID) {
		return AK_TASK_INTERRUPT_ID;
	}
	return akt_running;
}

static void akt_post(task_id_t des, uint8_t sig, uint8_t type, uint32_t len) {
	uint8_t r[11 + 1];

	r[0] = AKT_ID_POST;
	put_u32(&r[1], akt_ready ? akt_now() : 0);
	r[5] = akt_src();
	r[6] = des;
	r[7] = sig;
	r[8] = type;
	put_u16(&r[9], sat_u16(len));
	akt_push(r, 11);
}

static void akt_pool() {
	uint8_t r[9 + 1];

	r[0] = AKT_ID_POOL;
	put_u32(&r[1], akt_now());
	r[5] = (uint8_t)get_pure_msg_pool_used_max();
	r[6] = (uint8_t)get_common_msg_pool_used_max();
	r[7] = (uint8_t)get_dynamic_msg_pool_used_max();
	r[8] = (uint8_t)get_timer_msg_pool_used_max();
	akt_push(r, 9);
}

static void akt_info() {
	uint8_t r[20 + 1];

	r[0] = AKT_ID_INFO;
	put_u32(&r[1], akt_now());
	r[5] = AKT_FORMAT_VERSION;
	put_u32(&r[6], akt_port.cpu_hz);
	r[10] = akt_port.task_count;
	r[11] = AK_PURE_MSG_POOL_SIZE;
	r[12] = AK_COMMON_MSG_POOL_SIZE;
	r[13] = AK_DYNAMIC_MSG_POOL_SIZE;
	r[14] = AK_TIMER_POOL_SIZE;
	memcpy(&r[15], akt_port.app_ver, 4);
	r[19] = akt_port.flags;
	akt_push(r, 20);
}

void akt_trace_init(const akt_port_t* port) {
	uint32_t now = port->cycle_now();

	/* copied, so the caller can pass a local variable */
	akt_port = *port;
	akt_cycles_per_byte = port->cpu_hz / (AKT_BAUD / 10);
	akt_cycles_per_pool = (port->cpu_hz / 1000) * AKT_POOL_PERIOD_MS;
	akt_last_tx = now;
	akt_last_pool = now;
	akt_ready = 1;

	akt_info();
}

void akt_trace_poll() {
	uint8_t record[AKT_RECORD_MAX + 1];
	uint8_t frame[AKT_FRAME_MAX];
	uint8_t len;
	uint8_t frame_len;
	uint32_t now;

	if (!akt_ready) {
		return;
	}

	now = akt_now();

	if (now - akt_last_pool >= akt_cycles_per_pool) {
		akt_last_pool = now;
		akt_pool();
		if (++akt_pool_count >= AKT_INFO_EVERY_POOL) {
			akt_pool_count = 0;
			akt_info();
		}
	}

	/* refill the byte budget from the time the line had to drain the TX ring */
	akt_tx_cycles += now - akt_last_tx;
	akt_last_tx = now;
	if (akt_tx_cycles >= akt_cycles_per_byte) {
		uint32_t budget = akt_tx_budget + akt_tx_cycles / akt_cycles_per_byte;
		akt_tx_cycles %= akt_cycles_per_byte;
		akt_tx_budget = (budget > AKT_TX_BURST_MAX) ? AKT_TX_BURST_MAX : (uint16_t)budget;
	}

	for (;;) {
		uint16_t i;

		ENTRY_CRITICAL();

		if (akt_ring_used == 0 || AKT_WIRE_SIZE(akt_ring[akt_ring_head]) > akt_tx_budget) {
			EXIT_CRITICAL();
			break;
		}

		i = akt_ring_head;
		len = akt_ring[i];
		for (uint8_t k = 0; k < len; k++) {
			if (++i == AKT_RING_SIZE) {
				i = 0;
			}
			record[k] = akt_ring[i];
		}
		if (++i == AKT_RING_SIZE) {
			i = 0;
		}
		akt_ring_head = i;
		akt_ring_used -= len + 1;

		EXIT_CRITICAL();

		frame_len = akt_encode(record, len, frame);

		/* a whole frame per call, so text printed by the main loop never lands inside it */
		xfunc_output(0x00);
		for (uint8_t k = 0; k < frame_len; k++) {
			xfunc_output(frame[k]);
		}
		xfunc_output(0x00);
		akt_tx_budget -= frame_len + 2;
	}
}

void akt_trace_mark(uint8_t id, uint32_t value) {
	uint8_t r[10 + 1];

	r[0] = AKT_ID_USER;
	put_u32(&r[1], akt_ready ? akt_now() : 0);
	r[5] = id;
	put_u32(&r[6], value);
	akt_push(r, 10);
}

uint32_t akt_exec_begin(ak_msg_t* msg) {
	/* copied now: a handler that forwards msg with task_post() changes des_task_id */
	akt_exec.task_id = msg->des_task_id;
	akt_exec.sig = msg->sig;
	akt_exec.type = get_msg_type(msg);
#if defined(AK_TASK_OBJ_LOG_ENABLE)
	akt_exec.wait_ms = sat_u16(msg->dbg_handler.start_exe - msg->dbg_handler.start_post);
#else
	akt_exec.wait_ms = 0xFFFF;
#endif
	akt_running = msg->des_task_id;

	return akt_ready ? akt_now() : 0;
}

void akt_exec_end(uint32_t t_begin) {
	uint8_t r[14 + 1];

	akt_running = AK_TASK_IDLE_ID;

	if (!akt_ready) {
		return;
	}

	r[0] = AKT_ID_EXEC;
	put_u32(&r[1], t_begin);
	put_u32(&r[5], akt_now() - t_begin);
	r[9] = akt_exec.task_id;
	r[10] = akt_exec.sig;
	r[11] = akt_exec.type;
	put_u16(&r[12], akt_exec.wait_ms);
	akt_push(r, 14);
}

/*****************************************************************************
 * linker wrappers, enabled by AKT_LDFLAGS (--wrap=<symbol>)
 *****************************************************************************/
extern void __real_task_post_pure_msg(task_id_t des_task_id, uint8_t sig);
extern void __real_task_post_common_msg(task_id_t des_task_id, uint8_t sig, uint8_t* data, uint8_t len);
extern void __real_task_post_dynamic_msg(task_id_t des_task_id, uint8_t sig, uint8_t* data, uint32_t len);
extern uint8_t __real_timer_set(task_id_t des_task_id, timer_sig_t sig, uint32_t duty, timer_type_t type);
extern uint8_t __real_timer_remove_attr(task_id_t des_task_id, timer_sig_t sig);

void __wrap_task_post_pure_msg(task_id_t des_task_id, uint8_t sig) {
	akt_post(des_task_id, sig, PURE_MSG_TYPE, 0);
	__real_task_post_pure_msg(des_task_id, sig);
}

void __wrap_task_post_common_msg(task_id_t des_task_id, uint8_t sig, uint8_t* data, uint8_t len) {
	akt_post(des_task_id, sig, COMMON_MSG_TYPE, len);
	__real_task_post_common_msg(des_task_id, sig, data, len);
}

void __wrap_task_post_dynamic_msg(task_id_t des_task_id, uint8_t sig, uint8_t* data, uint32_t len) {
	akt_post(des_task_id, sig, DYNAMIC_MSG_TYPE, len);
	__real_task_post_dynamic_msg(des_task_id, sig, data, len);
}

uint8_t __wrap_timer_set(task_id_t des_task_id, timer_sig_t sig, uint32_t duty, timer_type_t type) {
	uint8_t r[12 + 1];

	r[0] = AKT_ID_TIMER_SET;
	put_u32(&r[1], akt_ready ? akt_now() : 0);
	r[5] = des_task_id;
	r[6] = sig;
	r[7] = (type == TIMER_PERIODIC) ? 1 : 0;
	put_u32(&r[8], duty);
	akt_push(r, 12);

	return __real_timer_set(des_task_id, sig, duty, type);
}

uint8_t __wrap_timer_remove_attr(task_id_t des_task_id, timer_sig_t sig) {
	uint8_t r[7 + 1];

	r[0] = AKT_ID_TIMER_REMOVE;
	put_u32(&r[1], akt_ready ? akt_now() : 0);
	r[5] = des_task_id;
	r[6] = sig;
	akt_push(r, 7);

	return __real_timer_remove_attr(des_task_id, sig);
}

#endif /* AKT_TRACE_EN */
