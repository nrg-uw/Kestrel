/*
traffic_generator.go - GTP-U mobile traffic generator for 5G networks.

This tool generates user plane traffic by encapsulating PCAP traces in GTP-U tunnels
with configurable QoS profiles. It simulates multiple UEs with realistic traffic patterns,
timing variations, and dynamic QoS changes.

Features:
- Generates authentic GTP-U packets with TEID and QFI headers
- Simulates multiple UEs (default: 100) with 3 concurrent flows each  
- Applies realistic traffic patterns: staggered starts, variable speeds, session churn
- Supports dynamic QoS changes via file-based QFI control
- Replays PCAP traces with timing preservation and rate variations

Usage:
  go run traffic_generator.go -iface eth0 -duration 10 -ue-count 50 -config profiles.json

*/

package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"

	"github.com/google/gopacket/pcapgo"
)

// ----------------- Config & CLI -----------------

type QosProfile struct {
	Application string `json:"application"`
	Description string `json:"description"`
	Pcap        string `json:"pcap"`
	Qfi         int    `json:"qfi"`
	IdleRange   []int  `json:"idle_range"` // [min,max] seconds between laps (used if --idle-mean-s < 0)
}

type Config struct {
	QosProfiles []QosProfile `json:"qos_profiles"`
}

func logMsg(flowID, msg string) {
	fmt.Printf("[%s] [Flow %s] %s\n", time.Now().Format("15:04:05"), flowID, msg)
}

func writeQfiFile(qfiFile string, qfi int) error {
	return os.WriteFile(qfiFile, []byte(fmt.Sprintf("%d", qfi)), 0o644)
}

func cleanupTempQfiFiles() {
	files, _ := filepath.Glob("/tmp/qfi_ue*_qfi*.txt")
	for _, f := range files {
		_ = os.Remove(f)
	}
}


var (
	SrcMACStr = envOr("SRC_MAC", "a0:36:9f:ba:36:ac")
	DstMACStr = envOr("DST_MAC", "e4:1d:2d:09:a8:30")
	SrcIPStr  = envOr("SRC_IP", "192.168.44.13")
	DstIPStr  = envOr("DST_IP", "192.168.44.18")
)

const (
	GTPUPort    = 2152
	GTPUHdrLen  = 16
	MaxFrame    = 1500
	qfiPollSecs = 1
)

/**************** Utility functions ****************/

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func parseMAC(s string) ([6]byte, error) {
	hw, err := net.ParseMAC(s)
	if err != nil {
		return [6]byte{}, err
	}
	var b [6]byte
	copy(b[:], hw[:6])
	return b, nil
}

func ip4(a string) (uint32, error) {
	ip := net.ParseIP(a).To4()
	if ip == nil {
		return 0, fmt.Errorf("bad ip %q", a)
	}
	return binary.BigEndian.Uint32(ip), nil
}

func checksum(b []byte) uint16 {
	var sum uint32
	for i := 0; i+1 < len(b); i += 2 {
		sum += uint32(binary.BigEndian.Uint16(b[i:]))
	}
	if len(b)%2 == 1 {
		sum += uint32(b[len(b)-1]) << 8
	}
	sum = (sum >> 16) + (sum & 0xFFFF)
	sum += (sum >> 16)
	return ^uint16(sum)
}

func htons(x uint16) uint16 { return (x<<8 | x>>8) }

func expRand(r *rand.Rand, mean float64) float64 {
	if mean <= 0 {
		return 0
	}
	return r.ExpFloat64() * mean
}


/***************** Raw socket GTP-U sender ****************/

type RawSender struct {
	fd     int
	sa     unix.SockaddrLinklayer
	srcMac [6]byte
	dstMac [6]byte
	srcIP  uint32
	dstIP  uint32
}

func NewRawSender(iface string) (*RawSender, error) {
	itf, err := net.InterfaceByName(iface)
	if err != nil {
		return nil, err
	}
	fd, err := unix.Socket(unix.AF_PACKET, unix.SOCK_RAW, int(htons(unix.ETH_P_ALL)))
	if err != nil {
		return nil, err
	}
	sa := unix.SockaddrLinklayer{Ifindex: itf.Index, Halen: 6}
	if err := unix.Bind(fd, &sa); err != nil {
		unix.Close(fd)
		return nil, err
	}
	srcMac, err := parseMAC(SrcMACStr)
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	dstMac, err := parseMAC(DstMACStr)
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	sip, err := ip4(SrcIPStr)
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	dip, err := ip4(DstIPStr)
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	return &RawSender{fd: fd, sa: sa, srcMac: srcMac, dstMac: dstMac, srcIP: sip, dstIP: dip}, nil
}

func (s *RawSender) Close() { _ = unix.Close(s.fd) }

// Build and send Ethernet+IPv4+UDP+GTP-U(T-PDU + PDU Session Container with QFI)
func (s *RawSender) SendGTPU(teid uint32, qfi uint8, inner []byte) error {
	maxInner := MaxFrame - (14 + 20 + 8 + GTPUHdrLen) // eth+ip+udp+gtpu
	if len(inner) > maxInner {
		inner = inner[:maxInner]
	}

	buf := make([]byte, 14+20+8+GTPUHdrLen+len(inner))
	// Ethernet
	copy(buf[0:6], s.dstMac[:])
	copy(buf[6:12], s.srcMac[:])
	binary.BigEndian.PutUint16(buf[12:14], 0x0800) // IPv4

	// IPv4 (manual)
	off := 14
	ipLen := uint16(20 + 8 + GTPUHdrLen + len(inner))
	buf[off+0] = (4 << 4) | 5 // V=4, IHL=5
	buf[off+1] = 0            // DSCP/ECN
	binary.BigEndian.PutUint16(buf[off+2:off+4], ipLen)
	binary.BigEndian.PutUint16(buf[off+4:off+6], 0)
	binary.BigEndian.PutUint16(buf[off+6:off+8], 0)
	buf[off+8] = 64
	buf[off+9] = 17 // UDP
	binary.BigEndian.PutUint32(buf[off+12:off+16], s.srcIP)
	binary.BigEndian.PutUint32(buf[off+16:off+20], s.dstIP)
	cs := checksum(buf[off : off+20])
	binary.BigEndian.PutUint16(buf[off+10:off+12], cs)

	// UDP
	off = 14 + 20
	binary.BigEndian.PutUint16(buf[off+0:off+2], 12345) // src port
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(GTPUPort))
	udpLen := uint16(8 + GTPUHdrLen + len(inner))
	binary.BigEndian.PutUint16(buf[off+4:off+6], udpLen)
	binary.BigEndian.PutUint16(buf[off+6:off+8], 0) // checksum 0

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
	buf[off+13] = 0x10 // RQI=0, QFI present
	buf[off+14] = qfi & 0x3F
	buf[off+15] = 0x00

	copy(buf[off+GTPUHdrLen:], inner)
	return unix.Sendto(s.fd, buf, 0, &s.sa)
}

/****************** PCAP sender with pacing and QFI control ****************/

func sendPcapPacedAdvanced(
	ctx context.Context, rs *RawSender, r *pcapgo.Reader,
	teid uint32, qfiFile string, qfi uint8,
	speed float64, randStart bool,
) error {
	if speed <= 0 {
		speed = 1.0
	}
	var (
		firstTS, origin time.Time
		curQFI          = qfi
		nextQFICheck    = time.Now()
		firstSeen       = false
	)

	// Optional: random entry point in PCAP based on timestamps
	if randStart {
		// Peek first packet to anchor timestamps
		data, ci, err := r.ReadPacketData()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		baseTS := ci.Timestamp
		// Draw 0..60s random offset (cheap and good enough without scanning whole file)
		offset := time.Duration(rand.Intn(60)) * time.Second

		// Walk forward until we reach baseTS+offset
		target := baseTS.Add(offset)
		if ci.Timestamp.Before(target) {
			for {
				data, ci, err = r.ReadPacketData()
				if err != nil {
					if errors.Is(err, io.EOF) {
						// If file ended before offset, just start at end (best-effort)
						firstTS = baseTS
						origin = time.Now()
						firstSeen = true
						break
					}
					return err
				}
				if !ci.Timestamp.Before(target) {
					break
				}
			}
		}
		// Send this packet immediately as the first one
		firstTS = ci.Timestamp
		origin = time.Now()
		firstSeen = true
		if time.Now().After(nextQFICheck) {
			if b, err := os.ReadFile(qfiFile); err == nil {
				if v, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil && v >= 0 && v <= 63 {
					curQFI = uint8(v)
				}
			}
			nextQFICheck = time.Now().Add(time.Second * qfiPollSecs)
		}
		if len(data) > 0 {
			if err := rs.SendGTPU(teid, curQFI, data); err != nil {
				return err
			}
		}
	}

	// Main loop
	for {
		data, ci, err := r.ReadPacketData()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		if !firstSeen {
			firstSeen = true
			firstTS = ci.Timestamp
			origin = time.Now()
		}
		// Absolute schedule scaled by speed factor
		delta := ci.Timestamp.Sub(firstTS)
		target := origin.Add(time.Duration(float64(delta) / speed))
		now := time.Now()
		if target.After(now) {
			select {
			case <-time.After(target.Sub(now)):
			case <-ctx.Done():
				return ctx.Err()
			}
		}

		// Periodic QFI refresh
		if time.Now().After(nextQFICheck) {
			if qfiFile != "" {
				if b, err := os.ReadFile(qfiFile); err == nil {
					if v, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil && v >= 0 && v <= 63 {
						curQFI = uint8(v)
					}
				}
			}
			nextQFICheck = time.Now().Add(time.Second * qfiPollSecs)
		}

		if len(data) > 0 {
			if err := rs.SendGTPU(teid, curQFI, data); err != nil {
				return err
			}
		}
	}
}

/****************** Replay loop with traffic variation ****************/

func replayLoopAdvanced(
	ctx context.Context,
	flowID, pcapPath string,
	teid uint32, qfi int,
	qfiFile string,
	useExpIdle bool, expIdleMean float64,
	randStart bool, speedFactor float64, sessionEndP float64,
	seed int64, rs *RawSender, wg *sync.WaitGroup,
) {
	defer wg.Done()
	r := rand.New(rand.NewSource(seed))
	laps := 0

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		f, err := os.Open(pcapPath)
		if err != nil {
			logMsg(flowID, fmt.Sprintf("open pcap: %v", err))
			return
		}
		rd, err := pcapgo.NewReader(f)
		if err != nil {
			f.Close()
			logMsg(flowID, fmt.Sprintf("pcap reader: %v", err))
			return
		}

		logMsg(flowID, fmt.Sprintf("Replaying (TEID=0x%x, QFI=%d, speed=%.3f, randStart=%v)",
			teid, qfi, speedFactor, randStart))

		err = sendPcapPacedAdvanced(ctx, rs, rd, teid, qfiFile, uint8(qfi), speedFactor, randStart)
		_ = f.Close()
		if err != nil && ctx.Err() == nil {
			logMsg(flowID, fmt.Sprintf("Replay error: %v", err))
		}

		laps++

		// Geometric session end
		if sessionEndP > 0 && r.Float64() < sessionEndP {
			off := time.Duration(expRand(r, 30.0)) * time.Second
			logMsg(flowID, fmt.Sprintf("Session ended after %d laps. Off=%.1fs", laps, off.Seconds()))
			select {
			case <-time.After(off):
			case <-ctx.Done():
				return
			}
			laps = 0
		}

		// Idle between laps
		sleepSec := 0.0
		if useExpIdle {
			sleepSec = expRand(r, expIdleMean)
		}
		if sleepSec > 0 {
			logMsg(flowID, fmt.Sprintf("Sleeping %.1fs", sleepSec))
			select {
			case <-time.After(time.Duration(sleepSec * float64(time.Second))):
			case <-ctx.Done():
				return
			}
		}
	}
}

func main() {
	
	iface := flag.String("iface", "enp2s0f0", "Network interface")
	duration := flag.Int("duration", 5, "Duration in minutes")
	baseTeid := flag.Int("base-teid", 1, "Base TEID for UEs")
	ueCount := flag.Int("ue-count", 100, "Number of UEs")
	seed := flag.Int64("seed", time.Now().UnixNano(), "Random seed")
	cfgPath := flag.String("config", "config.json", "Path to config.json with QoS profiles")

	/* Flags for traffic variation */
	startStagger := flag.Float64("start-stagger-s", 120.0, "Mean start staggering in seconds (exponential, no hard cap). 0 = start immediately")
	idleMean := flag.Float64("idle-mean-s", 15.0, "Mean idle between laps (exponential). <0 = use per-profile idle_range uniform")
	speedSkew := flag.Float64("speed-skew", 0.02, "Per-flow playback speed stddev as a fraction (e.g., ±2%)")
	randStart := flag.Bool("rand-start", true, "Randomize start offset within the PCAP on each lap")
	sessionGeomP := flag.Float64("session-end-p", 0.05, "Geometric per-lap session end probability (0..1). 0 disables session endings")

	flag.Parse()
	
	if *baseTeid+*ueCount > 4096 {
		fmt.Printf("[ERROR] TEID range overflow: base-teid (%d) + ue-count (%d) exceeds 4096.\n", *baseTeid, *ueCount)
		os.Exit(1)
	}

	seedVal := *seed
	fmt.Printf("[INFO] Using seed: %d\n", seedVal)
	rand.Seed(seedVal)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		fmt.Println("\n[INFO] Caught interrupt. Stopping all flows...")
		cancel()
	}()

	cfgBytes, err := os.ReadFile(*cfgPath)
	if err != nil {
		fmt.Printf("[ERROR] Could not read config: %v\n", err)
		os.Exit(1)
	}
	var cfg Config
	if err := json.Unmarshal(cfgBytes, &cfg); err != nil {
		fmt.Printf("[ERROR] JSON parse error: %v\n", err)
		os.Exit(1)
	}
	if len(cfg.QosProfiles) == 0 {
		fmt.Println("[ERROR] No QoS profiles in config.")
		os.Exit(1)
	}

	endTime := time.Now().Add(time.Duration(*duration*60) * time.Second)
	cleanupTempQfiFiles()
	fmt.Printf("[INFO] Launching %d UEs × 3 flows each on %s for %d minutes\n", *ueCount, *iface, *duration)

	// Shared raw sender
	rs, err := NewRawSender(*iface)
	if err != nil {
		fmt.Printf("[ERROR] raw socket: %v\n", err)
		os.Exit(1)
	}
	defer rs.Close()

	var wg sync.WaitGroup

	flowsPerUE := 3
	if len(cfg.QosProfiles) < flowsPerUE {
		flowsPerUE = len(cfg.QosProfiles)
	}

	for ueIdx := 0; ueIdx < *ueCount; ueIdx++ {
		idxs := rand.Perm(len(cfg.QosProfiles))[:flowsPerUE]
		for _, i := range idxs {
			p := cfg.QosProfiles[i]
			flowID := fmt.Sprintf("%03d_qfi%d", ueIdx, p.Qfi)
			teid := uint32(*baseTeid + ueIdx)
			qfiFile := fmt.Sprintf("/tmp/qfi_ue%d_qfi%d.txt", ueIdx, p.Qfi)
			_ = writeQfiFile(qfiFile, p.Qfi)

			// Resolve idle strategy
			useExpIdle := true
			expIdle := *idleMean
			if expIdle < 0 {
				// fallback to per-profile uniform if provided
				useExpIdle = false
				expIdle = 0
			}

			// Per-flow RNG
			flowSeed := seedVal + int64(ueIdx*len(cfg.QosProfiles)+i)
			rf := rand.New(rand.NewSource(flowSeed ^ 0x51504f))

			// Exponential start staggering (no hard cap)
			delay := 0.0
			if *startStagger > 0 {
				delay = expRand(rf, *startStagger)
			}
			logMsg(flowID, fmt.Sprintf("Start delay %.1fs (pcap=%s)", delay, p.Pcap))

			// Per-flow speed factor (bounded)
			sf := 1.0
			if *speedSkew > 0 {
				s := rf.NormFloat64() * (*speedSkew)
				sf = 1.0 + s
				if sf < 0.7 {
					sf = 0.7
				}
				if sf > 1.3 {
					sf = 1.3
				}
			}

			wg.Add(1)
			go func(flowID string, pcap string, teid uint32, qfi int, qfiFile string,
				useExpIdle bool, expIdleMean float64, randStart bool, sf float64, sessionEndP float64,
				seed int64,
			) {
				// staggered start
				select {
				case <-time.After(time.Duration(delay * float64(time.Second))):
				case <-ctx.Done():
					wg.Done()
					return
				}
				replayLoopAdvanced(ctx, flowID, pcap, teid, qfi, qfiFile,
					useExpIdle, expIdleMean, randStart, sf, *sessionGeomP,
					seed, rs, &wg)
			}(flowID, p.Pcap, teid, p.Qfi, qfiFile,
				useExpIdle, expIdle, *randStart, sf, *sessionGeomP,
				flowSeed)
		}
	}

	// global stop at duration
	go func() {
		<-time.After(time.Until(endTime))
		fmt.Println("[INFO] Duration elapsed. Stopping all flows...")
		cancel()
	}()
	wg.Wait()
	fmt.Println("\n[INFO] All flows stopped. Cleaning up.")
	cleanupTempQfiFiles()
}
