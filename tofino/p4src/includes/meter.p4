#ifndef METER_H
#define METER_H

control QoSMeter(
    inout my_ingress_headers_t hdr,
    inout my_ingress_metadata_t meta,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {

    DirectMeter(MeterType_t.BYTES) direct_meter;
    Register<register_entry_t, register_index_t>(REGISTER_SIZE, REGISTER_VALUE) drop_count_register;
    // Hash<register_index_t>(HashAlgorithm_t.CRC16) drop_count_hash;

    /* RegisterAction<type, index, return type> */
    RegisterAction<register_entry_t,register_index_t,register_entry_t> (drop_count_register) increment_drop_count = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
            value = value + 1;  
            read_value = value;
        }
    };

    /* RegisterAction<type, index, return type> */
    RegisterAction<register_entry_t,register_index_t,register_entry_t> (drop_count_register) read_drop_count = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
            read_value = value;
        }
    };
    
    
    /* Bit of a misnomer, but here we don't set the color directly.
     * We set the TrTCM params (CIR, PIR, CBS, PBS) based on which the color is determined.
     */
    action set_color() {
        // Execute the direct meter and write the color to metadata.
        meta.bridged_md.meter_color = direct_meter.execute();
    }

    table meter_table {
        key = {
            hdr.gtpu.teid : exact;
            hdr.gtpu_ext_psc.qfi : exact;
        }

        actions = {
            set_color;
        }

        meters = direct_meter;
        size = 1024;
    }

    action drop() {
        // Increment per-TEID+QFI drop counter
        register_entry_t drop_count = increment_drop_count.execute(meta.qos_flow_idx);

        // This is not used. RED packets are dropped, so they can't carry drop count.
        // However, we still update the metadata for consistency.
        // We need to get YELLOW or GREEN packets to carry the drop count.
        meta.bridged_md.drop_count = drop_count;
        ig_dprsr_md.drop_ctl = 1;
    }

    action count_drop() {
        register_entry_t drop_count = read_drop_count.execute(meta.qos_flow_idx);
        meta.bridged_md.drop_count = drop_count;
    }

    action nop() {
        // Do nothing
    }

    // In the context of QoS, we can use this to direct YELLOW packets to 
    // the best-effort queue.
    action set_queue(bit<5> qid) {
        ig_tm_md.qid = qid;
        count_drop();
    }

    // Minimal QoS enforcement table for demonstration.
    // This table is used to drop or deprioritize packets based on the color set by the meter.
    table qos_table {
        key = {
            meta.bridged_md.meter_color : exact;
        }

        actions = {
            drop;
            set_queue;
            count_drop;
            @defaultonly nop;
        }

        const default_action = nop;
        size = 8;
    }


    apply {
        
        // Compute the QoS flow index based on TEID and QFI.
        // Used later in QoS table for drop count.
        // meta.qos_flow_idx = drop_count_hash.get({hdr.gtpu.teid, hdr.gtpu_ext_psc.qfi});
        meta.qos_flow_idx = (bit<16>)(hdr.gtpu.teid << 4) | (bit<16>)(hdr.gtpu_ext_psc.qfi & 0xF);
        // ----------------------------------------------------------------------------
        // TrTCM based on TEID.
        // ----------------------------------------------------------------------------
        meter_table.apply();
        qos_table.apply();


    }
}



#endif /* METER_H */
