// agent-runner drives the portal's agent surface. It reads the operator conversation, asks omp
// what to do, and requests verbs; the portal decides what is allowed and records what happened.
//
// It holds no model credentials: omp owns authentication (omp auth-broker login). It has no shell
// and no git credential, because the limits that matter here are enforced by absence rather than
// by rules. Run it as its own user with no sudo and no read access to any world's .env.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/neuralyze/valheim-portal/internal/runner"
)

func main() {
	base := flag.String("portal", envOr("PORTAL_BASE_URL", "http://127.0.0.1:8080"), "portal base URL")
	tokenFile := flag.String("token-file", os.Getenv("PORTAL_AGENT_BRIDGE_TOKEN_FILE"), "file holding the bridge token")
	statePath := flag.String("state", envOr("AGENT_RUNNER_STATE", "/var/lib/valheim-agent-runner/cursor"), "file holding the inbox cursor")
	ompPath := flag.String("omp", envOr("AGENT_RUNNER_OMP", "omp"), "omp executable")
	model := flag.String("model", os.Getenv("AGENT_RUNNER_MODEL"), "model for omp to use; omp's default when empty")
	poll := flag.Duration("poll", 3*time.Second, "how often to read the inbox")
	once := flag.Bool("once", false, "read the inbox once and exit, for checking a deployment")
	flag.Parse()

	if *tokenFile == "" {
		fatal(errors.New("PORTAL_AGENT_BRIDGE_TOKEN_FILE or -token-file is required; the same token the portal was given"))
	}
	raw, err := os.ReadFile(*tokenFile)
	if err != nil {
		fatal(fmt.Errorf("bridge token: %w", err))
	}
	token := strings.TrimSpace(string(raw))
	if len(token) < 32 {
		fatal(errors.New("bridge token must be at least 32 characters"))
	}

	// omp -p is its non-interactive mode and --mode json makes the reply parseable. The prompt is
	// appended as the final argument by the decider.
	argv := []string{*ompPath, "-p", "--mode", "json"}
	if *model != "" {
		argv = append(argv, "--model", *model)
	}

	if err := os.MkdirAll(dir(*statePath), 0o750); err != nil {
		fatal(err)
	}

	agent := runner.New(runner.Config{
		BaseURL: *base, Token: token, StatePath: *statePath, Poll: *poll,
	}, runner.CommandDecider{Argv: argv})

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	vocabulary, err := agent.Vocabulary(ctx)
	if err != nil {
		fatal(fmt.Errorf("the portal's verb surface is unreachable: %w", err))
	}
	available := 0
	for _, verb := range vocabulary {
		if verb.Available {
			available++
		}
	}
	fmt.Printf("agent-runner: %d verbs, %d available, cursor %d, model %s\n",
		len(vocabulary), available, agent.Cursor(), orDefault(*model, "omp default"))

	if *once {
		if err := agent.Step(ctx); err != nil {
			fatal(err)
		}
		fmt.Printf("agent-runner: one pass done, cursor %d\n", agent.Cursor())
		return
	}
	if err := agent.Run(ctx); err != nil {
		fatal(err)
	}
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func orDefault(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

func dir(path string) string {
	if index := strings.LastIndex(path, "/"); index > 0 {
		return path[:index]
	}
	return "."
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "agent-runner:", err)
	os.Exit(1)
}
