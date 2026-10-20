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

/* record + crc + COBS overhead, the longest record is INFO (20 bytes) */
#define AKT_RECORD_MAX				(20)
#define AKT_FRAME_MAX				(AKT_RECORD_MAX + 2)

typedef struct {
	uint8_t task_id;
	uint8_t sig;
	uint8_t type;
	uint16_t wait_ms;
} akt_exec_t;

static akt_port_t akt_port;
static uint8_t akt_ready;

/* frames waiting for the line, each stored as [len][len bytes of COBS data] */
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

static inline void put_u16(uint8_t* p, uint16_t v) {
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
}

static inline void put_u32(uint8_t* p, uint32_t v) {
	p[0] = (uint8_t)v;
	p[1] = (uint8_t)(v >> 8);
	p[2] = (uint8_t)(v >> 16);
	p[3] = (uint8_t)(v >> 24);
}

static inline uint16_t sat_u16(uint32_t v) {
	return (v > 0xFFFF) ? 0xFFFF : (uint16_t)v;
}

/* CRC-8/SMBUS: poly 0x07, init 0, no reflection, no final xor */
static uint8_t akt_crc8(const uint8_t* data, uint8_t len) {
	uint8_t crc = 0;

	while (len--) {
		crc ^= *data++;
		for (uint8_t i = 0; i < 8; i++) {
			crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
		}
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

static void akt_ring_write(const uint8_t* data, uint8_t len) {
	uint16_t tail = (akt_ring_head + akt_ring_used) % AKT_RING_SIZE;

	for (uint8_t i = 0; i < len; i++) {
		akt_ring[tail] = data[i];
		tail = (tail + 1) % AKT_RING_SIZE;
	}
	akt_ring_used += len;
}

/* called from tasks and ISRs */
static void akt_push(uint8_t* record, uint8_t len) {
	uint8_t frame[AKT_FRAME_MAX];
	uint8_t frame_len;

	if (!akt_ready) {
		return;
	}

	frame_len = akt_encode(record, len, frame);

	ENTRY_CRITICAL();

	if (akt_dropped) {
		uint8_t ovf[8];
		uint8_t ovf_frame[10];
		uint8_t ovf_len;

		ovf[0] = AKT_ID_OVERFLOW;
		put_u32(&ovf[1], akt_port.cycle_now());
		put_u16(&ovf[5], akt_dropped);
		ovf_len = akt_encode(ovf, 7, ovf_frame);

		if (akt_ring_used + ovf_len + 1 + frame_len + 1 > AKT_RING_SIZE) {
			akt_dropped = sat_u16((uint32_t)akt_dropped + 1);
			EXIT_CRITICAL();
			return;
		}

		akt_ring_write(&ovf_len, 1);
		akt_ring_write(ovf_frame, ovf_len);
		akt_dropped = 0;
	}

	if (akt_ring_used + frame_len + 1 > AKT_RING_SIZE) {
		akt_dropped = sat_u16((uint32_t)akt_dropped + 1);
	}
	else {
		akt_ring_write(&frame_len, 1);
		akt_ring_write(frame, frame_len);
	}

	EXIT_CRITICAL();
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
	put_u32(&r[1], akt_ready ? akt_port.cycle_now() : 0);
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
	put_u32(&r[1], akt_port.cycle_now());
	r[5] = (uint8_t)get_pure_msg_pool_used_max();
	r[6] = (uint8_t)get_common_msg_pool_used_max();
	r[7] = (uint8_t)get_dynamic_msg_pool_used_max();
	r[8] = (uint8_t)get_timer_msg_pool_used_max();
	akt_push(r, 9);
}

static void akt_info() {
	uint8_t r[20 + 1];

	r[0] = AKT_ID_INFO;
	put_u32(&r[1], akt_port.cycle_now());
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
	uint8_t frame[AKT_FRAME_MAX];
	uint8_t len;
	uint32_t now;

	if (!akt_ready) {
		return;
	}

	now = akt_port.cycle_now();

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
		ENTRY_CRITICAL();

		if (akt_ring_used == 0 || akt_ring[akt_ring_head] + 2 > akt_tx_budget) {
			EXIT_CRITICAL();
			break;
		}

		len = akt_ring[akt_ring_head];
		for (uint8_t i = 0; i < len; i++) {
			frame[i] = akt_ring[(akt_ring_head + 1 + i) % AKT_RING_SIZE];
		}
		akt_ring_head = (akt_ring_head + 1 + len) % AKT_RING_SIZE;
		akt_ring_used -= len + 1;

		EXIT_CRITICAL();

		/* a whole frame per call, so text printed by the main loop never lands inside it */
		xfunc_output(0x00);
		for (uint8_t i = 0; i < len; i++) {
			xfunc_output(frame[i]);
		}
		xfunc_output(0x00);
		akt_tx_budget -= len + 2;
	}
}

void akt_trace_mark(uint8_t id, uint32_t value) {
	uint8_t r[10 + 1];

	r[0] = AKT_ID_USER;
	put_u32(&r[1], akt_ready ? akt_port.cycle_now() : 0);
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

	return akt_ready ? akt_port.cycle_now() : 0;
}

void akt_exec_end(uint32_t t_begin) {
	uint8_t r[14 + 1];

	akt_running = AK_TASK_IDLE_ID;

	if (!akt_ready) {
		return;
	}

	r[0] = AKT_ID_EXEC;
	put_u32(&r[1], t_begin);
	put_u32(&r[5], akt_port.cycle_now() - t_begin);
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
	put_u32(&r[1], akt_ready ? akt_port.cycle_now() : 0);
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
	put_u32(&r[1], akt_ready ? akt_port.cycle_now() : 0);
	r[5] = des_task_id;
	r[6] = sig;
	akt_push(r, 7);

	return __real_timer_remove_attr(des_task_id, sig);
}

#endif /* AKT_TRACE_EN */
