#include "../kernel/dld_admission.h"

#include <stdio.h>
#include <string.h>

/* Offline execution of the same policy used by initial DMA admission and
 * per-bank admission. Actions model hardware idleness and a terminal bank
 * result; fake clocks avoid delays, devices, root and kernel loading. */
struct scenario {
    long long now, busy_until, step, action_duration, first_grant;
    unsigned calls, pauses, grants;
    int grant_when_idle, final_result, state_masked, failure;
};

static long long fake_now(void *context)
{
    return ((struct scenario *)context)->now;
}

static int fake_attempt(void *context, int *granted)
{
    struct scenario *s = context;
    s->calls++;
    s->state_masked = 1;
    if (s->now < s->busy_until) {
        *granted = 0;
        s->state_masked = 0;
        return -EAGAIN;
    }
    *granted = s->grant_when_idle;
    if (*granted) {
        if (!s->grants) s->first_grant = s->now;
        s->grants++;
    }
    s->now += s->action_duration;
    s->state_masked = 0;
    return s->final_result;
}

static void fake_pause(void *context)
{
    struct scenario *s = context;
    if (s->state_masked || s->grants) s->failure = 1;
    s->pauses++;
    s->now += s->step;
}

static const struct dld_admission_ops ops = { fake_now, fake_attempt, fake_pause };
static unsigned checks;

#define CHECK(condition, description) do { checks++; if (!(condition)) { \
    fprintf(stderr, "FAIL admission: %s (line %d)\n", (description), __LINE__); return 1; \
} } while (0)

int main(void)
{
    struct scenario s;
    int result;

    memset(&s, 0, sizeof s);
    s.busy_until = 75000000LL; s.step = 150000LL;
    result = dld_admission_wait(&ops, &s, 1);
    CHECK(result == 0, "75 ms busy initial DMA admission should eventually succeed");
    CHECK(s.now >= 75000000LL && s.now < DLD_ADMISSION_TIMEOUT_NS, "success follows >20 ms busy period");
    CHECK(s.grants == 0 && !s.failure, "initial admission publishes or grants nothing");

    memset(&s, 0, sizeof s);
    s.busy_until = 75000000LL; s.step = 150000LL; s.grant_when_idle = 1;
    s.action_duration = 9240000LL;
    result = dld_admission_wait(&ops, &s, 0);
    CHECK(result == 0 && s.grants == 1, "one bank grant follows eventual idle");
    CHECK(s.first_grant >= s.busy_until && !s.failure, "no grant while DMA is busy or pause after grant");

    memset(&s, 0, sizeof s);
    s.busy_until = 2000000000LL; s.step = 150000LL; s.grant_when_idle = 1;
    result = dld_admission_wait(&ops, &s, 0);
    CHECK(result == -ETIMEDOUT && s.grants == 0, "permanent busy times out without grant");
    CHECK(s.now >= DLD_ADMISSION_TIMEOUT_NS && s.now < DLD_ADMISSION_TIMEOUT_NS + s.step,
          "busy wait uses the 1 s deadline");
    CHECK(s.calls > 200 && s.calls < DLD_ADMISSION_MAX_ATTEMPTS, "old 200-attempt cap cannot truncate admission");
    CHECK(!s.failure && !s.state_masked, "CPU state is restored during waiting and on timeout");

    memset(&s, 0, sizeof s);
    s.busy_until = 1; /* frozen clock: attempts, not wall time, must terminate */
    result = dld_admission_wait(&ops, &s, 1);
    CHECK(result == -ETIMEDOUT && s.calls == DLD_ADMISSION_MAX_ATTEMPTS, "frozen clock retains finite attempt guard");
    CHECK(!s.grants && !s.failure, "frozen clock never authorizes a busy bank");

    memset(&s, 0, sizeof s);
    s.grant_when_idle = 1; s.final_result = -ETIMEDOUT;
    result = dld_admission_wait(&ops, &s, 0);
    CHECK(result == -ETIMEDOUT && s.calls == 1 && s.grants == 1 && !s.pauses,
          "failed bank completion is terminal with no grace or retry");

    memset(&s, 0, sizeof s);
    s.grant_when_idle = 1; s.final_result = -EAGAIN;
    result = dld_admission_wait(&ops, &s, 0);
    CHECK(result == -EAGAIN && s.calls == 1 && !s.pauses,
          "even EAGAIN is never retried after a grant");

    memset(&s, 0, sizeof s);
    s.final_result = -EIO;
    result = dld_admission_wait(&ops, &s, 0);
    CHECK(result == -EIO && s.calls == 1 && !s.grants && !s.pauses,
          "pre-grant nonretryable error remains terminal");

    memset(&s, 0, sizeof s);
    s.action_duration = DLD_ADMISSION_TIMEOUT_NS;
    result = dld_admission_wait(&ops, &s, 1);
    CHECK(result == -ETIMEDOUT && !s.grants, "late read-only idle observation is not accepted");

    memset(&s, 0, sizeof s);
    s.busy_until = 999000000LL; s.step = 1000000LL;
    s.grant_when_idle = 1; s.action_duration = 9240000LL;
    result = dld_admission_wait(&ops, &s, 0);
    CHECK(result == 0 && s.grants == 1 && s.now > DLD_ADMISSION_TIMEOUT_NS,
          "a bank admitted before deadline keeps its independent completion rule");
    CHECK(!s.failure && s.first_grant < DLD_ADMISSION_TIMEOUT_NS,
          "crossing admission deadline does not reopen a completed bank");

    printf("PASS admission policy: %u checks; offline fake clock and hardware actions\n", checks);
    return 0;
}
