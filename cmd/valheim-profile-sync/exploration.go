package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// Sends this player's own revealed map and pins to the portal, so the shared map can show where people
// have actually been rather than where the server happened to load ground.
//
// The files are written by the exploration reporter plugin while the game runs, into the directory the
// launcher passes it. They are uploaded on the next sync - not while the game is running - because that
// is when a token exists and when nothing is competing for the disk.
//
// Nothing here is required for a profile to work. Every failure is reported and swallowed: a player
// whose map cannot be uploaded should still get their game.

const maxExplorationReportBytes = 8 << 20

// uploadExplorationReports sends every report for this world that has changed since it was last sent.
// The marker beside each file records the hash that was accepted, so an unchanged map is not re-sent on
// every launch - the file is rewritten by the plugin on every save whether or not anything moved.
func uploadExplorationReports(ctx context.Context, client *portalClient, request profileRequest, token, active string, reporter progressReporter) {
	// Two locations, on purpose. The launcher passes VALHEIM_EXPLORATION_DIR, but a client installed
	// before that existed does not, and the plugin then falls back to its own config directory - which
	// is inside active/ and therefore destroyed by the next sync. Sweeping both means a session
	// recorded by an older launcher is still collected, once, before it is lost.
	sent, failed := 0, 0
	for _, directory := range []string{
		explorationDirectory(active),
		filepath.Join(active, "BepInEx", "config", "exploration"),
	} {
		ok, bad := uploadReportsFrom(ctx, client, request, token, directory)
		sent += ok
		failed += bad
	}
	// Said through the progress reporter, not stderr: this is a windowed application, so anything
	// written to stderr is written to nowhere. A player who wonders why their map is not on the portal
	// deserves to have seen the reason go past.
	switch {
	case failed > 0:
		report(reporter, progressUpdate{Stage: "Map report", Detail: fmt.Sprintf("%d report(s) shared, %d could not be sent.", sent, failed), Percent: 99})
	case sent > 0:
		report(reporter, progressUpdate{Stage: "Map report", Detail: fmt.Sprintf("Shared %d map report(s) from your last session.", sent), Percent: 99})
	}
}
func uploadReportsFrom(ctx context.Context, client *portalClient, request profileRequest, token, directory string) (sent, failed int) {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return 0, 0
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasSuffix(name, ".explored") && !strings.HasSuffix(name, ".pins.json") {
			continue
		}
		if !strings.HasPrefix(name, request.World+"-") {
			continue
		}
		path := filepath.Join(directory, name)
		payload, err := os.ReadFile(path)
		if err != nil || len(payload) == 0 || len(payload) > maxExplorationReportBytes {
			continue
		}
		digest := sha256.Sum256(payload)
		marker := path + ".sent"
		if sent, err := os.ReadFile(marker); err == nil && strings.TrimSpace(string(sent)) == hex.EncodeToString(digest[:]) {
			continue
		}
		field := "explored"
		if strings.HasSuffix(name, ".pins.json") {
			field = "pins"
		}
		if err := client.uploadExploration(ctx, request, token, field, name, payload); err != nil {
			failed++
			continue
		}
		_ = os.WriteFile(marker, []byte(hex.EncodeToString(digest[:])), 0o644)
		sent++
	}
	return sent, failed
}

func (client *portalClient) uploadExploration(ctx context.Context, request profileRequest, token, field, name string, payload []byte) error {
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	part, err := form.CreateFormFile(field, name)
	if err != nil {
		return err
	}
	if _, err := part.Write(payload); err != nil {
		return err
	}
	if err := form.Close(); err != nil {
		return err
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost,
		client.endpoint("client", "exploration", request.World, request.Profile, request.ClientType), &body)
	if err != nil {
		return err
	}
	httpRequest.Header.Set("Authorization", "Bearer "+token)
	httpRequest.Header.Set("Content-Type", form.FormDataContentType())
	response, err := client.httpClient.Do(httpRequest)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4<<10))
	if response.StatusCode != http.StatusCreated {
		return fmt.Errorf("upload exploration: %s", response.Status)
	}
	return nil
}
