package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// A BepInEx .cfg edited one value at a time, byte-for-byte everywhere else.
//
// Two reasons this cannot be a parse-and-reserialise. The comment block above
// each key ("## description", "# Setting type:", "# Default value:",
// "# Acceptable values:") is the ONLY machine-readable schema for the whole
// feature - the portal's extractor reads exactly those comments to know a
// setting's type and its allowed values - so dropping or reflowing them breaks
// the configuration manager on the next extraction. And line endings are mixed
// WITHIN a single real file: /media/big4/projects/game/valheim/Hrafnheim/
// config_merged/bepinex/ZenDragon.ZenBreeding.cfg has LF on line 10
// ("Website = ...") and CRLF on line 14, in the same file. Normalising would
// rewrite lines nobody asked to change and turn a one-value edit into a whole
// file diff.
//
// So the file is held as its original lines with their own terminators attached,
// and an edit replaces only the text after the "=" on the one line that owns the
// key.
type configDocument struct {
	// bom is the UTF-8 byte order mark when the file had one. 26 of 100 plugin
	// manifests on this host carry one; config files are written by the same
	// mods, so it is re-emitted rather than silently stripped.
	bom bool
	// lines each include their own terminator, so joining them reproduces the
	// input exactly.
	lines []string
}

var utf8BOM = []byte{0xEF, 0xBB, 0xBF}

func trimUTF8BOM(data []byte) []byte {
	return bytes.TrimPrefix(data, utf8BOM)
}

func readBoundedFile(path string, limit int64) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > limit {
		return nil, fmt.Errorf("%s is larger than %d bytes", filepath.Base(path), limit)
	}
	return data, nil
}

func parseConfigDocument(data []byte) *configDocument {
	document := &configDocument{}
	if bytes.HasPrefix(data, utf8BOM) {
		document.bom = true
		data = data[len(utf8BOM):]
	}
	for len(data) > 0 {
		end := bytes.IndexByte(data, '\n')
		if end < 0 {
			document.lines = append(document.lines, string(data))
			break
		}
		document.lines = append(document.lines, string(data[:end+1]))
		data = data[end+1:]
	}
	return document
}

// readConfigDocument reports found=false for a missing file. A player who has
// never run this mod has no file, and that is the case that gets seeded.
func readConfigDocument(path string) (*configDocument, bool, error) {
	data, err := readBoundedFile(path, maxManagedConfigBytes)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, false, nil
		}
		return nil, false, err
	}
	return parseConfigDocument(data), true, nil
}

func (document *configDocument) bytes() []byte {
	size := 0
	for _, line := range document.lines {
		size += len(line)
	}
	buffer := bytes.NewBuffer(make([]byte, 0, size+len(utf8BOM)))
	if document.bom {
		buffer.Write(utf8BOM)
	}
	for _, line := range document.lines {
		buffer.WriteString(line)
	}
	return buffer.Bytes()
}

func (document *configDocument) write(path string) error {
	return writeFileAtomically(path, document.bytes())
}

func splitConfigLine(line string) (body, terminator string) {
	body = strings.TrimRight(line, "\n")
	body = strings.TrimRight(body, "\r")
	return body, line[len(body):]
}

func configLineIsComment(trimmed string) bool {
	return strings.HasPrefix(trimmed, "#") || strings.HasPrefix(trimmed, ";")
}

func configSectionName(trimmed string) (string, bool) {
	if !strings.HasPrefix(trimmed, "[") || !strings.HasSuffix(trimmed, "]") || len(trimmed) < 3 {
		return "", false
	}
	return strings.TrimSpace(trimmed[1 : len(trimmed)-1]), true
}

// locate reports the index of the line holding section/key, and the index one
// past the section's last line so a missing key can be appended inside its own
// section rather than orphaned at the end of the file.
//
// Section and key are matched exactly. BepInEx's own ConfigDefinition compares
// them ordinally, and the portal took these strings out of this same file, so a
// case-insensitive match would only ever paper over a mismatch that means
// something.
func (document *configDocument) locate(section, key string) (line int, sectionEnd int) {
	current := ""
	line, sectionEnd = -1, -1
	inSection := false
	for index, raw := range document.lines {
		body, _ := splitConfigLine(raw)
		trimmed := strings.TrimSpace(body)
		if name, isSection := configSectionName(trimmed); isSection {
			if inSection {
				return line, sectionEnd
			}
			current = name
			inSection = current == section
			if inSection {
				sectionEnd = index + 1
			}
			continue
		}
		if trimmed == "" || configLineIsComment(trimmed) {
			continue
		}
		name, _, split := strings.Cut(trimmed, "=")
		if !split {
			continue
		}
		if inSection {
			// Track the last real setting in the section, so an appended key
			// lands with its siblings instead of after a trailing comment block.
			sectionEnd = index + 1
			if strings.TrimSpace(name) == key {
				return index, sectionEnd
			}
		}
	}
	return line, sectionEnd
}

func (document *configDocument) value(section, key string) (string, bool) {
	index, _ := document.locate(section, key)
	if index < 0 {
		return "", false
	}
	body, _ := splitConfigLine(document.lines[index])
	_, raw, _ := strings.Cut(body, "=")
	return strings.TrimSpace(raw), true
}

// setValue writes value for section/key and reports whether the file changed.
//
// Only the text after the "=" is replaced: the key's own spelling, the spacing
// around the "=", the line's terminator, and every comment in the file are the
// original bytes.
func (document *configDocument) setValue(section, key, value string) (bool, error) {
	if strings.ContainsAny(value, "\r\n\x00") {
		return false, fmt.Errorf("value for %s is not a single line", key)
	}
	index, sectionEnd := document.locate(section, key)
	if index < 0 {
		return document.insert(section, key, value, sectionEnd)
	}
	body, terminator := splitConfigLine(document.lines[index])
	assignment := strings.Index(body, "=")
	prefix := body[:assignment+1]
	rest := body[assignment+1:]
	// Whatever the file already puts between "=" and its value stays, including
	// nothing at all: "Threshold=4" is a real shape, and normalising it to
	// "Threshold = 4" turns a one-value edit into a line nobody asked to change.
	spacing := rest[:len(rest)-len(strings.TrimLeft(rest, " \t"))]
	replacement := prefix + spacing + value + terminator
	if replacement == document.lines[index] {
		return false, nil
	}
	document.lines[index] = replacement
	return true, nil
}

// insert adds a key the file does not contain.
//
// This is the one path that cannot preserve a comment block, because there is no
// comment block to preserve: it only runs when the release shipped a config file
// that is missing a key the published baseline claims. The value still has to be
// applied - a forced setting that silently does nothing is worse than a config
// file without its documentation - and the mod rewrites the block itself on its
// next run.
func (document *configDocument) insert(section, key, value string, sectionEnd int) (bool, error) {
	if strings.ContainsAny(section, "[]\r\n\x00") || strings.ContainsAny(key, "=\r\n\x00") {
		return false, fmt.Errorf("cannot insert %q into section %q", key, section)
	}
	terminator := document.dominantTerminator()
	setting := key + " = " + value + terminator
	if sectionEnd >= 0 {
		document.lines = append(document.lines, "")
		copy(document.lines[sectionEnd+1:], document.lines[sectionEnd:])
		document.lines[sectionEnd] = setting
		return true, nil
	}
	if count := len(document.lines); count > 0 {
		if _, last := splitConfigLine(document.lines[count-1]); last == "" {
			document.lines[count-1] += terminator
		}
		document.lines = append(document.lines, terminator)
	}
	document.lines = append(document.lines, "["+section+"]"+terminator, setting)
	return true, nil
}

// dominantTerminator copies whatever the file already uses, because these files
// are mixed and an inserted line should not be the odd one out.
func (document *configDocument) dominantTerminator() string {
	windows, unix := 0, 0
	for _, raw := range document.lines {
		switch _, terminator := splitConfigLine(raw); terminator {
		case "\r\n":
			windows++
		case "\n":
			unix++
		}
	}
	if windows > unix {
		return "\r\n"
	}
	return "\n"
}

func writeFileAtomically(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".config-write-")
	if err != nil {
		return err
	}
	name := temporary.Name()
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		os.Remove(name)
		return err
	}
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		os.Remove(name)
		return err
	}
	if err := temporary.Close(); err != nil {
		os.Remove(name)
		return err
	}
	if err := replaceFile(name, path); err != nil {
		os.Remove(name)
		return err
	}
	return nil
}
