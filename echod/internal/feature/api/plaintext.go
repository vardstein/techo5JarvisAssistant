package api

import (
	"io"
	"net"
	"sync"

	esphome "github.com/ygelfand/go-esphome-device"
)

// Home Assistant first tries every new ESPHome device without encryption. A real ESPHome device that
// wants Noise answers that hello with a Noise frame carrying "Bad indicator byte", and aioesphomeapi
// reads the 0x01 that frame starts with as "this device requires encryption" and asks for the key.
// The library only closes the connection, which Home Assistant reports as connection_error, so a Show
// could never be added by address. refusePlaintext gives the answer ESPHome gives.

const plaintextIndicator = 0x00

// plaintextReject is a Noise frame (0x01, length 19) whose body is a handshake error (0x01) and the
// message ESPHome sends.
var plaintextReject = append([]byte{0x01, 0x00, 0x13, 0x01}, "Bad indicator byte"...)

// keyed is whether a server with this key refuses plaintext. Without a key it is plaintext, and with
// the zero key it takes plaintext so Home Assistant can add it (the library answers it only that).
func keyed(k *esphome.PSK) bool { return k != nil && !k.IsZero() }

type refusePlaintext struct {
	net.Listener
	encrypted func() bool
}

func (l refusePlaintext) Accept() (net.Conn, error) {
	c, err := l.Listener.Accept()
	if err != nil || !l.encrypted() {
		return c, err
	}
	return &firstByte{Conn: c}, nil
}

// firstByte looks at the first byte the client sends, on the library's own first read, so Accept never
// waits on a slow client.
type firstByte struct {
	net.Conn
	once    sync.Once
	refused bool
}

func (c *firstByte) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return c.Conn.Read(p)
	}
	n, err := 0, error(nil)
	first := false
	c.once.Do(func() {
		first = true
		n, err = c.Conn.Read(p)
		if n > 0 && p[0] == plaintextIndicator {
			c.refused = true
			_, _ = c.Conn.Write(plaintextReject)
		}
	})
	if c.refused {
		return 0, io.EOF
	}
	if first {
		return n, err
	}
	return c.Conn.Read(p)
}
