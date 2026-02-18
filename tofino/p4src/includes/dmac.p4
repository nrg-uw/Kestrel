#ifndef _DMAC_
#define _DMAC_

control Dmac(
    inout my_ingress_headers_t hdr,
    inout my_ingress_metadata_t meta,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {

    DirectCounter<bit<32>>(CounterType_t.PACKETS_AND_BYTES) dmac_broadcast_counter;
    DirectCounter<bit<32>>(CounterType_t.PACKETS_AND_BYTES) dmac_counter;

    action send(PortId_t port) {
        dmac_counter.count();
        ig_tm_md.ucast_egress_port = port;
    }   

    action set_mcast_grp(MulticastGroupId_t mcast_grp) {
        dmac_broadcast_counter.count();
        ig_tm_md.mcast_grp_a = mcast_grp;
    }

    table dmac_table {
        key = {
            hdr.ethernet.dst_addr:exact;
        }

        actions = {
            send;
            @defaultonly NoAction;
        }
        size = DMAC_TABLE_SIZE;
        default_action = NoAction;
        counters = dmac_counter;
    }

    table broadcast_table {
        key = {
            ig_intr_md.ingress_port: exact;
        }

        actions = {
            set_mcast_grp;
            @defaultonly NoAction;
        }
        size = BROADCAST_TABLE_SIZE;
        default_action = NoAction;
        counters = dmac_broadcast_counter;
    }
    

    apply {

        if (dmac_table.apply().miss){
            broadcast_table.apply();
        }
        
    }

}

#endif /* _DMAC_ */