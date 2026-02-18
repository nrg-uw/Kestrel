package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"math/rand"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

/************** Discovery & config **************/

const (
	tmpDirDefault     = "/tmp"
	qfiFilePatternStr = `^qfi_ue(\d+)_qfi(\d+)\.txt$`
	GTPUPort          = 2152
	GTPUHdrLen        = 16
	MaxFrame          = 1500
)

var (
	qfiFileRe = regexp.MustCompile(qfiFilePatternStr)

	// Env overrides for L2/L3 endpoints used on HPC3<->switch path
	SrcMACStr = envOr("SRC_MAC", "a0:36:9f:ba:36:ac")
	DstMACStr = envOr("DST_MAC", "e4:1d:2d:09:a8:30")
	SrcIPStr  = envOr("SRC_IP",  "192.168.44.13")
	DstIPStr  = envOr("DST_IP",  "192.168.44.18")
)

func envOr(k, def string) string { if v := os.Getenv(k); v != "" { return v }; return def }
func nowNs() int64               { return time.Now().UnixNano() }

func discoverExistingForQFI(tmpDir string, targetQFI int) ([]int, error) {
	ents, err := os.ReadDir(tmpDir)
	if err != nil { return nil, err }
	seen := map[int]struct{}{}
	var teids []int
	for _, e := range ents {
		if e.IsDir() { continue }
		if qfiFileRe.FindStringSubmatch(e.Name()) == nil { continue }
		var ueIdx, qfi int
		if _, err := fmt.Sscanf(e.Name(), "qfi_ue%d_qfi%d.txt", &ueIdx, &qfi); err != nil { continue }
		if qfi != targetQFI { continue }
		teid := ueIdx + 1
		if _, ok := seen[teid]; !ok {
			seen[teid] = struct{}{}
			teids = append(teids, teid)
		}
	}
	return teids, nil
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
	copy(buf[0:6], s.dstMac[:]); copy(buf[6:12], s.srcMac[:]); binary.BigEndian.PutUint16(buf[12:14], 0x0800)
	// IPv4
	off := 14
	ipLen := uint16(20 + 8 + GTPUHdrLen + len(inner))
	buf[off+0] = (4 << 4) | 5
	binary.BigEndian.PutUint16(buf[off+2:off+4], ipLen)
	binary.BigEndian.PutUint16(buf[off+6:off+8], 0)
	buf[off+8] = 64; buf[off+9] = 17
	binary.BigEndian.PutUint32(buf[off+12:off+16], s.srcIP)
	binary.BigEndian.PutUint32(buf[off+16:off+20], s.dstIP)
	cs := checksum(buf[off : off+20]); binary.BigEndian.PutUint16(buf[off+10:off+12], cs)
	// UDP
	off = 14 + 20
	binary.BigEndian.PutUint16(buf[off+0:off+2], 12345)
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(GTPUPort))
	udpLen := uint16(8 + GTPUHdrLen + len(inner))
	binary.BigEndian.PutUint16(buf[off+4:off+6], udpLen)
	// GTP-U + PDU Session Container (QFI)
	off = 14 + 20 + 8
	buf[off+0] = 0x34 // v1, PT=1, E=1, S=1
	buf[off+1] = 0xFF // T-PDU
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(len(inner)+8))
	binary.BigEndian.PutUint32(buf[off+4:off+8], teid)
	buf[off+11] = 0x85 // PDU Session Container
	buf[off+12] = 0x01 // length
	buf[off+13] = 0x10 // flags: QFI present
	buf[off+14] = qfi & 0x3F
	copy(buf[off+GTPUHdrLen:], inner)
	return unix.Sendto(s.fd, buf, 0, &s.sa)
}

/************** Microburst pacing **************/

func jitterPctInt(v, pct int) int {
	if pct <= 0 { return v }
	span := v * pct / 100
	delta := rand.Intn(2*span+1) - span
	if v+delta < 1 { return 1 }
	return v + delta
}
func jitterPctFloat64(v float64, pct int) float64 {
	if pct <= 0 || v == 0 { return v }
	span := v * float64(pct) / 100.0
	return v + (rand.Float64()*2*span - span)
}

// burstLoop: send QFI-marked GTP-U packets at ~pps, with ON/OFF (burst/idle) cycles until ctx done.
func burstLoop(ctx context.Context, rs *RawSender, teid uint32, qfi uint8, innerLen int, pps float64, burstMs, idleMs int) {
	if pps < 0 { pps = 0 }
	if innerLen < 1 { innerLen = 1200 }
	payload := make([]byte, innerLen)

	// Precompute spacing
	var gap time.Duration
	if pps > 0 {
		gap = time.Duration(float64(time.Second) / pps)
	}

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		// ON
		onUntil := time.Now().Add(time.Duration(burstMs) * time.Millisecond)
		for time.Now().Before(onUntil) {
			_ = rs.SendGTPU(teid, qfi, payload)
			if pps <= 0 {
				// best-effort: small yield to avoid pegging CPU
				time.Sleep(100 * time.Microsecond)
			} else {
				t := time.NewTimer(gap)
				select {
				case <-ctx.Done():
					t.Stop(); return
				case <-t.C:
				}
			}
		}

		// OFF
		if idleMs > 0 {
			select {
			case <-time.After(time.Duration(idleMs) * time.Millisecond):
			case <-ctx.Done():
				return
			}
		}
	}
}

/************** Main **************/

func main() {
	rand.Seed(time.Now().UnixNano())

	// Core flags
	qfi := flag.Int("qfi", 0, "Target QFI (required)")
	nFlows := flag.Int("n", 12, "Number of TEIDs to burst on (capped to available)")
	durationMs := flag.Int("duration-ms", 500, "Episode duration in milliseconds (>0)")
	iface := flag.String("iface", "enp2s0f0", "Network interface")

	// Traffic shape (per TEID)
	pps := flag.Float64("pps", 200_000, "Packets per second per TEID (0=best-effort)")
	innerLen := flag.Int("inner-len", 1200, "Inner payload bytes before GTP-U")
	burstMs := flag.Int("burst-ms", 20, "Burst ON (ms)")
	idleMs := flag.Int("idle-ms", 100, "Burst OFF (ms)")

	// Phasing/jitter
	lockstep := flag.Bool("lockstep", true, "Start all TEIDs simultaneously")
	alignMs := flag.Int("align-ms", 50, "Align start to next multiple of this many ms (0=disabled)")
	phaseMs := flag.Int("phase-ms", 0, "Additional random phase per TEID up to this many ms")
	jitterPct := flag.Int("jitter-pct", 0, "Randomize PPS/burst/idle by ±pct")

	episodeID := flag.String("episode-id", "RUN", "Episode ID")
	flag.Parse()

	if *qfi <= 0 { fmt.Println("[inject][ERROR] --qfi > 0 required"); os.Exit(2) }
	if *durationMs <= 0 { fmt.Println("[inject][ERROR] --duration-ms > 0 required"); os.Exit(2) }

	// Discover existing TEIDs for this QFI
	existing, err := discoverExistingForQFI(tmpDirDefault, *qfi)
	if err != nil {
		fmt.Printf("[inject][ERROR] discovery: %v\n", err)
		os.Exit(1)
	}
	if len(existing) == 0 {
		fmt.Printf("[inject][ERROR] no existing TEIDs found for QFI=%d (need %s)\n", *qfi, filepath.Join(tmpDirDefault, "qfi_ue*_qfi*.txt"))
		os.Exit(1)
	}
	if *nFlows < 1 { *nFlows = 1 }
	if *nFlows > len(existing) {
		fmt.Printf("[inject][WARN] requested n=%d but only %d available; capping\n", *nFlows, len(existing))
		*nFlows = len(existing)
	}
	rand.Shuffle(len(existing), func(i, j int) { existing[i], existing[j] = existing[j], existing[i] })
	teids := existing[:*nFlows]

	// Emit start event
	start := map[string]any{
		"event":      "start",
		"episode_id": *episodeID,
		"ts_ns":      nowNs(),
		"actors": map[string]any{
			"qfi": *qfi,
			"teids": func() []string {
				out := make([]string, len(teids))
				for i, t := range teids { out[i] = fmt.Sprintf("0x%x", t) }
				return out
			}(),
		},
		"n":           *nFlows,
		"iface":       *iface,
		"mode":        "raw_synth_microburst",
		"lockstep":    *lockstep,
		"burst_ms":    *burstMs,
		"idle_ms":     *idleMs,
		"duration_ms": *durationMs,
		"pps":         *pps,
		"inner_len":   *innerLen,
	}
	if b, _ := json.Marshal(start); true { fmt.Println(string(b)) }

	// Raw sender
	rs, err := NewRawSender(*iface)
	if err != nil {
		fmt.Printf("[inject][ERROR] raw socket: %v\n", err)
		os.Exit(1)
	}
	defer rs.Close()

	// Episode context
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*durationMs)*time.Millisecond)
	defer cancel()

	// Ctrl-C
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() { <-sigCh; fmt.Println("[inject] Caught signal, stopping..."); cancel() }()

	// Compute synchronized start
	startAt := time.Now().Add(150 * time.Millisecond)
	if *alignMs > 0 {
		align := time.Duration(*alignMs) * time.Millisecond
		startAt = time.Now().Truncate(align).Add(align)
	}

	// Launch per-TEID workers
	var wg sync.WaitGroup
	for _, teid := range teids {
		ppsT := *pps
		burstT := *burstMs
		idleT := *idleMs
		phaseT := time.Duration(0)

		if !*lockstep {
			ppsT = jitterPctFloat64(ppsT, *jitterPct)
			burstT = jitterPctInt(burstT, *jitterPct)
			idleT = jitterPctInt(idleT, *jitterPct)
			if *phaseMs > 0 {
				phaseT = time.Duration(rand.Intn(*phaseMs)) * time.Millisecond
			}
		}

		wg.Add(1)
		go func(teid int, pps float64, burst, idle, inner int, phase time.Duration) {
			defer wg.Done()
			// phase relative to reference start
			if phase > 0 {
				wait := startAt.Add(phase)
				now := time.Now()
				if wait.After(now) {
					select {
					case <-time.After(wait.Sub(now)):
					case <-ctx.Done():
						return
					}
				}
			} else {
				now := time.Now()
				if startAt.After(now) {
					select {
					case <-time.After(startAt.Sub(now)):
					case <-ctx.Done():
						return
					}
				}
			}
			burstLoop(ctx, rs, uint32(teid), uint8(*qfi), inner, pps, burst, idle)
		}(teid, ppsT, burstT, idleT, *innerLen, phaseT)
	}

	wg.Wait()

	// Emit stop event
	stop := map[string]any{
		"event":      "stop",
		"episode_id": *episodeID,
		"ts_ns":      nowNs(),
	}
	if b, _ := json.Marshal(stop); true { fmt.Println(string(b)) }
}
