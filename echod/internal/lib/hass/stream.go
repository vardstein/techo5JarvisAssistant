package hass

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// A camera's live view comes as one answer that never ends: /api/camera_proxy_stream sends picture
// after picture as multipart/x-mixed-replace for as long as it is read. The REST client above gives up
// on any request after fifteen seconds, so the stream has a client of its own, with a limit on how long
// Home Assistant may take to start answering and none on the answer.

// streamStart is how long Home Assistant has to start answering a stream.
const streamStart = 10 * time.Second

var streamHTTP = func() *http.Client {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.ResponseHeaderTimeout = streamStart
	return &http.Client{Transport: t}
}()

// Stream opens a GET on a path that keeps answering, read until ctx is done or the caller closes it.
func (c *Client) Stream(ctx context.Context, path string) (io.ReadCloser, error) {
	acc, err := c.access()
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, "GET", acc.URL+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+acc.Token)
	resp, err := streamHTTP.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized {
		c.refuse(acc.Token)
	}
	if resp.StatusCode/100 != 2 {
		resp.Body.Close()
		return nil, &statusError{method: "GET", path: path, status: resp.Status, code: resp.StatusCode}
	}
	return resp.Body, nil
}

// MJPEG reads the pictures out of a camera stream. Each part is a boundary line, headers, a blank line
// and the picture. Home Assistant announces its boundary as "--frameboundary" and then writes it with
// two dashes in front where the standard puts four, so a strict multipart reader finds no parts at all;
// this one takes any line starting with "--" as a boundary and reads each picture by its
// Content-Length, which Home Assistant always sends.
type MJPEG struct {
	r    *bufio.Reader
	most int
}

// mjpegHeaders bounds the header lines of one part; Home Assistant sends two.
const mjpegHeaders = 16

// NewMJPEG reads parts from r, refusing any picture over most bytes.
func NewMJPEG(r io.Reader, most int) *MJPEG {
	return &MJPEG{r: bufio.NewReaderSize(r, 1024), most: most}
}

// Next is the next picture.
func (m *MJPEG) Next() ([]byte, error) {
	// The line after a picture ends it; the boundary comes after that.
	for blank := 0; ; blank++ {
		line, err := m.line()
		if err != nil {
			return nil, err
		}
		if strings.HasPrefix(line, "--") {
			break
		}
		if line != "" || blank == 2 {
			return nil, fmt.Errorf("hass: camera stream: %.40q where a picture should start", line)
		}
	}
	size := -1
	for n := 0; ; n++ {
		line, err := m.line()
		if err != nil {
			return nil, err
		}
		if line == "" {
			break
		}
		if n == mjpegHeaders {
			return nil, errors.New("hass: camera stream: a picture with too many headers")
		}
		k, v, ok := strings.Cut(line, ":")
		if ok && strings.EqualFold(strings.TrimSpace(k), "Content-Length") {
			if size, err = strconv.Atoi(strings.TrimSpace(v)); err != nil || size < 0 {
				return nil, fmt.Errorf("hass: camera stream: a length of %.20q", v)
			}
		}
	}
	if size < 0 {
		return nil, errors.New("hass: camera stream: a picture without a length")
	}
	if size > m.most {
		return nil, fmt.Errorf("hass: camera stream: a picture of %d bytes, more than %d", size, m.most)
	}
	b := make([]byte, size)
	if _, err := io.ReadFull(m.r, b); err != nil {
		if errors.Is(err, io.EOF) {
			err = io.ErrUnexpectedEOF
		}
		return nil, err
	}
	return b, nil
}

// line is one header line without its line end.
func (m *MJPEG) line() (string, error) {
	b, err := m.r.ReadSlice('\n')
	if errors.Is(err, bufio.ErrBufferFull) {
		return "", errors.New("hass: camera stream: a header line over 1 KiB")
	}
	if err != nil {
		return "", err
	}
	return strings.TrimRight(string(b), "\r\n"), nil
}
