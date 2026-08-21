package main

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"time"
)

const (
	maxDiagnosticLogBytes    = int64(8 << 20)
	maxDiagnosticBundleBytes = int64(64 << 20)
	// A modded profile carries well over a hundred config files. Newest-first with
	// a hard cap keeps the bundle inside the upload limit while still capturing the
	// files a mod touched during the session.
	maxDiagnosticConfigFiles = 40
	maxDiagnosticConfigBytes = int64(512 << 10)
)

func startDiagnosticsCollector(gameDir, active string, pid int) {
	if pid < 1 {
		return
	}
	executable, err := os.Executable()
	if err != nil {
		return
	}
	command := exec.Command(executable, "--collect-diagnostics", "--game-dir", gameDir, "--profile-root", filepath.Dir(active), "--pid", strconv.Itoa(pid))
	command.SysProcAttr = detachedProcessAttributes()
	_ = command.Start()
}

func collectDiagnosticsAfterExit(ctx context.Context, gameDir, profileRoot string, pid int) error {
	if pid < 1 {
		return errors.New("invalid Valheim process")
	}
	state, present, err := loadProfileState(profileRoot)
	if err != nil {
		return err
	}
	if !present || state.DiagnosticsToken == "" || state.DiagnosticsEndpoint == "" {
		return nil
	}
	if _, err := url.ParseRequestURI(state.DiagnosticsEndpoint); err != nil {
		return errors.New("invalid diagnostics endpoint")
	}
	process, err := os.FindProcess(pid)
	if err != nil {
		return err
	}
	if _, err := process.Wait(); err != nil {
		return fmt.Errorf("wait for Valheim exit: %w", err)
	}
	bundle, err := buildDiagnosticsBundle(gameDir, profileRoot, state)
	if err != nil {
		return err
	}
	defer os.Remove(bundle)
	return uploadDiagnosticsBundle(ctx, state.DiagnosticsEndpoint, state.DiagnosticsToken, bundle)
}

func buildDiagnosticsBundle(gameDir, profileRoot string, state profileState) (string, error) {
	file, err := os.CreateTemp("", "valheim-diagnostics-*.zip")
	if err != nil {
		return "", err
	}
	path := file.Name()
	archive := zip.NewWriter(file)
	metadata, err := json.Marshal(struct {
		World, Profile, ClientType, ReleaseID string
		CollectedAt                           time.Time
	}{state.World, state.Profile, state.ClientType, state.ReleaseID, time.Now().UTC()})
	if err == nil {
		err = writeDiagnosticZipEntry(archive, "metadata.json", bytes.NewReader(metadata), int64(len(metadata)))
	}
	if err == nil {
		for _, source := range diagnosticSources(gameDir, profileRoot) {
			if source.path == "" {
				continue
			}
			input, openErr := os.Open(source.path)
			if openErr != nil {
				continue
			}
			info, statErr := input.Stat()
			if statErr == nil && !info.IsDir() {
				copyErr := writeDiagnosticZipEntry(archive, source.name, input, info.Size())
				if err == nil && copyErr != nil {
					err = copyErr
				}
			}
			input.Close()
			if err != nil {
				break
			}
		}
	}
	closeErr := archive.Close()
	fileCloseErr := file.Close()
	if err != nil || closeErr != nil || fileCloseErr != nil {
		os.Remove(path)
		if err != nil {
			return "", err
		}
		if closeErr != nil {
			return "", closeErr
		}
		return "", fileCloseErr
	}
	info, err := os.Stat(path)
	if err != nil || info.Size() < 1 || info.Size() > maxDiagnosticBundleBytes {
		os.Remove(path)
		if err != nil {
			return "", err
		}
		return "", errors.New("diagnostics bundle exceeds upload limit")
	}
	return path, nil
}

type diagnosticSource struct{ name, path string }

// firstExisting returns the first candidate that is an existing regular file.
func firstExisting(candidates ...string) string {
	for _, candidate := range candidates {
		if candidate == "" {
			continue
		}
		if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() {
			return candidate
		}
	}
	return ""
}

// unityLogDirectory is where Unity writes Player.log. The launcher does not pass
// -logfile, so Valheim always writes here rather than into the game directory.
func unityLogDirectory() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, "AppData", "LocalLow", "IronGate", "Valheim")
}

// newestProfilerReport returns the most recent LoadTimeProfiler report across the
// candidate directories. Reports are written as .log; .txt is accepted for older builds.
func newestProfilerReport(directories ...string) string {
	var reports []string
	for _, directory := range directories {
		if directory == "" {
			continue
		}
		for _, pattern := range []string{"*.log", "*.txt"} {
			matches, _ := filepath.Glob(filepath.Join(directory, pattern))
			reports = append(reports, matches...)
		}
	}
	sort.Slice(reports, func(i, j int) bool {
		left, leftErr := os.Stat(reports[i])
		right, rightErr := os.Stat(reports[j])
		return leftErr == nil && (rightErr != nil || left.ModTime().After(right.ModTime()))
	})
	if len(reports) == 0 {
		return ""
	}
	return reports[0]
}

func diagnosticSources(gameDir, profileRoot string) []diagnosticSource {
	active := filepath.Join(profileRoot, "active")
	unity := unityLogDirectory()
	var sources []diagnosticSource
	// BepInEx lives under the profile generation because Doorstop targets
	// <active>/BepInEx/core/BepInEx.Preloader.dll, not the Steam game directory.
	if path := firstExisting(
		filepath.Join(active, "BepInEx", "LogOutput.log"),
		filepath.Join(gameDir, "BepInEx", "LogOutput.log"),
	); path != "" {
		sources = append(sources, diagnosticSource{"BepInEx/LogOutput.log", path})
	}
	if path := firstExisting(
		filepath.Join(unity, "Player.log"),
		filepath.Join(gameDir, "Player.log"),
	); path != "" {
		sources = append(sources, diagnosticSource{"Player.log", path})
	}
	if path := firstExisting(filepath.Join(unity, "Player-prev.log")); path != "" {
		sources = append(sources, diagnosticSource{"Player-prev.log", path})
	}
	if path := newestProfilerReport(
		filepath.Join(active, "BepInEx", "config", "LoadTimeProfiler"),
		filepath.Join(gameDir, "BepInEx", "config", "LoadTimeProfiler"),
	); path != "" {
		sources = append(sources, diagnosticSource{"LoadTimeProfiler/latest.log", path})
	}
	// The two records the managed-settings merge leaves behind. Together they
	// answer "why does this player have a different value" from a bundle alone:
	// the baseline is what the portal last wrote, the divergence report names
	// every overridable setting whose value is the player's own and why it was
	// kept. Without them the config files below show a value with no account of
	// where it came from.
	if path := firstExisting(filepath.Join(active, settingsBaselineFilename)); path != "" {
		sources = append(sources, diagnosticSource{settingsBaselineFilename, path})
	}
	if path := firstExisting(filepath.Join(active, settingsDivergenceFile)); path != "" {
		sources = append(sources, diagnosticSource{settingsDivergenceFile, path})
	}
	// The effective configuration is the only way to tell whether a published
	// profile setting actually reached the client. Mods rewrite these files at
	// runtime and ServerSync overwrites synced entries at connect, so the shipped
	// value and the live value routinely differ. Without them a bundle can only
	// show symptoms, never which setting was in force.
	sources = append(sources, configSources(filepath.Join(active, "BepInEx", "config"))...)
	return sources
}

// configSources returns the client's live BepInEx configuration files, newest
// first, bounded so a profile with hundreds of mods cannot blow the upload limit.
func configSources(root string) []diagnosticSource {
	matches, err := filepath.Glob(filepath.Join(root, "*.cfg"))
	if err != nil || len(matches) == 0 {
		return nil
	}
	sort.Slice(matches, func(i, j int) bool {
		left, leftErr := os.Stat(matches[i])
		right, rightErr := os.Stat(matches[j])
		return leftErr == nil && (rightErr != nil || left.ModTime().After(right.ModTime()))
	})
	if len(matches) > maxDiagnosticConfigFiles {
		matches = matches[:maxDiagnosticConfigFiles]
	}
	sources := make([]diagnosticSource, 0, len(matches))
	for _, path := range matches {
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() || info.Size() > maxDiagnosticConfigBytes {
			continue
		}
		sources = append(sources, diagnosticSource{"config/" + filepath.Base(path), path})
	}
	return sources
}

func writeDiagnosticZipEntry(archive *zip.Writer, name string, source io.Reader, size int64) error {
	if size < 0 {
		return errors.New("invalid diagnostic log size")
	}
	entry, err := archive.Create(name)
	if err != nil {
		return err
	}
	_, err = io.Copy(entry, io.LimitReader(source, maxDiagnosticLogBytes))
	return err
}

func uploadDiagnosticsBundle(ctx context.Context, endpoint, token, path string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("bundle", "valheim-diagnostics.zip")
	if err == nil {
		_, err = io.Copy(part, file)
	}
	if closeErr := writer.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, &body)
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	response, err := (&http.Client{Timeout: time.Minute}).Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return fmt.Errorf("upload diagnostics: %s", response.Status)
	}
	return nil
}

func runDiagnosticsCollector(arguments []string) error {
	flags := newFlagSet("collect diagnostics")
	gameDir := flags.String("game-dir", "", "Steam Valheim directory")
	profileRoot := flags.String("profile-root", "", "profile root")
	pid := flags.Int("pid", 0, "Valheim process id")
	if err := flags.Parse(arguments); err != nil {
		return err
	}
	if flags.NArg() != 0 || *gameDir == "" || *profileRoot == "" || *pid < 1 {
		return errors.New("invalid diagnostics collector arguments")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 24*time.Hour)
	defer cancel()
	return collectDiagnosticsAfterExit(ctx, *gameDir, *profileRoot, *pid)
}

func newFlagSet(name string) *flag.FlagSet {
	return flag.NewFlagSet(name, flag.ContinueOnError)
}
