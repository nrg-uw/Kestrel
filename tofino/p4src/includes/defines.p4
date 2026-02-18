#ifndef _DEFINES_
#define _DEFINES_


typedef bit<48> mac_addr_t;
typedef bit<32> ipv4_addr_t;

enum bit<16> ether_type_t {
    IPV4 = 0x0800,
    ARP  = 0x0806,
    TPID = 0x8100,
    IPV6 = 0x86DD,
    MPLS = 0x8847
}

enum bit<8> ip_protocol_t {
    ICMP = 0x01,
    TCP  = 0x06,
    UDP  = 0x11
}

const bit<16> UDP_PORT_N3 = 0x0868; // 2152

#define IPV4_HOST_TABLE_SIZE 256
#define DMAC_TABLE_SIZE 256
#define BROADCAST_TABLE_SIZE 256
const bit<8> SWITCH_ID = 1;
#define INT_INGRESS_TABLE_SIZE 64
#define INT_EGRESS_TABLE_SIZE 64
#define INT_REPORT_LENGTH_BYTES 31
#define MIRROR_SESSION_ID 1

typedef bit<8>  pkt_type_t;
const pkt_type_t PKT_TYPE_DEFAULT = 0;
const pkt_type_t PKT_TYPE_NORMAL = 1;
const pkt_type_t PKT_TYPE_MIRROR = 2;

typedef bit<3> mirror_type_t;
const mirror_type_t MIRROR_TYPE_I2E = 1;
const mirror_type_t MIRROR_TYPE_E2E = 2;

const bit<8> GTPU_NEXT_EXT_NONE = 0x0;
const bit<8> GTPU_NEXT_EXT_PSC = 0x85;
const bit<8> GTPU_EXT_PSC_LEN = 8w1;

/******** REGISTER SIZING **********/
#define REGISTER_INDEX_WIDTH 16  /* index I*/
#define REGISTER_ENTRY_WIDTH 32  /* type T */
#define REGISTER_SIZE 4096  /* number of buckets */
typedef bit<REGISTER_INDEX_WIDTH> register_index_t;
typedef bit<REGISTER_ENTRY_WIDTH> register_entry_t;
const register_entry_t REGISTER_VALUE = 0x00000000; /* Initial value 0 */

/********* COLORS **********/
const bit<8> COLOR_GREEN = 0;
const bit<8> COLOR_YELLOW = 1;
const bit<8> COLOR_RED = 3;

/******* SKETCH IMPLEMENTATION *******/
const bit<32> WIDTH_W       = 512;              // number of columns
const bit<32> QID_COUNT     = 8;                // 0..7
const bit<32> WIDTH_WQ      = 4096;   // 4096
const bit<32> WIDTH_WLQ     = 32768;          // for 8 bins per bucket

typedef bit<9>  width_index_w_t;        // 0..511 (width)
typedef bit<12>  width_index_wq_t;      // 0..4095 (qid × width)
typedef bit<15>  width_index_wlq_t;   // 0..4095*8-1 for hist bins

/******* LATENCY BINNING *******/
const bit<8>  LAT_SHIFT_NS = 10;          // ~1us per tick; max ~67ms
const bit<8>  IAT_SHIFT_NS = 10;          // ~1us per tick; max ~67ms
const bit<16> MAX16 = 0xFFFF;

/******* IAT CCOMPUTATION *******/
typedef bit<32> ts32_t;          // 32-bit timestamp in microsecond-ish ticks
typedef bit<32> iat32_t;         // 32-bit IAT in same units
typedef bit<16> iat16_t;         // 16-bit IAT in same units

#endif /* _DEFINES_ */