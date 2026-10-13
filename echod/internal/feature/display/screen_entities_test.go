//go:build !dot

package display

import esphome "github.com/ygelfand/go-esphome-device"

// The screen's entities, for a Display put together by hand in a test.
func newPower() *esphome.Switch { p, _ := screenEntities(); return p }
func newLevel() *esphome.Number { _, l := screenEntities(); return l }
