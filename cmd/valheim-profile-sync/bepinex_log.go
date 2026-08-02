package main

import (
	"io"
	"os"
	"path/filepath"
	"strings"
)

// logCandidate is a path BepInEx might write this profile's log to, paired with
// the length that path had before the game was launched.
type logCandidate struct {
	Path     string
	Baseline int64
}

// logBaselines holds every candidate, in preference order.
type logBaselines []logCandidate

// bepinexLogCandidatePaths lists everywhere BepInEx might put this profile's log.
//
// BepInEx derives its root from the loader it was started with. This launcher
// points Doorstop at the profile's own Preloader while the game executable stays
// in the Steam directory, so the log can land beside either one. Guessing wrongly
// is not a harmless mistake: a stale file at the guessed path gets followed
// forever while the live log is ignored, which presents as a mirror that prints
// its own header and then nothing at all.
func bepinexLogCandidatePaths(active, gameDir string) []string {
	candidates := []string{filepath.Join(active, "BepInEx", "LogOutput.log")}
	if gameDir != "" {
		candidates = append(candidates, filepath.Join(gameDir, "BepInEx", "LogOutput.log"))
	}
	return candidates
}

// measureLogBaselines records how long each candidate is before launch. Anything
// already there is history: the profiles set AppendLog = true, so BepInEx never
// truncates, and replaying that history spent the whole line budget in seconds.
func measureLogBaselines(active, gameDir string) logBaselines {
	paths := bepinexLogCandidatePaths(active, gameDir)
	baselines := make(logBaselines, 0, len(paths))
	for _, path := range paths {
		var size int64
		if info, err := os.Stat(path); err == nil && info.Mode().IsRegular() {
			size = info.Size()
		}
		baselines = append(baselines, logCandidate{Path: path, Baseline: size})
	}
	return baselines
}

// growing returns the candidate that has actually been written since launch.
// Selecting on growth rather than on existence is the point: it finds the live log
// without the launcher having to know how BepInEx resolved its root.
func (baselines logBaselines) growing() (logCandidate, bool) {
	for _, candidate := range baselines {
		info, err := os.Stat(candidate.Path)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		if info.Size() > candidate.Baseline {
			return candidate, true
		}
		if info.Size() < candidate.Baseline {
			// Shrunk, so BepInEx truncated it for this run: all of it is ours.
			return logCandidate{Path: candidate.Path}, true
		}
	}
	return logCandidate{}, false
}

// Paths lists the candidates so a report can name every place that was checked.
func (baselines logBaselines) Paths() []string {
	paths := make([]string, 0, len(baselines))
	for _, candidate := range baselines {
		paths = append(paths, candidate.Path)
	}
	return paths
}

// maxStreamedLogLines bounds how much of the game's log is mirrored into the
// window. A real startup on the 114-package profile writes about 11,900 lines
// (836 KB), so the budget has to clear that with room to spare: the first cut at
// 2,000 stopped mirroring roughly a sixth of the way in, which looked exactly
// like the launcher losing touch with BepInEx.
const maxStreamedLogLines = 20000

// maxLogChunk bounds one poll's read. Bounding bytes rather than lines is what
// keeps the offset honest: everything read is always emitted, so no line can be
// skipped past by advancing the offset over output that was never shown.
const maxLogChunk = 64 << 10

// logFollower yields lines appended to a file since the last call.
//
// It reopens the file per poll instead of holding a handle: BepInEx owns this file
// for the life of the game, and a launcher holding its own handle is a launcher
// that can interfere with log rotation.
type logFollower struct {
	path      string
	offset    int64
	partial   string
	emitted   int
	Truncated bool
	// noticed keeps the truncation notice to a single line.
	noticed bool
}

// newLogFollowerAt follows a file from a known offset, so only output written
// after that point is reported. The offset is the log length captured just
// before the game was launched.
func newLogFollowerAt(path string, offset int64) *logFollower {
	return &logFollower{path: path, offset: offset}
}

// next returns the lines completed since the previous call. A shrinking file means
// the game restarted and rewrote its log, so following starts over.
func (follower *logFollower) next() []string {
	if follower.path == "" || follower.emitted >= maxStreamedLogLines {
		return nil
	}
	file, err := os.Open(follower.path)
	if err != nil {
		return nil
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil
	}
	if info.Size() < follower.offset {
		follower.offset = 0
		follower.partial = ""
	}
	available := info.Size() - follower.offset
	if available <= 0 {
		return nil
	}
	if available > maxLogChunk {
		available = maxLogChunk
	}
	if _, err := file.Seek(follower.offset, io.SeekStart); err != nil {
		return nil
	}
	buffer := make([]byte, available)
	read, err := io.ReadFull(file, buffer)
	if read <= 0 {
		return nil
	}
	if err != nil && err != io.ErrUnexpectedEOF && err != io.EOF {
		return nil
	}
	follower.offset += int64(read)
	text := follower.partial + string(buffer[:read])
	follower.partial = ""
	// Hold an unterminated tail back so a half-written line is never shown twice.
	if index := strings.LastIndexByte(text, '\n'); index < 0 {
		follower.partial = text
		return nil
	} else if index+1 < len(text) {
		follower.partial = text[index+1:]
		text = text[:index+1]
	}
	var lines []string
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimRight(line, "\r")
		if strings.TrimSpace(line) == "" {
			continue
		}
		if follower.emitted >= maxStreamedLogLines {
			follower.Truncated = true
			break
		}
		lines = append(lines, line)
		follower.emitted++
	}
	return lines
}
