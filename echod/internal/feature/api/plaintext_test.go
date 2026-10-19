package api

import (
	"bytes"
	"io"
	"net"
	"testing"
	"time"

	esphome "github.com/ygelfand/go-esphome-device"
)

// serveOnce accepts one connection through refusePlaintext and returns what the server side read.
func serveOnce(t *testing.T, encrypted bool, send []byte) (reply []byte, got []byte) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	l := refusePlaintext{Listener: ln, encrypted: func() bool { return encrypted }}
	read := make(chan []byte, 1)
	go func() {
		c, err := l.Accept()
		if err != nil {
			read <- nil
			return
		}
		defer c.Close()
		buf := make([]byte, len(send))
		n, _ := io.ReadFull(c, buf)
		read <- buf[:n]
	}()

	c, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if _, err := c.Write(send); err != nil {
		t.Fatal(err)
	}
	got = <-read
	_ = c.SetReadDeadline(time.Now().Add(2 * time.Second))
	reply, _ = io.ReadAll(c)
	return reply, got
}

func TestPlaintextHelloGetsESPHomesReject(t *testing.T) {
	reply, got := serveOnce(t, true, []byte{0x00, 0x00, 0x01})
	if !bytes.Equal(reply, plaintextReject) {
		t.Errorf("reply = %q, want %q", reply, plaintextReject)
	}
	if len(got) != 0 {
		t.Errorf("the server read %x from a refused client", got)
	}
}

func TestNoiseHelloPassesThrough(t *testing.T) {
	hello := []byte{0x01, 0x00, 0x00}
	reply, got := serveOnce(t, true, hello)
	if !bytes.Equal(got, hello) {
		t.Errorf("server read %x, want %x", got, hello)
	}
	if len(reply) != 0 {
		t.Errorf("unexpected reply %x", reply)
	}
}

func TestPlaintextDeviceTakesPlaintext(t *testing.T) {
	hello := []byte{0x00, 0x00, 0x01}
	_, got := serveOnce(t, false, hello)
	if !bytes.Equal(got, hello) {
		t.Errorf("server read %x, want %x", got, hello)
	}
}

// The zero key takes plaintext (the library answers it only what adding the device takes); a real key
// refuses it, and no key at all is a plaintext device.
func TestOnlyARealKeyRefusesPlaintext(t *testing.T) {
	var k esphome.PSK
	k[3] = 1
	for _, c := range []struct {
		name string
		psk  *esphome.PSK
		want bool
	}{{"none", nil, false}, {"zero", esphome.Unprovisioned(), false}, {"real", &k, true}} {
		if got := keyed(c.psk); got != c.want {
			t.Errorf("%s key: keyed %v, want %v", c.name, got, c.want)
		}
	}
}
