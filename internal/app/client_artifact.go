package app

import (
	"encoding/binary"
	"errors"
	"io"
	"os"
)

// The Windows client is a GUI application: cmd/valheim-profile-sync opens a
// window and writes nothing to a console. A build that omits -H=windowsgui
// still runs, but Windows attaches an empty console window beside the window
// the player came for. That flag lives in scripts/build-windows-client.sh, so a
// plain `go build` produces a binary that looks fine on disk and is wrong for a
// player. The portal therefore inspects what it is about to serve instead of
// trusting whoever produced it.

const (
	peSubsystemGUI     = 2
	peSubsystemConsole = 3
	// A PE keeps the offset of its NT header at 0x3c, and everything this
	// check needs sits inside the first few kilobytes.
	peHeaderReadLimit = 8 << 10
	peOffsetPointer   = 0x3c
	// Subsystem offset inside the optional header. It is the same for PE32
	// and PE32+, which differ only before it.
	peSubsystemOffset = 68
)

// clientArtifact is what the portal knows about the executable it publishes.
type clientArtifact struct {
	Size      int64
	Subsystem uint16
}

// GUI reports whether Windows will run the artifact without attaching a
// console. Anything else is a mis-built client.
func (a clientArtifact) GUI() bool { return a.Subsystem == peSubsystemGUI }

// Console distinguishes the common mistake from an unrecognised subsystem, so
// the operator-facing message can name the actual fix.
func (a clientArtifact) Console() bool { return a.Subsystem == peSubsystemConsole }

// inspectClientExecutable reads only the PE headers, so it stays cheap enough
// to run on every download request.
func inspectClientExecutable(path string) (clientArtifact, error) {
	file, err := os.Open(path)
	if err != nil {
		return clientArtifact{}, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return clientArtifact{}, err
	}
	if !info.Mode().IsRegular() {
		return clientArtifact{}, errors.New("client executable is not a regular file")
	}
	header := make([]byte, peHeaderReadLimit)
	read, err := io.ReadFull(file, header)
	if err != nil && !errors.Is(err, io.ErrUnexpectedEOF) && !errors.Is(err, io.EOF) {
		return clientArtifact{}, err
	}
	subsystem, err := peSubsystem(header[:read])
	if err != nil {
		return clientArtifact{}, err
	}
	return clientArtifact{Size: info.Size(), Subsystem: subsystem}, nil
}

func peSubsystem(header []byte) (uint16, error) {
	if len(header) < peOffsetPointer+4 || header[0] != 'M' || header[1] != 'Z' {
		return 0, errors.New("client executable is not a PE image")
	}
	nt := int(binary.LittleEndian.Uint32(header[peOffsetPointer:]))
	// COFF header is 20 bytes and the optional header follows it.
	optional := nt + 4 + 20
	if nt < 0 || optional+peSubsystemOffset+2 > len(header) {
		return 0, errors.New("client executable has a truncated PE header")
	}
	if string(header[nt:nt+4]) != "PE\x00\x00" {
		return 0, errors.New("client executable is not a PE image")
	}
	return binary.LittleEndian.Uint16(header[optional+peSubsystemOffset:]), nil
}

// clientArtifactProblem returns an operator-facing reason the published client
// must not be served, or an empty string when it is publishable.
func clientArtifactProblem(artifact clientArtifact, err error) string {
	switch {
	case errors.Is(err, os.ErrNotExist):
		return "no Windows client is published: build one with scripts/build-windows-client.sh"
	case err != nil:
		return "the published Windows client is unreadable: " + err.Error()
	case artifact.Console():
		return "the published Windows client was built for the console subsystem, so it opens an empty console window beside the application: rebuild it with scripts/build-windows-client.sh"
	case !artifact.GUI():
		return "the published Windows client has an unexpected PE subsystem: rebuild it with scripts/build-windows-client.sh"
	}
	return ""
}
