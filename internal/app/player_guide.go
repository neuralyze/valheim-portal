package app

import (
	"bytes"
	_ "embed"
	"errors"
	"fmt"
	"html/template"
	"net/http"
	"strings"

	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/extension"
	"github.com/yuin/goldmark/parser"
	"github.com/yuin/goldmark/text"
)

// The player guide is one document serving two audiences. A desktop player and a
// headset player need different instructions for the same action -- "hold LeftAlt
// and interact" is unreachable on a controller -- but almost everything else is
// shared, and two hand-maintained copies would drift within a week. So the source
// carries region markers and each request renders the audience it asked for.
//
//go:embed assets/player-guide.md
var playerGuideSource string

const (
	guideFlat = "flat"
	guideVR   = "vr"
)

// Region markers are HTML comments alone on a line, which keeps the source valid
// markdown that renders correctly in any editor or on a git forge.
const (
	guideOpenFlat  = "<!-- guide:flat -->"
	guideCloseFlat = "<!-- /guide:flat -->"
	guideOpenVR    = "<!-- guide:vr -->"
	guideCloseVR   = "<!-- /guide:vr -->"
)

// guideAudienceTitle is what the page calls the reader, and it deliberately matches
// the profile card wording a player clicked to get here.
func guideAudienceTitle(audience string) string {
	if audience == guideVR {
		return "VR headset"
	}
	return "Desktop"
}

// filterGuide returns the guide as the given audience should read it: shared blocks
// plus that audience's own regions, with the other audience's regions and every
// marker removed.
//
// Unbalanced markers are a content bug, not a request error, so this does not fail
// here -- an unclosed region simply runs to the end of the document, and
// validateGuideRegions is what makes that fail loudly, in a test, before it ships.
func filterGuide(source, audience string) string {
	lines := strings.Split(source, "\n")
	// Join rather than append a newline per line: Split leaves a trailing empty
	// element for a document ending in a newline, and writing a newline after it
	// would add one the source never had.
	kept := make([]string, 0, len(lines))
	region := ""
	for _, line := range lines {
		switch strings.TrimSpace(line) {
		case guideOpenFlat:
			region = guideFlat
			continue
		case guideOpenVR:
			region = guideVR
			continue
		case guideCloseFlat, guideCloseVR:
			region = ""
			continue
		}
		if region == "" || region == audience {
			kept = append(kept, line)
		}
	}
	return strings.Join(kept, "\n")
}

// validateGuideRegions reports the structural mistakes that would silently drop
// content: a region left open, a closer with no opener, and one region opened
// inside another. Each of those loses text from one of the two guides while the
// other still looks correct, which is exactly the failure a reader cannot see.
func validateGuideRegions(source string) error {
	region := ""
	for number, line := range strings.Split(source, "\n") {
		trimmed := strings.TrimSpace(line)
		open := ""
		switch trimmed {
		case guideOpenFlat:
			open = guideFlat
		case guideOpenVR:
			open = guideVR
		case guideCloseFlat, guideCloseVR:
			want := guideFlat
			if trimmed == guideCloseVR {
				want = guideVR
			}
			if region == "" {
				return fmt.Errorf("line %d: %s closes a region that was never opened", number+1, trimmed)
			}
			if region != want {
				return fmt.Errorf("line %d: %s closes a guide:%s region", number+1, trimmed, region)
			}
			region = ""
			continue
		default:
			continue
		}
		if region != "" {
			return fmt.Errorf("line %d: guide:%s opens inside guide:%s; regions may not nest", number+1, open, region)
		}
		region = open
	}
	if region != "" {
		return errors.New("guide:" + region + " region is never closed")
	}
	return nil
}

// guideSection is one entry in the table of contents. The anchor is whatever
// goldmark generated for that heading, never a second guess at slugifying it:
// a table of contents whose links do not match the page's own anchors is worse
// than no table of contents, because every entry looks clickable and does nothing.
type guideSection struct {
	Anchor string
	Title  string
}

// renderGuide turns the filtered markdown into HTML and lists its top-level
// sections. Tables are the one extension that matters -- the key reference would
// otherwise render as a wall of pipes -- and auto heading IDs are what make the
// contents links resolve.
func renderGuide(markdown string) (template.HTML, []guideSection, error) {
	source := []byte(markdown)
	converter := goldmark.New(
		goldmark.WithExtensions(extension.Table),
		goldmark.WithParserOptions(parser.WithAutoHeadingID()),
	)
	document := converter.Parser().Parse(text.NewReader(source))

	var sections []guideSection
	err := ast.Walk(document, func(node ast.Node, entering bool) (ast.WalkStatus, error) {
		heading, ok := node.(*ast.Heading)
		// Level 2 only: those are the numbered sections a reader navigates by. The
		// guide has around 60 headings in total and listing them all would be a
		// second document rather than a way into this one.
		if !entering || !ok || heading.Level != 2 {
			return ast.WalkContinue, nil
		}
		id, found := heading.AttributeString("id")
		if !found {
			return ast.WalkContinue, nil
		}
		anchor, ok := id.([]byte)
		if !ok {
			return ast.WalkContinue, nil
		}
		sections = append(sections, guideSection{
			Anchor: string(anchor),
			Title:  string(heading.Text(source)),
		})
		return ast.WalkContinue, nil
	})
	if err != nil {
		return "", nil, err
	}

	var buffer bytes.Buffer
	if err := converter.Renderer().Render(&buffer, source, document); err != nil {
		return "", nil, err
	}
	return template.HTML(buffer.String()), sections, nil
}

// playerGuide serves one audience's guide for a world the player may see. It is
// gated exactly like the world page it is linked from: the guide names this
// deployment's mods, ports and admin tooling, which is not public information.
func (s *Server) playerGuide(w http.ResponseWriter, r *http.Request) {
	world := r.PathValue("world")
	if !s.requireWorldAccess(w, r, world) {
		return
	}
	audience := r.PathValue("clientType")
	if audience != guideFlat && audience != guideVR {
		http.Error(w, "guide must be flat or vr", http.StatusNotFound)
		return
	}
	body, sections, err := renderGuide(filterGuide(playerGuideSource, audience))
	if err != nil {
		http.Error(w, "guide unavailable", http.StatusInternalServerError)
		return
	}
	render(w, playerGuideTemplate, map[string]any{
		"World":     world,
		"Audience":  audience,
		"Title":     guideAudienceTitle(audience),
		"Body":      body,
		"Sections":  sections,
		"OtherName": guideAudienceTitle(otherGuideAudience(audience)),
		"OtherLink": "/worlds/" + template.URLQueryEscaper(world) + "/guide/" + otherGuideAudience(audience),
		"SourceURL": s.cfg.SourceURL,
	})
}

func otherGuideAudience(audience string) string {
	if audience == guideVR {
		return guideFlat
	}
	return guideVR
}

const playerGuideTemplate = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{.Title}} player guide · {{.World}}</title></head>
<body class="guide">
<main class="shell">
<a class="back" href="/worlds/{{.World}}">&larr; {{.World}}</a>
<p class="guide-switch">You are reading the <strong>{{.Title}}</strong> guide.
Playing the other way? <a href="{{.OtherLink}}">{{.OtherName}} guide</a>.</p>
{{if .Sections}}<nav class="guide-toc" aria-label="Contents">
<h2>Contents</h2>
<ol>{{range .Sections}}<li><a href="#{{.Anchor}}">{{.Title}}</a></li>{{end}}</ol>
</nav>{{end}}
<article class="guide-body">
{{.Body}}
</article>
<footer class="guide-footer"><a href="{{.SourceURL}}">Source</a></footer>
</main>
</body></html>`
