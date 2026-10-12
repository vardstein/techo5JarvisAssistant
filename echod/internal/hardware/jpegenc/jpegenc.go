//go:build !dot && !spot

// Package jpegenc is the MT8163's JPEG encoder, driven through MediaTek's /proc/mtk_jpeg.
//
// A 640x360 picture takes the Show's two cores about 50 ms in image/jpeg; the encoder does it in
// well under a millisecond, and 1280x720 in about two. That is what lets the camera stream run
// at the sensor's own rate (GENBU-116).
//
// How it is driven, all of it measured on a 1st gen Show 5 (2026-10-12):
//
//   - Buffers come from ION's multimedia heap, configured for the encoder's two M4U ports (35 reads
//     the picture, 36 writes the JPEG), the way the camera gets its IMGO buffer. The heap is
//     uncached, so nothing needs syncing between the CPU and the encoder.
//   - The ports have to be put in translated mode after ENC_INIT, and INIT held for as long as they
//     are used. INIT powers larb 3, where the port setting lives, and this kernel (M4U behind the
//     TEE) only restores larb 0 after a power-down. Set before INIT, the setting is gone by the
//     first encode and the encoder takes the device addresses for physical ones. It did, once: the
//     EMI MPU caught the reads, because of the guard below.
//   - The guard: every device address, buffer end included, stays below 0x40000000, where DRAM
//     starts. Should a port ever be left untranslated, the encoder's accesses land outside memory,
//     the MPU refuses them, and nothing is overwritten. An address above that line is refused here
//     rather than used.
//   - The encoder stops writing at the end of the output buffer, so a picture that does not fit
//     ends as an error, never as a write past it.
//   - About one wait in a hundred comes back EFAULT a few hundred microseconds in: the driver read
//     an error status and reset the encoder. Encoding the same picture again has worked every time
//     (77 of 77 over 10,000 pictures), so one retry is built in, and a second failure is the
//     caller's cue to fall back to software.
//
// Only one process can hold the encoder (a second INIT is EBUSY), and only the 1st gen Show 5 has
// been tried; every other board keeps the software encoder until it has been.
package jpegenc

import (
	"errors"
	"fmt"
	"image"
	"log/slog"
	"os"
	"runtime"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"

	"github.com/HuskerMinion/techo5/echod/internal/layout"
)

const (
	ionHeapMultimedia = 10
	ionCmdSystem      = 0
	ionCmdMultimedia  = 1
	ionSysGetPhys     = 1
	ionMMConfigBuffer = 0

	portRDMA  = 35 // M4U_PORT_JPGENC_RDMA: the encoder reading the picture
	portBSDMA = 36 // M4U_PORT_JPGENC_BSDMA: the encoder writing the JPEG

	formatNV12 = 2

	// dramStart is where DRAM begins in the physical map; no device address may reach it.
	dramStart = 0x40000000

	// maxW and maxH are the largest picture the input buffer is sized for: the 2nd gen sensor's.
	maxW, maxH = 1600, 1200

	// outSize is the output buffer. A noisy 1600x1200 at Q90 stays well under half of it.
	outSize = 2 << 20

	// waitMS is how long one encode may take before the driver gives up on it.
	waitMS = 200

	// idle is how long the encoder stays powered after its last picture.
	idle = 10 * time.Second
)

// The ioctls on /proc/mtk_jpeg (jpeg_drv.h, magic 'x').
var (
	iocInit   = ioc(0, 'x', 11, 0)
	iocConfig = ioc(1, 'x', 12, unsafe.Sizeof(encIn{}))
	iocWait   = ioc(3, 'x', 13, unsafe.Sizeof(encOut{}))
	iocDeinit = ioc(0, 'x', 14, 0)
	iocStart  = ioc(0, 'x', 15, 0)
)

type ionAlloc struct {
	Len, Align, HeapIDMask, Flags uint32
	Handle                        int32
}
type ionFd struct{ Handle, Fd int32 }
type ionCustom struct{ Cmd, Arg uint32 }
type ionMMConfig struct {
	Handle                                           int32
	ModuleID, Security, Coherent, IovaStart, IovaEnd uint32
}
type ionMMData struct {
	MMCmd  uint32
	Config ionMMConfig
	_      [64]byte
}
type ionSysGetPhysP struct {
	Handle       int32
	PhyAddr, Len uint32
}
type ionSysData struct {
	SysCmd uint32
	Phys   ionSysGetPhysP
	_      [128]byte
}
type m4uPort struct {
	PortID                                            int32
	Virtuality, Security, Domain, Distance, Direction uint32
}

// encIn is JPEG_ENC_DRV_IN.
type encIn struct {
	DstAddr, DstSize, W, H  uint32
	EnableEXIF, AllocBuffer uint8
	_                       [2]byte
	Quality, Format         uint32
	DisableGMC, Restart     uint32
	SrcLuma, SrcChroma      uint32
	ImgStride, MemStride    uint32
	TotalDU                 uint32
	DstOffset, DstOffMask   uint32
}

// encOut is JPEG_ENC_DRV_OUT; the three results are user pointers, 32-bit as the compat ioctl
// reads them.
type encOut struct {
	Timeout                  int32
	FileSize, Result, Cycles uint32
}

// results is where the driver writes back. It is allocated on the heap and kept there: its address
// goes to the kernel inside encOut, where the garbage collector cannot see it.
type results struct{ size, result, cycles uint32 }

func ioc(dir, typ, nr, size uintptr) uintptr { return dir<<30 | size<<16 | typ<<8 | nr }

func ioctl(fd int, req uintptr, arg unsafe.Pointer) error {
	_, _, e := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), req, uintptr(arg))
	if e != 0 {
		return e
	}
	return nil
}

func ptr32(p unsafe.Pointer) uint32 { return uint32(uintptr(p)) }

// ErrUnavailable is a board without the encoder, or one it has not been tried on.
var ErrUnavailable = errors.New("no hardware JPEG encoder on this device")

// Available reports whether Encode can be tried at all.
func Available() bool {
	if !layout.Checkers() {
		return false
	}
	for _, p := range []string{"/proc/mtk_jpeg", "/dev/ion", "/proc/m4u"} {
		if _, err := os.Stat(p); err != nil {
			return false
		}
	}
	return true
}

// session is the encoder while it is held: INIT done, ports translated, buffers mapped.
type session struct {
	ion, jpg       int
	src, dst       []byte
	srcMVA, dstMVA uint32
	res            *results
}

var enc struct {
	sync.Mutex
	s      *session
	timer  *time.Timer
	broken error // set when opening failed in a way trying again will not change
}

// Encode encodes img as a JPEG at about quality (80 to 90; the encoder has four steps) and returns
// a copy the caller owns. Width and height must be even and at most 1600x1200.
func Encode(img *image.RGBA, quality int) ([]byte, error) {
	return EncodeNV12(img.Rect.Dx(), img.Rect.Dy(), quality, func(y, uv []byte, stride int) {
		toNV12(img, y, uv, stride)
	})
}

// EncodeNV12 is Encode for a picture that paints itself: fill writes w x h of NV12, full-range
// BT.601, luma rows stride bytes apart in y and the interleaved Cb Cr rows the same in uv. It runs
// with the encoder held, so it should be quick.
func EncodeNV12(w, h, quality int, fill func(y, uv []byte, stride int)) ([]byte, error) {
	if !Available() {
		return nil, ErrUnavailable
	}
	if w <= 0 || h <= 0 || w > maxW || h > maxH || w&1 != 0 || h&1 != 0 {
		return nil, fmt.Errorf("jpegenc: %dx%d is not a size the encoder takes here", w, h)
	}
	enc.Lock()
	defer enc.Unlock()
	if enc.broken != nil {
		return nil, enc.broken
	}
	if enc.s == nil {
		s, err := open()
		if err != nil {
			// EBUSY is somebody else holding the encoder, which may end; anything else is this
			// board saying no, and it is said once.
			if !errors.Is(err, syscall.EBUSY) {
				enc.broken = err
				slog.Warn("jpegenc: hardware encoder unusable, staying with software", "err", err)
			}
			return nil, err
		}
		enc.s = s
	}
	if enc.timer == nil {
		enc.timer = time.AfterFunc(idle, release)
	} else {
		enc.timer.Reset(idle)
	}
	return enc.s.encode(w, h, quality, fill)
}

// release powers the encoder down once nobody has used it for a while.
func release() {
	enc.Lock()
	defer enc.Unlock()
	if enc.s != nil {
		enc.s.close()
		enc.s = nil
	}
	enc.timer = nil
}

func open() (s *session, err error) {
	s = &session{ion: -1, jpg: -1, res: new(results)}
	defer func() {
		if err != nil {
			s.close()
		}
	}()
	if s.ion, err = syscall.Open("/dev/ion", syscall.O_RDWR|syscall.O_CLOEXEC, 0); err != nil {
		return nil, fmt.Errorf("open ion: %w", err)
	}
	stride := ceil(maxW, 16)
	srcSize := ceil(stride*ceil(maxH, 16), 4096) * 3 / 2
	if s.src, s.srcMVA, err = s.ionBuf(ceil(srcSize, 4096), portRDMA); err != nil {
		return nil, fmt.Errorf("input buffer: %w", err)
	}
	if s.dst, s.dstMVA, err = s.ionBuf(outSize, portBSDMA); err != nil {
		return nil, fmt.Errorf("output buffer: %w", err)
	}
	if uint64(s.srcMVA)+uint64(len(s.src)) > dramStart || uint64(s.dstMVA)+uint64(len(s.dst)) > dramStart {
		return nil, fmt.Errorf("device addresses %#x/%#x reach into DRAM's physical range", s.srcMVA, s.dstMVA)
	}
	if s.jpg, err = syscall.Open("/proc/mtk_jpeg", syscall.O_RDONLY|syscall.O_CLOEXEC, 0); err != nil {
		return nil, fmt.Errorf("open mtk_jpeg: %w", err)
	}
	if err := ioctl(s.jpg, iocInit, nil); err != nil {
		syscall.Close(s.jpg)
		s.jpg = -1
		return nil, fmt.Errorf("encoder init: %w", err)
	}
	for _, p := range []int32{portRDMA, portBSDMA} {
		if err := m4uConfig(p); err != nil {
			return nil, err
		}
	}
	return s, nil
}

func (s *session) close() {
	if s.jpg >= 0 {
		ioctl(s.jpg, iocDeinit, nil)
		syscall.Close(s.jpg)
	}
	if s.src != nil {
		unix.Munmap(s.src)
	}
	if s.dst != nil {
		unix.Munmap(s.dst)
	}
	if s.ion >= 0 {
		syscall.Close(s.ion) // frees the handles
	}
}

func (s *session) encode(w, h, quality int, fill func(y, uv []byte, stride int)) ([]byte, error) {
	stride := ceil(w, 16)
	chroma := ceil(stride*ceil(h, 16), 4096)
	fill(s.src[:chroma], s.src[chroma:], stride)
	in := heapIn()
	*in = encIn{
		DstAddr: s.dstMVA, DstSize: uint32(len(s.dst)), W: uint32(w), H: uint32(h),
		Quality: qualityCode(quality), Format: formatNV12,
		SrcLuma: s.srcMVA, SrcChroma: s.srcMVA + uint32(chroma),
		ImgStride: uint32(stride), MemStride: uint32(stride),
		TotalDU: uint32((stride/16)*(ceil(h, 16)/16)*6 - 1),
	}
	*s.res = results{}
	out := &encOut{Timeout: waitMS, FileSize: ptr32(unsafe.Pointer(&s.res.size)),
		Result: ptr32(unsafe.Pointer(&s.res.result)), Cycles: ptr32(unsafe.Pointer(&s.res.cycles))}
	var err error
	for try := 0; try < 2; try++ {
		if err = ioctl(s.jpg, iocConfig, unsafe.Pointer(in)); err != nil {
			break
		}
		if err = ioctl(s.jpg, iocStart, nil); err != nil {
			break
		}
		if err = ioctl(s.jpg, iocWait, unsafe.Pointer(out)); err == nil {
			break
		}
	}
	runtime.KeepAlive(in)
	runtime.KeepAlive(s.res)
	if err != nil {
		return nil, fmt.Errorf("jpegenc: %w", err)
	}
	if s.res.result != 0 || s.res.size == 0 || int(s.res.size) > len(s.dst) {
		return nil, fmt.Errorf("jpegenc: encoder result %d, size %d", s.res.result, s.res.size)
	}
	return append([]byte(nil), s.dst[:s.res.size]...), nil
}

// heapIn is an encIn the kernel reads by address; kept off the stack so it cannot move.
func heapIn() *encIn {
	p := new(encIn)
	escape(unsafe.Pointer(p))
	return p
}

var escape = func(unsafe.Pointer) {}

// ionBuf allocates size bytes for port, maps them, and returns the device address.
func (s *session) ionBuf(size int, port uint32) ([]byte, uint32, error) {
	a := ionAlloc{Len: uint32(size), Align: 4096, HeapIDMask: 1 << ionHeapMultimedia}
	if err := ioctl(s.ion, ioc(3, 'I', 0, unsafe.Sizeof(a)), unsafe.Pointer(&a)); err != nil {
		return nil, 0, fmt.Errorf("ion alloc: %w", err)
	}
	share := ionFd{Handle: a.Handle}
	if err := ioctl(s.ion, ioc(3, 'I', 4, unsafe.Sizeof(share)), unsafe.Pointer(&share)); err != nil {
		return nil, 0, fmt.Errorf("ion share: %w", err)
	}
	mem, err := unix.Mmap(int(share.Fd), 0, size, unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
	syscall.Close(int(share.Fd))
	if err != nil {
		return nil, 0, fmt.Errorf("mmap: %w", err)
	}
	cfg := new(ionMMData)
	escape(unsafe.Pointer(cfg))
	*cfg = ionMMData{MMCmd: ionMMConfigBuffer, Config: ionMMConfig{Handle: a.Handle, ModuleID: port}}
	c := ionCustom{Cmd: ionCmdMultimedia, Arg: ptr32(unsafe.Pointer(cfg))}
	err = ioctl(s.ion, ioc(3, 'I', 6, unsafe.Sizeof(c)), unsafe.Pointer(&c))
	runtime.KeepAlive(cfg)
	if err != nil {
		unix.Munmap(mem)
		return nil, 0, fmt.Errorf("ion config: %w", err)
	}
	phys := new(ionSysData)
	escape(unsafe.Pointer(phys))
	*phys = ionSysData{SysCmd: ionSysGetPhys, Phys: ionSysGetPhysP{Handle: a.Handle}}
	c = ionCustom{Cmd: ionCmdSystem, Arg: ptr32(unsafe.Pointer(phys))}
	err = ioctl(s.ion, ioc(3, 'I', 6, unsafe.Sizeof(c)), unsafe.Pointer(&c))
	runtime.KeepAlive(phys)
	if err != nil {
		unix.Munmap(mem)
		return nil, 0, fmt.Errorf("ion phys: %w", err)
	}
	if phys.Phys.PhyAddr == 0 || phys.Phys.PhyAddr&0xF != 0 {
		unix.Munmap(mem)
		return nil, 0, fmt.Errorf("bad device address %#x", phys.Phys.PhyAddr)
	}
	return mem, phys.Phys.PhyAddr, nil
}

// m4uConfig puts an M4U port into translated mode (MTK_M4U_T_CONFIG_PORT on /proc/m4u).
func m4uConfig(port int32) error {
	fd, err := syscall.Open("/proc/m4u", syscall.O_RDWR|syscall.O_CLOEXEC, 0)
	if err != nil {
		return fmt.Errorf("open /proc/m4u: %w", err)
	}
	defer syscall.Close(fd)
	p := m4uPort{PortID: port, Virtuality: 1, Distance: 1}
	if err := ioctl(fd, ioc(1, 'g', 11, 4), unsafe.Pointer(&p)); err != nil {
		return fmt.Errorf("m4u config port %d: %w", port, err)
	}
	return nil
}
