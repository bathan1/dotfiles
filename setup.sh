#!/usr/bin/env bash

# This file is self-contained so it can be downloaded without cloning the repo.

say() { printf '%s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; return 1; }

ask() {
    printf '%s ' "$1" >&3
    IFS= read -r REPLY <&3 || { fail 'Input closed; rerun from an interactive terminal.'; return 1; }
}

confirm() {
    ask "$1 [y/N]" || return 1
    [[ $REPLY == [yY] || $REPLY == [yY][eE][sS] ]]
}

ensure_packages() {
    local package status
    local -a missing=()
    for package in "$@"; do
        status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null) || status=''
        [[ $status == 'install ok installed' ]] || missing+=("$package")
    done
    ((${#missing[@]})) || return 0
    command -v sudo >/dev/null || { fail "Install sudo or ask an administrator to install: ${missing[*]}"; return 1; }
    say "Installing system packages: ${missing[*]}"
    sudo apt-get update && sudo apt-get install -y -- "${missing[@]}"
}

# Replacements follow symlinks, preserve permissions, and back up only changed files.
write_file() {
    local target=$1 source=$2
    python3 - "$target" "$source" <<'PY'
import os
import pathlib
import shutil
import sys
import tempfile
import time

target = pathlib.Path(sys.argv[1]).resolve()
data = pathlib.Path(sys.argv[2]).read_bytes()
if target.exists() and target.read_bytes() == data:
    sys.exit(0)
target.parent.mkdir(parents=True, exist_ok=True)
mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
if target.exists():
    backup = str(target) + '.dev-setup.bak.' + str(time.time_ns())
    shutil.copy2(target, backup)
    print('Backup: ' + backup)
fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.dev-setup-')
try:
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
    os.chmod(temporary, mode)
    os.replace(temporary, target)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
}

managed_block() {
    local target=$1 name=$2 body=$3 placement=${4:-append}
    local prepared
    prepared=$(mktemp "$WORK/block.XXXXXX") || return 1
    python3 - "$target" "$name" "$body" "$placement" > "$prepared" <<'PY'
import pathlib
import sys

path, name, body, placement = sys.argv[1:]
target = pathlib.Path(path)
text = target.read_text() if target.exists() else ''
begin, end = '# >>> dev-setup ' + name + ' >>>', '# <<< dev-setup ' + name + ' <<<'
lines = text.splitlines(keepends=True)
starts = [i for i, line in enumerate(lines) if line.rstrip('\n') == begin]
ends = [i for i, line in enumerate(lines) if line.rstrip('\n') == end]
if starts or ends:
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        sys.exit('Malformed managed block in ' + path + '; repair it before rerunning.')
    block = begin + '\n' + body.rstrip('\n') + '\n' + end + '\n'
    result = ''.join(lines[:starts[0]]) + block + ''.join(lines[ends[0] + 1:])
else:
    block = begin + '\n' + body.rstrip('\n') + '\n' + end + '\n'
    if placement == 'prepend':
        result = block + text
    else:
        result = text + ('\n' if text and not text.endswith('\n') else '') + block
sys.stdout.write(result)
PY
    local status=$?
    ((status == 0)) || return "$status"
    write_file "$target" "$prepared"
}

configure_shells() {
    local name=$1 body=$2 rc
    for rc in "$HOME/.bashrc" "${ZDOTDIR:-$HOME}/.zshrc"; do
        managed_block "$rc" "$name" "$body" || return 1
    done
}

ensure_gh() {
    local version
    version=$(gh --version 2>/dev/null | head -n 1 | awk '{print $3}') || version=''
    if [[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]] && dpkg --compare-versions "$version" ge 2.50.0; then
        return 0
    fi
    say 'Installing a compatible GitHub CLI from its official apt repository.'
    command -v sudo >/dev/null || { fail 'sudo is required to install GitHub CLI.'; return 1; }
    curl --fail --silent --show-error --location --proto '=https' \
        https://cli.github.com/packages/githubcli-archive-keyring.gpg -o "$WORK/githubcli.gpg" || return 1
    sudo mkdir -p /etc/apt/keyrings || return 1
    sudo install -m 644 "$WORK/githubcli.gpg" /etc/apt/keyrings/githubcli-archive-keyring.gpg || return 1
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' \
        "$(dpkg --print-architecture)" > "$WORK/github-cli.list"
    sudo install -m 644 "$WORK/github-cli.list" /etc/apt/sources.list.d/dev-setup-github-cli.list || return 1
    sudo apt-get update && sudo apt-get install -y gh || return 1
    hash -r
    version=$(gh --version | head -n 1 | awk '{print $3}')
    dpkg --compare-versions "$version" ge 2.50.0 || { fail 'An older gh executable shadows the installed CLI on PATH.'; return 1; }
}

choose_github_account() {
    local account index=1 choice current status
    local -a accounts=()
    # Environment tokens would override the account chosen through gh auth switch.
    unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
    unset GH_PROMPT_DISABLED
    export GH_HOST=github.com NO_COLOR=1
    gh auth status --hostname github.com > "$WORK/gh-status" 2>&1 || true
    while IFS= read -r account; do
        [[ -n $account ]] && accounts+=("$account")
    done < <(sed -nE 's/.*Logged in to github\.com account ([A-Za-z0-9-]+).*/\1/p' "$WORK/gh-status" | sort -u)
    say 'Choose a GitHub account:'
    for account in "${accounts[@]}"; do
        say "  $index) $account"
        ((index += 1))
    done
    say "  $index) Authenticate with another account in your browser"
    while true; do
        ask "Account [1-$index]:" || return 1
        choice=$REPLY
        [[ $choice =~ ^[0-9]{1,3}$ ]] && ((10#$choice >= 1 && 10#$choice <= index)) && break
        say 'Enter a number from the list.'
    done
    if ((10#$choice == index)); then
        say 'Waiting for GitHub browser authentication to finish...'
        gh auth login --hostname github.com --git-protocol ssh --web --skip-ssh-key --scopes admin:public_key <&3 || {
            status=$?
            fail "GitHub browser authentication failed (exit $status)."
            return "$status"
        }
        say 'GitHub browser authentication completed.'
    else
        current=$(gh api user --jq .login 2>/dev/null) || current=''
        account=${accounts[$((10#$choice - 1))]}
        if [[ $current != "$account" ]]; then
            gh auth switch --hostname github.com --user "$account" || return 1
        fi
    fi
    GITHUB_LOGIN=$(gh api user --jq .login) || {
        status=$?
        fail "Could not confirm the authenticated GitHub account (exit $status)."
        return "$status"
    }
    [[ $GITHUB_LOGIN =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || { fail 'GitHub returned an invalid username.'; return 1; }
    say "Selected GitHub account: $GITHUB_LOGIN"
}

ensure_key() {
    local key=$1 login=$2 private_fingerprint public_fingerprint
    if [[ ! -e $key ]]; then
        [[ ! -e $key.pub ]] || { fail "Public key exists without its private key: $key.pub"; return 1; }
        say 'Choose a passphrase for your new key (input is hidden; empty is allowed).'
        ssh-keygen -t ed25519 -f "$key" -C "$login@$(hostname)" <&3 || return 1
    elif [[ ! -s $key ]]; then
        fail "Private key is empty: $key"
        return 1
    fi
    chmod 600 "$key" || return 1
    if [[ ! -s $key.pub ]]; then
        ssh-keygen -y -f "$key" <&3 > "$WORK/public-key" || return 1
        write_file "$key.pub" "$WORK/public-key" || return 1
    fi
    # Reading stdin prevents ssh-keygen from substituting the adjacent .pub file.
    private_fingerprint=$(ssh-keygen -lf /dev/stdin < "$key" | awk '{print $2}') || return 1
    public_fingerprint=$(ssh-keygen -lf "$key.pub" | awk '{print $2}') || return 1
    [[ -n $private_fingerprint && $private_fingerprint == "$public_fingerprint" ]] || {
        fail "Public/private key mismatch at $key; repair the .pub file before rerunning."
        return 1
    }
}

upload_key() {
    local key=$1 material
    material=$(awk '{print $1 " " $2}' "$key.pub")
    if ! gh api --paginate user/keys --jq '.[].key' > "$WORK/registered-keys"; then
        say 'Cannot list account keys. Reauthorizing key-management permissions before one retry.'
        gh auth refresh --hostname github.com --scopes admin:public_key <&3 || return 1
        gh api --paginate user/keys --jq '.[].key' > "$WORK/registered-keys" || return 1
    fi
    if awk '{print $1 " " $2}' "$WORK/registered-keys" | grep -Fxq -- "$material"; then
        say 'Public key is already registered with this account.'
        return 0
    fi
    if ! gh ssh-key add "$key.pub" --title "$(hostname) (dev-setup)"; then
        say 'Key upload failed. Reauthorizing key-management permissions before one retry.'
        gh auth refresh --hostname github.com --scopes admin:public_key <&3 || return 1
        gh ssh-key add "$key.pub" --title "$(hostname) (dev-setup)" || return 1
    fi
}

install_agent_helper() {
    cat > "$WORK/agent.sh" <<'SH'
# Reuse a working agent, including forwarded agents, before starting a local one.
_dev_setup_agent() {
    local agent_status
    command -v ssh-add >/dev/null && command -v flock >/dev/null || return 0
    ssh-add -l >/dev/null 2>&1 && agent_status=0 || agent_status=$?
    [ "$agent_status" -le 1 ] && return 0
    (
        umask 077
        flock -x 9 || exit 1
        export SSH_AUTH_SOCK="$HOME/.ssh/dev-setup-agent.sock"
        ssh-add -l >/dev/null 2>&1 && agent_status=0 || agent_status=$?
        [ "$agent_status" -le 1 ] && exit 0
        rm -f -- "$SSH_AUTH_SOCK"
        ssh-agent -a "$SSH_AUTH_SOCK" >/dev/null 2>&1 9>&-
    ) 9> "$HOME/.ssh/dev-setup-agent.lock" || return 1
    SSH_AUTH_SOCK="$HOME/.ssh/dev-setup-agent.sock"
    export SSH_AUTH_SOCK
}
if _dev_setup_agent; then
    unset -f _dev_setup_agent
else
    unset -f _dev_setup_agent
    return 1
fi
SH
    write_file "$HOME/.ssh/dev-setup-agent.sh" "$WORK/agent.sh" || return 1
    # shellcheck disable=SC2016
    configure_shells ssh-agent '[ ! -r "$HOME/.ssh/dev-setup-agent.sh" ] || . "$HOME/.ssh/dev-setup-agent.sh"' || return 1
    # shellcheck source=/dev/null
    . "$HOME/.ssh/dev-setup-agent.sh"
}

configure_ssh_host() {
    local host=$1 key=$2 config="$HOME/.ssh/config" block
    # Explicit conflicting hosts and Includes need manual review before claiming this host.
    python3 - "$config" "$host" <<'PY'
import pathlib
import shlex
import sys

path, host = pathlib.Path(sys.argv[1]), sys.argv[2]
text = path.read_text() if path.exists() else ''
inside = False
for line in text.splitlines():
    if line == '# >>> dev-setup ssh-' + host + ' >>>':
        inside = True
    elif line == '# <<< dev-setup ssh-' + host + ' <<<':
        inside = False
    elif not inside:
        fields = shlex.split(line, comments=True)
        if not fields:
            continue
        directive = fields[0].lower()
        if directive == 'include' or (directive == 'host' and host.lower() in [f.lower() for f in fields[1:]]):
            sys.exit('Existing SSH Host/Include may configure ' + host + '; review ~/.ssh/config before rerunning.')
PY
    local status=$?
    ((status == 0)) || return "$status"
    [[ $key != *'"'* && $key != *$'\n'* && $key != *'%'* ]] || { fail 'SSH key path contains unsupported quote, newline, or percent characters.'; return 1; }
    printf -v block 'Host %s\n    HostName github.com\n    User git\n    IdentityFile "%s"\n    IdentitiesOnly yes\n    AddKeysToAgent yes\nHost *' "$host" "$key"
    managed_block "$config" "ssh-$host" "$block" prepend || return 1
    chmod 600 "$config"
}

verify_github() {
    local host=$1 login=$2 result status
    result=$(ssh -T -o ConnectTimeout=15 -o ConnectionAttempts=1 "$host" <&3 2>&1)
    status=$?
    say "$result"
    [[ $status == 1 && $result == *"Hi $login! You've successfully authenticated,"* ]] || {
        fail "SSH did not authenticate as $login through $host. Check network access, key authorization, and ssh -G $host."
        return 1
    }
}

setup_github() {
    local key alias fingerprint
    ensure_packages ca-certificates curl git openssh-client python3 util-linux || return 1
    ensure_gh || return 1
    choose_github_account || return 1
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" || return 1
    key="$HOME/.ssh/id_ed25519_github_$GITHUB_LOGIN"
    alias="github-$GITHUB_LOGIN"
    ensure_key "$key" "$GITHUB_LOGIN" || return 1
    install_agent_helper || return 1
    ssh-add "$key" <&3 || return 1
    fingerprint=$(ssh-keygen -lf "$key.pub" | awk '{print $2}')
    ssh-add -l | awk '{print $2}' | grep -Fxq -- "$fingerprint" || {
        fail 'The public key does not match the loaded private key; repair the .pub file before rerunning.'
        return 1
    }
    upload_key "$key" || return 1
    configure_ssh_host "$alias" "$key" || return 1
    verify_github "$alias" "$GITHUB_LOGIN" || return 1
    say "Clone URL: git@$alias:OWNER/REPOSITORY.git"
    if confirm "Also make $GITHUB_LOGIN the default account for github.com?"; then
        configure_ssh_host github.com "$key" || return 1
        verify_github github.com "$GITHUB_LOGIN" || return 1
        gh config set git_protocol ssh --host github.com || return 1
    fi
}

configure_nvm_shells() {
    local rc body quoted
    printf -v quoted '%q' "$NVM_DIR"
    # shellcheck disable=SC2016
    printf -v body 'export NVM_DIR=%s\n[ ! -s "$NVM_DIR/nvm.sh" ] || . "$NVM_DIR/nvm.sh"' "$quoted"
    for rc in "$HOME/.bashrc" "${ZDOTDIR:-$HOME}/.zshrc"; do
        if [[ -f $rc ]] && ! grep -Fq '# >>> dev-setup nvm >>>' "$rc" && \
            grep -Eq '^[[:space:]]*[^#].*(source|\.)[[:space:]]+"?\$\{?NVM_DIR\}?/nvm\.sh' "$rc"; then
            say "Keeping existing nvm initialization in $rc."
        else
            managed_block "$rc" nvm "$body" || return 1
        fi
    done
}

install_node_package() {
    local package=$1 executable=$2 version npm_cli selected_prefix
    selected_prefix=$(dirname "$(dirname "$(command -v node)")")
    if [[ -x $selected_prefix/bin/$executable ]] && "$selected_prefix/bin/$executable" --version; then
        say "Keeping installed $package."
        return 0
    fi
    npm_cli=$(command -v npm)
    npm view "$package@*" version engines --json > "$WORK/package-versions.json" || return 1
    version=$(node - "$WORK/package-versions.json" "$npm_cli" <<'JS'
const fs = require('fs');
const path = require('path');
const metadata = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const npmDirectory = path.dirname(fs.realpathSync(process.argv[3]));
const semver = require(require.resolve('semver', { paths: [npmDirectory] }));
const releases = (Array.isArray(metadata) ? metadata : [metadata]).filter(release =>
    semver.valid(release.version) && !semver.prerelease(release.version) &&
    semver.satisfies(process.versions.node, (release.engines || {}).node || '*')
).sort((a, b) => semver.rcompare(a.version, b.version));
if (!releases.length) {
    console.error('No stable release supports Node ' + process.versions.node + '. Upgrade Node explicitly and retry.');
    process.exit(1);
}
console.log(releases[0].version);
JS
    ) || return 1
    say "Installing $package@$version for $(node --version)."
    npm install --global --engine-strict "$package@$version" || return 1
    "$selected_prefix/bin/$executable" --version
}

setup_node() {
    local default_version selected_prefix npm_prefix
    ensure_packages ca-certificates curl git python3 xz-utils || return 1
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [[ ! -s $NVM_DIR/nvm.sh ]]; then
        if [[ -d $NVM_DIR && -n $(ls -A "$NVM_DIR") ]]; then
            fail "$NVM_DIR is nonempty but has no nvm.sh; repair or move it before rerunning."
            return 1
        fi
        curl --fail --silent --show-error --location --proto '=https' \
            https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh -o "$WORK/install-nvm.sh" || return 1
        PROFILE=/dev/null bash "$WORK/install-nvm.sh" || return 1
    fi
    # shellcheck source=/dev/null
    . "$NVM_DIR/nvm.sh" --no-use || return 1
    default_version=$(nvm version default) || default_version=N/A
    if [[ $default_version == v* ]] && nvm use --silent "$default_version" && node --version && npm --version; then
        say "Keeping default Node $default_version."
    else
        nvm install --lts || return 1
        nvm alias default "$(nvm current)" || return 1
    fi
    selected_prefix=$(dirname "$(dirname "$(command -v node)")")
    npm_prefix=$(npm prefix --global) || return 1
    [[ $selected_prefix == "$NVM_DIR"/versions/node/* && $npm_prefix == "$selected_prefix" ]] || {
        fail 'Node/npm are not using the selected nvm prefix. Check PATH and npm prefix settings.'
        return 1
    }
    configure_nvm_shells || return 1
    # Run outside a project so local npm configuration and packageManager pins do not interfere.
    cd "$WORK" || return 1
    install_node_package pnpm pnpm || return 1
    install_node_package typescript tsc || return 1
    node --version && npm --version && pnpm --version && tsc --version || return 1
    say 'Node tooling is ready. Open a new Bash/Zsh terminal to load nvm.'
}

main() {
    case ${1:-} in
        -h|--help)
            say 'Usage: bash setup.sh'
            say 'Interactive Ubuntu/WSL setup: GitHub SSH, Node.js + TypeScript + pnpm, or both.'
            return 0 ;;
        '') ;;
        *) fail 'Unknown argument. Use --help.'; return 1 ;;
    esac
    [[ $EUID != 0 ]] || { fail 'Run as your normal user, not with sudo. The installer requests sudo when needed.'; return 1; }
    # shellcheck source=/dev/null
    . /etc/os-release || return 1
    if [[ $ID != ubuntu ]] || ! dpkg --compare-versions "$VERSION_ID" ge 22.04; then
        fail 'This installer supports Ubuntu 22.04 and newer, including Ubuntu under WSL.'
        return 1
    fi
    exec 3<> /dev/tty || { fail 'An interactive terminal is required.'; return 1; }
    umask 077
    WORK=$(mktemp -d "${TMPDIR:-/tmp}/dev-setup.XXXXXXXX") || return 1
    trap 'rm -rf -- "$WORK"' EXIT
    trap 'say "Setup interrupted (SIGINT); remaining components were not run." >&2; exit 130' INT
    trap 'say "Setup terminated (SIGTERM); remaining components were not run." >&2; exit 143' TERM
    say 'Developer environment setup'
    say '  1) GitHub SSH automation'
    say '  2) Node.js + TypeScript + pnpm'
    say '  3) Both'
    say '  q) Quit'
    local selection status=0 result component_status
    local -a components=() results=()
    while true; do
        ask 'Choose [1/2/3/q]:' || return 1
        selection=$REPLY
        case $selection in
            1) components=(github); break ;;
            2) components=(node); break ;;
            3|'1 2'|'1,2') components=(github node); break ;;
            q|Q) return 0 ;;
            *) say 'Enter 1, 2, 3, or q.' ;;
        esac
    done
    say "Selected: ${components[*]}"
    for selection in "${components[@]}"; do
        say "--- Setting up $selection ---"
        if ("setup_$selection"); then
            results+=("$selection: ready")
        else
            component_status=$?
            say "Error: $selection setup failed (exit $component_status); continuing with any remaining selected components." >&2
            results+=("$selection: failed (exit $component_status) — fix the error above and rerun this component")
            status=1
        fi
    done
    say ''
    for result in "${results[@]}"; do say "$result"; done
    return "$status"
}

if [[ -z ${BASH_SOURCE[0]:-} || ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
fi
