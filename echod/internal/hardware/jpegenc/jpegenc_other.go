//go:build dot || spot

// Package jpegenc is the MT8163's JPEG encoder; on these devices it has not been tried, so every
// picture goes to the software encoder.
package jpegenc

import (
	"errors"
	"image"
)

var ErrUnavailable = errors.New("no hardware JPEG encoder on this device")

func Available() bool { return false }

func Encode(*image.RGBA, int) ([]byte, error) { return nil, ErrUnavailable }

func EncodeNV12(int, int, int, func(y, uv []byte, stride int)) ([]byte, error) {
	return nil, ErrUnavailable
}
