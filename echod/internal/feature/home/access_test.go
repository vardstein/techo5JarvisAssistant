package home

import (
	"testing"

	esphome "github.com/ygelfand/go-esphome-device"
)

// The installer deletes a Show's old Home Assistant token only once home_assistant answers that the
// new one is stored (GENBU-158). An action that stops answering leaves the old tokens behind, and one
// that answers a failed write as stored would let the installer delete the only token that works.
func TestTheAccessActionSaysWhetherTheTokenIsStored(t *testing.T) {
	a := (&Feature{}).accessAction()
	if a.Name != "home_assistant" || !a.Answers {
		t.Fatalf("action %q answers %v; the installer waits for home_assistant to answer", a.Name, a.Answers)
	}
	if got, err := a.Run(esphome.Call{}); err == nil {
		t.Fatalf("a call without a URL and token answered %v", got)
	}
}

// Which key a Show holds is told over the encrypted API only, and only Genbu's two names are taken
// (GENBU-196). The test machine has no API key, so both refuse.
func TestTheKeyActionsWantTheEncryptedAPI(t *testing.T) {
	names := map[string]bool{}
	for _, a := range keyActions() {
		names[a.Name] = true
		if !a.Answers {
			t.Errorf("%s does not answer", a.Name)
		}
		if got, err := a.Run(esphome.Call{}); err == nil {
			t.Errorf("%s answered %v without an API key", a.Name, got)
		}
	}
	if !names["home_assistant_key"] || !names["home_assistant_retry"] || len(names) != 2 {
		t.Fatalf("actions %v", names)
	}
}
