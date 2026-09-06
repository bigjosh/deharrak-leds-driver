#define _POSIX_C_SOURCE 200809L
/* Only linked into build/test-udp. Never opens a hardware device or mailbox. */
#include "dld_sender.h"

#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

volatile sig_atomic_t dld_cancelled;
static FILE *journal;
static unsigned sent;

static void cancelled(int number) { dld_cancelled = number; }

int dld_install_signals(char *error, size_t cap)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = cancelled;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) < 0 ||
        sigaction(SIGTERM, &action, NULL) < 0 ||
        sigaction(SIGHUP, &action, NULL) < 0) {
        snprintf(error, cap, "fake signal install: %s", strerror(errno));
        return -1;
    }
    return 0;
}

static unsigned setting(const char *name)
{
    const char *text = getenv(name);
    return text ? (unsigned)strtoul(text, NULL, 10) : 0;
}

int dld_sender_open(struct dld_sender *sender, char *error, size_t cap)
{
    const char *path = getenv("DLD_TEST_LOG");
    memset(sender, 0, sizeof(*sender));
    if (path == NULL || (journal = fopen(path, "a")) == NULL) {
        snprintf(error, cap, "fake sender needs writable DLD_TEST_LOG");
        return DLD_PREREQUISITE;
    }
    setvbuf(journal, NULL, _IONBF, 0);
    fprintf(journal, "OPEN\n");
    sent = 0;
    return DLD_OK;
}

int dld_sender_send(struct dld_sender *sender, uint32_t rgb,
                      struct dld_send_result *result, char *error, size_t cap)
{
    unsigned delay = setting("DLD_TEST_DELAY_MS");
    unsigned fail_after = setting("DLD_TEST_FAIL_AFTER");
    struct timespec remaining;
    (void)sender;
    memset(result, 0, sizeof(*result));
    result->quiet.submitted = 1;
    ++sent;
    fprintf(journal, "SEND %06lx\n", (unsigned long)rgb);
    remaining.tv_sec = delay / 1000;
    remaining.tv_nsec = (long)(delay % 1000) * 1000000L;
    while (nanosleep(&remaining, &remaining) < 0 && errno == EINTR) { }
    if (dld_cancelled || (fail_after && sent >= fail_after)) {
        fprintf(journal, "FAIL\n");
        snprintf(error, cap, "fake critical submitted-frame failure");
        return DLD_CRITICAL;
    }
    fprintf(journal, "DONE %06lx\n", (unsigned long)rgb);
    return DLD_OK;
}

void dld_sender_close(struct dld_sender *sender)
{
    (void)sender;
    fprintf(journal, "CLOSE\n");
    fclose(journal);
    journal = NULL;
}
