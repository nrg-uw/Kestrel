#ifndef QUEUE_H
#define QUEUE_H

control QueueMapper(
    inout my_ingress_headers_t hdr,
    inout my_ingress_metadata_t meta,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {

    action set_queue(bit<5> qid) {
        ig_tm_md.qid = qid;
    }

    action set_default_queue() {
        ig_tm_md.qid = 7;  // Best-effort default
    }

    table qfi_to_queue_table {
        key = {
            hdr.gtpu_ext_psc.qfi: exact;
        }
        actions = {
            set_queue;
            set_default_queue;
            NoAction;
        }
        size = 64;
        default_action = set_default_queue();
    }



    apply {
        if (hdr.gtpu_ext_psc.isValid()) {
            qfi_to_queue_table.apply();
        } else {
            ig_tm_md.qid = 4;
        }
    }
}



#endif /* QUEUE_H */
