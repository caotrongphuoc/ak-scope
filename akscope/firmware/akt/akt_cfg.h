#ifndef __AKT_CFG_H__
#define __AKT_CFG_H__

/* bytes of RAM for frames waiting to be sent, each frame takes its size + 1 */
#ifndef AKT_RING_SIZE
#define AKT_RING_SIZE				(512)
#endif

/* line rate used to pace the output, must match the console UART */
#ifndef AKT_BAUD
#if defined(SYS_CONSOLE_BAUDRATE)
#define AKT_BAUD					SYS_CONSOLE_BAUDRATE
#else
#define AKT_BAUD					(115200)
#endif
#endif

/* most bytes moved into the console TX ring (512 bytes) in one burst */
#ifndef AKT_TX_BURST_MAX
#define AKT_TX_BURST_MAX			(128)
#endif

/* POOL record period, also bounds the gap between timestamps (CYCCNT wraps every 134 s) */
#ifndef AKT_POOL_PERIOD_MS
#define AKT_POOL_PERIOD_MS			(1000)
#endif

/* INFO is sent again after this many POOL records, for a host that attaches late */
#ifndef AKT_INFO_EVERY_POOL
#define AKT_INFO_EVERY_POOL			(64)
#endif

#if defined(AKT_TRACE_EN) && defined(AK_TASK_LOG_CONSOLE_ENABLE)
#error "akt trace and AK_TASK_LOG_CONSOLE_ENABLE share the console, turn the kernel log off"
#endif

#endif /* __AKT_CFG_H__ */
