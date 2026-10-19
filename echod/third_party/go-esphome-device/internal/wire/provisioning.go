package wire

import (
	"bufio"
	"io"
	"sync"
)

// Provisioning is the transport of a device waiting for its first key. Home Assistant reaches
// such a device twice: in plaintext to read it and add it, then over Noise with the reserved
// all-zeros key to push the real key, so that one is never on the wire in the clear. The first
// byte the client sends says which of the two this connection is, so the choice waits for it.
type Provisioning struct {
	rw   io.ReadWriteCloser
	name string

	mu  sync.Mutex
	t   Transport
	enc bool
}

func NewProvisioning(rw io.ReadWriteCloser, deviceName string) *Provisioning {
	return &Provisioning{rw: rw, name: deviceName}
}

// readThrough reads what was peeked before reading on, and closes and writes the connection itself.
type readThrough struct {
	*bufio.Reader
	io.WriteCloser
}

func (p *Provisioning) Handshake() error {
	r := bufio.NewReader(p.rw)
	first, err := r.Peek(1)
	if err != nil {
		return err
	}

	rw := readThrough{Reader: r, WriteCloser: p.rw}
	var t Transport
	enc := first[0] == noiseIndicator
	if enc {
		t = NewNoise(rw, make([]byte, 32), p.name)
	} else {
		t = NewPlaintext(rw)
	}
	if err := t.Handshake(); err != nil {
		return err
	}

	p.mu.Lock()
	p.t, p.enc = t, enc
	p.mu.Unlock()
	return nil
}

// Encrypted reports whether the client chose zero-key Noise. Before the handshake it is false.
func (p *Provisioning) Encrypted() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.enc
}

func (p *Provisioning) chosen() Transport {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.t
}

func (p *Provisioning) Read() (Frame, error) {
	t := p.chosen()
	if t == nil {
		return Frame{}, ErrNotReady
	}
	return t.Read()
}

func (p *Provisioning) Write(f Frame) error {
	t := p.chosen()
	if t == nil {
		return ErrNotReady
	}
	return t.Write(f)
}

func (p *Provisioning) Close() error { return p.rw.Close() }
