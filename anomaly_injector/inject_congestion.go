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
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/google/gopacket/pcapgo"
	"golang.org/x/sys/unix"
)

/************ Config types ************/

type QosProfile struct {
	Application string `json:"application"`
	Description string `json:"description"`
	Pcap        string `json:"pcap"`
	Qfi         int    `json:"qfi"`
	IdleRange   []int  `json:"idle_range"` // not used here
}
type Config struct {
	QosProfiles []QosProfile `json:"qos_profiles"`
}

/************ Defaults & env helpers ************/

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// Defaults are safe /tmp-centric; orchestrators pass flags when ssh'ing.
var (
	defaultConfigPath = envOr("INJECT_CFG", "/tmp/traffic_injector_config.json")
	// traffic endpoints (MAC/IP) overridable via env to avoid rebuilding
	SrcMACStr = envOr("SRC_MAC", "a0:36:9f:ba:36:ac")
	DstMACStr = envOr("DST_MAC", "e4:1d:2d:09:a8:30")
	SrcIPStr  = envOr("SRC_IP", "192.168.44.13")
	DstIPStr  = envOr("DST_IP", "192.168.44.18")
)

const (
	tmpDirDefault     = "/tmp"
	qfiFilePatternStr = `^qfi_ue(\d+)_qfi(\d+)\.txt$`
	GTPUPort          = 2152
	GTPUHdrLen        = 16
	MaxFrame          = 1500
	qfiPollSecs       = 1
)

var qfiFileRe = regexp.MustCompile(qfiFilePatternStr)

/************ Small utils ************/

func nowNs() int64 { return time.Now().UnixNano() }

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

/************ Raw sender ************/

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

func (s *RawSender) SendGTPU(teid uint32, qfi uint8, inner []byte) error {
	maxInner := MaxFrame - (14 + 20 + 8 + GTPUHdrLen)
	if len(inner) > maxInner {
		inner = inner[:maxInner]
	}

	buf := make([]byte, 14+20+8+GTPUHdrLen+len(inner))
	// ETH
	copy(buf[0:6], s.dstMac[:])
	copy(buf[6:12], s.srcMac[:])
	binary.BigEndian.PutUint16(buf[12:14], 0x0800)
	// IPv4
	off := 14
	ipLen := uint16(20 + 8 + GTPUHdrLen + len(inner))
	buf[off+0] = (4 << 4) | 5
	buf[off+1] = 0
	binary.BigEndian.PutUint16(buf[off+2:off+4], ipLen)
	binary.BigEndian.PutUint16(buf[off+4:off+6], 0)
	binary.BigEndian.PutUint16(buf[off+6:off+8], 0)
	buf[off+8] = 64
	buf[off+9] = 17
	binary.BigEndian.PutUint32(buf[off+12:off+16], s.srcIP)
	binary.BigEndian.PutUint32(buf[off+16:off+20], s.dstIP)
	cs := checksum(buf[off : off+20])
	binary.BigEndian.PutUint16(buf[off+10:off+12], cs)
	// UDP
	off = 14 + 20
	binary.BigEndian.PutUint16(buf[off+0:off+2], 12345)
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(GTPUPort))
	udpLen := uint16(8 + GTPUHdrLen + len(inner))
	binary.BigEndian.PutUint16(buf[off+4:off+6], udpLen)
	binary.BigEndian.PutUint16(buf[off+6:off+8], 0)
	// GTP-U (with PDU Session Container for QFI)
	off = 14 + 20 + 8
	buf[off+0] = 0x34 // V1 PT=1 E=1 S=1
	buf[off+1] = 0xFF // T-PDU
	binary.BigEndian.PutUint16(buf[off+2:off+4], uint16(len(inner)+8))
	binary.BigEndian.PutUint32(buf[off+4:off+8], teid)
	buf[off+8] = 0
	buf[off+9] = 0
	buf[off+10] = 0
	buf[off+11] = 0x85 // PDU Sess Container IE
	buf[off+12] = 0x01
	buf[off+13] = 0x10
	buf[off+14] = qfi & 0x3F
	buf[off+15] = 0x00

	copy(buf[off+GTPUHdrLen:], inner)
	return unix.Sendto(s.fd, buf, 0, &s.sa)
}

/************ Config + PCAP resolver ************/

func readConfig(path string) (*Config, string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	var cfg Config
	if err := json.Unmarshal(b, &cfg); err != nil {
		return nil, "", err
	}
	return &cfg, filepath.Dir(path), nil
}
func resolvePcapForQFI(cfg *Config, cfgDir string, qfi int) (string, error) {
	for _, p := range cfg.QosProfiles {
		if p.Qfi != qfi {
			continue
		}
		raw := p.Pcap
		if raw == "" {
			return "", fmt.Errorf("pcap empty for qfi=%d", qfi)
		}
		if !filepath.IsAbs(raw) {
			abs := filepath.Join(cfgDir, raw)
			if _, err := os.Stat(abs); err == nil {
				return abs, nil
			}
			return "", fmt.Errorf("resolved PCAP missing: %s", abs)
		}
		if _, err := os.Stat(raw); err != nil {
			return "", fmt.Errorf("PCAP missing: %s", raw)
		}
		return raw, nil
	}
	return "", fmt.Errorf("no profile for qfi=%d", qfi)
}

/************ TEID & discovery helpers ************/

func discoverActive(tmpDir string, targetQFI int) (allPairs [][2]int, teidsForQFI []int, inUse map[int]struct{}, err error) {
	entries, err := os.ReadDir(tmpDir)
	if err != nil {
		return nil, nil, map[int]struct{}{}, err
	}
	inUse = make(map[int]struct{})
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		m := qfiFileRe.FindStringSubmatch(e.Name())
		if m == nil {
			continue
		}
		var ueIdx, qfi int
		if _, err := fmt.Sscanf(e.Name(), "qfi_ue%d_qfi%d.txt", &ueIdx, &qfi); err != nil {
			continue
		}
		teid := ueIdx + 1
		allPairs = append(allPairs, [2]int{teid, qfi})
		inUse[teid] = struct{}{}
		if qfi == targetQFI {
			teidsForQFI = append(teidsForQFI, teid)
		}
	}
	return allPairs, teidsForQFI, inUse, nil
}

func pickTEIDs(n int, existing []int, inUse map[int]struct{}, teidStart int) (picked []int, nExist int, nNew int) {
	if n < 1 {
		n = 1
	}
	targetExist := (n * 3) / 4 // ~75% existing
	if targetExist > len(existing) {
		nExist = len(existing)
	} else {
		nExist = targetExist
	}
	nNew = n - nExist

	if len(existing) > 1 {
		rand.Shuffle(len(existing), func(i, j int) { existing[i], existing[j] = existing[j], existing[i] })
	}
	picked = append(picked, existing[:nExist]...)

	maxSeen := teidStart - 1
	for t := range inUse {
		if t > maxSeen {
			maxSeen = t
		}
	}
	next := maxSeen + 1
	if next < teidStart {
		next = teidStart
	}
	for len(picked) < n {
		if _, ok := inUse[next]; !ok {
			picked = append(picked, next)
			inUse[next] = struct{}{}
		}
		next++
	}
	if len(picked) > 1 {
		rand.Shuffle(len(picked), func(i, j int) { picked[i], picked[j] = picked[j], picked[i] })
	}
	return picked, nExist, nNew
}

func ueIdxFromTEID(teid int) int { return teid - 1 }

func writeQFIFile(teid, qfi int) (string, error) {
	ueIdx := ueIdxFromTEID(teid)
	path := filepath.Join(tmpDirDefault, fmt.Sprintf("qfi_ue%d_qfi%d.txt", ueIdx, qfi))
	return path, os.WriteFile(path, []byte(strconv.Itoa(qfi)), 0o644)
}

/************ PCAP paced sender ************/

func sendPcapPaced(
	ctx context.Context, rs *RawSender, r *pcapgo.Reader,
	teid uint32, qfiFile string, qfi uint8, speed float64, randStart bool,
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

	// Optional random entry
	if randStart {
		data, ci, err := r.ReadPacketData()
		if err != nil {
			if errors.Is(err, io.EOF) {
				return nil
			}
			return err
		}
		baseTS := ci.Timestamp
		offset := time.Duration(rand.Intn(60)) * time.Second
		target := baseTS.Add(offset)
		if ci.Timestamp.Before(target) {
			for {
				data, ci, err = r.ReadPacketData()
				if err != nil {
					if errors.Is(err, io.EOF) {
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
		firstTS = ci.Timestamp
		origin = time.Now()
		firstSeen = true
		if time.Now().After(nextQFICheck) && qfiFile != "" {
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
		if time.Now().After(nextQFICheck) && qfiFile != "" {
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
}

/************ Main ************/

func main() {
	// Flags (everything overridable at runtime)
	qfi := flag.Int("qfi", 0, "Target QFI (required)")
	nFlows := flag.Int("n", 6, "Number of concurrent flows")
	duration := flag.Int("duration", 30, "Duration (seconds)")
	iface := flag.String("iface", "enp2s0f0", "Replay interface")
	episodeID := flag.String("episode-id", "RUN", "Episode ID")
	configPath := flag.String("config", defaultConfigPath, "Path to config.json (QFI→PCAP profiles)")
	teidStart := flag.Int("teid-start", 101, "Minimum TEID for new flows")
	randStart := flag.Bool("rand-start", true, "Randomize entry point within PCAP")
	speed := flag.Float64("speed", 1.0, "Playback speed factor")
	flag.Parse()

	if *qfi <= 0 {
		fmt.Println(`[inject][ERROR] --qfi must be > 0`)
		os.Exit(2)
	}

	// Seed RNG
	rand.Seed(time.Now().UnixNano())

	// Load config & resolve PCAP for this QFI
	cfg, cfgDir, err := readConfig(*configPath)
	if err != nil {
		fmt.Printf("[inject][ERROR] read config: %v\n", err)
		os.Exit(1)
	}
	pcap, err := resolvePcapForQFI(cfg, cfgDir, *qfi)
	if err != nil {
		fmt.Printf("[inject][ERROR] %v\n", err)
		os.Exit(1)
	}

	// Discover + pick TEIDs (mix of existing/new)
	_, activeForQFI, inUse, _ := discoverActive(tmpDirDefault, *qfi)
	teids, nExist, nNew := pickTEIDs(*nFlows, activeForQFI, inUse, *teidStart)

	// Emit start JSON (as orchestrators expect)
	start := map[string]any{
		"event":      "start",
		"episode_id": *episodeID,
		"ts_ns":      nowNs(),
		"actors": map[string]any{
			"qfi": *qfi,
			"teids": func() []string {
				out := make([]string, len(teids))
				for i, t := range teids {
					out[i] = fmt.Sprintf("0x%x", t)
				}
				return out
			}(),
		},
		"n":          *nFlows,
		"n_existing": nExist,
		"n_new":      nNew,
		"pcap":       pcap,
		"iface":      *iface,
	}
	js, _ := json.Marshal(start)
	fmt.Println(string(js))

	// Context + signals
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(*duration)*time.Second)
	defer cancel()
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-sigCh
		fmt.Println("[inject] Caught signal, stopping…")
		cancel()
	}()

	// Raw sender
	rs, err := NewRawSender(*iface)
	if err != nil {
		fmt.Printf("[inject][ERROR] raw socket: %v\n", err)
		os.Exit(1)
	}
	defer rs.Close()

	// Start flows
	var wg sync.WaitGroup
	qfiFiles := make([]string, 0, len(teids))
	for _, teid := range teids {
		// create /tmp/qfi_ueX_qfiY.txt for discovery
		path, _ := writeQFIFile(teid, *qfi)
		qfiFiles = append(qfiFiles, path)

		wg.Add(1)
		go func(teid int, qfiVal int, qfiFile string) {
			defer wg.Done()
			f, err := os.Open(pcap)
			if err != nil {
				fmt.Printf("[inject][ERROR] open pcap: %v\n", err)
				return
			}
			rd, err := pcapgo.NewReader(f)
			if err != nil {
				_ = f.Close()
				fmt.Printf("[inject][ERROR] pcap reader: %v\n", err)
				return
			}
			err = sendPcapPaced(ctx, rs, rd, uint32(teid), qfiFile, uint8(qfiVal), *speed, *randStart)
			_ = f.Close()
			_ = err // best-effort; errors are expected on cancel
		}(teid, *qfi, path)
	}

	// Wait
	wg.Wait()

	// Cleanup qfi temp files
	for _, p := range qfiFiles {
		_ = os.Remove(p)
	}

	// Emit stop JSON
	stop := map[string]any{
		"event":      "stop",
		"episode_id": *episodeID,
		"ts_ns":      nowNs(),
		"actors": map[string]any{
			"qfi": *qfi,
			"teids": func() []string {
				out := make([]string, len(teids))
				for i, t := range teids {
					out[i] = fmt.Sprintf("0x%x", t)
				}
				return out
			}(),
		},
	}
	js2, _ := json.Marshal(stop)
	fmt.Println(string(js2))
}
