// postcard_collector.c
// Single-thread AF_PACKET V3 INT-XD postcard collector.

#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/filter.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <net/if.h>
#include <netinet/ip.h>
#include <netinet/udp.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define INTERFACE "eth2"
#define BLOCK_SIZE (1 << 22)
#define FRAME_SIZE 2048
#define BLOCK_NR 64
#define INT_PORT 4567

static volatile sig_atomic_t stop = 0;
static int mode_stats = 0;
static int mode_binary = 0;
static int filter_qdepth = 1;      // default: only emit qdepth > 0
static int retire_ms = 20;         // TPACKETv3 block retire timeout (ms)
static size_t batch_bytes = 65536; // binary batching threshold

#pragma pack(push, 1)
// Binary record layout v2 (appends timestamps to previous fields)
typedef struct {
  uint8_t switch_id;
  uint32_t hop_latency;
  uint8_t egress_qid;
  uint32_t queue_depth; // 24-bit expanded
  uint16_t egress_port;
  uint16_t ingress_port;
  uint32_t teid;
  uint8_t qfi;
  uint8_t meter_color;
  uint16_t packet_length;
  uint32_t drop_count;
  uint64_t sw_timestamp_ns;   // from switch: 48-bit on wire → u64
  uint64_t host_timestamp_ns; // from kernel: tp_sec/tp_nsec → u64
} postcard_wire_v2;
#pragma pack(pop)

static void handle_sigint(int sig) {
  (void)sig;
  stop = 1;
}

// Read 48-bit big-endian value from p[0..5] and expand to uint64_t
static inline uint64_t read_be48(const uint8_t *p) {
  return ((uint64_t)p[0] << 40) | ((uint64_t)p[1] << 32) |
         ((uint64_t)p[2] << 24) | ((uint64_t)p[3] << 16) |
         ((uint64_t)p[4] << 8) | ((uint64_t)p[5]);
}

static inline uint64_t ns_now(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static int attach_udp_bpf(int sock_fd, uint16_t dport) {
  struct sock_filter code[] = {
      {0x28, 0, 0, 0x0000000c}, // ldh [12] EtherType
      {0x15, 0, 9, 0x00000800}, // if != IP, reject
      {0x30, 0, 0, 0x00000017}, // ldb [23] ip proto
      {0x15, 0, 7, 0x00000011}, // if != UDP, reject
      {0x28, 0, 0, 0x00000014}, // ldh [20] frag off
      {0x45, 5, 0, 0x00001fff}, // if frag, reject
      {0xb1, 0, 0, 0x0000000e}, // ldh [ip.hlen*4 + 2] (dst port)
      {0x15, 0, 3, 0x00000000}, // placeholder for port
      {0x6, 0, 0, 0x00040000},  // accept 262144 bytes
      {0x6, 0, 0, 0x00000000},  // reject
  };
  code[7].k = htons(dport);
  struct sock_fprog f = {
      .len = (unsigned short)(sizeof(code) / sizeof(code[0])), .filter = code};
  if (setsockopt(sock_fd, SOL_SOCKET, SO_ATTACH_FILTER, &f, sizeof(f)) < 0) {
    perror("SO_ATTACH_FILTER");
    return -1;
  }
  return 0;
}

int main(int argc, char **argv) {
  signal(SIGINT, handle_sigint);

  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--stats"))
      mode_stats = 1;
    else if (!strcmp(argv[i], "--nofilter"))
      filter_qdepth = 0;
    else if (!strcmp(argv[i], "--binary"))
      mode_binary = 1;
    else if (!strcmp(argv[i], "--retire-ms") && i + 1 < argc) {
      retire_ms = atoi(argv[++i]);
      if (retire_ms < 1)
        retire_ms = 1;
    } else if (!strcmp(argv[i], "--batch-bytes") && i + 1 < argc) {
      long v = atol(argv[++i]);
      if (v > 1024)
        batch_bytes = (size_t)v;
    }
  }

  // Resolve ifindex
  struct ifreq ifr = {0};
  int tmp = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
  if (tmp < 0) {
    perror("tmp socket");
    return 1;
  }
  strncpy(ifr.ifr_name, INTERFACE, IFNAMSIZ - 1);
  if (ioctl(tmp, SIOCGIFINDEX, &ifr) < 0) {
    perror("SIOCGIFINDEX");
    close(tmp);
    return 1;
  }
  int ifindex = ifr.ifr_ifindex;
  close(tmp);

  // Socket
  int sock_fd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_IP));
  if (sock_fd < 0) {
    perror("socket");
    return 1;
  }

  int version = TPACKET_V3;
  if (setsockopt(sock_fd, SOL_PACKET, PACKET_VERSION, &version,
                 sizeof(version)) < 0) {
    perror("PACKET_VERSION");
  }

  // Bind
  struct sockaddr_ll sll = {0};
  sll.sll_family = AF_PACKET;
  sll.sll_ifindex = ifindex;
  sll.sll_protocol = htons(ETH_P_IP);
  if (bind(sock_fd, (struct sockaddr *)&sll, sizeof(sll)) < 0) {
    perror("bind");
    close(sock_fd);
    return 1;
  }

  // Filter UDP dport INT_PORT
  if (attach_udp_bpf(sock_fd, INT_PORT) < 0) {
    fprintf(stderr, "Warning: proceeding without BPF filter\n");
  }

  // RX ring
  struct tpacket_req3 req = {0};
  req.tp_block_size = BLOCK_SIZE;
  req.tp_block_nr = BLOCK_NR;
  req.tp_frame_size = FRAME_SIZE;
  req.tp_frame_nr = (BLOCK_SIZE * BLOCK_NR) / FRAME_SIZE;
  req.tp_retire_blk_tov = retire_ms;
  req.tp_feature_req_word = TP_FT_REQ_FILL_RXHASH;
  if (setsockopt(sock_fd, SOL_PACKET, PACKET_RX_RING, &req, sizeof(req)) < 0) {
    perror("PACKET_RX_RING");
    close(sock_fd);
    return 1;
  }

  // Map ring
  size_t ring_size = (size_t)req.tp_block_size * req.tp_block_nr;
  uint8_t *ring =
      mmap(NULL, ring_size, PROT_READ | PROT_WRITE, MAP_SHARED, sock_fd, 0);
  if (ring == MAP_FAILED) {
    perror("mmap");
    close(sock_fd);
    return 1;
  }

  // Buffers
  setvbuf(stdout, NULL, _IOFBF, 1 << 20); // big stdio buffer for text
  uint8_t *bbuf = (uint8_t *)malloc(batch_bytes);
  size_t bfill = 0;
  uint64_t last_flush_ns = ns_now();

  size_t block_offset = 0;
  struct pollfd pfd = {.fd = sock_fd, .events = POLLIN};

  uint64_t last_stat_ns = ns_now();
  uint64_t pps = 0, qdpos = 0;

  while (!stop) {
    if (poll(&pfd, 1, 1000) <= 0)
      continue;

    struct tpacket_block_desc *block = (void *)(ring + block_offset);
    if (!(block->hdr.bh1.block_status & TP_STATUS_USER))
      continue;

    uint8_t *frame = (uint8_t *)block + block->hdr.bh1.offset_to_first_pkt;

    for (int i = 0; i < block->hdr.bh1.num_pkts; i++) {
      struct tpacket3_hdr *hdr = (void *)frame;
      frame += hdr->tp_next_offset;

      struct ethhdr *eth = (void *)hdr + hdr->tp_mac;
      if (ntohs(eth->h_proto) != ETH_P_IP)
        continue;

      struct iphdr *ip = (void *)hdr + hdr->tp_net;
      if (ip->protocol != IPPROTO_UDP)
        continue;

      size_t ip_len = ip->ihl * 4;
      struct udphdr *udp = (void *)ip + ip_len;
      if (ntohs(udp->dest) != INT_PORT)
        continue;

      uint8_t *payload = (uint8_t *)udp + sizeof(*udp);
      size_t payload_len = ntohs(udp->len) - sizeof(*udp);

      // Wire layout (minimum 31 bytes):
      // 0:  switch_id (1)
      // 1-4: hop_latency (4)
      // 5:  egress_qid (1)
      // 6-8: queue_depth (3)
      // 9-10: egress_port (2)
      // 11-12: ingress_port (2)
      // 13-16: teid (4)
      // 17: qfi (1)
      // 18: meter_color (1)
      // 19-20: packet_length (2)
      // 21-24: drop_count (4)
      // 25-30: timestamp_ns (48-bit big-endian)
      if (payload_len < 31)
        continue;

      // Host timestamp from kernel (seconds + nanoseconds)
      uint64_t host_ts_ns =
          (uint64_t)hdr->tp_sec * 1000000000ULL + (uint64_t)hdr->tp_nsec;

      // Build output record
      postcard_wire_v2 w = {0};
      w.switch_id = payload[0];
      w.hop_latency = ntohl(*(uint32_t *)(payload + 1));
      w.egress_qid = payload[5];
      w.queue_depth = ((uint32_t)payload[6] << 16) |
                      ((uint32_t)payload[7] << 8) | (uint32_t)payload[8];
      w.egress_port = ntohs(*(uint16_t *)(payload + 9));
      w.ingress_port = ntohs(*(uint16_t *)(payload + 11));
      w.teid = ntohl(*(uint32_t *)(payload + 13));
      w.qfi = payload[17];
      w.meter_color = payload[18];
      w.packet_length = ntohs(*(uint16_t *)(payload + 19));
      w.drop_count = ntohl(*(uint32_t *)(payload + 21));
      w.sw_timestamp_ns = read_be48(payload + 25); // 48b → u64
      w.host_timestamp_ns = host_ts_ns;

      pps++;
      if (w.queue_depth > 0)
        qdpos++;

      if (filter_qdepth && w.queue_depth == 0)
        continue;

      if (mode_binary) {
        if (bfill + sizeof(w) > batch_bytes) {
          (void)write(STDOUT_FILENO, bbuf, bfill);
          bfill = 0;
          last_flush_ns = ns_now();
        }
        memcpy(bbuf + bfill, &w, sizeof(w));
        bfill += sizeof(w);
      } else if (!mode_stats) {
        printf("[Postcard] sw_ts=%" PRIu64 " host_ts=%" PRIu64
               " switch_id=%u hop_lat=%u "
               "eg_qid=%u qdepth=%u eg_port=%u in_port=%u TEID=0x%08x QFI=%u "
               "color=%u "
               "pkt_len=%u drop_cnt=%u\n",
               w.sw_timestamp_ns, w.host_timestamp_ns, w.switch_id,
               w.hop_latency, w.egress_qid, w.queue_depth, w.egress_port,
               w.ingress_port, w.teid, w.qfi, w.meter_color, w.packet_length,
               w.drop_count);
      }
    }

    block->hdr.bh1.block_status = TP_STATUS_KERNEL;
    block_offset = (block_offset + BLOCK_SIZE) % ring_size;

    // Periodic flush for binary
    if (mode_binary) {
      uint64_t now = ns_now();
      if (bfill > 0 && (now - last_flush_ns) >= 1000000ULL) { // ~1 ms
        (void)write(STDOUT_FILENO, bbuf, bfill);
        bfill = 0;
        last_flush_ns = now;
      }
    }

    // Kernel stats once/sec
    uint64_t now2 = ns_now();
    if (now2 - last_stat_ns >= 1000000000ULL) {
      struct tpacket_stats_v3 st = {0};
      socklen_t sl = sizeof(st);
      if (!getsockopt(sock_fd, SOL_PACKET, PACKET_STATISTICS, &st, &sl)) {
        fprintf(stderr, "[Pcap] pkts=%u drops=%u freeze=%u\n", st.tp_packets,
                st.tp_drops, st.tp_freeze_q_cnt);
      }
      if (mode_stats) {
        fprintf(stderr, "[PPS] total=%" PRIu64 ", qd>0=%" PRIu64 "\n", pps,
                qdpos);
      }
      pps = qdpos = 0;
      last_stat_ns = now2;
    }
  }

  printf("sizeof(postcard_wire_v2)=%zu\n", sizeof(postcard_wire_v2));

  if (mode_binary && bfill > 0)
    (void)write(STDOUT_FILENO, bbuf, bfill);
  free(bbuf);
  munmap(ring, ring_size);
  close(sock_fd);
  return 0;
}
