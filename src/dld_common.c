#define _POSIX_C_SOURCE 200809L
#define _GNU_SOURCE
#include "dld_common.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

static int fail(char *error, size_t cap, const char *format, ...)
{
    va_list args;
    va_start(args, format);
    if (cap != 0) vsnprintf(error, cap, format, args);
    va_end(args);
    return -1;
}

static int hex_digit(unsigned char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

int dld_parse_color(const char *text, uint32_t *rgb)
{
    uint32_t value = 0;
    size_t i;
    if (!text || !rgb) return -1;
    if (text[0] == '0' && (text[1] == 'x' || text[1] == 'X')) text += 2;
    if (strlen(text) != 6) return -1;
    for (i = 0; i < 6; ++i) {
        int digit = hex_digit((unsigned char)text[i]);
        if (digit < 0) return -1;
        value = (value << 4) | (uint32_t)digit;
    }
    *rgb = value;
    return 0;
}

const char *dld_profile_name(uint32_t profile_id)
{
    switch (profile_id) {
    case DLD_PROFILE_WS2812B: return "ws2812b";
    case DLD_PROFILE_WS2811_HS: return "ws2811-hs";
    case DLD_PROFILE_WS2812B_BGR: return "ws2812b-bgr";
    case DLD_PROFILE_WS2811_HS_BGR: return "ws2811-hs-bgr";
    default: return NULL;
    }
}

uint32_t dld_wire_color(uint32_t profile_id, uint32_t rgb)
{
    rgb &= UINT32_C(0xffffff);
    if (profile_id == DLD_PROFILE_WS2812B)
        return ((rgb & UINT32_C(0xff00)) << 8) |
               ((rgb & UINT32_C(0xff0000)) >> 8) | (rgb & 0xffu);
    if (profile_id == DLD_PROFILE_WS2812B_BGR || profile_id == DLD_PROFILE_WS2811_HS_BGR)
        return ((rgb & UINT32_C(0xff)) << 16) | (rgb & UINT32_C(0xff00)) |
               ((rgb >> 16) & UINT32_C(0xff));
    return rgb;
}

struct json_reader {
    const unsigned char *text;
    size_t size;
    size_t pos;
    char *error;
    size_t cap;
};

static int json_fail(struct json_reader *r, const char *message)
{
    return fail(r->error, r->cap, "configuration byte %lu: %s",
                (unsigned long)r->pos, message);
}

static void whitespace(struct json_reader *r)
{
    while (r->pos < r->size) {
        unsigned char c = r->text[r->pos];
        if (c != ' ' && c != '\t' && c != '\r' && c != '\n') break;
        ++r->pos;
    }
}

static int token(struct json_reader *r, unsigned char wanted)
{
    whitespace(r);
    if (r->pos == r->size || r->text[r->pos] != wanted)
        return json_fail(r, "unexpected token");
    ++r->pos;
    return 0;
}

/* The schema has only ASCII field/profile names. Decode JSON escapes (including
 * \u00xx) before comparing names; embedded NUL/non-ASCII names cannot match.
 * Rejecting them here is equivalent to rejecting an unknown schema name and
 * avoids truncation, malformed UTF-8 and surrogate ambiguities.
 */
static int json_name(struct json_reader *r, char *out, size_t cap)
{
    size_t length = 0;
    if (token(r, '"') < 0) return -1;
    while (r->pos < r->size) {
        unsigned c = r->text[r->pos++];
        if (c == '"') {
            out[length] = '\0';
            return 0;
        }
        if (c < 0x20) return json_fail(r, "unescaped control character");
        if (c == '\\') {
            unsigned k;
            if (r->pos == r->size) return json_fail(r, "incomplete escape");
            c = r->text[r->pos++];
            switch (c) {
            case '"': case '\\': case '/': break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case 'n': c = '\n'; break;
            case 'r': c = '\r'; break;
            case 't': c = '\t'; break;
            case 'u':
                c = 0;
                for (k = 0; k < 4; ++k) {
                    int h;
                    if (r->pos == r->size)
                        return json_fail(r, "incomplete Unicode escape");
                    h = hex_digit(r->text[r->pos++]);
                    if (h < 0) return json_fail(r, "invalid Unicode escape");
                    c = (c << 4) | (unsigned)h;
                }
                break;
            default: return json_fail(r, "invalid string escape");
            }
        }
        if (c == 0 || c >= 0x80)
            return json_fail(r, "unsupported field or profile name");
        if (length + 1 >= cap) return json_fail(r, "unknown name is too long");
        out[length++] = (char)c;
    }
    return json_fail(r, "unterminated string");
}

static int json_length(struct json_reader *r, uint32_t *out)
{
    uint32_t value = 0;
    int negative = 0;
    whitespace(r);
    if (r->pos < r->size && r->text[r->pos] == '-') {
        negative = 1;
        ++r->pos;
    }
    if (r->pos == r->size || r->text[r->pos] < '0' || r->text[r->pos] > '9')
        return json_fail(r, "length must be a JSON integer from 0 through 300");
    if (r->text[r->pos] == '0') {
        ++r->pos;
        if (r->pos < r->size && r->text[r->pos] >= '0' && r->text[r->pos] <= '9')
            return json_fail(r, "leading zero in integer");
    } else {
        do {
            value = value * 10u + (unsigned)(r->text[r->pos++] - '0');
            if (value > DLD_MAX_LENGTH) return json_fail(r, "length exceeds 300");
        } while (r->pos < r->size && r->text[r->pos] >= '0' && r->text[r->pos] <= '9');
    }
    if (negative && value != 0) return json_fail(r, "negative string length");
    if (r->pos < r->size && (r->text[r->pos] == '.' || r->text[r->pos] == 'e' || r->text[r->pos] == 'E'))
        return json_fail(r, "length must be an integer, not a fraction or exponent");
    *out = value;
    return 0;
}

int dld_parse_config(const char *json, size_t length,
                     struct dld_config *config, char *error, size_t cap)
{
    struct json_reader r;
    struct dld_config parsed;
    unsigned seen = 0;
    if (!json || !config) return fail(error, cap, "missing configuration text");
    r.text = (const unsigned char *)json;
    r.size = length; r.pos = 0; r.error = error; r.cap = cap;
    memset(&parsed, 0, sizeof(parsed));
    if (token(&r, '{') < 0) return -1;
    for (;;) {
        char name[32];
        whitespace(&r);
        if (r.pos < r.size && r.text[r.pos] == '}') { ++r.pos; break; }
        if (json_name(&r, name, sizeof(name)) < 0 || token(&r, ':') < 0) return -1;
        if (strcmp(name, "pixel_type") == 0) {
            char profile[32];
            if (seen & 1u) return json_fail(&r, "duplicate pixel_type field");
            seen |= 1u;
            if (json_name(&r, profile, sizeof(profile)) < 0) return -1;
            if (strcmp(profile, "ws2812b") == 0) parsed.profile_id = DLD_PROFILE_WS2812B;
            else if (strcmp(profile, "ws2811-hs") == 0) parsed.profile_id = DLD_PROFILE_WS2811_HS;
            else if (strcmp(profile, "ws2812b-bgr") == 0) parsed.profile_id = DLD_PROFILE_WS2812B_BGR;
            else if (strcmp(profile, "ws2811-hs-bgr") == 0) parsed.profile_id = DLD_PROFILE_WS2811_HS_BGR;
            else return json_fail(&r, "unknown pixel_type profile");
        } else if (strcmp(name, "string_lengths") == 0) {
            unsigned i;
            if (seen & 2u) return json_fail(&r, "duplicate string_lengths field");
            seen |= 2u;
            if (token(&r, '[') < 0) return -1;
            for (i = 0; i < DLD_STRING_COUNT; ++i) {
                if (i && token(&r, ',') < 0) return -1;
                if (json_length(&r, &parsed.string_lengths[i]) < 0) return -1;
            }
            if (token(&r, ']') < 0) return -1;
        } else return json_fail(&r, "unknown configuration field");
        whitespace(&r);
        if (r.pos < r.size && r.text[r.pos] == '}') { ++r.pos; break; }
        if (token(&r, ',') < 0) return -1;
        whitespace(&r);
        if (r.pos == r.size || r.text[r.pos] == '}') return json_fail(&r, "trailing comma");
    }
    whitespace(&r);
    if (r.pos != r.size) return json_fail(&r, "trailing content after JSON object");
    if (seen != 3u) return json_fail(&r, "pixel_type and string_lengths are required");
    *config = parsed;
    return 0;
}

int dld_read_config(const char *path, struct dld_config *config,
                    char *error, size_t cap)
{
    FILE *file = fopen(path, "rb");
    char *data = NULL;
    size_t length = 0, capacity = 4096;
    int result = DLD_PREREQUISITE;
    if (!file) {
        fail(error, cap, "cannot read configuration %s: %s", path, strerror(errno));
        return result;
    }
    data = malloc(capacity);
    if (!data) { fail(error, cap, "out of memory reading configuration"); goto done; }
    for (;;) {
        size_t count = fread(data + length, 1, capacity - length, file);
        length += count;
        if (ferror(file)) { fail(error, cap, "cannot read configuration: %s", strerror(errno)); goto done; }
        if (feof(file)) break;
        if (length == capacity) {
            char *grown;
            if (capacity > SIZE_MAX / 2) { fail(error, cap, "configuration is too large"); goto done; }
            capacity *= 2;
            grown = realloc(data, capacity);
            if (!grown) { fail(error, cap, "out of memory reading configuration"); goto done; }
            data = grown;
        }
    }
    result = dld_parse_config(data, length, config, error, cap) < 0 ? DLD_BAD_ARGUMENT : DLD_OK;
done:
    free(data);
    fclose(file);
    return result;
}

static uint32_t maximum(uint32_t a, uint32_t b) { return a > b ? a : b; }

void dld_bank_info(const struct dld_config *config, uint32_t masks[3],
                   uint32_t lengths[3])
{
    const uint32_t *l = config->string_lengths;
    masks[0] = (l[0] ? 1u << 3 : 0) | (l[1] ? 1u << 4 : 0) | (l[5] ? 1u << 1 : 0);
    masks[1] = (l[2] ? 1u << 12 : 0) | (l[4] ? 1u << 14 : 0);
    masks[2] = l[3] ? 1u << 26 : 0;
    lengths[0] = maximum(maximum(l[0], l[1]), l[5]);
    lengths[1] = maximum(l[2], l[4]);
    lengths[2] = l[3];
}

unsigned dld_group_count(const struct dld_config *config)
{
    uint32_t masks[3], lengths[3];
    dld_bank_info(config, masks, lengths);
    return (masks[0] != 0) + (masks[1] != 0) + (masks[2] != 0);
}

int dld_compute_budget(const struct dld_config *config, uint64_t cpu_hz,
                       struct dld_budget *budget, char *error, size_t cap)
{
    uint32_t masks[3], lengths[3], longest = 0;
    uint64_t settle_cycles, data_cycles, product, count;
    struct dld_budget computed;
    unsigned i;
    if (!config || !budget || !dld_profile_name(config->profile_id))
        return fail(error, cap, "unsupported timing profile");
    if (cpu_hz == 0 || cpu_hz > DLD_CPU_MAX_HZ)
        return fail(error, cap, "unsupported CPU frequency bound");
    for (i = 0; i < DLD_STRING_COUNT; ++i) {
        if (config->string_lengths[i] > DLD_MAX_LENGTH)
            return fail(error, cap, "unsupported string length");
        longest = maximum(longest, config->string_lengths[i]);
    }
    dld_bank_info(config, masks, lengths);
    settle_cycles = (uint64_t)DLD_RESET_CYCLES + DLD_SETTLE_MARGIN_CYCLES;
    if (longest > 0) settle_cycles += (uint64_t)(longest - 1) * DLD_PROPAGATION_CYCLES;
    data_cycles = UINT64_C(24) * DLD_BIT_CYCLES * (lengths[0] + lengths[1] + lengths[2]);
    /* Initial profiles use a 200 MHz PRU: conversion is exact (5 ns).
     * Keep upward rounding explicit so a profile/clock change stays safe.
     */
    computed.settle_ns = (settle_cycles * UINT64_C(1000000000) + DLD_PRU_HZ - 1) / DLD_PRU_HZ;
    computed.quiet_ns = ((data_cycles + settle_cycles) * UINT64_C(1000000000) + DLD_PRU_HZ - 1) / DLD_PRU_HZ;
    computed.quiet_ns += DLD_ACCEPTANCE_NS + DLD_CONTROL_NS + DLD_HOST_GUARD_NS;
    if (computed.quiet_ns > UINT64_MAX / cpu_hz)
        return fail(error, cap, "host spin multiplication overflow");
    product = computed.quiet_ns * cpu_hz;
    /* ceil division without overflowing product + denominator - 1. */
    count = product / (UINT64_C(1000000000) * DLD_SPIN_MIN_CYCLES);
    if (product % (UINT64_C(1000000000) * DLD_SPIN_MIN_CYCLES)) ++count;
    if (count == 0 || count > UINT32_MAX) return fail(error, cap, "host spin count overflow");
    computed.iterations = (uint32_t)count;
    *budget = computed;
    return 0;
}

int dld_check_mailbox(const struct dld_command *snapshot,
                      struct dld_config *config, char *error, size_t cap)
{
    unsigned i;
    struct dld_config checked;
    if (snapshot->magic != DLD_COMMAND_MAGIC || snapshot->abi_version != DLD_ABI_VERSION ||
        !dld_profile_name(snapshot->profile_id)) goto invalid;
    checked.profile_id = snapshot->profile_id;
    for (i = 0; i < DLD_STRING_COUNT; ++i) {
        if (snapshot->string_lengths[i] > DLD_MAX_LENGTH) goto invalid;
        checked.string_lengths[i] = snapshot->string_lengths[i];
    }
    if (snapshot->error_detail != 0 || snapshot->status == DLD_STATUS_ERROR) goto invalid;
    if (snapshot->status != DLD_STATUS_READY && snapshot->status != DLD_STATUS_DONE &&
        snapshot->status != DLD_STATUS_RUNNING && snapshot->status != DLD_STATUS_WAIT_BANK) goto invalid;
    if (snapshot->status == DLD_STATUS_RUNNING || snapshot->status == DLD_STATUS_WAIT_BANK ||
        snapshot->request_seq != snapshot->completion_seq) {
        fail(error, cap, "driver is busy: an outstanding request is not ready for another send");
        return DLD_BUSY;
    }
    if (snapshot->bank_ready != 0 || snapshot->bank_grant > DLD_BANK_GPIO0 ||
        snapshot->bank_done > DLD_BANK_GPIO0 || snapshot->accepted_seq != snapshot->completion_seq) goto invalid;
    if (snapshot->status == DLD_STATUS_READY) {
        if (snapshot->request_seq || snapshot->bank_done || snapshot->bank_grant) goto invalid;
    } else {
        uint32_t masks[3], lengths[3], last = 0;
        dld_bank_info(&checked, masks, lengths);
        for (i = 0; i < 3; ++i) if (lengths[i]) last = i + 1;
        if (snapshot->bank_done != last || snapshot->bank_grant != last) goto invalid;
    }
    *config = checked;
    return DLD_OK;
invalid:
    fail(error, cap, "initialized command block is missing, incompatible or invalid; run dld-init CONFIG_FILE");
    return DLD_NOT_INITIALIZED;
}

void dld_memory_barrier(void)
{
#if defined(__arm__)
    __asm__ __volatile__("dmb sy" ::: "memory");
#else
    /* Pure parser/protocol unit tests may be built on a non-ARM host. */
    __sync_synchronize();
#endif
}

void dld_snapshot(volatile const struct dld_command *command,
                   struct dld_command *snapshot)
{
    unsigned i;
    snapshot->status = command->status;
    dld_memory_barrier();
    snapshot->magic = command->magic;
    snapshot->abi_version = command->abi_version;
    snapshot->profile_id = command->profile_id;
    for (i = 0; i < DLD_STRING_COUNT; ++i) snapshot->string_lengths[i] = command->string_lengths[i];
    snapshot->wire_color = command->wire_color;
    snapshot->request_seq = command->request_seq;
    snapshot->completion_seq = command->completion_seq;
    snapshot->error_detail = command->error_detail;
    snapshot->bank_ready = command->bank_ready;
    snapshot->bank_grant = command->bank_grant;
    snapshot->bank_done = command->bank_done;
    snapshot->accepted_seq = command->accepted_seq;
}

void dld_read_completion(volatile const struct dld_command *command,
                         struct dld_completion *completion)
{
    completion->status = command->status;
    dld_memory_barrier();
    completion->completion_seq = command->completion_seq;
    completion->error_detail = command->error_detail;
}

int dld_completion_matches(const struct dld_completion *completion,
                            uint32_t request_seq)
{
    return completion->status == DLD_STATUS_DONE && completion->error_detail == 0 &&
            completion->completion_seq == request_seq;
}

int dld_lock(char *error, size_t cap)
{
    struct stat st;
    int fd = open(DLD_LOCK_PATH, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) { fail(error, cap, "cannot open command lock: %s", strerror(errno)); return -DLD_PREREQUISITE; }
    if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode)) {
        fail(error, cap, "command lock is not an accessible regular file");
        close(fd); return -DLD_PREREQUISITE;
    }
    if (flock(fd, LOCK_EX | LOCK_NB) < 0) {
        int code = errno == EWOULDBLOCK || errno == EAGAIN ? DLD_BUSY : DLD_PREREQUISITE;
        fail(error, cap, code == DLD_BUSY ? "another dld command holds the command lock" : "cannot acquire command lock: %s", strerror(errno));
        close(fd); return -code;
    }
    return fd;
}

volatile sig_atomic_t dld_cancelled = 0;

static void cancelled(int signal_number) { dld_cancelled = signal_number; }

int dld_install_signals(char *error, size_t cap)
{
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = cancelled;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) < 0 || sigaction(SIGTERM, &action, NULL) < 0 ||
        sigaction(SIGHUP, &action, NULL) < 0)
        return fail(error, cap, "cannot install cancellation handlers: %s", strerror(errno));
    return 0;
}

int dld_validate_cpu(uint64_t *cpu_hz, char *error, size_t cap)
{
    FILE *file;
    char line[256];
    unsigned long max_khz = 0;
    int implementer = 0, part = 0;
#if !defined(__arm__)
    (void)cpu_hz; (void)file; (void)line; (void)max_khz; (void)implementer; (void)part;
    return fail(error, cap, "hardware commands require the supported Cortex-A8 ARM build");
#else
    file = fopen("/proc/cpuinfo", "r");
    if (!file) return fail(error, cap, "cannot verify Cortex-A8 CPU: %s", strerror(errno));
    while (fgets(line, sizeof(line), file)) {
        char *colon = strchr(line, ':');
        if (!colon) continue;
        if (strncmp(line, "CPU implementer", 15) == 0) implementer = strtoul(colon + 1, NULL, 0) == 0x41;
        if (strncmp(line, "CPU part", 8) == 0) part = strtoul(colon + 1, NULL, 0) == 0xc08;
    }
    fclose(file);
    if (!implementer || !part) return fail(error, cap, "spin timing supports only ARM Cortex-A8");
    file = fopen("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "r");
    if (!file) return fail(error, cap, "cannot verify CPU frequency ceiling: %s", strerror(errno));
    if (fscanf(file, "%lu", &max_khz) != 1) max_khz = 0;
    fclose(file);
    if (max_khz == 0 || max_khz > DLD_CPU_MAX_HZ / 1000)
        return fail(error, cap, "unsupported CPU maximum frequency: %lu kHz", max_khz);
    /* Always use the validated board ceiling, not a transient scaling_cur_freq.
     * Overclocking or changing the supported operating-point table is outside
     * this diagnostic implementation's supported timing conditions.
     */
    *cpu_hz = DLD_CPU_MAX_HZ;
    return 0;
#endif
}

const char *dld_error_name(uint32_t detail)
{
    switch (detail) {
    case DLD_ERROR_NONE: return "none";
    case DLD_ERROR_MAGIC: return "invalid magic";
    case DLD_ERROR_ABI: return "unsupported ABI";
    case DLD_ERROR_PROFILE: return "unsupported profile";
    case DLD_ERROR_LENGTH: return "invalid length";
    case DLD_ERROR_TIMING: return "unsupported timing";
    case DLD_ERROR_DIRECTION: return "LED pin is an input";
    case DLD_ERROR_INIT_CLEAR: return "initial GPIO clear failed";
    case DLD_ERROR_BANK_CLEAR: return "end-of-bank GPIO clear failed";
    case DLD_ERROR_INTERNAL: return "unexpected firmware state";
    case DLD_ERROR_GATE: return "invalid or stale bank grant";
    default: return "unknown firmware error";
    }
}

void dld_print_configuration(const struct dld_config *config)
{
    const uint32_t *l = config->string_lengths;
    printf("pixel_type=%s lengths=%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 " groups=%u\n",
           dld_profile_name(config->profile_id), l[0], l[1], l[2], l[3], l[4], l[5], dld_group_count(config));
}
