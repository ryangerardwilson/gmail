#!/usr/bin/env bash
set -euo pipefail

APP=gmail
REPO="ryangerardwilson/gmail"
APP_HOME="$HOME/.${APP}"
INSTALL_DIR="$APP_HOME/bin"
APP_DIR="$APP_HOME/app"
COMPLETION_DIR="$APP_HOME/completions"
COMPLETION_FILE="$COMPLETION_DIR/${APP}.bash"

MUTED='\033[0;2m'
RED='\033[0;31m'
ORANGE='\033[38;5;214m'
NC='\033[0m'

usage() {
  cat <<USAGE
${APP} Installer

Usage: install.sh [options]

Options:
  -h, --help              Display this help message
  -v, --version <version> Install a specific version (e.g., 0.1.0 or v0.1.0)
  -b, --binary <path>     Install from a local binary instead of downloading
      --no-modify-path    Don't modify shell config files (.zshrc, .bashrc, etc.)

Examples:
  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/${REPO}/main/install.sh | bash -s -- --version 0.1.0
  ./install.sh --binary /path/to/gmail
USAGE
}

requested_version=${VERSION:-}
no_modify_path=false
binary_path=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -v|--version)
      [[ -n "${2:-}" ]] || { echo -e "${RED}Error: --version requires an argument${NC}"; exit 1; }
      requested_version="$2"
      shift 2
      ;;
    -b|--binary)
      [[ -n "${2:-}" ]] || { echo -e "${RED}Error: --binary requires a path${NC}"; exit 1; }
      binary_path="$2"
      shift 2
      ;;
    --no-modify-path)
      no_modify_path=true
      shift
      ;;
    *)
      echo -e "${ORANGE}Warning: Unknown option '$1'${NC}" >&2
      shift
      ;;
  esac
done

print_message() {
  local level=$1
  local message=$2
  local color="${NC}"
  [[ "$level" == "error" ]] && color="${RED}"
  echo -e "${color}${message}${NC}"
}

mkdir -p "$INSTALL_DIR"
mkdir -p "$COMPLETION_DIR"

if [[ -n "$binary_path" ]]; then
  [[ -f "$binary_path" ]] || { print_message error "Binary not found: $binary_path"; exit 1; }
  print_message info "\n${MUTED}Installing ${NC}${APP}${MUTED} from local binary: ${NC}${binary_path}"
  cp "$binary_path" "${INSTALL_DIR}/${APP}"
  chmod 755 "${INSTALL_DIR}/${APP}"
  specific_version="local"
else
  raw_os=$(uname -s)
  arch=$(uname -m)

  if [[ "$raw_os" != "Linux" ]]; then
    print_message error "Unsupported OS: $raw_os (this installer supports Linux only)"
    exit 1
  fi

  if [[ "$arch" != "x86_64" ]]; then
    print_message error "Unsupported arch: $arch (this installer supports x86_64 only)"
    exit 1
  fi

  command -v curl >/dev/null 2>&1 || { print_message error "'curl' is required but not installed."; exit 1; }
  command -v tar  >/dev/null 2>&1 || { print_message error "'tar' is required but not installed."; exit 1; }

  mkdir -p "$APP_DIR"
  candidate_filenames=(
    "${APP}-linux-x64.tar.gz"
    "${APP}-linux-x86_64.tar.gz"
    "${APP}-linux-amd64.tar.gz"
  )

  if [[ -z "$requested_version" ]]; then
    release_url_prefix="https://github.com/${REPO}/releases/latest/download"
    specific_version="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
      | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' || true)"
    [[ -n "$specific_version" ]] || specific_version="latest"
  else
    requested_version="${requested_version#v}"
    release_url_prefix="https://github.com/${REPO}/releases/download/v${requested_version}"
    specific_version="${requested_version}"

    http_status=$(curl -sI -o /dev/null -w "%{http_code}" "https://github.com/${REPO}/releases/tag/v${requested_version}")
    if [[ "$http_status" == "404" ]]; then
      print_message error "Release v${requested_version} not found"
      print_message info  "${MUTED}See available releases: ${NC}https://github.com/${REPO}/releases"
      exit 1
    fi
  fi

  if command -v "${APP}" >/dev/null 2>&1; then
    installed_version=$(${APP} -v 2>/dev/null || true)
    if [[ -n "$installed_version" && "$installed_version" == "$specific_version" ]]; then
      print_message info "${MUTED}${APP} version ${NC}${specific_version}${MUTED} already installed${NC}"
      exit 0
    fi
  fi

  print_message info "\n${MUTED}Installing ${NC}${APP} ${MUTED}version: ${NC}${specific_version}"
  tmp_dir="${TMPDIR:-/tmp}/${APP}_install_$$"
  mkdir -p "$tmp_dir"
  archive_path=""
  for filename in "${candidate_filenames[@]}"; do
    url="${release_url_prefix}/${filename}"
    if curl -# -fL -o "$tmp_dir/$filename" "$url"; then
      archive_path="$tmp_dir/$filename"
      break
    fi
  done
  if [[ -z "$archive_path" ]]; then
    print_message error "Could not download a supported Linux archive for this release."
    print_message info  "Checked filenames: ${candidate_filenames[*]}"
    print_message info  "${MUTED}See available release assets:${NC} https://github.com/${REPO}/releases"
    exit 1
  fi
  if ! tar -tzf "$archive_path" >/dev/null 2>&1; then
    print_message error "Downloaded asset is not a valid gzip tar archive: $(basename "$archive_path")"
    print_message info  "${MUTED}See available release assets:${NC} https://github.com/${REPO}/releases"
    exit 1
  fi

  tar -xzf "$archive_path" -C "$tmp_dir"

  extracted_binary="$tmp_dir/${APP}/${APP}"
  if [[ ! -f "$extracted_binary" ]]; then
    extracted_binary="$(find "$tmp_dir" -type f -name "$APP" -perm -111 2>/dev/null | head -n 1 || true)"
  fi
  if [[ -z "$extracted_binary" || ! -f "$extracted_binary" ]]; then
    print_message error "Archive did not contain expected binary '${APP}'"
    print_message info  "${MUTED}See available release assets:${NC} https://github.com/${REPO}/releases"
    exit 1
  fi

  bundle_dir=""
  while IFS= read -r dir; do
    if [[ -f "$dir/${APP}" && -d "$dir/_internal" ]]; then
      bundle_dir="$dir"
      break
    fi
  done < <(find "$tmp_dir" -type f -name "$APP" -perm -111 -printf '%h\n' 2>/dev/null | sort -u)
  if [[ -z "$bundle_dir" ]]; then
    payload_dir="$(dirname "$extracted_binary")"
    if [[ -f "$payload_dir/${APP}" ]]; then
      bundle_dir="$payload_dir"
    fi
  fi
  if [[ -z "$bundle_dir" || ! -f "$bundle_dir/${APP}" ]]; then
    print_message error "Could not locate extracted app bundle directory for '${APP}'."
    print_message info  "${MUTED}See available release assets:${NC} https://github.com/${REPO}/releases"
    exit 1
  fi

  rm -rf "$APP_DIR"
  mkdir -p "$APP_DIR/${APP}"
  cp -a "$bundle_dir/." "$APP_DIR/${APP}/"

  if [[ ! -d "$APP_DIR/${APP}/_internal" ]]; then
    print_message error "Installed bundle is missing '_internal' runtime directory."
    print_message info  "Installed from: $(basename "$archive_path")"
    print_message info  "${MUTED}See available release assets:${NC} https://github.com/${REPO}/releases"
    exit 1
  fi
  if [[ ! -f "$APP_DIR/${APP}/_internal/libpython3.11.so.1.0" ]]; then
    print_message error "Installed runtime is missing libpython3.11.so.1.0"
    print_message info  "Installed from: $(basename "$archive_path")"
    print_message info  "${MUTED}See available release assets:${NC} https://github.com/${REPO}/releases"
    exit 1
  fi
  chmod 755 "$APP_DIR/${APP}/${APP}"
  rm -rf "$tmp_dir"

  cat > "${INSTALL_DIR}/${APP}" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
"${HOME}/.${APP}/app/${APP}/${APP}" "\$@"
SHIM
  chmod 755 "${INSTALL_DIR}/${APP}"
fi

add_to_path() {
  local config_file=$1
  local command=$2

  if grep -Fxq "$command" "$config_file" 2>/dev/null; then
    print_message info "${MUTED}PATH entry already present in ${NC}$config_file"
  elif [[ -w "$config_file" ]]; then
    {
      echo ""
      echo "# ${APP}"
      echo "$command"
    } >> "$config_file"
    print_message info "${MUTED}Added ${NC}${APP}${MUTED} to PATH in ${NC}$config_file"
  else
    print_message info "Add this to your shell config:"
    print_message info "  $command"
  fi
}

add_shell_line() {
  local config_file=$1
  local line=$2
  local label=$3

  if grep -Fxq "$line" "$config_file" 2>/dev/null; then
    print_message info "${MUTED}${label} already present in ${NC}$config_file"
  elif [[ -w "$config_file" ]]; then
    {
      echo ""
      echo "# ${APP}"
      echo "$line"
    } >> "$config_file"
    print_message info "${MUTED}Added ${label} to ${NC}$config_file"
  else
    print_message info "Add this to your shell config:"
    print_message info "  $line"
  fi
}

write_bash_completion() {
  cat > "$COMPLETION_FILE" <<'BASHCOMP'
#!/usr/bin/env bash

_gmail_complete_values() {
  local mode="$1"
  local preset="$2"
  MODE="$mode" PRESET="$preset" python3 - <<'PY'
import json
import os
from pathlib import Path

mode = os.environ.get("MODE", "")
preset = os.environ.get("PRESET", "")

cfg = os.environ.get("GMAIL_CLI_CONFIG")
if cfg:
    path = Path(cfg).expanduser()
else:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        path = Path(xdg).expanduser() / "gmail" / "config.json"
    else:
        path = Path("~/.config/gmail/config.json").expanduser()

if not path.exists():
    raise SystemExit(0)

try:
    raw = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)

accounts = raw.get("accounts", {})
if not isinstance(accounts, dict):
    raise SystemExit(0)

if mode == "presets":
    for key in sorted(accounts.keys()):
        if isinstance(key, str):
            print(key)
    raise SystemExit(0)

if not preset:
    raise SystemExit(0)

account = accounts.get(preset)
if not isinstance(account, dict):
    raise SystemExit(0)

contacts = account.get("contacts", {})
if not isinstance(contacts, dict):
    raise SystemExit(0)

for alias, email in sorted(contacts.items()):
    if not isinstance(alias, str) or not isinstance(email, str):
        continue
    a = alias.strip()
    e = email.strip()
    if not a or not e:
        continue
    print(a)
PY
}

_gmail_completion() {
  local cur prev cword
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  cword="$COMP_CWORD"

  if [[ $cword -eq 1 ]]; then
    COMPREPLY=( $(compgen -W "-v -u auth sc ti td st $(_gmail_complete_values presets '')" -- "$cur") )
    return 0
  fi

  local preset="${COMP_WORDS[1]}"
  local cmd="${COMP_WORDS[2]}"

  if [[ "$preset" == "auth" ]]; then
    return 0
  fi

  if [[ $cword -eq 2 ]]; then
    COMPREPLY=( $(compgen -W "s ls r o mr mra mur mstr mustr d ms si sc sa se cn" -- "$cur") )
    return 0
  fi

  if [[ "$cmd" == "s" && $cword -eq 3 ]]; then
    COMPREPLY=( $(compgen -W "$(_gmail_complete_values contacts "$preset")" -- "$cur") )
    return 0
  fi

  if [[ "$prev" == "-cc" || "$prev" == "-bcc" ]]; then
    COMPREPLY=( $(compgen -W "$(_gmail_complete_values contacts "$preset")" -- "$cur") )
    return 0
  fi
}

complete -F _gmail_completion gmail
BASHCOMP
  chmod 644 "$COMPLETION_FILE"
}

write_bash_completion

if [[ "$no_modify_path" != "true" ]]; then
  if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
    current_shell=$(basename "${SHELL:-bash}")

    case "$current_shell" in
      zsh)  config_candidates=("$HOME/.zshrc" "$HOME/.zshenv" "$XDG_CONFIG_HOME/zsh/.zshrc" "$XDG_CONFIG_HOME/zsh/.zshenv") ;;
      bash) config_candidates=("$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile" "$XDG_CONFIG_HOME/bash/.bashrc" "$XDG_CONFIG_HOME/bash/.bash_profile") ;;
      fish) config_candidates=("$HOME/.config/fish/config.fish") ;;
      *)    config_candidates=("$HOME/.profile" "$HOME/.bashrc") ;;
    esac

    config_file=""
    for f in "${config_candidates[@]}"; do
      if [[ -f "$f" ]]; then config_file="$f"; break; fi
    done

    if [[ -z "$config_file" ]]; then
      print_message info "${MUTED}No shell config file found. Manually add:${NC}"
      print_message info "  export PATH=$INSTALL_DIR:\$PATH"
    else
      if [[ "$current_shell" == "fish" ]]; then
        add_to_path "$config_file" "fish_add_path $INSTALL_DIR"
      else
        add_to_path "$config_file" "export PATH=$INSTALL_DIR:\$PATH"
      fi
    fi
  fi
fi

XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
bash_completion_candidates=(
  "$HOME/.bashrc"
  "$HOME/.bash_profile"
  "$HOME/.profile"
  "$XDG_CONFIG_HOME/bash/.bashrc"
  "$XDG_CONFIG_HOME/bash/.bash_profile"
)
bash_completion_file=""
for f in "${bash_completion_candidates[@]}"; do
  if [[ -f "$f" ]]; then
    bash_completion_file="$f"
    break
  fi
done

if [[ -n "$bash_completion_file" ]]; then
  add_shell_line "$bash_completion_file" "source \"$COMPLETION_FILE\"" "bash completion"
else
  print_message info "${MUTED}Bash completion installed at ${NC}$COMPLETION_FILE"
  print_message info "${MUTED}Add to your shell config:${NC} source \"$COMPLETION_FILE\""
fi

echo ""
print_message info "${MUTED}Installed ${NC}${APP}${MUTED} to ${NC}${INSTALL_DIR}/${APP}"
print_message info "${MUTED}Run:${NC} ${APP} -h"
echo ""
