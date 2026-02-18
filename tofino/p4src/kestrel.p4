#include <core.p4>
#include <tna.p4>

#include "includes/defines.p4"
#include "includes/headers.p4"
#include "includes/util.p4"
#include "includes/parser.p4"
#include "includes/forward.p4"
#include "includes/dmac.p4"
#include "includes/int.p4"
#include "includes/checksum.p4"
#include "includes/queue.p4"
#include "includes/meter.p4"


/*************************************************************************
 **************  I N G R E S S   P R O C E S S I N G   *******************
 *************************************************************************/


/***********************  P A R S E R  **************************/
parser IngressParser(
    packet_in pkt,
    out my_ingress_headers_t hdr,
    out my_ingress_metadata_t meta,
    out ingress_intrinsic_metadata_t ig_intr_md) {
    
    TofinoIngressParser() tofino_parser;
    IPIngressParser() ip_parser;

    state start {
        tofino_parser.apply(pkt, ig_intr_md);
        ip_parser.apply(pkt, hdr, meta, ig_intr_md);
        transition accept;
    }

}


/***************** M A T C H - A C T I O N  *********************/

control Ingress(
    inout my_ingress_headers_t hdr,
    inout my_ingress_metadata_t meta,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_tm_md) {
    
    apply {

        // Initialize bridged metadata 
        meta.bridged_md.setValid();
        meta.bridged_md.pkt_type = PKT_TYPE_NORMAL;
        meta.bridged_md.ingress_tstamp = ig_intr_md.ingress_mac_tstamp;
        meta.bridged_md.ingress_port = (bit<16>) ig_intr_md.ingress_port;
        meta.bridged_md.ucast_egress_port = (bit<16>) ig_tm_md.ucast_egress_port;

        if (hdr.ipv4.isValid()) {
            Forward.apply(hdr, meta, ig_intr_md, ig_prsr_md, ig_dprsr_md, ig_tm_md);
            QueueMapper.apply(hdr, meta, ig_intr_md, ig_prsr_md, ig_dprsr_md, ig_tm_md);

            // Metering and policing
            QoSMeter.apply(hdr, meta, ig_intr_md, ig_prsr_md, ig_dprsr_md, ig_tm_md);
            
            
        } else {
            Dmac.apply(hdr, meta, ig_intr_md, ig_prsr_md, ig_dprsr_md, ig_tm_md);
        }
        
    }
}

/*********************  D E P A R S E R  ************************/

control IngressDeparser(
    packet_out pkt,
    inout my_ingress_headers_t hdr,
    in my_ingress_metadata_t meta,
    in ingress_intrinsic_metadata_for_deparser_t ig_dprsr_md) {

    apply {

        // bridge header for sending metadata from ingress to egress
        pkt.emit(meta.bridged_md); 

        // standard headers
        pkt.emit(hdr.ethernet);
        pkt.emit(hdr.arp);
        pkt.emit(hdr.ipv4);
        pkt.emit(hdr.tcp);
        pkt.emit(hdr.udp);
        pkt.emit(hdr.gtpu);
        pkt.emit(hdr.gtpu_options);
        pkt.emit(hdr.gtpu_ext_psc);

    }
}

/*************************************************************************
 ****************  E G R E S S   P R O C E S S I N G   *******************
 *************************************************************************/



/***********************  P A R S E R  **************************/
parser EgressParser(
    packet_in pkt,
    out my_egress_headers_t hdr,
    out my_egress_metadata_t meta,
    out egress_intrinsic_metadata_t eg_intr_md) {

    TofinoEgressParser() tofino_parser;
    IPEgressParser() ip_parser;

    state start {
        tofino_parser.apply(pkt, eg_intr_md);
        ip_parser.apply(pkt, hdr, meta, eg_intr_md);
        transition accept;
    }
}

/***************** M A T C H - A C T I O N  *********************/

control Egress(
    inout my_egress_headers_t hdr,
    inout my_egress_metadata_t meta,
    in egress_intrinsic_metadata_t eg_intr_md,
    in egress_intrinsic_metadata_from_parser_t eg_prsr_md,
    inout egress_intrinsic_metadata_for_deparser_t eg_dprsr_md,
    inout egress_intrinsic_metadata_for_output_port_t eg_oport_md) {

    apply {
        
        // If normal packet, apply watchlist
        if (meta.pkt_type == PKT_TYPE_NORMAL){
            IntWatchList.apply(hdr, meta, eg_intr_md, eg_prsr_md, eg_dprsr_md, eg_oport_md);
        }

        // If mirror packet, apply postcard
        if (meta.pkt_type == PKT_TYPE_MIRROR){
            IntStats.apply(hdr, meta, eg_intr_md, eg_prsr_md, eg_dprsr_md, eg_oport_md);
            IntPostcard.apply(hdr, meta, eg_intr_md, eg_prsr_md, eg_dprsr_md, eg_oport_md);
        }
        
    }
}

/*********************  D E P A R S E R  ************************/
control EgressDeparser(
    packet_out pkt,
    inout my_egress_headers_t hdr,
    in my_egress_metadata_t meta,
    in egress_intrinsic_metadata_for_deparser_t eg_dprsr_md) {

    Mirror() mirror;

    apply {

        // Clone packet from egress to egress
        // This way postcards can get the egress queue depth of actual traffic
        // If packet is in the watchlist, then MIRROR_TYPE_E2E is set
        if (eg_dprsr_md.mirror_type == MIRROR_TYPE_E2E){
        
            mirror.emit<mirror_metadata_t>(
                
                // The mirror session id decides the output port
                // This should be set using the controller
                meta.mirror_session_id, {

                    // Hack: We need to specify PKT_TYPE_MIRROR here
                    // But since compiler complains about constant in the parser, we set it in metadata and then use it here
                    meta.pkt_type_mirror, 
                    meta.hop_latency,
                    meta.bridged_md.ingress_port,
                    meta.egress_port,
                    meta.egress_qid,
                    meta.queue_depth,
                    meta.teid,
                    meta.qfi,
                    meta.bridged_md.meter_color,
                    meta.packet_length,
                    meta.bridged_md.drop_count,
                    meta.bridged_md.ingress_tstamp
                }
            );
        }


        ComputeIpv4Checksum.apply(hdr);
        
        // INT postcard headers
        pkt.emit(hdr.report_ethernet);
        pkt.emit(hdr.report_ipv4);
        pkt.emit(hdr.report_udp);
        pkt.emit(hdr.report_header);

        // standard headers
        pkt.emit(hdr.ethernet);
        pkt.emit(hdr.arp);
        pkt.emit(hdr.ipv4);
        pkt.emit(hdr.tcp);
        pkt.emit(hdr.udp);
        pkt.emit(hdr.gtpu);
        pkt.emit(hdr.gtpu_options);
        pkt.emit(hdr.gtpu_ext_psc);
    }
}

/*************************************************************************
 ************************ FINAL ASSEMBLY *********************************
**************************************************************************/

Pipeline(
    IngressParser(),
    Ingress(),
    IngressDeparser(),
    EgressParser(),
    Egress(),
    EgressDeparser()
) pipe;

Switch(pipe) main;
