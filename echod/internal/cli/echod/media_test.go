package echod

import (
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"
)

// A bench take plays a file copied to the device: a path or a file:// url opens it, and a url is left
// as it was (GENBU-160).
func TestMediaPlaysAFileOnTheDevice(t *testing.T) {
	dir := t.TempDir()
	wav := filepath.Join(dir, "music48.wav")
	if err := os.WriteFile(wav, []byte("RIFF....WAVE"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := source("http://example.org/m.wav"); got != "http://example.org/m.wav" {
		t.Errorf("a url became %q", got)
	}
	for _, arg := range []string{wav, "file://" + wav} {
		url := source(arg)
		if url != "file://"+wav {
			t.Fatalf("%s became %q", arg, url)
		}
		resp, err := http.Get(url)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK || string(body) != "RIFF....WAVE" {
			t.Errorf("%s: %d %q", arg, resp.StatusCode, body)
		}
	}
}
