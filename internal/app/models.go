package app

import "time"

type ReleaseStatus string

const (
	Draft     ReleaseStatus = "draft"
	Published ReleaseStatus = "published"
	Archived  ReleaseStatus = "archived"
)

type Release struct {
	ID          string
	World       string
	Profile     string
	ClientType  string
	Version     string
	Notes       string
	Status      ReleaseStatus
	Maintenance bool
	PublishedAt *time.Time
	PublishedBy string
	CreatedAt   time.Time
}

type Artifact struct {
	ID        string
	ReleaseID string
	Kind      string
	Name      string
	SHA256    string
	Size      int64
	Path      string
	CreatedAt time.Time
}

// ProfileManifest is the immutable definition packaged in a profile artifact.
// Package content is fetched separately from the Thunderstore repository.
type ProfileManifest struct {
	Schema     int    `json:"schema"`
	World      string `json:"world"`
	Profile    string `json:"profile"`
	ClientType string `json:"client_type"`
	// "player" or "admin". Absent on definitions built before the split, which
	// profileKindOf treats as player editions rather than rejecting.
	Audience  string            `json:"audience,omitempty"`
	Packages  []ProfilePackage  `json:"packages"`
	Companion *ProfileCompanion `json:"companion,omitempty"`
}

type ProfilePackage struct {
	Namespace string `json:"namespace"`
	Name      string `json:"name"`
	Version   string `json:"version"`
	Filename  string `json:"filename"`
	SHA256    string `json:"sha256"`
	Size      int64  `json:"size"`
}

type ProfileCompanion struct {
	Filename string `json:"filename"`
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
}

// PublicWorld is intentionally limited to player-facing connection metadata.
// It never includes save, process, credential, or host filesystem details.
// Description is an operator-authored blurb rendered as plain text; it is
// never trusted markup.
type PublicWorld struct {
	Name          string
	JoinAddress   string
	Status        string
	ServerVersion string
	Description   string
	Enabled       bool
	UpdatedAt     time.Time
}

// SteamIdentity is a Steam account the portal has seen or been told about.
// PersonaName mirrors the Steam profile name; Label is an operator-set
// override. Both are display metadata: authorization only ever uses SteamID.
type SteamIdentity struct {
	SteamID     string
	PersonaName string
	Label       string
	FirstSeenAt time.Time
	LastSeenAt  time.Time
}

func (i SteamIdentity) DisplayName() string { return displayName(i.Label, i.PersonaName) }

type WorldMember struct {
	World       string
	SteamID     string
	PersonaName string
	Label       string
	Role        string
	GrantedBy   string
	CreatedAt   time.Time
}

func (m WorldMember) IsAdmin() bool { return m.Role == "admin" }

func (m WorldMember) DisplayName() string { return displayName(m.Label, m.PersonaName) }

// displayName prefers the operator-set label so a deliberate note always wins
// over whatever the account currently calls itself on Steam.
func displayName(label, persona string) string {
	if label != "" {
		return label
	}
	if persona != "" {
		return persona
	}
	return "Unnamed player"
}

type AuditEvent struct {
	ID        int64
	Actor     string
	Action    string
	Target    string
	Detail    string
	CreatedAt time.Time
}

type Job struct {
	ID          string
	World       string
	Operation   string
	Status      string
	RequestedBy string
	Detail      string
	CreatedAt   time.Time
	FinishedAt  *time.Time
}
