/*
 * Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
 *
 * This file is part of Thrifty-X.
 *
 * SPDX-License-Identifier: GPL-3.0-only
 */

#include <pthread.h>
#include <signal.h>
#include <unistd.h>

#include "sigthread.h"

static sigset_t stop_signals;
static sigthread_callback_t callback;
static void *callback_ctx;

static void *sigthread_main(void *arg)
{
    (void)arg;
    int received = 0;
    for (;;) {
        int signo;
        if (sigwait(&stop_signals, &signo) != 0) {
            continue;
        }
        if (++received > 1) {
            /* The user asked twice: the clean stop is stuck. */
            _exit(128 + signo);
        }
        callback(signo, callback_ctx);
    }
    return NULL;
}

int sigthread_start(sigthread_callback_t on_signal, void *ctx)
{
    pthread_t thread;

    callback = on_signal;
    callback_ctx = ctx;
    sigemptyset(&stop_signals);
    sigaddset(&stop_signals, SIGINT);
    sigaddset(&stop_signals, SIGTERM);
    sigaddset(&stop_signals, SIGQUIT);
    if (pthread_sigmask(SIG_BLOCK, &stop_signals, NULL) != 0) {
        return -1;
    }
    signal(SIGPIPE, SIG_IGN);
    if (pthread_create(&thread, NULL, sigthread_main, NULL) != 0) {
        return -1;
    }
    pthread_detach(thread);
    return 0;
}
