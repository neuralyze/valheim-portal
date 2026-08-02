# What changed and why

<!-- One paragraph. The reason matters more than the diff; the diff is below. -->

# How it was verified

<!--
Name the exact command and its outcome, e.g.
`go test ./internal/app -run TestWorldLiveness` passes, or
`scripts/build-windows-client.sh && file dist/ValheimProfileSync.exe` reports (GUI).
"Tests pass" is not verification.
-->

# Checklist

- [ ] `gofmt -l` reports nothing for the files this PR touches
- [ ] `go build -buildvcs=false ./...`
- [ ] `go vet ./...`
- [ ] `go test ./...`
- [ ] Docs under `docs/` updated, if behaviour or configuration changed
- [ ] No secret, world name, hostname, or absolute host path added — in code,
      tests, comments, docs, or fixtures
