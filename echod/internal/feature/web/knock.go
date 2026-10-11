package web

import (
	"net"
	"sync"

	"github.com/HuskerMinion/techo5/echod/internal/lib/hook"
)

// A browser that asks for https:// on a port that only speaks http://.
//
// Browsers with an HTTPS-only mode (LibreWolf has it on out of the box, Firefox and Chrome offer it)
// turn the http:// typed in front of the device's address into https:// before asking. What arrives
// here is then a TLS hello instead of a request: the HTTP server can only send it away, no page
// handler ever sees it, and from the device it looked as if nobody had come at all. So the port
// looks at the first byte of every connection, and a TLS handshake is told to whoever listens, for
// the screen to say what to type instead.

// HTTPSKnock fires when a connection to the port opens with a TLS handshake.
var HTTPSKnock hook.Hook[struct{}]

// tlsHandshake is the first byte of every TLS connection: the record type of a handshake.
const tlsHandshake = 0x16

// sniffListener hands out connections that report a TLS hello as they are first read.
type sniffListener struct{ net.Listener }

func (l sniffListener) Accept() (net.Conn, error) {
	c, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	return &sniffConn{Conn: c}, nil
}

type sniffConn struct {
	net.Conn
	once sync.Once
}

func (c *sniffConn) Read(p []byte) (int, error) {
	n, err := c.Conn.Read(p)
	if n > 0 {
		c.once.Do(func() {
			if p[0] == tlsHandshake {
				HTTPSKnock.Emit(struct{}{})
			}
		})
	}
	return n, err
}
