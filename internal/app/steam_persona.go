package app

import (
	"context"
	"encoding/json"
	"encoding/xml"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	steamSummariesEndpoint = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
	steamProfileEndpoint   = "https://steamcommunity.com/profiles/"
	// Steam's summaries endpoint accepts 100 IDs per call.
	steamPersonaBatchSize = 100
	steamPersonaTimeout   = 8 * time.Second
	// Keyless lookups hit one profile per request, so bound the fan-out to
	// stay a polite client of steamcommunity.com.
	steamPersonaWorkers = 4
	steamPersonaMaxName = 64
)

// personaResolver maps SteamID64s to Steam persona names. Missing entries mean
// "not resolvable", never an error: names are display metadata only.
type personaResolver func(ctx context.Context, steamIDs []string) map[string]string

// syncSteamPersonas resolves the given accounts and stores every name it got.
// Every failure is survivable: the admin page falls back to the raw Steam ID.
func (s *Server) syncSteamPersonas(ctx context.Context, steamIDs []string) {
	if len(steamIDs) == 0 {
		return
	}
	personas := s.personas(ctx, steamIDs)
	if len(personas) == 0 {
		return
	}
	s.store.SetSteamPersonaNames(ctx, personas)
}

// fetchSteamPersonas uses the official Web API when an API key is configured,
// and otherwise reads the public community profile of each account. The keyless
// path only resolves accounts whose Steam profile is public.
func (s *Server) fetchSteamPersonas(ctx context.Context, steamIDs []string) map[string]string {
	wanted := make([]string, 0, len(steamIDs))
	seen := make(map[string]struct{}, len(steamIDs))
	for _, steamID := range steamIDs {
		if !validSteamID(steamID) {
			continue
		}
		if _, duplicate := seen[steamID]; duplicate {
			continue
		}
		seen[steamID] = struct{}{}
		wanted = append(wanted, steamID)
	}
	if len(wanted) == 0 {
		return nil
	}
	client := &http.Client{Timeout: steamPersonaTimeout}
	if s.cfg.SteamAPIKey != "" {
		return steamPersonasFromWebAPI(ctx, client, steamSummariesEndpoint, s.cfg.SteamAPIKey, wanted)
	}
	return steamPersonasFromPublicProfiles(ctx, client, steamProfileEndpoint, wanted)
}

func steamPersonasFromWebAPI(ctx context.Context, client *http.Client, endpoint, key string, steamIDs []string) map[string]string {
	personas := make(map[string]string, len(steamIDs))
	for start := 0; start < len(steamIDs); start += steamPersonaBatchSize {
		end := min(start+steamPersonaBatchSize, len(steamIDs))
		query := url.Values{"key": {key}, "steamids": {strings.Join(steamIDs[start:end], ",")}}
		body, err := steamGet(ctx, client, endpoint+"?"+query.Encode())
		if err != nil {
			continue
		}
		for steamID, persona := range parseSteamPlayerSummaries(body) {
			personas[steamID] = persona
		}
	}
	return personas
}

func steamPersonasFromPublicProfiles(ctx context.Context, client *http.Client, endpoint string, steamIDs []string) map[string]string {
	type result struct{ steamID, persona string }
	pending := make(chan string)
	// Buffered so a worker never blocks on a consumer that gave up early.
	results := make(chan result, len(steamIDs))
	for range min(steamPersonaWorkers, len(steamIDs)) {
		go func() {
			for steamID := range pending {
				persona := ""
				// The XML view of a community profile is the only account
				// name source that needs no API key.
				if body, err := steamGet(ctx, client, endpoint+steamID+"/?xml=1"); err == nil {
					persona = parseSteamProfileXML(body)
				}
				results <- result{steamID: steamID, persona: persona}
			}
		}()
	}
	go func() {
		defer close(pending)
		for _, steamID := range steamIDs {
			select {
			case pending <- steamID:
			case <-ctx.Done():
				return
			}
		}
	}()
	personas := make(map[string]string, len(steamIDs))
	for range steamIDs {
		select {
		case found := <-results:
			if found.persona != "" {
				personas[found.steamID] = found.persona
			}
		case <-ctx.Done():
			return personas
		}
	}
	return personas
}

func steamGet(ctx context.Context, client *http.Client, endpoint string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, errors.New("steam responded " + response.Status)
	}
	return io.ReadAll(io.LimitReader(response.Body, 1<<20))
}

func parseSteamPlayerSummaries(body []byte) map[string]string {
	var payload struct {
		Response struct {
			Players []struct {
				SteamID     string `json:"steamid"`
				PersonaName string `json:"personaname"`
			} `json:"players"`
		} `json:"response"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil
	}
	personas := make(map[string]string, len(payload.Response.Players))
	for _, player := range payload.Response.Players {
		if !validSteamID(player.SteamID) {
			continue
		}
		if persona := sanitizePersonaName(player.PersonaName); persona != "" {
			personas[player.SteamID] = persona
		}
	}
	return personas
}

func parseSteamProfileXML(body []byte) string {
	var payload struct {
		SteamID string `xml:"steamID"`
	}
	if err := xml.Unmarshal(body, &payload); err != nil {
		return ""
	}
	return sanitizePersonaName(payload.SteamID)
}

// sanitizePersonaName keeps persona names single-line and bounded. Steam allows
// far more exotic display names than the admin tables should ever render.
func sanitizePersonaName(persona string) string {
	persona = strings.Map(func(c rune) rune {
		if c < 0x20 || c == 0x7f {
			return -1
		}
		return c
	}, persona)
	persona = strings.TrimSpace(persona)
	if runes := []rune(persona); len(runes) > steamPersonaMaxName {
		persona = strings.TrimSpace(string(runes[:steamPersonaMaxName]))
	}
	return persona
}
