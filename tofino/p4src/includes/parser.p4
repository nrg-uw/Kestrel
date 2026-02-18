#ifndef _PARSER_
#define _PARSER_

parser IPIngressParser(
    packet_in pkt,
    out my_ingress_headers_t hdr,
    out my_ingress_metadata_t meta,
    out ingress_intrinsic_metadata_t ig_intr_md) {

    state start {
        meta.bridged_md.setInvalid();
        meta.qos_flow_idx = 0;
        transition parse_ethernet;
        
    }

    state parse_ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            ether_type_t.ARP: parse_arp;
            ether_type_t.IPV4:  parse_ipv4;
            default: accept;
        }
    }

    state parse_arp {
        pkt.extract(hdr.arp);
        transition accept;
    }

    state parse_ipv4 {
        pkt.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            ip_protocol_t.TCP: parse_tcp;
            ip_protocol_t.UDP: parse_udp;
            default: accept;
        }
    }

    state parse_tcp {
        pkt.extract(hdr.tcp);
        transition accept;
    }

    state parse_udp {
        pkt.extract(hdr.udp);
        transition select(hdr.udp.dst_port) {
            UDP_PORT_N3: parse_gtpu;
            default: accept;
        }
    }


    state parse_gtpu {
        pkt.extract(hdr.gtpu);
        transition select(hdr.gtpu.ex_flag, hdr.gtpu.seq_flag, hdr.gtpu.npdu_flag){
            (0, 0, 0)   :  accept;
            default     :  parse_gtpu_options;
        }
    }

    state parse_gtpu_options {
        pkt.extract(hdr.gtpu_options);
        bit<8> gtpu_ext_len = pkt.lookahead<bit<8>>();
        transition select(hdr.gtpu_options.next_ext, gtpu_ext_len){
            (GTPU_NEXT_EXT_PSC, GTPU_EXT_PSC_LEN): parse_gtpu_ext_psc;
            default: accept;
        }
    }

    state parse_gtpu_ext_psc{
        pkt.extract(hdr.gtpu_ext_psc);
        transition accept;
    }

}

parser IPEgressParser(
    packet_in pkt,
    out my_egress_headers_t hdr,
    out my_egress_metadata_t meta,
    out egress_intrinsic_metadata_t eg_intr_md) {

    state start {

        /* Set metadata to invalid/default values */
        meta.bridged_md.setInvalid();
        meta.mirror_md.setInvalid();
        meta.teid = 0;
        meta.qfi = 0;
        meta.src_port = 0;
        meta.dst_port = 0;
        meta.hop_latency = 0;
        meta.egress_port = 0;
        meta.egress_qid = 0;
        meta.queue_depth = 0;
        meta.mirror_session_id = 0;
        meta.pkt_type = PKT_TYPE_DEFAULT;
        meta.pkt_type_mirror = 0;
        meta.packet_length = 0;
        meta.qos_flow_idx = 0;
        meta.lat_q16 = 0;
        meta.iat_q16_r1 = 0;
        meta.iat_q16_r2 = 0;
        meta.iat_q16_r3 = 0;
        meta.bucket_idx_r1 = 0;
        meta.bucket_idx_r2 = 0;
        meta.bucket_idx_r3 = 0;
        meta.bucket_qidx_r1 = 0;
        meta.bucket_qidx_r2 = 0;
        meta.bucket_qidx_r3 = 0;
        meta.lat_bin_idx_r1 = 0;
        meta.lat_bin_idx_r2 = 0;
        meta.lat_bin_idx_r3 = 0;
        meta.lat_idx_r1 = 0;
        meta.lat_idx_r2 = 0;
        meta.lat_idx_r3 = 0;
        meta.iat_bin_idx_r1 = 0;
        meta.iat_bin_idx_r2 = 0;
        meta.iat_bin_idx_r3 = 0;
        meta.iat_idx_r1 = 0;
        meta.iat_idx_r2 = 0;
        meta.iat_idx_r3 = 0;



        pkt_type_t pkt_type = pkt.lookahead<pkt_type_t>();
        transition select(pkt_type) {
            PKT_TYPE_NORMAL: parse_bridge;
            PKT_TYPE_MIRROR: parse_mirror;
            default: parse_bridge;
        }
        
    }

    state parse_mirror {
        pkt.extract(meta.mirror_md);
        meta.pkt_type = PKT_TYPE_MIRROR;
        transition accept;
    }

    state parse_bridge {
        pkt.extract(meta.bridged_md);
        meta.pkt_type = PKT_TYPE_NORMAL;
        transition parse_ethernet;
    }

    state parse_ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            ether_type_t.ARP: parse_arp;
            ether_type_t.IPV4:  parse_ipv4;
            default: accept;
        }
    }

    state parse_arp {
        pkt.extract(hdr.arp);
        transition accept;
    }

    state parse_ipv4 {
        pkt.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            ip_protocol_t.TCP: parse_tcp;
            ip_protocol_t.UDP: parse_udp;
            default: accept;
        }
    }

    state parse_tcp {
        pkt.extract(hdr.tcp);
        meta.src_port = hdr.tcp.src_port;
        meta.dst_port = hdr.tcp.dst_port;
        transition accept;
    }

    state parse_udp {
        pkt.extract(hdr.udp);
        meta.src_port = hdr.udp.src_port;
        meta.dst_port = hdr.udp.dst_port;
        transition select(hdr.udp.dst_port) {
            UDP_PORT_N3: parse_gtpu;
            default: accept;
        }
    }


    state parse_gtpu {
        pkt.extract(hdr.gtpu);
        meta.teid = hdr.gtpu.teid;
        transition select(hdr.gtpu.ex_flag, hdr.gtpu.seq_flag, hdr.gtpu.npdu_flag){
            (0, 0, 0)   :  accept;
            default     :  parse_gtpu_options;
        }
    }

    state parse_gtpu_options {
        pkt.extract(hdr.gtpu_options);
        bit<8> gtpu_ext_len = pkt.lookahead<bit<8>>();
        transition select(hdr.gtpu_options.next_ext, gtpu_ext_len){
            (GTPU_NEXT_EXT_PSC, GTPU_EXT_PSC_LEN): parse_gtpu_ext_psc;
            default: accept;
        }
    }

    state parse_gtpu_ext_psc{
        pkt.extract(hdr.gtpu_ext_psc);
        meta.qfi = (bit<8>) hdr.gtpu_ext_psc.qfi;
        transition accept;
    }

}

#endif /* _PARSER_ */