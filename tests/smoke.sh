#!/usr/bin/env bash
set -eo pipefail

cd /opt/dev-setup
bash -n setup.sh
shellcheck setup.sh tests/smoke.sh
python3 -m unittest discover -s tests -v

# This test runs only in a disposable container with its own home and packages.
# shellcheck source=setup.sh
source ./setup.sh
WORK=$(mktemp -d)
trap 'rm -rf -- "$WORK"' EXIT
exec 3<> /dev/null
(setup_node)
bash -ic 'node --version && npm --version && pnpm --version && tsc --version'
zsh -ic 'node --version && npm --version && pnpm --version && tsc --version'
before=$(sha256sum "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.nvm/alias/default")
(setup_node)
after=$(sha256sum "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.nvm/alias/default")
[[ $before == "$after" ]]
[[ $(find "$HOME" -name '*.dev-setup.bak.*' | wc -l) -le 1 ]]
printf 'Real Node install, fresh shells, and repeated-run checks passed.\n'

export NVM_DIR="$HOME/.nvm"
# shellcheck source=/dev/null
. "$NVM_DIR/nvm.sh"
mkdir "$WORK/project"
printf 'const message: string = "TypeScript works"; console.log(message);\n' > "$WORK/project/check.ts"
tsc "$WORK/project/check.ts" --outDir "$WORK/project/output"
[[ $(pnpm exec node "$WORK/project/output/check.js") == 'TypeScript works' ]]

# An older user-selected Node must survive repair of its missing global tools.
nvm install 18
nvm alias default 18
older_version=$(nvm current)
(setup_node)
[[ $(nvm version default) == "$older_version" ]]
[[ $(node --version) == "$older_version" ]]
pnpm --version
tsc --version
printf 'Existing older Node and compatible package installation passed.\n'

ensure_gh
gh --version
ensure_gh
printf 'GitHub CLI installation and repeated-run checks passed (no authentication).\n'

(setup_python)
bash -ic 'uv --version && python3.15 --version'
zsh -ic 'uv --version && python3.15 --version'
before=$(sha256sum "$HOME/.bashrc" "$HOME/.zshrc")
(setup_python)
after=$(sha256sum "$HOME/.bashrc" "$HOME/.zshrc")
[[ $before == "$after" ]]
"$HOME/.local/bin/uv" venv --python 3.15 --managed-python "$WORK/python-project/.venv"
"$WORK/python-project/.venv/bin/python" -c 'import ssl, sqlite3; print("Python environment works")'
printf 'Python and uv install, fresh shells, virtual environment, and repeated-run checks passed.\n'
