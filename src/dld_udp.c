#define _POSIX_C_SOURCE 200809L
#include "dld_sender.h"
#include "dld_opc.h"
#include "dld_flash.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <netdb.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

/* A finite batch prevents continuous ingress from starving transmission.
 * This is a latest-valid update within each batch, not a reliable frame queue.
 */
#define DLD_UDP_BATCH 64u
#define DLD_UDP_IDLE_MS 250
#define DLD_UDP_PACKET_CAP 65535u

struct packet_counts {
    uint64_t received;
    uint64_t valid;
    uint64_t malformed;
    uint64_t unsupported;
    uint64_t coalesced;
    uint64_t sent;
    uint64_t flash_frames;
    uint64_t startup_flashes;
    uint64_t idle_flashes;
    uint64_t interrupted_flashes;
};

static void usage(FILE *stream)
{
    fprintf(stream, "usage: dld-udp [--bind ADDRESS] [--port PORT]\n"
            "               [--no-startup-flash] [--no-idle-flash]\n"
            "  ADDRESS: numeric IPv4 or IPv6 address (default ::, dual-stack)\n"
            "  PORT: 1..65535 (default 7890); foreground OPC UDP receiver\n"
            "  --no-startup-flash: suppress the initial green smooth flash\n"
            "  --no-idle-flash: suppress red smooth flashes after 60s of inactivity\n");
}

static int monotonic_now(uint64_t *now, char *error, size_t cap)
{
    struct timespec stamp;
    if (clock_gettime(CLOCK_MONOTONIC, &stamp) < 0) {
        snprintf(error, cap, "read monotonic clock: %s", strerror(errno));
        return -1;
    }
    *now = (uint64_t)stamp.tv_sec * UINT64_C(1000000000) +
           (uint64_t)stamp.tv_nsec;
    return 0;
}

/* Round up to milliseconds, including the extra ns for a strictly greater
 * than 60s deadline. Cap all waits so a just-delivered signal cannot strand us.
 */
static int idle_wait_ms(uint64_t now, uint64_t activity, int enabled)
{
    uint64_t elapsed = now >= activity ? now - activity : 0;
    uint64_t remaining;
    if (!enabled) return DLD_UDP_IDLE_MS;
    if (elapsed > DLD_FLASH_IDLE_NS) return 0;
    remaining = DLD_FLASH_IDLE_NS - elapsed + 1;
    if (remaining >= (uint64_t)DLD_UDP_IDLE_MS * UINT64_C(1000000))
        return DLD_UDP_IDLE_MS;
    return (int)((remaining + UINT64_C(999999)) / UINT64_C(1000000));
}

static int valid_port(const char *text)
{
    unsigned port = 0;
    const unsigned char *p = (const unsigned char *)text;
    if (*p == '\0') return 0;
    for (; *p != '\0'; ++p) {
        if (*p < '0' || *p > '9') return 0;
        port = port * 10u + (unsigned)(*p - '0');
        if (port > 65535u) return 0;
    }
    return port != 0;
}

static int open_socket(const char *address, const char *port,
                        char *error, size_t cap)
{
    struct addrinfo hints, *addresses = NULL, *entry;
    int result, fd = -1, saved_error = EADDRNOTAVAIL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_DGRAM;
    hints.ai_protocol = IPPROTO_UDP;
    hints.ai_flags = AI_NUMERICHOST | AI_NUMERICSERV;
    result = getaddrinfo(address, port, &hints, &addresses);
    if (result != 0) {
        snprintf(error, cap, "invalid numeric bind address %s: %s",
                 address, gai_strerror(result));
        return -DLD_BAD_ARGUMENT;
    }
    for (entry = addresses; entry != NULL; entry = entry->ai_next) {
        int flags, dual_stack = 0;
        fd = socket(entry->ai_family, entry->ai_socktype, entry->ai_protocol);
        if (fd < 0) { saved_error = errno; continue; }
        /* Preserve LEDscape's IPv4 and IPv6 wildcard reception explicitly.
         * Do not enable SO_REUSEADDR/SO_REUSEPORT: one owner per endpoint.
         */
        if (entry->ai_family == AF_INET6 &&
            setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY,
                       &dual_stack, sizeof(dual_stack)) < 0)
            goto socket_failed;
        flags = fcntl(fd, F_GETFD);
        if (flags < 0 || fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0)
            goto socket_failed;
        flags = fcntl(fd, F_GETFL);
        if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
            goto socket_failed;
        if (bind(fd, entry->ai_addr, entry->ai_addrlen) < 0)
            goto socket_failed;
        break;
socket_failed:
        saved_error = errno;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(addresses);
    if (fd < 0) {
        snprintf(error, cap, "bind UDP [%s]:%s: %s", address, port,
                 strerror(saved_error));
        return -DLD_PREREQUISITE;
    }
    return fd;
}

static int receive_batch(int fd, unsigned char *packet, uint32_t *rgb,
                           struct packet_counts *counts, uint64_t *activity,
                           char *error, size_t cap)
{
    unsigned i;
    int have_color = 0;
    for (i = 0; i < DLD_UDP_BATCH && !dld_cancelled; ++i) {
        struct msghdr message;
        struct iovec buffer;
        enum dld_opc_result parsed;
        uint32_t candidate;
        ssize_t received;
        memset(&message, 0, sizeof(message));
        buffer.iov_base = packet;
        buffer.iov_len = DLD_UDP_PACKET_CAP;
        message.msg_iov = &buffer;
        message.msg_iovlen = 1;
        received = recvmsg(fd, &message, 0);
        if (received < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            if (errno == EINTR) {
                if (dld_cancelled) break;
                continue;
            }
            snprintf(error, cap, "receive UDP: %s", strerror(errno));
            return -1;
        }
        ++counts->received;
        /* Activity means any consumed datagram, even if it is not usable OPC. */
        if (monotonic_now(activity, error, cap) < 0) return -1;
        if (message.msg_flags & MSG_TRUNC) {
            ++counts->malformed;
            continue;
        }
        parsed = dld_opc_color(packet, (size_t)received, &candidate);
        if (parsed == DLD_OPC_MALFORMED) { ++counts->malformed; continue; }
        if (parsed == DLD_OPC_UNSUPPORTED) { ++counts->unsupported; continue; }
        ++counts->valid;
        if (have_color) ++counts->coalesced;
        *rgb = candidate;
        have_color = 1;
    }
    return have_color;
}

int main(int argc, char **argv)
{
    const char *address = "::", *port = "7890";
    struct dld_sender sender;
    struct packet_counts counts;
    struct dld_flash flash;
    uint64_t now, activity = 0;
    unsigned char packet[DLD_UDP_PACKET_CAP];
    char error[512] = "";
    int i, fd = -1, sender_opened = 0, code = DLD_OK;
    int startup_enabled = 1, idle_enabled = 1;
    int announced_ready = 0;
    enum { FLASH_NONE, FLASH_STARTUP, FLASH_IDLE } flashing = FLASH_NONE;
    memset(&counts, 0, sizeof(counts));
    for (i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--help") && argc == 2) { usage(stdout); return DLD_OK; }
        if (!strcmp(argv[i], "--bind") && i + 1 < argc) address = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc) port = argv[++i];
        else if (!strcmp(argv[i], "--no-startup-flash")) startup_enabled = 0;
        else if (!strcmp(argv[i], "--no-idle-flash")) idle_enabled = 0;
        else { usage(stderr); return DLD_BAD_ARGUMENT; }
    }
    if (!valid_port(port) || *address == '\0') { usage(stderr); return DLD_BAD_ARGUMENT; }
    if (dld_install_signals(error, sizeof(error)) < 0) {
        code = DLD_PREREQUISITE; goto done;
    }
    fd = open_socket(address, port, error, sizeof(error));
    if (fd < 0) { code = -fd; goto done; }
    code = dld_sender_open(&sender, error, sizeof(error));
    if (code != DLD_OK) goto done;
    sender_opened = 1;
    if (monotonic_now(&activity, error, sizeof(error)) < 0) {
        code = DLD_PREREQUISITE; goto done;
    }
    if (startup_enabled) {
        dld_flash_start(&flash, UINT32_C(0x00ff00), activity);
        flashing = FLASH_STARTUP;
    }
    fprintf(stderr, "dld-udp: listening on [%s]:%s; OPC RGB, batch limit %u; "
            "startup flash %s, idle flash %s\n", address, port, DLD_UDP_BATCH,
            startup_enabled ? "on" : "off", idle_enabled ? "on" : "off");
    if (!startup_enabled) {
        fprintf(stderr, "dld-udp: ready\n");
        announced_ready = 1;
    }
    while (!dld_cancelled) {
        struct pollfd watched;
        struct dld_send_result result;
        uint32_t rgb = 0;
        int ready, have_color, final_frame = 0;
        /* Check queued input before every animation frame. A finite batch
         * allows sends to make progress even under continuous UDP traffic.
         */
        have_color = receive_batch(fd, packet, &rgb, &counts, &activity,
                                   error, sizeof(error));
        if (have_color < 0) { code = DLD_PREREQUISITE; break; }
        if (dld_cancelled) break;
        if (monotonic_now(&now, error, sizeof(error)) < 0) {
            code = DLD_PREREQUISITE; break;
        }
        if (have_color) {
            if (flashing != FLASH_NONE) ++counts.interrupted_flashes;
            flashing = FLASH_NONE;
        } else {
            if (flashing == FLASH_NONE && idle_enabled &&
                dld_flash_idle_due(now, activity)) {
                dld_flash_start(&flash, UINT32_C(0xff0000), now);
                flashing = FLASH_IDLE;
            }
            if (flashing != FLASH_NONE) {
                /* Startup logging/input handling can delay the first frame.
                 * Its animation clock starts here; the idle baseline remains
                 * the successful attachment time recorded above.
                 */
                if (!flash.initial_sent) flash.started_ns = now;
                final_frame = dld_flash_next(&flash, now, &rgb);
            }
        }
        if (have_color || flashing != FLASH_NONE) {
            code = dld_sender_send(&sender, rgb, &result, error, sizeof(error));
            if (code != DLD_OK) break;
            if (!announced_ready && (have_color || final_frame)) {
                fprintf(stderr, "dld-udp: ready\n");
                announced_ready = 1;
            }
            if (have_color) ++counts.sent;
            else {
                ++counts.flash_frames;
                if (final_frame) {
                    if (flashing == FLASH_STARTUP) ++counts.startup_flashes;
                    else {
                        ++counts.idle_flashes;
                        /* Start the next interval after final black completes. */
                        if (monotonic_now(&activity, error, sizeof(error)) < 0) {
                            code = DLD_PREREQUISITE; break;
                        }
                    }
                    flashing = FLASH_NONE;
                }
            }
            continue;
        }
        watched.fd = fd;
        watched.events = POLLIN;
        watched.revents = 0;
        /* A signal delivered between the condition and poll cannot leave the
         * daemon asleep indefinitely. No hardware polling occurs while idle.
         */
        ready = poll(&watched, 1, idle_wait_ms(now, activity, idle_enabled));
        if (ready < 0) {
            if (errno == EINTR) continue;
            snprintf(error, sizeof(error), "poll UDP: %s", strerror(errno));
            code = DLD_PREREQUISITE; break;
        }
        if (!ready || dld_cancelled) continue;
        if (watched.revents & (POLLERR | POLLHUP | POLLNVAL)) {
            snprintf(error, sizeof(error), "UDP socket poll error: events=0x%x",
                     (unsigned)watched.revents);
            code = DLD_PREREQUISITE; break;
        }
    }
done:
    if (sender_opened) dld_sender_close(&sender);
    if (fd >= 0) close(fd);
    if (code != DLD_OK) fprintf(stderr, "dld-udp: %s\n", error);
    fprintf(stderr, "dld-udp: stopped; received=%" PRIu64 " valid=%" PRIu64
            " malformed=%" PRIu64 " unsupported=%" PRIu64
            " coalesced=%" PRIu64 " sent=%" PRIu64
            " flash_frames=%" PRIu64 " startup_flashes=%" PRIu64
            " idle_flashes=%" PRIu64 " interrupted_flashes=%" PRIu64
            " signal=%d exit=%d\n",
            counts.received, counts.valid, counts.malformed, counts.unsupported,
            counts.coalesced, counts.sent, counts.flash_frames,
            counts.startup_flashes, counts.idle_flashes, counts.interrupted_flashes,
            (int)dld_cancelled, code);
    return code;
}
