#ifndef __AKT_TRACE_H__
#define __AKT_TRACE_H__

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>

#include "ak.h"
#include "message.h"

#include "akt_cfg.h"

#define AKT_FORMAT_VERSION			(1)

#define AKT_ID_INFO					(0x01)
#define AKT_ID_POST					(0x02)
#define AKT_ID_EXEC					(0x03)
#define AKT_ID_TIMER_SET			(0x04)
#define AKT_ID_TIMER_REMOVE			(0x05)
#define AKT_ID_POOL					(0x06)
#define AKT_ID_OVERFLOW				(0x07)
#define AKT_ID_USER					(0x08)

#define AKT_FLAG_RELEASE			(0x01)

typedef struct {
	/* free running cycle counter, see io_cycle_counter_get() in README */
	uint32_t (*cycle_now)(void);
	uint32_t cpu_hz;
	uint8_t task_count;
	uint8_t app_ver[4];
	uint8_t flags;
} akt_port_t;

#if defined(AKT_TRACE_EN)

extern void akt_trace_init(const akt_port_t* port);

/* polling task, add it to app_task_polling_table */
extern void akt_trace_poll();

extern void akt_trace_mark(uint8_t id, uint32_t value);

/* used by AKT_WRAP, not called directly */
extern uint32_t akt_exec_begin(ak_msg_t* msg);
extern void akt_exec_end(uint32_t t_begin);

/* AKT_WRAP(fn) defines a traced copy of handler fn, AKT_TASK(fn) names it in the task table */
#define AKT_WRAP(fn)											\
	static void akt_wrap_##fn(ak_msg_t* msg) {					\
		uint32_t t_begin = akt_exec_begin(msg);					\
		fn(msg);												\
		akt_exec_end(t_begin);									\
	}

#define AKT_TASK(fn)				akt_wrap_##fn
#define AKT_MARK(id, value)			akt_trace_mark((id), (value))

#else

#define akt_trace_init(port)
#define AKT_WRAP(fn)
#define AKT_TASK(fn)				fn
#define AKT_MARK(id, value)

#endif

#ifdef __cplusplus
}
#endif

#endif /* __AKT_TRACE_H__ */
