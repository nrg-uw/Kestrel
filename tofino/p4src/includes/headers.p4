#ifndef _HEADERS_
#define _HEADERS_

// Ethernet header definition
header ethernet_h {
    mac_addr_t dst_addr;
    mac_addr_t src_addr;
    ether_type_t ether_type;
}

header arp_t {
    bit<16> htype;     // Hardware Type (e.g., 1 for Ethernet)
    bit<16> ptype;     // Protocol Type (e.g., 0x0800 for IPv4)
    bit<8>  hlen;      // Hardware Address Length (e.g., 6 for MAC)
    bit<8>  plen;      // Protocol Address Length (e.g., 4 for IPv4)
    bit<16> opcode;    // Operation: 1 = request, 2 = reply

    mac_addr_t sender_mac;    // Sender Hardware Address
    ipv4_addr_t sender_ip;     // Sender Protocol Address
    mac_addr_t target_mac;    // Target Hardware Address
    ipv4_addr_t target_ip;     // Target Protocol Address
}


// IPv4 header definition
header ipv4_h {
    bit<4>   version;
    bit<4>   ihl;
    bit<6>   dscp;
    bit<2>   ecn;
    bit<16>  total_len;
    bit<16>  identification;
    bit<3>   flags;
    bit<13>  frag_offset;
    bit<8>   ttl;
    bit<8>   protocol;
    bit<16>  hdr_checksum;
    ipv4_addr_t  src_addr;
    ipv4_addr_t  dst_addr;
}

header tcp_h {
    bit<16> src_port;
    bit<16> dst_port;
    bit<32> seq_no;
    bit<32> ack_no;
    bit<4>  data_offset;
    bit<3>  res;
    bit<3>  ecn;
    bit<6>  ctrl;
    bit<16> window;
    bit<16> checksum;
    bit<16> urgent_ptr;
}

header udp_h {
    bit<16> src_port;
    bit<16> dst_port;
    bit<16> length_;
    bit<16> checksum;
}

// GTPU v1
header gtpu_h {
    bit<3>  version;    /* version */
    bit<1>  pt;         /* protocol type */
    bit<1>  reserved;      /* reserved */
    bit<1>  ex_flag;    /* next extension header present? */
    bit<1>  seq_flag;   /* sequence no. */
    bit<1>  npdu_flag;  /* n-pdu number present ? */
    bit<8>  msg_type;    /* message type */
    bit<16> msg_len;     /* message length */
    bit<32>  teid;       /* tunnel endpoint id */
}

header gtpu_options_h {
    bit<16> seq_num;   /* Sequence number */
    bit<8>  n_pdu_num; /* N-PDU number */
    bit<8>  next_ext;  /* Next extension header */
}

// GTPU extension: PDU Session Container (PSC) -- 3GPP TS 38.415 version 15.2.0
// https://www.etsi.org/deliver/etsi_ts/138400_138499/138415/15.02.00_60/ts_138415v150200p.pdf
header gtpu_ext_psc_h {
    bit<8> len;      /* Length in 4-octet units (common to all extensions) */
    bit<4> type;     /* Uplink or downlink */
    bit<4> spare0;   /* Reserved */
    bit<1> ppp;      /* Paging Policy Presence (UL only, not supported) */
    bit<1> rqi;      /* Reflective QoS Indicator (UL only) */
    bit<6> qfi;      /* QoS Flow Identifier */
    bit<8> next_ext;
}

/* Ingress header */
struct my_ingress_headers_t {
    ethernet_h ethernet;
    arp_t arp;
    ipv4_h ipv4;
    tcp_h tcp;
    udp_h udp;
    gtpu_h gtpu;
    gtpu_options_h gtpu_options;
    gtpu_ext_psc_h  gtpu_ext_psc;
}

// Header to move metadata from ingress to egress
header bridged_metadata_t {
    pkt_type_t pkt_type;
    bit<48> ingress_tstamp;
    bit<16> ingress_port;
    bit<16> ucast_egress_port;
    bit<8> meter_color;
    bit<32> drop_count;
}

// Header to move metadata to cloned packet
// Mirror header can be at most 32 bytes long.
header mirror_metadata_t {
    pkt_type_t pkt_type;  // 8 bits
    bit<32> hop_latency; // egress - ingress
    bit<16> ingress_port;
    bit<16> egress_port;
    bit<8> egress_qid;
    bit<24> queue_depth;
    bit<32> teid;
    bit<8> qfi;
    bit<8> meter_color;  // color (GREEN, YELLOW, RED)
    bit<16> packet_length;
    bit<32> drop_count; // Counter for dropped packets (RED)
    bit<48> ingress_tstamp; // Timestamp when packet was received
}

struct my_ingress_metadata_t {
    bridged_metadata_t bridged_md;
    bit<16> qos_flow_idx; // Index for QoS flow (TEID + QFI)
}

header int_report_h {
    bit<8> switch_id;
    bit<32> hop_latency;      // egress - ingress
    bit<8> egress_qid; 
    bit<24> queue_depth;
    bit<16> egress_port;
    bit<16> ingress_port;
    bit<32> teid;  
    bit<8> qfi; // QoS Flow Identifier
    bit<8> meter_color;
    bit<16> packet_length;
    // bit<32> packet_count;
    // bit<32> byte_count; 
    bit<32> drop_count;   
    bit<48> ingress_tstamp; // Timestamp when packet was received  
}

struct my_egress_headers_t {

    // standard headers
    ethernet_h ethernet;
    arp_t arp;
    ipv4_h ipv4;
    tcp_h tcp;
    udp_h udp;
    gtpu_h gtpu;
    gtpu_options_h gtpu_options;
    gtpu_ext_psc_h gtpu_ext_psc;

    // INT report headers
    ethernet_h report_ethernet;
    ipv4_h report_ipv4;
    udp_h report_udp;
    int_report_h report_header;

}


struct my_egress_metadata_t {
    bridged_metadata_t bridged_md;
    mirror_metadata_t mirror_md;
    bit<32> teid;
    bit<8> qfi;
    bit<16> src_port;
    bit<16> dst_port;
    bit<32> hop_latency;
    bit<16> egress_port;
    bit<8> egress_qid;
    bit<24> queue_depth;
    MirrorId_t mirror_session_id;
    pkt_type_t pkt_type;
    pkt_type_t pkt_type_mirror;
    bit<16> packet_length;
    // bit<32> packet_count;
    // bit<32> byte_count;
    bit<16> qos_flow_idx; // Index for QoS flow (TEID + QFI)
    bit<16> lat_q16;   // latency quantized to 16 bits, 1us resolution

    /* For IAT calculation, timestamp of previous packet is looked up from a register
     * This is done independently for each row in the sketch
     * So we need to maintain 4 quantized IAT values
     */
    bit<16> iat_q16_r1;   // IAT quantized to 16 bits, 1us resolution
    bit<16> iat_q16_r2;   
    bit<16> iat_q16_r3;      

    /* Sketch related metadata */
    bit<16> bucket_idx_r1;
    bit<16> bucket_idx_r2;
    bit<16> bucket_idx_r3;

    bit<16> bucket_qidx_r1;
    bit<16> bucket_qidx_r2;
    bit<16> bucket_qidx_r3;


    bit<16> lat_bin_idx_r1;
    bit<16> lat_bin_idx_r2;
    bit<16> lat_bin_idx_r3;

    bit<16> lat_idx_r1;
    bit<16> lat_idx_r2;
    bit<16> lat_idx_r3;


    bit<16> iat_bin_idx_r1;
    bit<16> iat_bin_idx_r2;
    bit<16> iat_bin_idx_r3;

    bit<16> iat_idx_r1;
    bit<16> iat_idx_r2;
    bit<16> iat_idx_r3;
    
}

struct color_pair_t {
    bit<32> green;
    bit<32> yellow;
}

struct traffic_stats_t {
    bit<32> packet_count;
    bit<32> byte_count;
}



#endif /* _HEADERS_ */