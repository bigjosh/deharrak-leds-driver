/* AM335x / Linux 3.8 Cortex-A8 bank-gated quiet-window helper. */
#include <linux/module.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
#include <linux/io.h>
#include <linux/cpu.h>
#include <linux/cpufreq.h>
#include <linux/perf_event.h>
#include <linux/ktime.h>
#include <linux/delay.h>
#include <linux/sched.h>
#include <linux/capability.h>
#include <linux/netdevice.h>
#include <linux/rtnetlink.h>
#include <linux/pm_runtime.h>
#include <linux/ratelimit.h>
#include <net/net_namespace.h>
#include <asm/cputype.h>
#include <asm/irqflags.h>

#include "dld_abi.h"
#include "dld_profiles.h"
#include "dld_quiet.h"
#include "dld_admission.h"

#define PRUSS_PHYS 0x4a300000UL
#define CPSW_PHYS 0x4a100000UL
#define CLOCK_PHYS 0x44e00000UL
#define PRU_CONTROL_PHYS 0x4a322000UL
#define CPSW_CLOCK 0x014
#define PRUSS_CLOCK 0x0e8
#define CPSW_DMA_CONTROL 0x820
#define CPSW_DMA_STATUS 0x824
#define CPSW_COMMAND_IDLE (1U << 3)
#define CPSW_IDLE (1U << 31)
/* DMASTATUS RX_ERR_CODE[15:12], TX_ERR_CODE[23:20]. */
#define CPSW_ERROR_MASK 0x00f0f000U
#define REQUIRED_CPU_KHZ 1000000U
#define CPU_CYCLES_PER_PRU_CYCLE (REQUIRED_CPU_KHZ * 1000U / DLD_PRU_HZ)
#define CPU_CYCLES_PER_PIXEL (24U * DLD_BIT_CYCLES * CPU_CYCLES_PER_PRU_CYCLE)
#define GATE_TIMEOUT_NS 20000000LL
#define FINAL_TIMEOUT_NS 20000000LL
#define DMA_DRAIN_CYCLES 500000U
#define BANK_GUARD_CYCLES 600000U
#define BANK_MAX_CYCLES 10000000U
#define TIMER_ITERATION_CAP 30000000U
#define TIMER_FAULT 0x80000000U
#define GRANT_WITHHELD 0x40000000U

static DEFINE_MUTEX(send_mutex);
static void __iomem *mailbox;
static void __iomem *cpsw;
static void __iomem *clocks;
static void __iomem *pru_control;
static void __iomem *gpio[3];
static struct perf_event *cycle_event;
static const unsigned long gpio_physical[3] = { 0x44e07000UL, 0x4804c000UL, 0x481ac000UL };
static const u32 gpio_clocks[3] = { 0x408, 0xac, 0xb0 };
static const u32 gpio_masks[3] = { 1U << 26, (1U << 12) | (1U << 14), (1U << 1) | (1U << 3) | (1U << 4) };

/* These fixed AM335x resources are read only. The clock state is checked
 * BEFORE reading a controller: a powered/gated register access can abort.
 * Each busy mask excludes reserved bits and buffer-available indicators.
 */
struct idle_engine {
    const char *name;
    unsigned long physical;
    u32 clock_offset;
    u32 status_offset;
    u32 busy_mask;
    void __iomem *base;
};
static struct idle_engine engines[] = {
    { "MMC0", 0x48060000UL, 0x03c, 0x224, 0x307, NULL },
    { "MMC1", 0x481d8000UL, 0x0f4, 0x224, 0x307, NULL },
    { "EDMA_CC", 0x49000000UL, 0x0bc, 0x640, 0x00073f17, NULL },
    { "EDMA_TC0", 0x49800000UL, 0x024, 0x100, 0x77, NULL },
    { "EDMA_TC1", 0x49900000UL, 0x0fc, 0x100, 0x77, NULL },
    { "EDMA_TC2", 0x49a00000UL, 0x100, 0x100, 0x77, NULL },
};

extern u64 dld_quiet_bank_entry(void __iomem *command, u32 ordinal,
                                u32 cycles, u32 iteration_cap);

static inline u32 ccnt(void)
{
    u32 value;
    asm volatile("mrc p15, 0, %0, c9, c13, 0" : "=r"(value));
    return value;
}

static inline void drain_writes(void)
{
    asm volatile("dsb sy" ::: "memory");
}

static bool mailbox_functional(void)
{
    return (readl(clocks + PRUSS_CLOCK) & 0x30003U) == 2U;
}

static bool pmu_ready(void)
{
    u32 control, enabled, before, after;
    if (!cycle_event || ACCESS_ONCE(cycle_event->state) != PERF_EVENT_STATE_ACTIVE ||
        ACCESS_ONCE(cycle_event->hw.idx) != 0)
        return false;
    asm volatile("mrc p15, 0, %0, c9, c12, 0" : "=r"(control));
    asm volatile("mrc p15, 0, %0, c9, c12, 1" : "=r"(enabled));
    if (!(control & 1U) || (control & 8U) || !(enabled & (1U << 31)))
        return false; /* disabled or divide-by-64 is inadmissible */
    before = ccnt();
    asm volatile(".rept 32\n\tnop\n\t.endr" ::: "memory");
    after = ccnt();
    return after != before;
}

static u32 bank_length(const struct dld_quiet_send *request, unsigned ordinal)
{
    const u32 *n = request->string_lengths;
    if (ordinal == DLD_BANK_GPIO2) return max(n[0], max(n[1], n[5]));
    if (ordinal == DLD_BANK_GPIO1) return max(n[2], n[4]);
    return n[3];
}

static u32 wire_color(u32 profile, u32 rgb)
{
    if (profile == DLD_PROFILE_WS2812B)
        return ((rgb & 0xff0000) >> 8) | ((rgb & 0x00ff00) << 8) | (rgb & 0xff);
    if (profile == DLD_PROFILE_WS2811_HS) return rgb;
    return ((rgb & 0xff) << 16) | (rgb & 0xff00) | ((rgb >> 16) & 0xff);
}

static void snapshot(struct dld_quiet_send *request)
{
    request->request_seq = readl(mailbox + DLD_OFF_REQUEST_SEQ);
    request->completion_seq = readl(mailbox + DLD_OFF_COMPLETION_SEQ);
    request->status = readl(mailbox + DLD_OFF_STATUS);
    request->error_detail = readl(mailbox + DLD_OFF_ERROR_DETAIL);
    request->accepted_seq = readl(mailbox + DLD_OFF_ACCEPTED_SEQ);
    request->bank_ready = readl(mailbox + DLD_OFF_BANK_READY);
    request->bank_grant = readl(mailbox + DLD_OFF_BANK_GRANT);
    request->bank_done = readl(mailbox + DLD_OFF_BANK_DONE);
}

static int validate(struct dld_quiet_send *request)
{
    unsigned i, last_bank = 0;
    if (request->api_version != DLD_QUIET_API_VERSION || request->rgb > 0xffffff ||
        request->reserved[0] || request->reserved[1] ||
        request->profile_id < DLD_PROFILE_WS2812B || request->profile_id > DLD_PROFILE_WS2811_HS_BGR)
        return -EINVAL;
    for (i = 0; i < DLD_STRING_COUNT; i++)
        if (request->string_lengths[i] > DLD_MAX_LENGTH) return -EINVAL;
    if (readl(mailbox + DLD_OFF_MAGIC) != DLD_COMMAND_MAGIC ||
        readl(mailbox + DLD_OFF_ABI_VERSION) != DLD_ABI_VERSION ||
        readl(mailbox + DLD_OFF_PROFILE_ID) != request->profile_id)
        return -EPROTO;
    for (i = 0; i < DLD_STRING_COUNT; i++)
        if (readl(mailbox + DLD_OFF_LENGTHS + 4 * i) != request->string_lengths[i])
            return -EPROTO;
    snapshot(request);
    if (request->error_detail || request->status == DLD_STATUS_ERROR) return -EPROTO;
    if (request->status == DLD_STATUS_RUNNING || request->status == DLD_STATUS_WAIT_BANK ||
        request->request_seq != request->completion_seq)
        return -EBUSY;
    if ((request->status != DLD_STATUS_READY && request->status != DLD_STATUS_DONE) ||
        request->request_seq != request->previous_seq)
        return -EPROTO;
    for (i = 1; i <= 3; i++) if (bank_length(request, i)) last_bank = i;
    if (request->bank_ready) return -EPROTO;
    if (request->status == DLD_STATUS_READY) {
        if (request->request_seq || request->accepted_seq || request->bank_grant || request->bank_done)
            return -EPROTO;
    } else if (request->accepted_seq != request->request_seq ||
               request->bank_grant != last_bank || request->bank_done != last_bank) {
        return -EPROTO;
    }
    return 0;
}

static bool fixed_cpu(struct cpufreq_policy *policy)
{
    return num_online_cpus() == 1 && cpu_online(0) && policy &&
        ACCESS_ONCE(policy->min) == REQUIRED_CPU_KHZ &&
        ACCESS_ONCE(policy->max) == REQUIRED_CPU_KHZ &&
        ACCESS_ONCE(policy->cur) == REQUIRED_CPU_KHZ;
}

static int other_dma_idle(struct dld_quiet_send *request)
{
    unsigned pass, i;
    /* First admit MMC, then CC/TC; repeat to reject activity appearing while
     * those observations were made. In the IRQ/FIQ-off single-core window no
     * Linux thread/callback can submit a new controller request in between.
     */
    for (pass = 0; pass < 2; pass++) {
        for (i = 0; i < ARRAY_SIZE(engines); i++) {
            struct idle_engine *engine = &engines[i];
            u32 clock = readl(clocks + engine->clock_offset);
            u32 mode = clock & 3U;
            u32 idle = (clock >> 16) & 3U;
            u32 status;
            if (mode == 0 && idle == 3 &&
                (i < 3 || (clock & (1U << 18)))) continue; /* TC also standby */
            if (mode != 2 || idle != 0) {
                request->blocked_engine = i + 2;
                request->blocked_status = clock;
                return -EAGAIN; /* transitional/unproven; do not touch block */
            }
            status = readl(engine->base + engine->status_offset);
            if (status & engine->busy_mask) {
                request->blocked_engine = i + 2;
                request->blocked_status = status;
                return -EAGAIN;
            }
        }
    }
    request->blocked_engine = request->blocked_status = 0;
    return 0;
}

static long long admission_now(void *context)
{
    (void)context;
    return ktime_to_ns(ktime_get());
}

static void admission_pause(void *context)
{
    (void)context;
    usleep_range(100, 200); /* IRQs enabled: pending DMA completions run. */
}

static int other_dma_attempt(void *context, int *granted)
{
    struct dld_quiet_send *request = context;
    unsigned long flags;
    int error;
    *granted = 0;
    /* Runtime-PM callbacks cannot gate a block between clock qualification
     * and its controller read. Restore CPU state before every pause. */
    preempt_disable();
    local_irq_save(flags);
    local_fiq_disable();
    error = other_dma_idle(request);
    local_irq_restore(flags);
    preempt_enable();
    return error;
}

static int wait_other_dma(struct dld_quiet_send *request)
{
    static const struct dld_admission_ops ops = {
        admission_now, other_dma_attempt, admission_pause
    };
    return dld_admission_wait(&ops, request, 1);
}

static int wait_gate(struct dld_quiet_send *request, unsigned ordinal, bool final)
{
    s64 deadline = ktime_to_ns(ktime_get()) + (final ? FINAL_TIMEOUT_NS : GATE_TIMEOUT_NS);
    unsigned attempts;
    for (attempts = 0; attempts < 200; attempts++) {
        u32 status, error, accepted, ready, completed;
        bool match;
        unsigned long flags;
        if (ktime_to_ns(ktime_get()) >= deadline) return -ETIMEDOUT;
        preempt_disable();
        local_irq_save(flags);
        local_fiq_disable();
        if (!mailbox_functional()) {
            local_irq_restore(flags);
            preempt_enable();
            return -ENODEV;
        }
        status = readl(mailbox + DLD_OFF_STATUS);
        error = readl(mailbox + DLD_OFF_ERROR_DETAIL);
        accepted = readl(mailbox + DLD_OFF_ACCEPTED_SEQ);
        if (final) {
            completed = readl(mailbox + DLD_OFF_COMPLETION_SEQ);
            match = status == DLD_STATUS_DONE && completed == request->request_seq &&
                    accepted == request->request_seq;
        } else {
            ready = readl(mailbox + DLD_OFF_BANK_READY);
            match = status == DLD_STATUS_WAIT_BANK && ready == ordinal &&
                    accepted == request->request_seq;
        }
        local_irq_restore(flags);
        preempt_enable();
        if (error || status == DLD_STATUS_ERROR) return -EIO;
        if (match) return ktime_to_ns(ktime_get()) < deadline ? 0 : -ETIMEDOUT;
        usleep_range(100, 200);
    }
    return -ETIMEDOUT;
}

static int try_bank(struct dld_quiet_send *request, struct cpufreq_policy *policy, unsigned ordinal)
{
    unsigned index = ordinal - 1, attempts;
    unsigned long flags;
    u32 saved_control = 0, status = 0, start, drain_start, elapsed, observed;
    u64 result;
    int error = 0;
    bool changed_dma = false;

    request->stage = DLD_QUIET_STAGE_DMA;
    preempt_disable();
    local_irq_save(flags); /* saves original CPSR, including its FIQ bit */
    local_fiq_disable();
    start = ccnt();
    if (!fixed_cpu(policy)) {
        request->stage = DLD_QUIET_STAGE_CPU;
        error = -ERANGE;
        goto restore;
    }
    if (!pmu_ready()) {
        request->stage = DLD_QUIET_STAGE_PMU;
        error = -ENODEV;
        goto restore;
    }
    if (!mailbox_functional()) { error = -ENODEV; goto restore; }
    error = other_dma_idle(request);
    if (error) goto restore;
    status = readl(clocks + CPSW_CLOCK);
    if ((status & 0x30003U) != 2U) {
        request->blocked_engine = 1;
        request->blocked_status = status;
        error = -EAGAIN;
        goto restore;
    }
    saved_control = readl(cpsw + CPSW_DMA_CONTROL);
    if (saved_control & CPSW_COMMAND_IDLE) {
        request->blocked_engine = 1;
        request->blocked_status = saved_control;
        error = -EBUSY;
        goto restore;
    }
    writel(saved_control | CPSW_COMMAND_IDLE, cpsw + CPSW_DMA_CONTROL);
    changed_dma = true;
    drain_writes();
    if (readl(cpsw + CPSW_DMA_CONTROL) != (saved_control | CPSW_COMMAND_IDLE)) {
        error = -EIO;
        goto restore;
    }
    drain_start = ccnt();
    for (attempts = 0; attempts < 100000; attempts++) {
        status = readl(cpsw + CPSW_DMA_STATUS);
        /* Reading STATUS clears error-channel fields. Preserve the complete
         * first error observation and refuse the bank rather than hiding it.
         */
        if (status & CPSW_ERROR_MASK) { error = -EIO; break; }
        if (status & CPSW_IDLE) break;
        if ((u32)(ccnt() - drain_start) >= DMA_DRAIN_CYCLES) { error = -ETIMEDOUT; break; }
    }
    request->dma_status[index] = status;
    request->dma_drain_cycles[index] = ccnt() - drain_start;
    if (attempts == 100000) error = -ETIMEDOUT;
    if (error || !(status & CPSW_IDLE)) {
        request->blocked_engine = 1;
        request->blocked_status = status;
        if (!error) error = -ETIMEDOUT;
        goto restore;
    }
    error = other_dma_idle(request);
    if (error) goto restore;
    request->stage = DLD_QUIET_STAGE_BANK;
    result = dld_quiet_bank_entry(mailbox, ordinal, request->budget_cycles[index], TIMER_ITERATION_CAP);
    observed = (u32)result;
    elapsed = (u32)(result >> 32);
    request->elapsed_cycles[index] = elapsed & ~(TIMER_FAULT | GRANT_WITHHELD);
    if (!(elapsed & GRANT_WITHHELD)) request->granted_mask |= 1U << index;
    request->bank_done = observed;
    if (elapsed & TIMER_FAULT) {
        request->timer_fault_mask |= 1U << index;
        error = -ETIME;
    } else if (observed != ordinal) {
        error = -ETIMEDOUT; /* exactly one post-spin observation, no grace */
    } else {
        request->completed_mask |= 1U << index;
    }
restore:
    if (error && (request->granted_mask & (1U << index)) && mailbox_functional()) {
        /* Immediately withdraw PRU execution before resuming CPSW. Full
         * bounded stop/reset and all-bank fail-low follow with IRQs enabled. */
        writel(1, pru_control);
        writel(0, mailbox + DLD_OFF_MAGIC);
        drain_writes();
    }
    if (changed_dma) {
        writel(saved_control, cpsw + CPSW_DMA_CONTROL);
        drain_writes();
        if (readl(cpsw + CPSW_DMA_CONTROL) != saved_control) {
            request->dma_restore_failed_mask |= 1U << index;
            error = -EIO; /* a failed restore must never take EAGAIN retry */
        }
        drain_writes();
    }
    request->irq_off_cycles[index] = ccnt() - start;
    /* ARM arch_local_irq_restore writes the saved CPSR control byte, restoring
     * BOTH IRQ and FIQ masks exactly. Never unconditionally enable either.
     */
    local_irq_restore(flags);
    preempt_enable();
    return error;
}

struct bank_admission_context {
    struct dld_quiet_send *request;
    struct cpufreq_policy *policy;
    unsigned ordinal;
};

static int bank_admission_attempt(void *context, int *granted)
{
    struct bank_admission_context *bank = context;
    int error = try_bank(bank->request, bank->policy, bank->ordinal);
    *granted = !!(bank->request->granted_mask & (1U << (bank->ordinal - 1)));
    return error;
}

static int run_bank(struct dld_quiet_send *request, struct cpufreq_policy *policy, unsigned ordinal)
{
    static const struct dld_admission_ops ops = {
        admission_now, bank_admission_attempt, admission_pause
    };
    struct bank_admission_context context = { request, policy, ordinal };
    return dld_admission_wait(&ops, &context, 0);
}

static void failed_frame_cleanup(struct dld_quiet_send *request)
{
    s64 deadline;
    unsigned pending = 3, attempt, core, bank;
    unsigned long flags;
    /* Disable both owned cores before polling either. SOFT_RST_N stays high
     * until RUNSTATE confirms completion of any in-flight instruction. */
    preempt_disable();
    local_irq_save(flags);
    local_fiq_disable();
    if (mailbox_functional()) {
        writel(0, mailbox + DLD_OFF_MAGIC);
        writel(1, pru_control);
        writel(1, pru_control + 0x2000);
        drain_writes();
    } else {
        request->cleanup_failed_mask |= 1U;
    }
    local_irq_restore(flags);
    preempt_enable();
    deadline = ktime_to_ns(ktime_get()) + GATE_TIMEOUT_NS;
    for (attempt = 0; attempt < 200 && pending; attempt++) {
        if (ktime_to_ns(ktime_get()) >= deadline) break;
        preempt_disable();
        local_irq_save(flags);
        local_fiq_disable();
        if (!mailbox_functional()) {
            local_irq_restore(flags);
            preempt_enable();
            break;
        }
        for (core = 0; core < 2; core++) {
            if (!(pending & (1U << core))) continue;
            if (!(readl(pru_control + core * 0x2000) & (1U << 15))) {
                writel(0, pru_control + core * 0x2000);
                pending &= ~(1U << core);
                request->cleanup_stopped_mask |= 1U << core;
            }
        }
        drain_writes();
        local_irq_restore(flags);
        preempt_enable();
        if (pending) usleep_range(100, 200);
    }
    if (pending) request->cleanup_failed_mask |= 1U;
    /* Do not gate/enable clocks behind the GPIO driver. Init established all
     * outputs; attempt every functional bank even if another one failed. */
    preempt_disable();
    local_irq_save(flags);
    local_fiq_disable();
    for (bank = 0; bank < 3; bank++) {
        if ((readl(clocks + gpio_clocks[bank]) & 0x30003U) != 2U) {
            request->cleanup_failed_mask |= 1U << (bank + 1);
            continue;
        }
        for (attempt = 0; attempt < 8; attempt++) {
            writel(gpio_masks[bank], gpio[bank] + 0x190);
            drain_writes();
            if (!(readl(gpio[bank] + 0x13c) & gpio_masks[bank])) break;
        }
        if (attempt < 8) request->cleanup_low_mask |= 1U << bank;
        else request->cleanup_failed_mask |= 1U << (bank + 1);
    }
    local_irq_restore(flags);
    preempt_enable();
}

static int perform_send(struct dld_quiet_send *request)
{
    struct cpufreq_policy *policy = NULL;
    struct net_device *net = NULL;
    struct device *power_device = NULL;
    bool online_held = false, rtnl_held = false, power_held = false;
    unsigned ordinal;
    unsigned long flags;
    int error;
    request->stage = DLD_QUIET_STAGE_VALIDATE;
    if (!mutex_trylock(&send_mutex)) return -EBUSY;
    get_online_cpus();
    online_held = true;
    if (num_online_cpus() != 1 || !cpu_online(0)) { error = -ENODEV; goto done; }
    policy = cpufreq_cpu_get(0);
    request->stage = DLD_QUIET_STAGE_CPU;
    if (!fixed_cpu(policy)) { error = -ERANGE; goto done; }
    request->cpu_khz = REQUIRED_CPU_KHZ;
    request->stage = DLD_QUIET_STAGE_PMU;
    preempt_disable();
    error = pmu_ready() ? 0 : -ENODEV;
    preempt_enable();
    if (error) goto done;
    request->stage = DLD_QUIET_STAGE_VALIDATE;
    preempt_disable();
    local_irq_save(flags);
    local_fiq_disable();
    error = mailbox_functional() ? validate(request) : -ENODEV;
    local_irq_restore(flags);
    preempt_enable();
    if (error) goto done;
    if (!rtnl_trylock()) { error = -EBUSY; goto done; }
    rtnl_held = true;
    net = dev_get_by_name(&init_net, "eth0");
    if (!net || !netif_running(net) || !net->dev.parent ||
        strcmp(dev_driver_string(net->dev.parent), "cpsw")) {
        error = -ENODEV;
        goto done;
    }
    power_device = net->dev.parent;
    error = pm_runtime_get_sync(power_device);
    power_held = true; /* get increments usage even on an error */
    if (error < 0) goto done;
    error = 0;
    error = wait_other_dma(request);
    if (error) goto done;
    if (signal_pending(current)) { error = -EINTR; goto done; }
    for (ordinal = 1; ordinal <= 3; ordinal++) {
        u32 length = bank_length(request, ordinal);
        /* 24 * 1200 ns per pixel at a fixed 1 GHz, plus 600 us guard.
         * Maximum 300-pixel budget is 9.24 ms, before <=0.5 ms DMA drain.
         */
        request->budget_cycles[ordinal - 1] = length ? length * CPU_CYCLES_PER_PIXEL + BANK_GUARD_CYCLES : 0;
        if (request->budget_cycles[ordinal - 1] >= BANK_MAX_CYCLES) { error = -ERANGE; goto done; }
    }
    request->stage = DLD_QUIET_STAGE_PUBLISH;
    request->request_seq = request->previous_seq + 1U;
    preempt_disable();
    local_irq_save(flags);
    local_fiq_disable();
    if (!mailbox_functional()) {
        local_irq_restore(flags);
        preempt_enable();
        error = -ENODEV;
        goto done;
    }
    writel(0, mailbox + DLD_OFF_BANK_GRANT);
    writel(wire_color(request->profile_id, request->rgb), mailbox + DLD_OFF_WIRE_COLOR);
    wmb();
    request->submitted = 1;
    writel(request->request_seq, mailbox + DLD_OFF_REQUEST_SEQ);
    drain_writes();
    local_irq_restore(flags);
    preempt_enable();
    /* Once submitted, finish this finite request even if its userspace sender
     * receives a fatal signal. There is no forced cancellation between banks.
     */
    for (ordinal = 1; ordinal <= 3; ordinal++) {
        if (!bank_length(request, ordinal)) continue;
        request->stage = DLD_QUIET_STAGE_GATE;
        error = wait_gate(request, ordinal, false);
        if (error) goto submitted_done;
        error = run_bank(request, policy, ordinal);
        if (error) goto submitted_done;
        cond_resched(); /* pending work may run while PRU waits at next gate */
    }
    request->stage = DLD_QUIET_STAGE_FINAL;
    error = wait_gate(request, 0, true);
    if (!error) request->stage = DLD_QUIET_STAGE_COMPLETE;
submitted_done:
    preempt_disable();
    local_irq_save(flags);
    local_fiq_disable();
    if (mailbox_functional())
        snapshot(request); /* diagnostics, never an additional completion grace */
    else if (!error) error = -ENODEV;
    local_irq_restore(flags);
    preempt_enable();
    if (error) failed_frame_cleanup(request);
done:
    /* All bank and cleanup helpers have restored the caller's saved CPU
     * masks/preemption state before reaching here. Keep recovery failures
     * observable even when SIGKILL or bad result memory prevents copyout.
     * No printk or ratelimit work occurs inside a protected bank window. */
    if (request->cleanup_failed_mask || request->dma_restore_failed_mask)
        pr_err_ratelimited("dld-quiet: request=%u result=%d stage=%u recovery incomplete: "
                           "cleanup_failed=0x%x dma_restore_failed=0x%x "
                           "stopped=0x%x low=0x%x; explicit operator recovery required\n",
                           request->request_seq, error, request->stage,
                           request->cleanup_failed_mask, request->dma_restore_failed_mask,
                           request->cleanup_stopped_mask, request->cleanup_low_mask);
    if (power_held) pm_runtime_put(power_device);
    if (net) dev_put(net);
    if (rtnl_held) rtnl_unlock();
    if (policy) cpufreq_cpu_put(policy);
    if (online_held) put_online_cpus();
    mutex_unlock(&send_mutex);
    return error;
}

static long quiet_ioctl(struct file *file, unsigned int command, unsigned long argument)
{
    struct dld_quiet_send request;
    void __user *user = (void __user *)argument;
    (void)file;
    if (command != DLD_QUIET_IOCTL_SEND) return -ENOTTY;
    if (!capable(CAP_SYS_RAWIO)) return -EPERM;
    if (copy_from_user(&request, user, sizeof(request))) return -EFAULT;
    memset((char *)&request + offsetof(struct dld_quiet_send, result), 0,
           sizeof(request) - offsetof(struct dld_quiet_send, result));
    request.result = perform_send(&request);
    if (copy_to_user(user, &request, sizeof(request))) return -EFAULT;
    return 0;
}

static const struct file_operations quiet_fops = {
    .owner = THIS_MODULE,
    .unlocked_ioctl = quiet_ioctl,
    .llseek = no_llseek,
};
static struct miscdevice quiet_device = {
    .minor = MISC_DYNAMIC_MINOR,
    .name = "dld-quiet",
    .fops = &quiet_fops,
    .mode = 0600,
};

static void release_resources(void)
{
    unsigned i;
    if (cycle_event) { perf_event_release_kernel(cycle_event); cycle_event = NULL; }
    for (i = 0; i < ARRAY_SIZE(engines); i++)
        if (engines[i].base) { iounmap(engines[i].base); engines[i].base = NULL; }
    for (i = 0; i < 3; i++)
        if (gpio[i]) { iounmap(gpio[i]); gpio[i] = NULL; }
    if (pru_control) { iounmap(pru_control); pru_control = NULL; }
    if (clocks) { iounmap(clocks); clocks = NULL; }
    if (cpsw) { iounmap(cpsw); cpsw = NULL; }
    if (mailbox) { iounmap(mailbox); mailbox = NULL; }
}

static int __init quiet_init(void)
{
    struct perf_event_attr attr;
    unsigned i;
    int error = -ENODEV;
    BUILD_BUG_ON(DLD_ABI_VERSION != 4);
    BUILD_BUG_ON(DLD_COMMAND_BYTES != 72);
    BUILD_BUG_ON((REQUIRED_CPU_KHZ * 1000U) % DLD_PRU_HZ);
    BUILD_BUG_ON(DLD_MAX_LENGTH * CPU_CYCLES_PER_PIXEL + BANK_GUARD_CYCLES >= BANK_MAX_CYCLES);
    BUILD_BUG_ON((DLD_RESET_CYCLES + DLD_MAX_LENGTH * DLD_PROPAGATION_CYCLES +
                  DLD_SETTLE_MARGIN_CYCLES) * CPU_CYCLES_PER_PRU_CYCLE >=
                 FINAL_TIMEOUT_NS); /* 1 GHz: one CPU cycle per ns */
    get_online_cpus();
    if (num_online_cpus() != 1 || !cpu_online(0) ||
        (read_cpuid_id() & 0xff0ffff0U) != 0x410fc080U)
        goto fail;
    memset(&attr, 0, sizeof(attr));
    attr.type = PERF_TYPE_HARDWARE;
    attr.size = sizeof(attr);
    attr.config = PERF_COUNT_HW_CPU_CYCLES;
    attr.pinned = 1;
    attr.exclusive = 1;
    cycle_event = perf_event_create_kernel_counter(&attr, 0, NULL, NULL, NULL);
    if (IS_ERR(cycle_event)) { error = PTR_ERR(cycle_event); cycle_event = NULL; goto fail; }
    preempt_disable();
    error = pmu_ready() ? 0 : -EBUSY;
    preempt_enable();
    if (error) {
        pr_err("dld-quiet: exclusive CPU cycle counter unavailable (check PMU/watchdog ownership)\n");
        goto fail;
    }
    mailbox = ioremap_nocache(PRUSS_PHYS, PAGE_SIZE);
    cpsw = ioremap_nocache(CPSW_PHYS, PAGE_SIZE);
    clocks = ioremap_nocache(CLOCK_PHYS, PAGE_SIZE);
    pru_control = ioremap_nocache(PRU_CONTROL_PHYS, 0x3000);
    if (!mailbox || !cpsw || !clocks || !pru_control) { error = -ENOMEM; goto fail; }
    for (i = 0; i < 3; i++) {
        gpio[i] = ioremap_nocache(gpio_physical[i], PAGE_SIZE);
        if (!gpio[i]) { error = -ENOMEM; goto fail; }
    }
    for (i = 0; i < ARRAY_SIZE(engines); i++) {
        engines[i].base = ioremap_nocache(engines[i].physical, PAGE_SIZE);
        if (!engines[i].base) { error = -ENOMEM; goto fail; }
    }
    error = misc_register(&quiet_device);
    if (error) goto fail;
    put_online_cpus();
    pr_info("dld-quiet: ABI4 bank gates, exclusive PMU, IRQ/FIQ masks, CPSW idle and MMC/EDMA checks\n");
    return 0;
fail:
    release_resources();
    put_online_cpus();
    return error;
}

static void __exit quiet_exit(void)
{
    misc_deregister(&quiet_device);
    release_resources();
}

module_init(quiet_init);
module_exit(quiet_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("AM335x DLD bounded per-bank protected PRU command helper");
MODULE_AUTHOR("DLD project");
