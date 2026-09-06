#include "dld_common.h"
#include "dld_profiles.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static unsigned checks;
static unsigned failures;

#define CHECK(label, condition) do { \
    ++checks; \
    if (!(condition)) { \
        ++failures; \
        fprintf(stderr, "FAIL line %u: %s\n", (unsigned)__LINE__, (label)); \
    } \
} while (0)

static int parse_config(const char *text, size_t length,
                        struct dld_config *config)
{
    char error[160];
    int result;
    memset(error, 0, sizeof(error));
    result = dld_parse_config(text, length, config, error, sizeof(error));
    if (result != 0)
        CHECK("configuration errors include a diagnostic", error[0] != '\0');
    return result;
}

static void test_colors(void)
{
    static const struct {
        const char *text;
        uint32_t expected;
    } valid[] = {
        { "000000", 0 }, { "FFFFFF", 0xffffffu },
        { "aBcDeF", 0xabcdefu }, { "0x123456", 0x123456u },
        { "0X001234", 0x1234u }, { "0x000000", 0 },
        { "0Xffffff", 0xffffffu }
    };
    static const char *invalid[] = {
        "", "0x", "12345", "1234567", "0x12345", "0x1234567",
        "12345g", "#123456", " 123456", "123456 ", "123456\n",
        "+123456", "-123456", "0x0x123456"
    };
    const char *digits = "0123456789ABCDEFabcdef";
    size_t i;
    unsigned position;
    uint32_t value;
    for (i = 0; i < sizeof(valid) / sizeof(valid[0]); ++i) {
        value = 0xdeadbeefu;
        CHECK(valid[i].text, dld_parse_color(valid[i].text, &value) == 0);
        CHECK("parsed RGB value", value == valid[i].expected);
    }
    for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        CHECK("invalid color rejected", dld_parse_color(invalid[i], &value) != 0);

    /* Exercise each hexadecimal character in every input position. */
    for (position = 0; position < 6; ++position) {
        for (i = 0; digits[i] != '\0'; ++i) {
            char text[] = "000000";
            unsigned nibble = i < 16 ? (unsigned)i : (unsigned)i - 6;
            text[position] = digits[i];
            CHECK("hex digit accepted in every position",
                  dld_parse_color(text, &value) == 0);
            CHECK("hex digit has correct significance",
                  value == ((uint32_t)nibble << (4u * (5u - position))));
        }
    }

    CHECK("WS2812B diagnostic profile uses GRB",
          dld_wire_color(DLD_PROFILE_WS2812B, 0x123456u) == 0x341256u);
    CHECK("WS2811 diagnostic profile uses RGB",
          dld_wire_color(DLD_PROFILE_WS2811_HS, 0x123456u) == 0x123456u);
    CHECK("WS2812B BGR profile reverses red and blue",
          dld_wire_color(DLD_PROFILE_WS2812B_BGR, 0x123456u) == 0x563412u);
    CHECK("WS2811 BGR profile reverses red and blue",
          dld_wire_color(DLD_PROFILE_WS2811_HS_BGR, 0x123456u) == 0x563412u);
    CHECK("black is independent of byte order",
          dld_wire_color(DLD_PROFILE_WS2812B, 0) == 0 &&
          dld_wire_color(DLD_PROFILE_WS2811_HS, 0) == 0 &&
          dld_wire_color(DLD_PROFILE_WS2812B_BGR, 0) == 0 &&
          dld_wire_color(DLD_PROFILE_WS2811_HS_BGR, 0) == 0);
    CHECK("white is independent of byte order",
          dld_wire_color(DLD_PROFILE_WS2812B, 0xffffffu) == 0xffffffu &&
          dld_wire_color(DLD_PROFILE_WS2811_HS, 0xffffffu) == 0xffffffu &&
          dld_wire_color(DLD_PROFILE_WS2812B_BGR, 0xffffffu) == 0xffffffu &&
          dld_wire_color(DLD_PROFILE_WS2811_HS_BGR, 0xffffffu) == 0xffffffu);
}

static void test_configuration(void)
{
    static const char *valid[] = {
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,1,2,180,299,300]}",
        " \n{ \"string_lengths\" : [300,300,300,300,300,300],"
        "\"pixel_type\" : \"ws2811-hs\" }\t\r\n",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[-0,0,0,0,0,0]}",
        "{\"\\u0070ixel_type\":\"\\u0077s2812b\","
        "\"string\\u005flengths\":[1,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b-bgr\",\"string_lengths\":[1,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2811-hs-bgr\",\"string_lengths\":[1,0,0,0,0,0]}"
    };
    static const char *invalid[] = {
        "", " \t\r\n", "[]", "null", "{}",
        "{\"pixel_type\":\"ws2812b\"}",
        "{\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"unknown\",\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"WS2812B\",\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":null,\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":1,\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":null}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[301,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[-1,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[4294967296,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[999999999999999999999999,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[1.0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[1e0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[01,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[+1,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[true,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[\"1\",0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[null,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0],\"extra\":0}",
        "{\"pixel_type\":\"ws2812b\",\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"\\u0070ixel_type\":\"ws2811-hs\",\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0],\"string_lengths\":[1,1,1,1,1,1]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0],}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0,]}",
        "{\"pixel_type\":\"ws2812b\" \"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0]",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0]}x",
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0]}//comment",
        "{\"pixel_type\":\"ws2812\\q\",\"string_lengths\":[0,0,0,0,0,0]}",
        "{\"pixel_type\":\"\\u00G1\",\"string_lengths\":[0,0,0,0,0,0]}"
    };
    static const char embedded_nul[] =
        "{\"pixel_type\":\"ws2812b\",\"string_lengths\":[0,0,0,0,0,0]}\0x";
    struct dld_config config;
    size_t i;
    for (i = 0; i < sizeof(valid) / sizeof(valid[0]); ++i)
        CHECK("valid panel JSON accepted",
              parse_config(valid[i], strlen(valid[i]), &config) == 0);
    for (i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        CHECK("invalid panel JSON rejected",
              parse_config(invalid[i], strlen(invalid[i]), &config) != 0);
    CHECK("embedded NUL does not truncate file validation",
          parse_config(embedded_nul, sizeof(embedded_nul) - 1, &config) != 0);
    CHECK("length-limited parser accepts its exact input slice",
          parse_config(embedded_nul, sizeof(embedded_nul) - 3, &config) == 0);

    CHECK("representative configuration parsed",
          parse_config(valid[0], strlen(valid[0]), &config) == 0);
    CHECK("profile identifier preserved", config.profile_id == DLD_PROFILE_WS2812B);
    CHECK("length array preserves physical pin order",
          config.string_lengths[0] == 0 && config.string_lengths[1] == 1 &&
          config.string_lengths[2] == 2 && config.string_lengths[3] == 180 &&
          config.string_lengths[4] == 299 && config.string_lengths[5] == 300);
    CHECK("WS2812B profile name", strcmp(dld_profile_name(DLD_PROFILE_WS2812B), "ws2812b") == 0);
    CHECK("WS2811 profile name", strcmp(dld_profile_name(DLD_PROFILE_WS2811_HS), "ws2811-hs") == 0);
    CHECK("WS2812B BGR profile name",
          dld_profile_name(DLD_PROFILE_WS2812B_BGR) != NULL &&
          strcmp(dld_profile_name(DLD_PROFILE_WS2812B_BGR), "ws2812b-bgr") == 0);
    CHECK("WS2811 BGR profile name",
          dld_profile_name(DLD_PROFILE_WS2811_HS_BGR) != NULL &&
          strcmp(dld_profile_name(DLD_PROFILE_WS2811_HS_BGR), "ws2811-hs-bgr") == 0);
    CHECK("WS2812B BGR configuration preserves profile identifier",
          parse_config(valid[5], strlen(valid[5]), &config) == 0 &&
          config.profile_id == DLD_PROFILE_WS2812B_BGR);
    CHECK("WS2811 BGR configuration preserves profile identifier",
          parse_config(valid[6], strlen(valid[6]), &config) == 0 &&
          config.profile_id == DLD_PROFILE_WS2811_HS_BGR);
}

static void test_banks_and_budgets(void)
{
    static const unsigned bank_for_pin[6] = { 0, 0, 1, 2, 1, 0 };
    static const unsigned gpio_bit[6] = { 3, 4, 12, 26, 14, 1 };
    struct dld_config config;
    struct dld_budget budget, earlier, slower;
    uint32_t masks[3], lengths[3];
    char error[160];
    unsigned pin, bank;
    int result;
    memset(&config, 0, sizeof(config));
    config.profile_id = DLD_PROFILE_WS2812B;

    dld_bank_info(&config, masks, lengths);
    CHECK("all-disabled bank masks", masks[0] == 0 && masks[1] == 0 && masks[2] == 0);
    CHECK("all-disabled bank lengths", lengths[0] == 0 && lengths[1] == 0 && lengths[2] == 0);
    result = dld_compute_budget(&config, 1000000000ULL, &budget, error, sizeof(error));
    CHECK("all-disabled budget remains valid", result == 0);
    if (result == 0) {
        CHECK("all-disabled settling includes reset and engineering margin", budget.settle_ns == 400000ULL);
        CHECK("all-disabled send still waits", budget.quiet_ns >= budget.settle_ns && budget.iterations > 0);
    }

    for (pin = 0; pin < 6; ++pin) {
        memset(config.string_lengths, 0, sizeof(config.string_lengths));
        config.string_lengths[pin] = 17;
        dld_bank_info(&config, masks, lengths);
        for (bank = 0; bank < 3; ++bank) {
            CHECK("single-pin GPIO mapping",
                  masks[bank] == (bank == bank_for_pin[pin] ? (1u << gpio_bit[pin]) : 0u));
            CHECK("single-pin bank maximum",
                  lengths[bank] == (bank == bank_for_pin[pin] ? 17u : 0u));
        }
    }

    for (pin = 0; pin < 6; ++pin)
        config.string_lengths[pin] = 300;
    dld_bank_info(&config, masks, lengths);
    CHECK("full GPIO masks preserve unrelated bits",
          masks[0] == 0x1au && masks[1] == 0x5000u && masks[2] == 0x04000000u);
    CHECK("full bank lengths", lengths[0] == 300 && lengths[1] == 300 && lengths[2] == 300);
    result = dld_compute_budget(&config, 1000000000ULL, &budget, error, sizeof(error));
    CHECK("maximum configuration budget", result == 0);
    if (result == 0) {
        CHECK("longest-chain engineering settling allowance", budget.settle_ns == 699000ULL);
        CHECK("quiet budget includes all three 8.64 ms passes and settling",
              budget.quiet_ns >= 25920000ULL + budget.settle_ns);
        CHECK("maximum configuration spin has iterations", budget.iterations > 0);
        earlier = budget;

        config.profile_id = DLD_PROFILE_WS2812B_BGR;
        result = dld_compute_budget(&config, 1000000000ULL, &budget, error, sizeof(error));
        CHECK("WS2812B BGR uses the same timing budget",
              result == 0 && budget.settle_ns == earlier.settle_ns && budget.quiet_ns == earlier.quiet_ns);
        config.profile_id = DLD_PROFILE_WS2811_HS_BGR;
        result = dld_compute_budget(&config, 1000000000ULL, &budget, error, sizeof(error));
        CHECK("WS2811 BGR uses the same timing budget",
              result == 0 && budget.settle_ns == earlier.settle_ns && budget.quiet_ns == earlier.quiet_ns);
        config.profile_id = DLD_PROFILE_WS2812B;

        /* Shortening a non-maximal neighbor must not shorten its bank pass. */
        config.string_lengths[1] = 1;
        result = dld_compute_budget(&config, 1000000000ULL, &budget, error, sizeof(error));
        CHECK("unequal lengths budget", result == 0);
        if (result == 0)
            CHECK("unchanged bank maxima preserve duration",
                  budget.settle_ns == earlier.settle_ns && budget.quiet_ns == earlier.quiet_ns);

        config.string_lengths[3] = 0;
        result = dld_compute_budget(&config, 1000000000ULL, &budget, error, sizeof(error));
        CHECK("skipped bank budget", result == 0);
        if (result == 0) {
            CHECK("removing a bank shortens wait without losing chain allowance",
                  budget.quiet_ns < earlier.quiet_ns && budget.settle_ns == earlier.settle_ns);
            CHECK("remaining two bank streams fit in quiet budget",
                  budget.quiet_ns >= 17280000ULL + budget.settle_ns);
            result = dld_compute_budget(&config, 500000000ULL, &slower, error, sizeof(error));
            CHECK("lower CPU frequency budget", result == 0);
            if (result == 0)
                CHECK("CPU frequency affects countdown, not PRU duration",
                      slower.quiet_ns == budget.quiet_ns && slower.iterations < budget.iterations && slower.iterations > 0);
        }
    }
    CHECK("zero CPU frequency rejected",
          dld_compute_budget(&config, 0, &budget, error, sizeof(error)) != 0);
    CHECK("overflowing CPU/count calculation rejected",
          dld_compute_budget(&config, UINT64_MAX, &budget, error, sizeof(error)) != 0);
    result = dld_compute_budget(&config, 333333333ULL, &budget, error, sizeof(error));
    CHECK("fractional iteration budget", result == 0);
    if (result == 0) {
        uint64_t required = budget.quiet_ns * 333333333ULL;
        uint64_t per_iteration = 1000000000ULL * DLD_SPIN_MIN_CYCLES;
        CHECK("rounding never shortens the wait",
              (uint64_t)budget.iterations * per_iteration >= required);
        CHECK("countdown is the rounded-up count",
              budget.iterations > 0 &&
              (uint64_t)(budget.iterations - 1u) * per_iteration < required);
    }
}

static void test_mailbox(void)
{
    struct dld_command command, baseline;
    struct dld_config config;
    char error[160];
    memset(&baseline, 0, sizeof(baseline));
    baseline.magic = DLD_COMMAND_MAGIC;
    baseline.abi_version = DLD_ABI_VERSION;
    baseline.profile_id = DLD_PROFILE_WS2812B;
    baseline.string_lengths[0] = 300;
    baseline.string_lengths[5] = 1;
    baseline.status = DLD_STATUS_READY;

#define MAILBOX_EXPECT(label, expected) \
    CHECK((label), dld_check_mailbox(&command, &config, error, sizeof(error)) == (expected))

    command = baseline;
    MAILBOX_EXPECT("fresh READY is usable", 0);
    CHECK("ready check returns retained configuration",
          config.profile_id == DLD_PROFILE_WS2812B &&
          config.string_lengths[0] == 300 && config.string_lengths[5] == 1);

    command.request_seq = 1;
    MAILBOX_EXPECT("published request is busy before READY changes", 6);
    command.status = DLD_STATUS_DONE;
    MAILBOX_EXPECT("published request is busy before old DONE changes", 6);
    command.status = DLD_STATUS_RUNNING;
    command.completion_seq = 1;
    MAILBOX_EXPECT("RUNNING remains busy even with matching sequences", 6);
    command.status = DLD_STATUS_DONE;
    command.accepted_seq = 1;
    command.bank_done = DLD_BANK_GPIO2;
    command.bank_grant = DLD_BANK_GPIO2;
    MAILBOX_EXPECT("completed independent request permits reuse", 0);

    command.request_seq = UINT32_MAX;
    command.completion_seq = UINT32_MAX;
    command.accepted_seq = UINT32_MAX;
    MAILBOX_EXPECT("maximum sequence completion is usable", 0);
    command.request_seq = 0;
    MAILBOX_EXPECT("wrapped request does not match prior completion", 6);
    command.completion_seq = 0;
    command.accepted_seq = 0;
    MAILBOX_EXPECT("wrapped DONE remains usable", 0);

    command.bank_ready = DLD_BANK_GPIO2;
    MAILBOX_EXPECT("DONE with an open gate is invalid", 7);
    command.bank_ready = 0;
    command.bank_done = DLD_BANK_GPIO1;
    MAILBOX_EXPECT("DONE for the wrong final bank is invalid", 7);
    command.bank_done = DLD_BANK_GPIO2;
    command.accepted_seq = 99;
    MAILBOX_EXPECT("DONE accepted sequence mismatch is invalid", 7);
    command = baseline;
    command.bank_grant = DLD_BANK_GPIO2;
    MAILBOX_EXPECT("READY with a stale grant is invalid", 7);
    command.status = DLD_STATUS_WAIT_BANK;
    MAILBOX_EXPECT("gate-waiting request is busy", 6);
    command = baseline;
    command.abi_version = 3;
    MAILBOX_EXPECT("ungated ABI3 mailbox is invalid", 7);

    command = baseline;
    command.status = DLD_STATUS_ERROR;
    command.request_seq = 1;
    command.error_detail = DLD_ERROR_BANK_CLEAR;
    MAILBOX_EXPECT("firmware error is invalid even with outstanding sequence", 7);
    command = baseline;
    command.error_detail = DLD_ERROR_INTERNAL;
    MAILBOX_EXPECT("READY with an error is invalid", 7);
    command = baseline;
    command.magic = 0;
    MAILBOX_EXPECT("missing command magic rejected", 7);
    command = baseline;
    command.abi_version = DLD_ABI_VERSION + 1u;
    MAILBOX_EXPECT("incompatible ABI rejected", 7);
    command = baseline;
    command.profile_id = DLD_PROFILE_WS2812B_BGR;
    MAILBOX_EXPECT("retained WS2812B BGR profile is usable", 0);
    CHECK("mailbox preserves WS2812B BGR identifier", config.profile_id == DLD_PROFILE_WS2812B_BGR);
    command.profile_id = DLD_PROFILE_WS2811_HS_BGR;
    MAILBOX_EXPECT("retained WS2811 BGR profile is usable", 0);
    CHECK("mailbox preserves WS2811 BGR identifier", config.profile_id == DLD_PROFILE_WS2811_HS_BGR);
    command.profile_id = 99;
    MAILBOX_EXPECT("unsupported retained profile rejected", 7);
    command = baseline;
    command.string_lengths[4] = 301;
    MAILBOX_EXPECT("out-of-range retained length rejected", 7);
    command = baseline;
    command.status = DLD_STATUS_INITIALIZING;
    MAILBOX_EXPECT("unfinished initialization is not usable", 7);
    command.status = 42;
    MAILBOX_EXPECT("unknown status is invalid", 7);
#undef MAILBOX_EXPECT
}

static void test_completion(void)
{
    struct dld_completion completion;
    completion.status = DLD_STATUS_DONE;
    completion.completion_seq = 37;
    completion.error_detail = DLD_ERROR_NONE;
    CHECK("current successful completion matches",
          dld_completion_matches(&completion, 37));
    CHECK("old DONE cannot complete a new request",
          !dld_completion_matches(&completion, 38));
    completion.status = DLD_STATUS_RUNNING;
    CHECK("matching sequence before final DONE is insufficient",
          !dld_completion_matches(&completion, 37));
    completion.status = DLD_STATUS_READY;
    CHECK("readiness is not completion", !dld_completion_matches(&completion, 37));
    completion.status = DLD_STATUS_DONE;
    completion.error_detail = DLD_ERROR_BANK_CLEAR;
    CHECK("DONE with an error cannot succeed", !dld_completion_matches(&completion, 37));
    completion.status = DLD_STATUS_ERROR;
    CHECK("firmware ERROR cannot succeed", !dld_completion_matches(&completion, 37));
    completion.status = DLD_STATUS_DONE;
    completion.error_detail = DLD_ERROR_NONE;
    completion.completion_seq = 0;
    CHECK("wrapped completion matches current request",
          dld_completion_matches(&completion, 0));
    CHECK("wrapped completion does not match previous request",
          !dld_completion_matches(&completion, UINT32_MAX));
}

int main(void)
{
    test_colors();
    test_configuration();
    test_banks_and_budgets();
    test_mailbox();
    test_completion();
    if (failures != 0) {
        fprintf(stderr, "%u of %u common checks failed\n", failures, checks);
        return 1;
    }
    printf("PASS common: %u checks\n", checks);
    return 0;
}
