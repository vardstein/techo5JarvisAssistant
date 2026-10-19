package esphomedevice

import (
	"context"
	"io"
	"log/slog"
	"net"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	"github.com/ygelfand/go-esphome-device/api"
	"github.com/ygelfand/go-esphome-device/internal/wire"
)

func testInfo() Info {
	return Info{
		Name:          "echolocal-kitchen",
		FriendlyName:  "Kitchen Echo",
		MACAddress:    "aa:bb:cc:dd:ee:ff",
		Manufacturer:  "Amazon",
		Model:         "Echo Dot 2",
		Version:       "test",
		SuggestedArea: "Kitchen",

		ProjectName:    "Example.Echo Dot 2",
		ProjectVersion: "v1.2.3",
	}
}

// startServer runs a plaintext server on a loopback port and returns a client speaking
// the same transport. Noise is covered in internal/wire.
func startServer(t *testing.T, h Handler) (*Server, *testPeer) {
	t.Helper()
	return startServerWith(t, &Server{Handler: h})
}

// startServerWith is startServer for a server set up beyond its handler. The client speaks
// plaintext whatever the server's key.
func startServerWith(t *testing.T, srv *Server) (*Server, *testPeer) {
	t.Helper()

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}

	srv.Info = testInfo()
	srv.Logger = slog.New(slog.DiscardHandler)

	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() {
		defer close(done)
		_ = srv.Serve(ctx, ln)
	}()

	nc, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		cancel()
		t.Fatalf("dial: %v", err)
	}

	t.Cleanup(func() {
		_ = nc.Close()
		cancel()
		<-done
	})

	return srv, &testPeer{t: t, transport: wire.NewPlaintext(nc), conn: nc}
}

type testPeer struct {
	t         *testing.T
	transport wire.Transport
	conn      net.Conn
}

func (p *testPeer) send(msg proto.Message) {
	p.t.Helper()
	info, ok := Lookup(msg)
	if !ok {
		p.t.Fatalf("no id for %T", msg)
	}
	data, err := proto.Marshal(msg)
	if err != nil {
		p.t.Fatalf("marshal: %v", err)
	}
	if err := p.transport.Write(wire.Frame{Type: info.ID, Data: data}); err != nil {
		p.t.Fatalf("write: %v", err)
	}
}

func (p *testPeer) recv() proto.Message {
	p.t.Helper()
	_ = p.conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	frame, err := p.transport.Read()
	if err != nil {
		p.t.Fatalf("read: %v", err)
	}
	msg, ok := NewMessage(frame.Type)
	if !ok {
		p.t.Fatalf("unknown message id %d", frame.Type)
	}
	if err := proto.Unmarshal(frame.Data, msg); err != nil {
		p.t.Fatalf("unmarshal: %v", err)
	}
	return msg
}

func (p *testPeer) hello() *api.HelloResponse {
	p.t.Helper()
	p.send(&api.HelloRequest{
		ClientInfo:      "test client",
		ApiVersionMajor: APIVersionMajor,
		ApiVersionMinor: APIVersionMinor,
	})
	resp, ok := p.recv().(*api.HelloResponse)
	if !ok {
		p.t.Fatal("expected HelloResponse")
	}
	return resp
}

func TestHelloHandshake(t *testing.T) {
	_, peer := startServer(t, nil)

	resp := peer.hello()
	if resp.GetName() != "echolocal-kitchen" {
		t.Errorf("name = %q, want echolocal-kitchen", resp.GetName())
	}
	if resp.GetApiVersionMajor() != APIVersionMajor {
		t.Errorf("api major = %d, want %d", resp.GetApiVersionMajor(), APIVersionMajor)
	}
	if resp.GetServerInfo() == "" {
		t.Error("server_info should not be empty")
	}
}

func TestDeviceInfo(t *testing.T) {
	_, peer := startServer(t, nil)
	peer.hello()

	peer.send(&api.DeviceInfoRequest{})
	resp, ok := peer.recv().(*api.DeviceInfoResponse)
	if !ok {
		t.Fatal("expected DeviceInfoResponse")
	}

	want := testInfo()
	if resp.GetName() != want.Name {
		t.Errorf("name = %q, want %q", resp.GetName(), want.Name)
	}
	if resp.GetFriendlyName() != want.FriendlyName {
		t.Errorf("friendly_name = %q, want %q", resp.GetFriendlyName(), want.FriendlyName)
	}
	if resp.GetMacAddress() != want.MACAddress {
		t.Errorf("mac = %q, want %q", resp.GetMacAddress(), want.MACAddress)
	}
	if resp.GetSuggestedArea() != want.SuggestedArea {
		t.Errorf("suggested_area = %q, want %q", resp.GetSuggestedArea(), want.SuggestedArea)
	}
	if resp.GetProjectName() != want.ProjectName || resp.GetProjectVersion() != want.ProjectVersion {
		t.Errorf("project = %q %q, want %q %q", resp.GetProjectName(), resp.GetProjectVersion(), want.ProjectName, want.ProjectVersion)
	}
	if !resp.GetApiEncryptionSupported() {
		t.Error("api_encryption_supported should be true")
	}
	if resp.GetApiEncryptionProvisionable() {
		t.Error("a device that isn't waiting for a key says it can be given one")
	}
}

// A device waiting for its key lets Home Assistant read it and finish connecting, and nothing more:
// the listing is empty, nothing reaches the handler, nobody counts as subscribed, and a key sent in
// plaintext is turned down.
func TestUnkeyedDeviceAnswersOnlyWhatAddingItTakes(t *testing.T) {
	handled := make(chan proto.Message, 4)
	keys := make(chan PSK, 1)
	subscribed := make(chan struct{}, 1)
	srv, peer := startServerWith(t, &Server{
		PSK: Unprovisioned(),
		Handler: HandlerFunc(func(_ context.Context, _ *Conn, msg proto.Message) error {
			handled <- msg
			return nil
		}),
		OnSetEncryptionKey: func(k PSK) error { keys <- k; return nil },
		OnSubscribed:       func() { subscribed <- struct{}{} },
	})
	peer.hello()

	peer.send(&api.DeviceInfoRequest{})
	info, ok := peer.recv().(*api.DeviceInfoResponse)
	if !ok {
		t.Fatal("expected DeviceInfoResponse")
	}
	if !info.GetApiEncryptionProvisionable() || !info.GetApiEncryptionSupported() {
		t.Errorf("provisionable %v, supported %v: Home Assistant won't push a key",
			info.GetApiEncryptionProvisionable(), info.GetApiEncryptionSupported())
	}

	peer.send(&api.ListEntitiesRequest{})
	if _, ok := peer.recv().(*api.ListEntitiesDoneResponse); !ok {
		t.Fatal("the listing didn't end at once")
	}

	peer.send(&api.SubscribeStatesRequest{})
	peer.send(&api.SubscribeLogsRequest{Level: api.LogLevel_LOG_LEVEL_VERY_VERBOSE})
	peer.send(&api.ButtonCommandRequest{Key: 1})
	peer.send(&api.NoiseEncryptionSetKeyRequest{Key: make([]byte, 31)})
	peer.send(&api.NoiseEncryptionSetKeyRequest{Key: []byte("0123456789abcdef0123456789abcdef")})
	for range 2 {
		resp, ok := peer.recv().(*api.NoiseEncryptionSetKeyResponse)
		if !ok {
			t.Fatal("expected NoiseEncryptionSetKeyResponse")
		}
		if resp.GetSuccess() {
			t.Error("a key sent in plaintext was taken")
		}
	}
	peer.send(&api.PingRequest{})
	if _, ok := peer.recv().(*api.PingResponse); !ok {
		t.Fatal("expected PingResponse")
	}

	select {
	case msg := <-handled:
		t.Errorf("the handler got %T", msg)
	case k := <-keys:
		t.Errorf("a key was stored: %v", k.IsZero())
	case <-subscribed:
		t.Error("the subscribed hook fired")
	default:
	}
	srv.mu.Lock()
	for c := range srv.conns {
		if c.StatesSubscribed() || c.LogsSubscribed() {
			t.Error("the connection counts as subscribed")
		}
	}
	srv.mu.Unlock()
}

// recording is a transport that keeps what is written to it, standing in for zero-key Noise.
type recording struct {
	encrypted bool
	written   []wire.Frame
}

func (r *recording) Handshake() error          { return nil }
func (r *recording) Read() (wire.Frame, error) { return wire.Frame{}, io.EOF }
func (r *recording) Write(f wire.Frame) error  { r.written = append(r.written, f); return nil }
func (r *recording) Close() error              { return nil }
func (r *recording) Encrypted() bool           { return r.encrypted }

// Over zero-key Noise the key is taken, but not the all-zeros one, which would leave the device
// open as it is.
func TestUnkeyedDeviceTakesAKeyOverZeroKeyNoise(t *testing.T) {
	var stored []PSK
	tr := &recording{encrypted: true}
	c := newConn(tr, testInfo(), nil, slog.New(slog.DiscardHandler), hooks{
		setKey: func(k PSK) error { stored = append(stored, k); return nil },
	})

	key := []byte("0123456789abcdef0123456789abcdef")
	for _, raw := range [][]byte{make([]byte, 32), key} {
		if err := c.dispatch(t.Context(), &api.NoiseEncryptionSetKeyRequest{Key: raw}); err != nil {
			t.Fatal(err)
		}
	}

	var answers []bool
	for _, f := range tr.written {
		var resp api.NoiseEncryptionSetKeyResponse
		if err := proto.Unmarshal(f.Data, &resp); err != nil {
			t.Fatal(err)
		}
		answers = append(answers, resp.GetSuccess())
	}
	if len(answers) != 2 || answers[0] || !answers[1] {
		t.Errorf("answers %v, want the zero key turned down and the real one taken", answers)
	}
	if len(stored) != 1 || string(stored[0][:]) != string(key) {
		t.Errorf("stored %d keys, want just the real one", len(stored))
	}
}

func TestPing(t *testing.T) {
	_, peer := startServer(t, nil)
	peer.hello()

	peer.send(&api.PingRequest{})
	if _, ok := peer.recv().(*api.PingResponse); !ok {
		t.Fatal("expected PingResponse")
	}
}

func TestDisconnectClosesConnection(t *testing.T) {
	_, peer := startServer(t, nil)
	peer.hello()

	peer.send(&api.DisconnectRequest{})
	if _, ok := peer.recv().(*api.DisconnectResponse); !ok {
		t.Fatal("expected DisconnectResponse")
	}

	_ = peer.conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, err := peer.transport.Read(); err == nil {
		t.Error("expected the server to close after disconnect")
	} else if err != io.EOF {
		t.Logf("closed with %v", err)
	}
}

func TestBroadcastRequiresSubscription(t *testing.T) {
	srv, peer := startServer(t, nil)
	peer.hello()

	// Nothing subscribed yet, so this must not reach anyone.
	if err := srv.Broadcast(&api.PingRequest{}); err != nil {
		t.Fatalf("broadcast: %v", err)
	}

	peer.send(&api.SubscribeStatesRequest{})

	// Wait for the server to record the subscription before broadcasting.
	deadline := time.Now().Add(2 * time.Second)
	for {
		srv.mu.Lock()
		var subscribed bool
		for c := range srv.conns {
			subscribed = subscribed || c.StatesSubscribed()
		}
		srv.mu.Unlock()
		if subscribed {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("subscription never registered")
		}
		time.Sleep(5 * time.Millisecond)
	}

	if err := srv.Broadcast(&api.PingRequest{}); err != nil {
		t.Fatalf("broadcast: %v", err)
	}
	if _, ok := peer.recv().(*api.PingRequest); !ok {
		t.Fatal("expected the broadcast to arrive")
	}
}

func TestHandlerReceivesUnhandledMessages(t *testing.T) {
	got := make(chan proto.Message, 4)
	h := HandlerFunc(func(_ context.Context, _ *Conn, msg proto.Message) error {
		got <- msg
		return nil
	})

	_, peer := startServer(t, h)
	peer.hello()
	peer.send(&api.ListEntitiesRequest{})

	select {
	case msg := <-got:
		if _, ok := msg.(*api.ListEntitiesRequest); !ok {
			t.Errorf("handler got %T, want *api.ListEntitiesRequest", msg)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("handler never saw the message")
	}
}

func TestLifecycleMessagesBypassHandler(t *testing.T) {
	got := make(chan proto.Message, 4)
	h := HandlerFunc(func(_ context.Context, _ *Conn, msg proto.Message) error {
		got <- msg
		return nil
	})

	_, peer := startServer(t, h)
	peer.hello()
	peer.send(&api.PingRequest{})
	peer.recv()

	select {
	case msg := <-got:
		t.Errorf("handler should not see lifecycle messages, got %T", msg)
	case <-time.After(200 * time.Millisecond):
	}
}
