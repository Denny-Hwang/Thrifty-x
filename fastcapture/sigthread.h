/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

/*
 * Termination signals handled on a dedicated thread.
 *
 * A handler installed with signal() runs on whichever thread the signal
 * interrupts and may only call async-signal-safe functions.  Stopping a
 * capture takes the ring buffer's mutex (circbuf_cancel), so doing it
 * from a handler can deadlock against the very thread it interrupted.
 * Instead the signals are blocked in every thread and one thread waits
 * for them with sigwait() and runs the stop callback as ordinary code.
 */

#ifndef SIGTHREAD_H
#define SIGTHREAD_H

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*sigthread_callback_t)(int signo, void *ctx);

/// Block SIGINT, SIGTERM and SIGQUIT in the calling thread (threads it
/// creates later inherit the mask), ignore SIGPIPE (a write to a closed
/// pipe then fails with EPIPE, which the caller checks), and start a
/// thread that calls on_signal(signo, ctx) on the first of them.  A
/// second signal exits at once with status 128 + signo.
///
/// Call before any other thread is created (libairspy, libusb, FFTW).
/// Returns 0 on success.
int sigthread_start(sigthread_callback_t on_signal, void *ctx);

#ifdef __cplusplus
}
#endif

#endif /* SIGTHREAD_H */
