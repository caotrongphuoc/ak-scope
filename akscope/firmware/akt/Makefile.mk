# akt trace, enable with: make AKT_TRACE=1
AKT_TRACE ?= 0

CFLAGS		+= -I./sources/app/akt
CPPFLAGS	+= -I./sources/app/akt

VPATH += sources/app/akt

SOURCES += sources/app/akt/akt_trace.c

ifeq ($(AKT_TRACE),1)
CFLAGS		+= -DAKT_TRACE_EN
CPPFLAGS	+= -DAKT_TRACE_EN

# application/Makefile must pass $(AKT_LDFLAGS) to the linker, see README.md
AKT_LDFLAGS =							\
		--wrap=task_post_pure_msg		\
		--wrap=task_post_common_msg		\
		--wrap=task_post_dynamic_msg	\
		--wrap=timer_set				\
		--wrap=timer_remove_attr
endif
