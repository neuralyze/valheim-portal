package main

import (
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

const (
	protocolScheme = "valheim-profile-sync"
	protocolAction = "sync"
	clientFlat     = "flat"
	clientVR       = "vr"
)

type profileRequest struct {
	Portal     *url.URL
	World      string
	Profile    string
	ClientType string
}

func parseProfileURL(raw string) (profileRequest, error) {
	if len(raw) == 0 || len(raw) > 8192 {
		return profileRequest{}, errors.New("invalid profile sync URL")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return profileRequest{}, fmt.Errorf("parse profile sync URL: %w", err)
	}
	if !strings.EqualFold(u.Scheme, protocolScheme) || !strings.EqualFold(u.Host, protocolAction) || u.Opaque != "" || u.User != nil || u.Fragment != "" || u.RawPath != "" || (u.Path != "" && u.Path != "/") {
		return profileRequest{}, errors.New("profile sync URL is not a supported command")
	}
	values, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return profileRequest{}, errors.New("profile sync URL has an invalid query")
	}
	const required = 4
	if len(values) != required {
		return profileRequest{}, errors.New("profile sync URL must contain only portal, world, profile, and client_type")
	}
	for _, key := range []string{"portal", "world", "profile", "client_type"} {
		if len(values[key]) != 1 || values.Get(key) == "" {
			return profileRequest{}, errors.New("profile sync URL is missing a required value")
		}
	}
	portal, err := parsePortalURL(values.Get("portal"))
	if err != nil {
		return profileRequest{}, err
	}
	request := profileRequest{
		Portal:     portal,
		World:      values.Get("world"),
		Profile:    values.Get("profile"),
		ClientType: values.Get("client_type"),
	}
	if err := request.validate(); err != nil {
		return profileRequest{}, err
	}
	return request, nil
}

func parsePortalURL(raw string) (*url.URL, error) {
	portal, err := url.Parse(raw)
	if err != nil {
		return nil, errors.New("portal must be an HTTPS URL")
	}
	if !strings.EqualFold(portal.Scheme, "https") || portal.Host == "" || portal.Hostname() == "" || portal.User != nil || portal.Opaque != "" || portal.Fragment != "" || portal.RawQuery != "" || portal.ForceQuery {
		return nil, errors.New("portal must be an HTTPS URL without credentials, query, or fragment")
	}
	if strings.ContainsRune(portal.Path, '\x00') {
		return nil, errors.New("portal URL contains an invalid path")
	}
	portal.Path = strings.TrimRight(portal.Path, "/")
	portal.RawPath = ""
	return portal, nil
}

func (request profileRequest) validate() error {
	if request.Portal == nil {
		return errors.New("profile sync request is missing a portal")
	}
	if _, err := parsePortalURL(request.Portal.String()); err != nil {
		return err
	}
	if !validIdentifier(request.World) || !validIdentifier(request.Profile) {
		return errors.New("world and profile must be safe identifiers")
	}
	if request.ClientType != clientFlat && request.ClientType != clientVR {
		return errors.New("client_type must be flat or vr")
	}
	return nil
}

func validIdentifier(value string) bool {
	if len(value) == 0 || len(value) > 80 || value == "." || value == ".." {
		return false
	}
	for i := range value {
		c := value[i]
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.') {
			return false
		}
	}
	return true
}

func safeProfileComponent(value string) string {
	base := strings.ToUpper(strings.SplitN(value, ".", 2)[0])
	reserved := base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" || (len(base) == 4 && (strings.HasPrefix(base, "COM") || strings.HasPrefix(base, "LPT")) && base[3] >= '1' && base[3] <= '9')
	if strings.HasSuffix(value, ".") || reserved {
		return "id-" + hex.EncodeToString([]byte(value))
	}
	return value
}

func profileRoot(localAppData string, request profileRequest) (string, error) {
	if err := request.validate(); err != nil {
		return "", err
	}
	if strings.TrimSpace(localAppData) == "" {
		return "", errors.New("LOCALAPPDATA is not set")
	}
	storage, _, err := loadProfileStorageDirectory(localAppData)
	if err != nil {
		return "", err
	}
	profiles := filepath.Join(storage, "profiles")
	name := safeProfileComponent(request.World) + "--" + safeProfileComponent(request.Profile) + "--" + request.ClientType
	root := filepath.Join(profiles, name)
	relative, err := filepath.Rel(profiles, root)
	if err != nil || relative == "." || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		return "", errors.New("profile root escapes the profile store")
	}
	return root, nil
}

func profileShortcutURL(request profileRequest) string {
	values := url.Values{}
	values.Set("portal", request.Portal.String())
	values.Set("world", request.World)
	values.Set("profile", request.Profile)
	values.Set("client_type", request.ClientType)
	return (&url.URL{Scheme: protocolScheme, Host: protocolAction, RawQuery: values.Encode()}).String()
}

var errProfileLocked = errors.New("another profile synchronization is already active")

var profileLockMutexes sync.Map

type profileLock struct {
	file  *os.File
	path  string
	mutex *sync.Mutex
	once  sync.Once
}

func acquireProfileLock(root string) (*profileLock, error) {
	cleanRoot := filepath.Clean(root)
	stored, _ := profileLockMutexes.LoadOrStore(cleanRoot, &sync.Mutex{})
	mutex := stored.(*sync.Mutex)
	mutex.Lock()
	if err := os.MkdirAll(cleanRoot, 0o700); err != nil {
		mutex.Unlock()
		return nil, err
	}
	path := filepath.Join(cleanRoot, ".sync.lock")
	file, err := createLockFile(path)
	if errors.Is(err, os.ErrExist) && clearStaleLock(path) {
		// Whoever wrote that lock is gone and can never release it. Losing the
		// retry to a real competitor is fine: it reports as locked below.
		file, err = createLockFile(path)
	}
	if err != nil {
		mutex.Unlock()
		if errors.Is(err, os.ErrExist) {
			return nil, errProfileLocked
		}
		return nil, err
	}
	if _, err := fmt.Fprintf(file, "%d\n", os.Getpid()); err != nil {
		file.Close()
		os.Remove(path)
		mutex.Unlock()
		return nil, err
	}
	return &profileLock{file: file, path: path, mutex: mutex}, nil
}

func createLockFile(path string) (*os.File, error) {
	return os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
}

// clearStaleLock removes a lock file whose owning launcher is no longer running,
// reporting whether it did.
//
// The lock is a plain exclusive-create file, so nothing releases it when the
// process dies. Closing the window mid-sync therefore used to wedge the profile
// permanently: every later run failed with "another profile synchronization is
// already active" until the file was deleted by hand. The pid written at acquire
// time is what makes that recoverable, so it is finally read back here.
func clearStaleLock(path string) bool {
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	pid, convErr := strconv.Atoi(strings.TrimSpace(string(data)))
	if convErr != nil || pid <= 0 {
		// Empty or unparseable: the writer died between creating the file and
		// recording itself, so no live process can be holding it.
		return os.Remove(path) == nil
	}
	if pid == os.Getpid() || processAliveByPID(pid) {
		return false
	}
	return os.Remove(path) == nil
}

func (lock *profileLock) Close() error {
	if lock == nil {
		return nil
	}
	var result error
	lock.once.Do(func() {
		if err := lock.file.Close(); err != nil {
			result = err
		}
		if err := os.Remove(lock.path); err != nil && !errors.Is(err, os.ErrNotExist) && result == nil {
			result = err
		}
		lock.mutex.Unlock()
	})
	return result
}
