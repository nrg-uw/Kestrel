#ifndef _CHECKSUM_
#define _CHECKSUM_

control ComputeIpv4Checksum(inout my_egress_headers_t hdr) {

    Checksum() ipv4_checksum;

    apply {

        /* Update the IPv4 header checksum */
        if (hdr.ipv4.isValid()){
            hdr.ipv4.hdr_checksum = ipv4_checksum.update({ 
                hdr.ipv4.version,
                hdr.ipv4.ihl,
                hdr.ipv4.dscp,
                hdr.ipv4.ecn,
                hdr.ipv4.total_len,
                hdr.ipv4.identification,
                hdr.ipv4.flags,
                hdr.ipv4.frag_offset,
                hdr.ipv4.ttl,
                hdr.ipv4.protocol,
                hdr.ipv4.src_addr,
                hdr.ipv4.dst_addr 
            });
        }
    }
}

#endif /* _CHECKSUM_ */