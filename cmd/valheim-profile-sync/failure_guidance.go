package main

import (
	"errors"
	"fmt"
	"strings"
)

// failureGuidance is what the window tells a player after a failed update.
//
// The first line never changes and is not negotiable: a failed sync replaces nothing, so
// whatever was installed before is still installed and still playable. That is why this
// failure was survivable at all.
//
// The ADVICE changed on 2026-09-14. It used to say "confirm Steam is signed in, confirm
// your account has access to this world, check your internet connection" for every
// failure. Measured that day: the operator's Steam was signed in, their world access was
// fine, the portal itself answered every request, and the actual fault was a single
// Thunderstore CDN edge that would not complete a TCP handshake from their network -
// 92.38.145.145:443 timing out for them while the same URL returned 200 in 0.065 s from
// the server. Three bullets, all wrong, none naming the host that failed. So when the
// cause is a package download the guidance now names the package, names the host, and
// says the one thing that was actually true: try again.
func failureGuidance(title string, err error) string {
	lines := []string{
		title,
		"",
		"No profile was replaced. Your previous working profile remains available.",
		"",
	}
	lines = append(lines, guidanceBullets(err)...)
	lines = append(lines, "", fmt.Sprintf("Technical detail: %v", err))
	return strings.Join(lines, "\r\n")
}

func guidanceBullets(err error) []string {
	var download *packageDownloadError
	if errors.As(err, &download) {
		if download.Network {
			bullets := []string{
				"This was a network failure while downloading one mod file. Steam and your access to this world are not involved, and nothing is wrong with your profile.",
				"",
				"What to do next:",
				fmt.Sprintf("• %s could not be reached from your connection after %d attempts, while the rest of this update worked.", download.Host, download.Attempts),
				fmt.Sprintf("• The file was %s.", download.Package),
				"• Try the profile link again. A single mod-download server being unreachable is usually temporary, and the mods already downloaded are kept, so a second run resumes rather than restarting.",
			}
			if download.Mirrored {
				bullets = append(bullets, "• This world's own server was tried as a second source and could not supply the file either, so the problem is more likely your network path than that one server.")
			}
			return append(bullets, "• If it keeps failing on the same file, a different network - a phone hotspot, or a VPN - takes a different route to that server and usually gets through.")
		}
		return []string{
			fmt.Sprintf("One mod file could not be installed: %s.", download.Package),
			"",
			"What to do next:",
			fmt.Sprintf("• %s answered, but not with the file this profile expects, so this is a problem with the published profile rather than with your computer.", download.Host),
			"• Try the profile link again in case the publish is still settling.",
			"• If it keeps failing, report this message - the world's admin has to republish that mod.",
		}
	}
	return []string{
		"What to do next:",
		"• Confirm Steam is signed in.",
		"• Confirm your account has access to this world.",
		"• Try the profile link again.",
	}
}
