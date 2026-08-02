package main

import (
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/neuralyze/valheim-portal/internal/agent"
	"github.com/neuralyze/valheim-portal/internal/app"
)

func main() {
	mode := flag.String("mode", "portal", "portal or agent")
	flag.Parse()
	if err := run(*mode); err != nil {
		slog.Error("service stopped", "error", err)
		os.Exit(1)
	}
}
func run(mode string) error {
	switch mode {
	case "portal":
		cfg, err := app.LoadConfig()
		if err != nil {
			return err
		}
		store, err := app.OpenStore(cfg.DatabasePath)
		if err != nil {
			return err
		}
		defer store.Close()
		client, err := app.NewAgentClient(cfg.AgentSocket, cfg.AgentTokenFile)
		if err != nil {
			return err
		}
		server, err := app.NewServer(cfg, store, client)
		if err != nil {
			return err
		}
		httpServer := &http.Server{Addr: cfg.ListenAddr, Handler: server.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 30 * time.Second, WriteTimeout: 15 * time.Minute, IdleTimeout: 90 * time.Second}
		stop := make(chan os.Signal, 1)
		signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
		go func() { <-stop; httpServer.Close() }()
		slog.Info("portal listening", "address", cfg.ListenAddr)
		err = httpServer.ListenAndServe()
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case "agent":
		worlds := map[string]struct{}{}
		for _, world := range strings.Split(os.Getenv("AGENT_ALLOWED_WORLDS"), ",") {
			if world = strings.TrimSpace(world); world != "" {
				worlds[world] = struct{}{}
			}
		}
		if len(worlds) == 0 {
			return errors.New("AGENT_ALLOWED_WORLDS is required")
		}
		cfg := agent.Config{Socket: os.Getenv("AGENT_SOCKET"), TokenFile: os.Getenv("AGENT_TOKEN_FILE"), ScriptDir: os.Getenv("AGENT_SCRIPT_DIR"), WorldRoot: os.Getenv("AGENT_WORLD_ROOT"), AllowedWorlds: worlds}
		if cfg.Socket == "" || cfg.TokenFile == "" || cfg.ScriptDir == "" || cfg.WorldRoot == "" {
			return errors.New("agent socket, token, script, and world root are required")
		}
		slog.Info("agent listening", "socket", cfg.Socket, "worlds", len(worlds))
		return agent.Serve(cfg)
	default:
		return fmt.Errorf("unknown mode %q", mode)
	}
}
