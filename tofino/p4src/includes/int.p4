control IntStats(
    inout my_egress_headers_t hdr,
    inout my_egress_metadata_t meta,
    in egress_intrinsic_metadata_t eg_intr_md,
    in egress_intrinsic_metadata_from_parser_t eg_prsr_md,
    inout egress_intrinsic_metadata_for_deparser_t eg_dprsr_md,
    inout egress_intrinsic_metadata_for_output_port_t eg_oport_md) {
    
    /* ----------------------------------------------------------------------
     * Hashes
     * - We use 3 independent hash functions to map (TEID, QFI) to sketch rows
     * - Each hash function is WIDTH_W = 9 bits wide and maps to [0..511] buckets
     * ---------------------------------------------------------------------- 
     */
    Hash<bit<9>>(HashAlgorithm_t.CRC16) hash_r1;
    Hash<bit<9>>(HashAlgorithm_t.CRC16) hash_r2;
    Hash<bit<9>>(HashAlgorithm_t.CRC16) hash_r3;

    /* ----------------------------------------------------------------------
     * Hash computation
     * - We hash on (TEID, QFI) to get 9 bit bucket index for each row.
     * - We use different seeds for each row to get independent hashes.
     * 
     * Note: We use a 9 bit hash so that bucket_idx is in [0..511] which fits in 512 buckets.
     * But we cast it to 16 bits to avoid bit slicing issues later. 
     * ---------------------------------------------------------------------- 
     */

    action compute_hash_r1(){
        meta.bucket_idx_r1 = (bit<16>) hash_r1.get({2w0, hdr.gtpu.teid, hdr.gtpu_ext_psc.qfi});
    }
    
    action compute_hash_r2(){
        meta.bucket_idx_r2 = (bit<16>) hash_r2.get({2w1, hdr.gtpu.teid, hdr.gtpu_ext_psc.qfi});
    }

    action compute_hash_r3(){
        meta.bucket_idx_r3 = (bit<16>) hash_r3.get({2w2, hdr.gtpu.teid, hdr.gtpu_ext_psc.qfi});
    }

    /* ----------------------------------------------------------------------
     * QID namespacing
     * - We maintain separate sketches per egress queue (QID).
     * - We do this by adding an offset to the bucket index based on QID.
     * - Each QID gets a block of WIDTH_W = 512 buckets.
     * ---------------------------------------------------------------------- 
     */
    action set_bucket_qidx_r1(bit<16> off) {
        meta.bucket_qidx_r1 = meta.bucket_idx_r1 + off;
    }

    table bucket_qidx_tbl_r1 {
        key = { meta.egress_qid : exact; }
        actions = { set_bucket_qidx_r1; }
        size = 8;
        const entries = {
            (0): set_bucket_qidx_r1(0);
            (1): set_bucket_qidx_r1(512);
            (2): set_bucket_qidx_r1(1024);
            (3): set_bucket_qidx_r1(1536);
            (4): set_bucket_qidx_r1(2048);
            (5): set_bucket_qidx_r1(2560);
            (6): set_bucket_qidx_r1(3072);
            (7): set_bucket_qidx_r1(3584);
        }
        const default_action = set_bucket_qidx_r1(0);
    }

    action set_bucket_qidx_r2(bit<16> off) {
        meta.bucket_qidx_r2 = meta.bucket_idx_r2 + off;
    }

    table bucket_qidx_tbl_r2 {
        key = { meta.egress_qid : exact; }
        actions = { set_bucket_qidx_r2; }
        size = 8;
        const entries = {
            (0): set_bucket_qidx_r2(0);
            (1): set_bucket_qidx_r2(512);
            (2): set_bucket_qidx_r2(1024);
            (3): set_bucket_qidx_r2(1536);
            (4): set_bucket_qidx_r2(2048);
            (5): set_bucket_qidx_r2(2560);
            (6): set_bucket_qidx_r2(3072);
            (7): set_bucket_qidx_r2(3584);
        }
        const default_action = set_bucket_qidx_r2(0);
    }

    action set_bucket_qidx_r3(bit<16> off) {
        meta.bucket_qidx_r3 = meta.bucket_idx_r3 + off;
    }

    table bucket_qidx_tbl_r3 {
        key = { meta.egress_qid : exact; }
        actions = { set_bucket_qidx_r3; }
        size = 8;
        const entries = {
            (0): set_bucket_qidx_r3(0);
            (1): set_bucket_qidx_r3(512);
            (2): set_bucket_qidx_r3(1024);
            (3): set_bucket_qidx_r3(1536);
            (4): set_bucket_qidx_r3(2048);
            (5): set_bucket_qidx_r3(2560);
            (6): set_bucket_qidx_r3(3072);
            (7): set_bucket_qidx_r3(3584);
        }
        const default_action = set_bucket_qidx_r3(0);
    }


    /* ------------------------------------------------------------------------------
     * Registers - Traffic stats (packet/byte count)
     * - We maintain 3 sets of registers, one per row.
     * - Each register has width WQ where WQ = W * 8 = 4096 to hold 8 QID namespaced buckets.
     * - For metrics with 8 bins, we use width WLQ = W * 8 * 8 = 32768 to hold 8 bins per bucket.
     * ---------------------------------------------------------------------- 
     */
    Register<traffic_stats_t, bit<16>>(WIDTH_WQ) traffic_stats_r1;
    Register<traffic_stats_t, bit<16>>(WIDTH_WQ) traffic_stats_r2;
    Register<traffic_stats_t, bit<16>>(WIDTH_WQ) traffic_stats_r3;


    RegisterAction<traffic_stats_t, bit<16>, bit<32>> (traffic_stats_r1) bump_traffic_stats_r1 = {
        void apply(inout traffic_stats_t value, out bit<32> read_value) {
            value.packet_count = value.packet_count + 1;
            value.byte_count = value.byte_count + (bit<32>) meta.mirror_md.packet_length;  
            read_value = value.packet_count;
        }
    };

    RegisterAction<traffic_stats_t, bit<16>, bit<32>> (traffic_stats_r2) bump_traffic_stats_r2 = {
        void apply(inout traffic_stats_t value, out bit<32> read_value) {
            value.packet_count = value.packet_count + 1;
            value.byte_count = value.byte_count + (bit<32>) meta.mirror_md.packet_length;  
            read_value = value.packet_count;
        }
    };

    RegisterAction<traffic_stats_t, bit<16>, bit<32>> (traffic_stats_r3) bump_traffic_stats_r3 = {
        void apply(inout traffic_stats_t value, out bit<32> read_value) {
            value.packet_count = value.packet_count + 1;
            value.byte_count = value.byte_count + (bit<32>) meta.mirror_md.packet_length;  
            read_value = value.packet_count;
        }
    };


    action bump_traffic_stats_r1_action() {
        bump_traffic_stats_r1.execute(meta.bucket_qidx_r1);
    }

    action bump_traffic_stats_r2_action() {
        bump_traffic_stats_r2.execute(meta.bucket_qidx_r2);
    }

    action bump_traffic_stats_r3_action() {
        bump_traffic_stats_r3.execute(meta.bucket_qidx_r3);
    }


    /* ----------------------------------------------------------------------
     * Registers - Color counts (green/yellow)
     * - We maintain 3 sets of registers, one per row.
     * - Each register has width WQ where WQ = W * 8 = 4096 to hold 8 QID namespaced buckets.
     * ---------------------------------------------------------------------- 
     */

    Register<color_pair_t, bit<16>>(WIDTH_WQ) color_count_r1;
    Register<color_pair_t, bit<16>>(WIDTH_WQ) color_count_r2;
    Register<color_pair_t, bit<16>>(WIDTH_WQ) color_count_r3;
    

    RegisterAction<color_pair_t, bit<16>, bit<32>>(color_count_r1) bump_color_count_r1 = {
        void apply(inout color_pair_t value, out bit<32> read_value) {
            read_value = value.yellow;
            if (meta.mirror_md.meter_color == COLOR_GREEN) {
                value.green  = value.green + 1;
            } else {
                value.yellow = value.yellow + 1;
            }
        }
    };

    RegisterAction<color_pair_t, bit<16>, bit<32>>(color_count_r2) bump_color_count_r2 = {
        void apply(inout color_pair_t value, out bit<32> read_value) {
            read_value = value.yellow;
            if (meta.mirror_md.meter_color == COLOR_GREEN) {
                value.green  = value.green + 1;
            } else {
                value.yellow = value.yellow + 1;
            }
        }
    };

    RegisterAction<color_pair_t, bit<16>, bit<32>>(color_count_r3) bump_color_count_r3 = {
        void apply(inout color_pair_t value, out bit<32> read_value) {
            read_value = value.yellow;
            if (meta.mirror_md.meter_color == COLOR_GREEN) {
                value.green  = value.green + 1;
            } else {
                value.yellow = value.yellow + 1;
            }
        }
    };


    action bump_color_count_r1_action() {
        bump_color_count_r1.execute(meta.bucket_qidx_r1);
    }

    action bump_color_count_r2_action() {
        bump_color_count_r2.execute(meta.bucket_qidx_r2);
    }

    action bump_color_count_r3_action() {
        bump_color_count_r3.execute(meta.bucket_qidx_r3);
    }


    /* ------------------------------------------------------------------------------
     * Latency histogram
     * - We maintain 3 sets of registers, one per row.
     * - Each register has width WLQ where WLQ = W * 8 * 8 = 32768 to hold 8 bins per QID namespaced bucket.
     * ------------------------------------------------------------------------------ 
     */

    /* First we need to select which latency bin (0..7) the latency falls into based on (latency, qid) 
     * We use the range match feature of tables to do this.
     * The latency ranges are set by the control plane per QID.
     * We store the selected bin in meta.lat_bin_idx_rX.
     */

    action set_latency_bin_idx_r1(bit<16> bin) {
        meta.lat_bin_idx_r1 = bin;
        meta.lat_idx_r1 = (bit<16>) meta.bucket_qidx_r1 << 3;
    }

    table lat_bin_table_r1 {
        key = {
            meta.lat_q16 : range;
            meta.egress_qid : exact;
        }
        actions = {
            set_latency_bin_idx_r1;
        }
        size = 64;  /* 8 QIDs x 8 bins = 64 entries */
        const default_action = set_latency_bin_idx_r1(7);  /* Default action: put in highest bin */
    }

    action set_latency_bin_idx_r2(bit<16> bin) {
        meta.lat_bin_idx_r2 = bin;
        meta.lat_idx_r2 = (bit<16>) meta.bucket_qidx_r2 << 3;
    }

    table lat_bin_table_r2 {
        key = {
            meta.lat_q16 : range;
            meta.egress_qid : exact;
        }
        actions = {
            set_latency_bin_idx_r2;
        }
        size = 64;  /* 8 QIDs x 8 bins = 64 entries */
        const default_action = set_latency_bin_idx_r2(7);  /* Default action: put in highest bin */
    }

    action set_latency_bin_idx_r3(bit<16> bin) {
        meta.lat_bin_idx_r3 = bin;
        meta.lat_idx_r3 = (bit<16>) meta.bucket_qidx_r3 << 3;
    }

    table lat_bin_table_r3 {
        key = {
            meta.lat_q16 : range;
            meta.egress_qid : exact;
        }
        actions = {
            set_latency_bin_idx_r3;
        }
        size = 64;  /* 8 QIDs x 8 bins = 64 entries */
        const default_action = set_latency_bin_idx_r3(7);  /* Default action: put in highest bin */
    }

    /* Now we can set the final index into the latency histogram register */
    action set_lat_idx_r1() {
        meta.lat_idx_r1 = meta.lat_idx_r1 + meta.lat_bin_idx_r1;
    }

    action set_lat_idx_r2() {
        meta.lat_idx_r2 = meta.lat_idx_r2 + meta.lat_bin_idx_r2;
    }

    action set_lat_idx_r3() {
        meta.lat_idx_r3 = meta.lat_idx_r3 + meta.lat_bin_idx_r3;
    }

    /* Now that we have the final index, we can bump the counter in the latency histogram */

    Register<register_entry_t, bit<16>>(WIDTH_WLQ) lat_hist_r1;
    Register<register_entry_t, bit<16>>(WIDTH_WLQ) lat_hist_r2;
    Register<register_entry_t, bit<16>>(WIDTH_WLQ) lat_hist_r3;

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(lat_hist_r1) bump_lat_r1 = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
        value = value + 1;
        read_value = value;
        }
    };

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(lat_hist_r2) bump_lat_r2 = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
        value = value + 1;
        read_value = value;
        }
    };

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(lat_hist_r3) bump_lat_r3 = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
        value = value + 1;
        read_value = value;
        }
    };


    action bump_lat_r1_action() {
        bump_lat_r1.execute(meta.lat_idx_r1);
    }

    action bump_lat_r2_action() {
        bump_lat_r2.execute(meta.lat_idx_r2);  
    }

    action bump_lat_r3_action() {
        bump_lat_r3.execute(meta.lat_idx_r3);  
    }


    /* --------------------------------------------------------------------------------------
     * IAT calculation:
     * - We maintain a register of previous timestamp per bucket.
     * - For each packet, we look up the previous timestamp for the bucket it hashes to.
     * - We compute IAT as (now - previous).
     * - We store the current timestamp back into the register for the next packet.
     * 
     * Note: This is done independently for each row in the sketch.
     * So we need 4 separate registers and actions.
     * ------------------------------------------------------------------------------------
     */

    Register<register_entry_t, bit<16>>(WIDTH_WQ) prev_ts_us_r1;
    Register<register_entry_t, bit<16>>(WIDTH_WQ) prev_ts_us_r2;
    Register<register_entry_t, bit<16>>(WIDTH_WQ) prev_ts_us_r3;

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(prev_ts_us_r1) compute_iat_us_r1 = {
        void apply(inout register_entry_t prev_us, out register_entry_t iat_us) {
            // Current packet timestamp in ~microsecond-ish units
            // Shift right by 10 to convert from ns to ~us
            register_entry_t now_us = (register_entry_t)(meta.mirror_md.ingress_tstamp >> IAT_SHIFT_NS);
            iat_us = now_us - prev_us;
            prev_us = now_us;
        }
    };

    
    RegisterAction<register_entry_t, bit<16>, register_entry_t>(prev_ts_us_r2) compute_iat_us_r2 = {
        void apply(inout register_entry_t prev_us, out register_entry_t iat_us) {
            register_entry_t now_us = (register_entry_t)(meta.mirror_md.ingress_tstamp >> IAT_SHIFT_NS);
            iat_us = now_us - prev_us;
            prev_us = now_us;
        }
    };

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(prev_ts_us_r3) compute_iat_us_r3 = {
        void apply(inout register_entry_t prev_us, out register_entry_t iat_us) {
            register_entry_t now_us = (register_entry_t)(meta.mirror_md.ingress_tstamp >> IAT_SHIFT_NS);
            iat_us = now_us - prev_us;
            prev_us = now_us;
        }
    };

    /* Note: do we need saturation logic here? 
     * Should be OK because we are interested in small IATs only for anomaly detection.
     */
    action compute_iat_us_r1_action() {
        meta.iat_q16_r1 = (iat16_t) compute_iat_us_r1.execute(meta.bucket_qidx_r1);
    }
    
    action compute_iat_us_r2_action() {
        meta.iat_q16_r2 = (iat16_t) compute_iat_us_r2.execute(meta.bucket_qidx_r2);
    }

    action compute_iat_us_r3_action() {
        meta.iat_q16_r3 = (iat16_t) compute_iat_us_r3.execute(meta.bucket_qidx_r3);
    }

    /* --------------------------------------------------------------------------------------
     * IAT histogram:
     * - We maintain 3 sets of registers, one per row.
        * - Each register has width WLQ where WLQ = W * 8 * 8 = 32768 to hold 8 bins per QID namespaced bucket.
     * - We use range match to map (IAT, QID) to a bin (0..7).
     * - We add the bin to the pre-shifted bucket index to get the final index.
     * - We bump the counter in the IAT histogram register at that index.
     * ------------------------------------------------------------------------------------
     */

    /* First we need to select which IAT bin (0..7) the IAT falls into based on (IAT, qid) 
     * We use the range match feature of tables to do this.
     * The IAT ranges are set by the control plane per QID.
     * We store the selected bin in meta.iat_bin_idx_rX.

     * Note: In contrast to latency, we have different iat_q16_rX values for each row.
     * This is because IAT is computed based on previous timestamp per row.
     */

    action set_iat_bin_idx_r1(bit<16> bin) {
        meta.iat_bin_idx_r1 = bin;
        meta.iat_idx_r1 = (bit<16>) meta.bucket_qidx_r1 << 3;
    }

    table iat_bin_table_r1 {
        key = {
            meta.iat_q16_r1: range; 
            meta.egress_qid: exact;
        }
        actions = { set_iat_bin_idx_r1; }
        size = 64;
        
        const default_action = set_iat_bin_idx_r1(0);  // short IAT -> bin 0
    }

    action set_iat_bin_idx_r2(bit<16> bin) {
        meta.iat_bin_idx_r2 = bin;
        meta.iat_idx_r2 = (bit<16>) meta.bucket_qidx_r2 << 3;
    }

    table iat_bin_table_r2 {
        key = {
            meta.iat_q16_r2: range; 
            meta.egress_qid: exact;
        }
        actions = { set_iat_bin_idx_r2; }
        size = 64;
        
        const default_action = set_iat_bin_idx_r2(0);
    }

    action set_iat_bin_idx_r3(bit<16> bin) {
        meta.iat_bin_idx_r3 = bin;
        meta.iat_idx_r3 = (bit<16>) meta.bucket_qidx_r3 << 3;
    }

    table iat_bin_table_r3 {
        key = {
            meta.iat_q16_r3: range; 
            meta.egress_qid: exact;
        }
        actions = { set_iat_bin_idx_r3; }
        size = 64;
        
        const default_action = set_iat_bin_idx_r3(0);  // short IAT -> bin 0
    }

    /* Now we can set the final index into the IAT histogram register */
    action set_iat_idx_r1() {
        meta.iat_idx_r1 = meta.iat_idx_r1 + meta.iat_bin_idx_r1;
    }

    action set_iat_idx_r2() {
        meta.iat_idx_r2 = meta.iat_idx_r2 + meta.iat_bin_idx_r2;
    }

    action set_iat_idx_r3() {
        meta.iat_idx_r3 = meta.iat_idx_r3 + meta.iat_bin_idx_r3;
    }

    /* Now that we have the final index, we can bump the counter in the IAT histogram register */


    Register<register_entry_t, bit<16>>(WIDTH_WLQ) iat_hist_r1;
    Register<register_entry_t, bit<16>>(WIDTH_WLQ) iat_hist_r2;
    Register<register_entry_t, bit<16>>(WIDTH_WLQ) iat_hist_r3;

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(iat_hist_r1) bump_iat_r1 = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
            value = value + 1;
            read_value = value;
        }
    };

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(iat_hist_r2) bump_iat_r2 = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
            value = value + 1;
            read_value = value;
        }
    };

    RegisterAction<register_entry_t, bit<16>, register_entry_t>(iat_hist_r3) bump_iat_r3 = {
        void apply(inout register_entry_t value, out register_entry_t read_value) {
            value = value + 1;
            read_value = value;
        }
    };

    action bump_iat_r1_action() {
        bump_iat_r1.execute(meta.iat_idx_r1);
    }

    action bump_iat_r2_action() {
        bump_iat_r2.execute(meta.iat_idx_r2);
    }

    action bump_iat_r3_action() {
        bump_iat_r3.execute(meta.iat_idx_r3);
    }


    apply {

        /* 1) Compute bucket indices (raw) for each row */
        compute_hash_r1();
        compute_hash_r2();
        compute_hash_r3();
        

        /* 2) Namespace by QID */
        bucket_qidx_tbl_r1.apply();
        bucket_qidx_tbl_r2.apply();
        bucket_qidx_tbl_r3.apply();

        /* 3) Bump traffic stats and color counts */
        bump_traffic_stats_r1_action();
        bump_color_count_r1_action();
        bump_traffic_stats_r2_action();
        bump_color_count_r2_action();
        bump_traffic_stats_r3_action();
        bump_color_count_r3_action();

        /* 4) Compute latency */
        /* 4a) Convert latency to ~microsecond-ish units
         * We shift right by LAT_SHIFT_NS to convert from ns to ~us
         * This gives us a latency value in the range [0..65535] for latencies up to ~65ms

         * Note: We quantize to 16 bits because the range match in the table only allows up to 16 bits.
         */
        meta.lat_q16 = (bit<16>)(meta.hop_latency >> LAT_SHIFT_NS);
        /* 4b) Compute latency bin */
        lat_bin_table_r1.apply();
        lat_bin_table_r2.apply();
        lat_bin_table_r3.apply();
        /* 4c) Set final index into latency histogram */
        set_lat_idx_r1();
        set_lat_idx_r2();
        set_lat_idx_r3();
        /* 4d) Bump latency histogram */
        bump_lat_r1_action();
        bump_lat_r2_action();
        bump_lat_r3_action();

        /* 5) Compute IAT */
        /* 5a) Compute IAT in ~microsecond-ish units */
        compute_iat_us_r1_action();
        compute_iat_us_r2_action();
        compute_iat_us_r3_action();
        /* 5b) Compute IAT bin */
        iat_bin_table_r1.apply();
        iat_bin_table_r2.apply();
        iat_bin_table_r3.apply();
        /* 5c) Set final index into IAT histogram */
        set_iat_idx_r1();
        set_iat_idx_r2();
        set_iat_idx_r3();
        /* 5d) Bump IAT histogram */
        bump_iat_r1_action();
        bump_iat_r2_action();
        bump_iat_r3_action();
        
    }

}



control IntWatchList(
    inout my_egress_headers_t hdr,
    inout my_egress_metadata_t meta,
    in egress_intrinsic_metadata_t eg_intr_md,
    in egress_intrinsic_metadata_from_parser_t eg_prsr_md,
    inout egress_intrinsic_metadata_for_deparser_t eg_dprsr_md,
    inout egress_intrinsic_metadata_for_output_port_t eg_oport_md) {

    DirectCounter<bit<32>>(CounterType_t.PACKETS_AND_BYTES) int_watchlist_counter;

    action mark_to_report() {

        meta.hop_latency = (bit<32>)(eg_prsr_md.global_tstamp - meta.bridged_md.ingress_tstamp);
        meta.egress_port = (bit<16>) eg_intr_md.egress_port;
        meta.egress_qid = (bit<8>) eg_intr_md.egress_qid;
        meta.queue_depth = (bit<24>) eg_intr_md.enq_qdepth;
        meta.mirror_session_id = 1;

        // Subtract 4 bytes for the CRC
        // This makes it in-line with what Wireshark reports
        meta.packet_length = eg_intr_md.pkt_length - 4; 

        // Set the mirror type to 2
        // Section 5.3 of Tofino Native Arch, setting mirror_type sets it as valid
        eg_dprsr_md.mirror_type = MIRROR_TYPE_E2E;
        meta.pkt_type_mirror = PKT_TYPE_MIRROR;
     
        int_watchlist_counter.count();

    }

    table int_watchlist_table {
        key = {
            hdr.ipv4.src_addr: exact;
            hdr.ipv4.dst_addr: exact;
            hdr.ipv4.protocol: exact;
            meta.src_port: ternary;
            meta.dst_port: ternary;
        }
        actions = {
            mark_to_report;
            @defaultonly NoAction;
        }
        size = INT_EGRESS_TABLE_SIZE;
        const default_action = NoAction;
        counters = int_watchlist_counter;
    }

    apply {
        int_watchlist_table.apply();
    }
}




control IntPostcard(
    inout my_egress_headers_t hdr,
    inout my_egress_metadata_t meta,
    in egress_intrinsic_metadata_t eg_intr_md,
    in egress_intrinsic_metadata_from_parser_t eg_prsr_md,
    inout egress_intrinsic_metadata_for_deparser_t eg_dprsr_md,
    inout egress_intrinsic_metadata_for_output_port_t eg_oport_md) {

    DirectCounter<bit<32>>(CounterType_t.PACKETS_AND_BYTES) int_postcard_counter;

    /* INT Report structure */
    /* [Eth][IP][UDP][INT REPORT] */
    action generate_postcard(bit<48> src_mac, bit<32> src_ip, bit<48> collector_mac, bit<32> collector_ip, bit<16> collector_port) {

        // Ethernet **********************************************************
        hdr.report_ethernet.setValid();
        hdr.report_ethernet.dst_addr = collector_mac;
        hdr.report_ethernet.src_addr = src_mac;
        hdr.report_ethernet.ether_type = ether_type_t.IPV4;

        // IPv4 **************************************************************
        hdr.report_ipv4.setValid();
        hdr.report_ipv4.version = 4;
        hdr.report_ipv4.ihl = 5;
        hdr.report_ipv4.dscp = 0;
        hdr.report_ipv4.ecn = 0;

        // [IP header = 20][UDP header = 8][INT REPORT]
        hdr.report_ipv4.total_len = (bit<16>)(20 + 8 + INT_REPORT_LENGTH_BYTES);

        hdr.report_ipv4.identification = 0;
        hdr.report_ipv4.flags = 0;
        hdr.report_ipv4.frag_offset = 0;
        hdr.report_ipv4.ttl = 64;
        hdr.report_ipv4.protocol = 17; // UDP
        hdr.report_ipv4.src_addr = src_ip;
        hdr.report_ipv4.dst_addr = collector_ip;

        // UDP ***************************************************************
        hdr.report_udp.setValid();
        hdr.report_udp.src_port = 8001;
        hdr.report_udp.dst_port = collector_port;
        hdr.report_udp.checksum = 0;  // Disable checksum, RFC 768

        hdr.report_udp.length_ = (bit<16>)(8 + INT_REPORT_LENGTH_BYTES);

        // INT report header ************************************************
        // If this is a mirror packet, it should be carrying the mirror header
        // which contains fields from the original packet
        hdr.report_header.setValid();
        hdr.report_header.switch_id = SWITCH_ID;
        hdr.report_header.hop_latency = meta.mirror_md.hop_latency;
        hdr.report_header.egress_qid = meta.mirror_md.egress_qid;
        hdr.report_header.queue_depth = meta.mirror_md.queue_depth;

        hdr.report_header.egress_port = meta.mirror_md.egress_port;
        hdr.report_header.ingress_port = meta.mirror_md.ingress_port;
        hdr.report_header.teid = meta.mirror_md.teid;
        hdr.report_header.qfi = meta.mirror_md.qfi;
        hdr.report_header.meter_color = meta.mirror_md.meter_color;

        hdr.report_header.packet_length = meta.mirror_md.packet_length;

        // hdr.report_header.packet_count = meta.packet_count;
        // hdr.report_header.byte_count = meta.byte_count;

        hdr.report_header.drop_count = meta.mirror_md.drop_count;
        hdr.report_header.ingress_tstamp = meta.mirror_md.ingress_tstamp;



        // Set payload as invalid
        // We don't need the payload in the postcard
        hdr.ethernet.setInvalid();
        hdr.ipv4.setInvalid();
        hdr.tcp.setInvalid();
        hdr.udp.setInvalid();
        hdr.gtpu.setInvalid();
        hdr.gtpu_options.setInvalid();
        hdr.gtpu_ext_psc.setInvalid();
        

        int_postcard_counter.count();
    }

    table int_postcard_table {
        key = {
            meta.pkt_type: exact;  // match on PKT_TYPE_MIRROR
        }
        actions = {
            generate_postcard;
            @defaultonly NoAction;
        }
        size = INT_EGRESS_TABLE_SIZE;
        const default_action = NoAction;
        counters = int_postcard_counter;
    }

    apply {
        int_postcard_table.apply();
    }


}