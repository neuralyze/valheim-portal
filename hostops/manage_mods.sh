#!/usr/bin/env bash
# Safe wrapper for the manifest-driven Valheim mod controller.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hostops/lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
require_portal_tools
require_valheim_root
CONTROLLER="$PORTAL_TOOLS_DIR/valheim_mods.py"
WORLDS_ROOT="$VALHEIM_ROOT"

_manage_mods_worlds() {
    local path
    for path in "$WORLDS_ROOT"/*; do
        [[ -f "$path/mods/.active-mod-profile" ]] && basename -- "$path"
    done
}

_manage_mods_profiles() {
    # The world argument is accepted and ignored: every profile is available to every
    # server now, so completing per world would hide the one an operator wants to link.
    local path
    for path in "$WORLDS_ROOT/profiles"/*; do
        [[ -f "$path/profile-manifest.json" ]] && basename -- "$path"
    done
}

_manage_mods_complete() {
    local current previous='' index=${COMP_CWORD:-0} words=("${COMP_WORDS[@]:1}") world=
    current=${COMP_WORDS[index]:-}
    if (( index > 0 )); then
        previous=${COMP_WORDS[index - 1]:-}
    fi
    for word in "${words[@]}"; do
        [[ $word != -* ]] && { world=$word; break; }
    done
    if (( index == 1 )); then
        mapfile -t COMPREPLY < <(compgen -W "$(_manage_mods_worlds) --world --manifest --help" -- "$current")
    elif [[ $previous == --profile && -n $world ]]; then
        mapfile -t COMPREPLY < <(compgen -W "$(_manage_mods_profiles "$world")" -- "$current")
    elif (( index == 2 )) && [[ -n $world ]]; then
        mapfile -t COMPREPLY < <(compgen -W 'list check-updates notes search add remove update export-code deploy' -- "$current")
    else
        mapfile -t COMPREPLY < <(compgen -W '--profile --manifest --client-only --reason --all --apply --interactive --help' -- "$current")
    fi
}

_manage_mods_print_fish_completion() {
    cat <<EOF
function __manage_mods_worlds
    for world in $WORLDS_ROOT/*
        test -f "\$world/mods/.active-mod-profile"; and basename "\$world"
    end
end

function __manage_mods_profiles
    for profile in $WORLDS_ROOT/profiles/*
        test -f "\$profile/profile-manifest.json"; and basename "\$profile"
    end
end
complete -c manage_mods.sh -f
complete -c manage_mods.sh -n 'test (count (commandline -opc)) -eq 1' -a '(__manage_mods_worlds)' -d 'World'
complete -c manage_mods.sh -n 'test (count (commandline -opc)) -eq 2' -a 'list check-updates notes search add remove update export-code deploy'
complete -c manage_mods.sh -l profile -r -a '(__manage_mods_profiles)' -d 'Profile'
complete -c manage_mods.sh -l manifest -r -d 'Manifest path'
complete -c manage_mods.sh -l client-only -d 'Install for clients only'
complete -c manage_mods.sh -l reason -r -d 'Removal reason'
complete -c manage_mods.sh -l all -d 'Update every package'
complete -c manage_mods.sh -l apply -d 'Apply a mutation or deployment'
complete -c manage_mods.sh -l interactive -d 'Open the interactive manager'
EOF
}

_manage_mods_choose() {
    local prompt=$1
    shift
    local choice
    PS3="$prompt "
    select choice in "$@" "Cancel"; do
        [[ $choice == Cancel ]] && return 1
        [[ -n $choice ]] && { REPLY_VALUE=$choice; return 0; }
        echo "Choose a numbered option." >&2
    done
}

_manage_mods_confirm() {
    local reply
    read -r -p "$1 [y/N] " reply
    [[ $reply =~ ^[Yy]([Ee][Ss])?$ ]]
}

_manage_mods_installed_packages() {
    local world=$1 profile=$2 identifier
    while read -r _scope identifier _version; do
        [[ -n ${identifier:-} ]] && printf '%s\n' "$identifier"
    done < <(python3 "$CONTROLLER" --world "$world" --profile "$profile" list)
}

_manage_mods_profile_management() {
    local -a profiles
    local world=$1 action source destination
    while true; do
        _manage_mods_choose "Profile management:" "List profiles" "Create empty profile" "Copy existing profile" "Remove profile" "Back" || return 0
        action=$REPLY_VALUE
        case $action in
            "List profiles")
                python3 "$CONTROLLER" --world "$world" profile list
                ;;
            "Create empty profile")
                read -r -p "New profile name: " destination
                [[ -n $destination ]] || continue
                _manage_mods_confirm "Create empty profile $destination for $world?" &&
                    python3 "$CONTROLLER" --world "$world" profile create "$destination"
                ;;
            "Copy existing profile")
                mapfile -t profiles < <(_manage_mods_profiles "$world")
                ((${#profiles[@]})) || { echo "No source profiles found." >&2; continue; }
                _manage_mods_choose "Copy from:" "${profiles[@]}" || continue
                source=$REPLY_VALUE
                read -r -p "New profile name: " destination
                [[ -n $destination ]] || continue
                _manage_mods_confirm "Copy $source to $destination for $world?" &&
                    python3 "$CONTROLLER" --world "$world" profile copy "$source" "$destination"
                ;;
            "Remove profile")
                mapfile -t profiles < <(_manage_mods_profiles "$world")
                ((${#profiles[@]})) || { echo "No profiles found." >&2; continue; }
                _manage_mods_choose "Profile to remove:" "${profiles[@]}" || continue
                source=$REPLY_VALUE
                _manage_mods_confirm "Permanently remove profile $source from $world?" &&
                    python3 "$CONTROLLER" --world "$world" profile remove "$source"
                ;;
            "Back")
                return 0
                ;;
        esac
    done
}

_manage_mods_interactive() {
    local -a worlds profiles packages results actions
    local world profile action query package scope selected
    mapfile -t worlds < <(_manage_mods_worlds)
    ((${#worlds[@]})) || { echo "No worlds with active mod profiles found." >&2; return 2; }
    _manage_mods_choose "World:" "${worlds[@]}" || return 0
    world=$REPLY_VALUE
    while true; do
        mapfile -t profiles < <(_manage_mods_profiles "$world")
        _manage_mods_choose "Profile:" "${profiles[@]}" "Manage profiles" || return 0
        if [[ $REPLY_VALUE == "Manage profiles" ]]; then
            _manage_mods_profile_management "$world"
            continue
        fi
        profile=$REPLY_VALUE
        break
    done
    actions=("List installed mods" "Check for updates" "Read update notes" "Add mod" "Remove mod" "Update mods" "Export profile code" "Deploy server plugins")

    while true; do
        echo
        echo "World: $world  Profile: $profile"
        _manage_mods_choose "Action:" "${actions[@]}" || return 0
        action=$REPLY_VALUE
        case $action in
            "List installed mods")
                python3 "$CONTROLLER" --world "$world" --profile "$profile" list
                ;;
            "Check for updates")
                python3 "$CONTROLLER" --world "$world" --profile "$profile" check-updates || true
                ;;
            "Read update notes")
                # Offered next to the check, because a version number alone is not a reason to take
                # an update: seven were taken blind in one session, one of them a skills mod three
                # minor versions ahead.
                python3 "$CONTROLLER" --world "$world" --profile "$profile" notes || true
                ;;
            "Add mod")
                read -r -p "Search Thunderstore package: " query
                [[ -n $query ]] || continue
                mapfile -t results < <(python3 "$CONTROLLER" --world "$world" --profile "$profile" search "$query" || true)
                ((${#results[@]})) || { echo "No packages matched." >&2; continue; }
                _manage_mods_choose "Package:" "${results[@]}" || continue
                selected=$REPLY_VALUE
                package=${selected%% *}
                _manage_mods_choose "Install scope:" "shared (server and clients)" "client-only" || continue
                scope=$REPLY_VALUE
                if _manage_mods_confirm "Add $package to $world/$profile?"; then
                    if [[ $scope == client-only ]]; then
                        python3 "$CONTROLLER" --world "$world" --profile "$profile" add "$package" --client-only
                    else
                        python3 "$CONTROLLER" --world "$world" --profile "$profile" add "$package"
                    fi
                fi
                ;;
            "Remove mod")
                mapfile -t packages < <(_manage_mods_installed_packages "$world" "$profile")
                ((${#packages[@]})) || { echo "No installed packages found." >&2; continue; }
                _manage_mods_choose "Package to remove:" "${packages[@]}" || continue
                package=$REPLY_VALUE
                read -r -p "Removal reason: " query
                [[ -n $query ]] || { echo "A removal reason is required." >&2; continue; }
                _manage_mods_confirm "Remove $package from $world/$profile?" &&
                    python3 "$CONTROLLER" --world "$world" --profile "$profile" remove "$package" --reason "$query"
                ;;
            "Update mods")
                _manage_mods_choose "Update action:" "Preview all updates" "Apply all updates" || continue
                if [[ $REPLY_VALUE == "Apply all updates" ]]; then
                    _manage_mods_confirm "Record and download all available updates?" &&
                        python3 "$CONTROLLER" --world "$world" --profile "$profile" update --all --apply
                else
                    python3 "$CONTROLLER" --world "$world" --profile "$profile" update --all
                fi
                ;;
            "Export profile code")
                _manage_mods_confirm "Generate a new profile code?" &&
                    python3 "$CONTROLLER" --world "$world" --profile "$profile" export-code
                ;;
            "Deploy server plugins")
                _manage_mods_choose "Deployment action:" "Preview deployment" "Apply deployment" || continue
                if [[ $REPLY_VALUE == "Apply deployment" ]]; then
                    _manage_mods_confirm "Replace deployed plugins after the controller checks the server is stopped?" &&
                        python3 "$CONTROLLER" --world "$world" --profile "$profile" deploy --apply
                else
                    python3 "$CONTROLLER" --world "$world" --profile "$profile" deploy
                fi
                ;;
        esac
    done
}

case ${1:-} in
    --bash-completion)
        _manage_mods_complete
        complete -F _manage_mods_complete manage_mods.sh
        # shellcheck disable=SC2317  # reached when executed rather than sourced
        return 0 2>/dev/null || exit 0
        ;;
    --fish-completion)
        _manage_mods_print_fish_completion
        exit 0
        ;;
    --worlds)
        _manage_mods_worlds
        exit 0
        ;;
    --interactive)
        [[ -t 0 && -t 1 ]] || { echo "Interactive mode requires a terminal." >&2; exit 2; }
        _manage_mods_interactive
        exit $?
        ;;
esac

if (($# == 0)); then
    [[ -t 0 && -t 1 ]] || { echo "Interactive mode requires a terminal." >&2; exit 2; }
    _manage_mods_interactive
    exit $?
fi

if [[ $1 == --world || $1 == --manifest ]]; then
    exec python3 "$CONTROLLER" "$@"
fi

world=$1
shift
exec python3 "$CONTROLLER" --world "$world" "$@"
