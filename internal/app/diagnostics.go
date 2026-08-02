package app

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

const maxDiagnosticsBundleBytes = int64(64 << 20)

type DiagnosticBundle struct {
	ID         string    `json:"id"`
	World      string    `json:"world"`
	Profile    string    `json:"profile"`
	ClientType string    `json:"client_type"`
	ReleaseID  string    `json:"release_id"`
	SteamID    string    `json:"steam_id"`
	Name       string    `json:"name"`
	SHA256     string    `json:"sha256"`
	Size       int64     `json:"size"`
	Path       string    `json:"-"`
	CreatedAt  time.Time `json:"created_at"`
}

func (s *Server) clientDiagnostics(w http.ResponseWriter, r *http.Request) {
	world, profile, clientType := r.PathValue("world"), r.PathValue("profile"), r.PathValue("clientType")
	claims, ok, err := s.validDeviceToken(r.Context(), r, world, profile, clientType)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	if !ok || claims.Scope != deviceTokenScopeDiagnostics {
		http.Error(w, "client diagnostics authorization required", http.StatusUnauthorized)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxDiagnosticsBundleBytes)
	defer r.Body.Close()
	if err := r.ParseMultipartForm(maxDiagnosticsBundleBytes); err != nil {
		http.Error(w, "invalid diagnostics upload", http.StatusBadRequest)
		return
	}
	file, header, err := r.FormFile("bundle")
	if err != nil {
		http.Error(w, "diagnostics bundle required", http.StatusBadRequest)
		return
	}
	defer file.Close()
	if header.Filename != "valheim-diagnostics.zip" {
		http.Error(w, "invalid diagnostics bundle", http.StatusBadRequest)
		return
	}

	id := randomID()
	directory := filepath.Join(s.cfg.ArtifactRoot, "diagnostics", world, id)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		http.Error(w, "storage failure", http.StatusInternalServerError)
		return
	}
	path := filepath.Join(directory, header.Filename)
	out, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o640)
	if err != nil {
		http.Error(w, "storage failure", http.StatusInternalServerError)
		return
	}
	hash := sha256.New()
	size, copyErr := io.Copy(io.MultiWriter(out, hash), io.LimitReader(file, maxDiagnosticsBundleBytes+1))
	closeErr := out.Close()
	if copyErr != nil || closeErr != nil || size < 1 || size > maxDiagnosticsBundleBytes {
		os.Remove(path)
		http.Error(w, "diagnostics upload failed", http.StatusBadRequest)
		return
	}
	bundle := DiagnosticBundle{ID: id, World: world, Profile: profile, ClientType: clientType, ReleaseID: claims.ReleaseID, SteamID: claims.SteamID, Name: header.Filename, SHA256: hex.EncodeToString(hash.Sum(nil)), Size: size, Path: path, CreatedAt: time.Now().UTC()}
	if err := s.store.AddDiagnosticBundle(r.Context(), bundle); err != nil {
		os.Remove(path)
		http.Error(w, "storage failure", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"id": id})
}

func (s *Server) listDiagnostics(w http.ResponseWriter, r *http.Request) {
	bundles, err := s.store.DiagnosticBundles(r.Context(), 100)
	if err != nil {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(bundles)
}

func (s *Server) downloadDiagnostics(w http.ResponseWriter, r *http.Request) {
	bundle, err := s.store.DiagnosticBundle(r.Context(), r.PathValue("id"))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			http.NotFound(w, r)
			return
		}
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	file, err := os.Open(bundle.Path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || info.Size() != bundle.Size {
		http.Error(w, "diagnostics integrity failure", http.StatusServiceUnavailable)
		return
	}
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil || hex.EncodeToString(hash.Sum(nil)) != bundle.SHA256 {
		http.Error(w, "diagnostics integrity failure", http.StatusServiceUnavailable)
		return
	}
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		http.Error(w, "diagnostics integrity failure", http.StatusServiceUnavailable)
		return
	}
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", `attachment; filename="valheim-diagnostics.zip"`)
	http.ServeContent(w, r, bundle.Name, info.ModTime(), file)
}

func (s *Store) AddDiagnosticBundle(ctx context.Context, bundle DiagnosticBundle) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO diagnostics(id, world, profile, client_type, release_id, steam_id, name, sha256, size, path, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)`, bundle.ID, bundle.World, bundle.Profile, bundle.ClientType, bundle.ReleaseID, bundle.SteamID, bundle.Name, bundle.SHA256, bundle.Size, bundle.Path, bundle.CreatedAt.Format(time.RFC3339Nano))
	return err
}

func (s *Store) DiagnosticBundles(ctx context.Context, limit int) ([]DiagnosticBundle, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, world, profile, client_type, release_id, steam_id, name, sha256, size, path, created_at FROM diagnostics ORDER BY created_at DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	bundles := make([]DiagnosticBundle, 0)
	for rows.Next() {
		bundle, err := scanDiagnosticBundle(rows)
		if err != nil {
			return nil, err
		}
		bundles = append(bundles, bundle)
	}
	return bundles, rows.Err()
}

func (s *Store) DiagnosticBundle(ctx context.Context, id string) (DiagnosticBundle, error) {
	if !validID(id) {
		return DiagnosticBundle{}, os.ErrNotExist
	}
	return scanDiagnosticBundle(s.db.QueryRowContext(ctx, `SELECT id, world, profile, client_type, release_id, steam_id, name, sha256, size, path, created_at FROM diagnostics WHERE id=?`, id))
}

type diagnosticRow interface{ Scan(...any) error }

func scanDiagnosticBundle(row diagnosticRow) (DiagnosticBundle, error) {
	var bundle DiagnosticBundle
	var createdAt string
	if err := row.Scan(&bundle.ID, &bundle.World, &bundle.Profile, &bundle.ClientType, &bundle.ReleaseID, &bundle.SteamID, &bundle.Name, &bundle.SHA256, &bundle.Size, &bundle.Path, &createdAt); err != nil {
		return DiagnosticBundle{}, err
	}
	parsed, err := time.Parse(time.RFC3339Nano, createdAt)
	if err != nil {
		return DiagnosticBundle{}, err
	}
	bundle.CreatedAt = parsed
	return bundle, nil
}
