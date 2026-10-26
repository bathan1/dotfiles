# Developer setup

Copy and paste this into Bash or Zsh to download, run, and choose what to install:

```bash
curl -fsSL https://raw.githubusercontent.com/bathan1/dotfiles/main/setup.sh | bash
```

The installer reads your choices from the terminal, so interactive prompts work
even when the script is piped into Bash. To save and inspect it before running:

```bash
curl -fsSL https://raw.githubusercontent.com/bathan1/dotfiles/main/setup.sh -o setup.sh
# Inspect setup.sh, then run:
bash setup.sh
```

The download URL becomes available once this change is pushed to `main`. You can
also run `bash setup.sh` from this checkout. Review the downloaded script before
running it. No repository checkout or adjacent files are required.

The menu offers **GitHub SSH**, **Node.js + TypeScript + pnpm**, their combination,
**Python 3.15 + uv**, or **all three components**. Choose **4** for Python or **5** for all.
It supports native Ubuntu 22.04+ and Ubuntu under WSL, with Bash and Zsh shell
initialization. Run it as your normal user: it requests `sudo` for system packages
only. It needs internet access and an interactive terminal. WSL installations
remain inside Linux; Windows installations and terminal settings are untouched.

## GitHub SSH

Choose a saved GitHub CLI account or sign into another account through GitHub's
browser/device-code flow. If no browser opens, follow the URL and code printed by
`gh` on another device. No account, email, token, or private key is embedded in the
script. Environment tokens are ignored inside this component so they cannot
override your account selection. Your chosen account becomes the active `gh`
account; other saved accounts remain available.

The installer creates a dedicated Ed25519 key at
`~/.ssh/id_ed25519_github_<username>` and prompts for a passphrase. It reuses that
key on subsequent runs and uploads only its public key, only if absent from the
selected account. Existing unrelated keys are preserved. GitHub may request
browser authorization for key-management permissions. GitHub CLI manages its own
credential storage using its normal platform behavior.

The SSH alias `github-<username>` supports cloning with:

```bash
git clone git@github-YOUR_USERNAME:OWNER/REPOSITORY.git
```

You can additionally make the account the default for normal `git@github.com:...`
URLs. Existing repository remotes and Git author identity are not changed. An
existing explicit SSH host or `Include` directive triggers a conflict message
instead of an automatic override; review `~/.ssh/config` and rerun. Wildcard or
`Match` settings may also affect SSH; the final authentication check must match
the chosen account. First connections retain SSH's normal host authenticity
prompt; verify GitHub's [published host fingerprints][fingerprints].

A shared agent helper in `~/.ssh/dev-setup-agent.sh` reuses an available agent,
including forwarded agents, or starts a local agent using a locked socket.
It never asks for a passphrase during shell startup. The installer adds the key
interactively; after restarting the agent or rebooting, the first SSH operation
may request its passphrase again. Your choice of an empty passphrase is respected.

GitHub CLI older than 2.50.0 is updated through [GitHub's official apt repository][gh-linux],
as some Ubuntu-packaged releases use deprecated APIs. Other system packages are
installed only when missing. Organization SSO authorization, if required, remains
an organization-specific step; successful account authentication does not grant
access to every repository.

## Node.js, TypeScript, and pnpm

The script installs nvm **v0.40.8** if missing, honors an exported `NVM_DIR`, and
keeps an existing nvm installation. It reuses a working nvm default Node version;
otherwise it installs current LTS and makes that the default. System Node and
other nvm versions are retained.

Missing pnpm and TypeScript are installed globally into the selected nvm Node
version using npm, without `sudo`. The installer selects the latest stable
release compatible with that Node version's published engine requirements.
Working pnpm/TypeScript commands in the selected Node installation are preserved;
ordinary reruns do not upgrade them. Different Node versions have separate global
packages. Other package-manager installations remain untouched.

Open a new terminal after installation. Bash and Zsh receive guarded nvm
initialization; existing recognizable nvm initialization is kept, and `ZDOTDIR`
is respected when exported. The script does not install or change your shell,
copy this repository's personal `.zshrc`, or create projects.

Global `tsc` is useful for quick experiments. For reproducible project builds,
install TypeScript in the project too:

```bash
pnpm add --save-dev typescript
pnpm exec tsc --init
```

## Python and uv

Python has no separate LTS release line. This setup uses **Python 3.15**, a stable
release supported through October 2031 according to the [Python support schedule][python-support].
It installs [uv][uv-install] with Astral's installer into `~/.local/bin` if missing
and reuses an existing working uv installation. A broken uv command stops setup.

uv installs its managed Python 3.15 distribution with a versioned executable
(`python3.15`), leaving the system `python3` unchanged. Reruns reuse an installed
3.15 version; setup does not force upgrades or replace conflicting executables.
Bash and Zsh receive managed PATH blocks for uv and Python, including custom
`UV_PYTHON_BIN_DIR` and `ZDOTDIR` locations. Each job reports success or failure,
and any failure stops setup.

Open a new terminal after setup. To create a project environment:

```bash
uv venv --python 3.15
uv pip install requests
uv run --python 3.15 python --version
```

To update later, use `uv python upgrade 3.15`; update uv through its original
installation method (`uv self update` for the standalone installer).

## Reruns and recovery

Rerun the script and select the incomplete component. Each job prints a start
message and its success or failure, with color in terminals (set `NO_COLOR=1` to
disable color). The first failure stops the entire script with a nonzero exit
status; remaining jobs are not run. Successful changes remain in place for the
next run. This is installation/repair, not an automatic upgrade or uninstall tool.

Changed user configuration files receive adjacent
`.dev-setup.bak.<timestamp>` backups. Unchanged files are not rewritten or backed
up again. Managed blocks are updated in place; symlinks are preserved and their
targets are backed up. To undo a configuration change, inspect the backup and
restore its contents, or remove the relevant `dev-setup` block. Do not delete SSH
keys as a troubleshooting step. Backups do not cover package-manager operations
or GitHub CLI's own credential files.

For SSH failures, try `ssh -G github-YOUR_USERNAME` and
`ssh -vT github-YOUR_USERNAME`. A mismatched `.pub` file, existing SSH routing,
revoked credentials, network restrictions, or missing organization authorization
may need attention. A missing `.pub` file is recovered from its private key.

For Node failures, check `nvm current`, `npm prefix -g`, and your `.npmrc` for a
custom `prefix`. An incomplete, nonempty nvm directory is left for you to repair
rather than overwritten. If your Node version is too old, upgrade it explicitly
with nvm before rerunning. Existing versions are never silently replaced just to
make a package installation succeed.

## Development checks

```bash
bash -n setup.sh
shellcheck setup.sh tests/smoke.sh
python3 -m unittest discover -s tests -v
docker build -f tests/Dockerfile -t dev-setup-test .
docker run --rm dev-setup-test
```

The unit tests use temporary homes and mocked GitHub/network/package commands.
The container smoke test downloads real nvm/Node/npm packages, checks fresh Bash
and Zsh sessions, compiles TypeScript, and repeats installation to verify stable
state. It also exercises an older Node default and installs GitHub CLI without
logging into GitHub. It also installs Python and uv, creates a virtual environment,
and checks fresh shells and repeated runs. Change the Docker build argument `UBUNTU_VERSION` to test
another Ubuntu release. Live browser authorization, account key upload, and
WSL-specific integration require manual testing on an appropriate machine.

[fingerprints]: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
[gh-linux]: https://github.com/cli/cli/blob/trunk/docs/install_linux.md
[python-support]: https://devguide.python.org/versions/
[uv-install]: https://docs.astral.sh/uv/getting-started/installation/
