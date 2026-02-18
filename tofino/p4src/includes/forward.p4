#ifndef _FORWARD_
#define _FORWARD_

control Forward(
    inout my_ingress_headers_t hdr,
    inout my_ingress_metadata_t meta,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {

    DirectCounter<bit<32>>(CounterType_t.PACKETS_AND_BYTES) ipv4_host_counter;

    action send(PortId_t port) {
        ipv4_host_counter.count();
        ig_tm_md.ucast_egress_port = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    action drop() {
        ipv4_host_counter.count();
        ig_dprsr_md.drop_ctl = 1;
    }

    table ipv4_host_table {
        key = {
            hdr.ipv4.dst_addr: exact;
        }
        actions = {
            send;
            @defaultonly drop;
        }
        size = IPV4_HOST_TABLE_SIZE;
        const default_action = drop;
        counters = ipv4_host_counter;
    }

    apply {
        if (hdr.ipv4.isValid()) {
           ipv4_host_table.apply();
        }
    }

}

#endif /* _FORWARD_ */