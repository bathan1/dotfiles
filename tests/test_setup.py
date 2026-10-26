import os
import pathlib
import shlex
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'setup.sh'


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.work = self.root / 'work'
        self.work.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), WORK=str(self.work),
                        PATH=str(self.bin) + ':/usr/bin:/bin', LC_ALL='C')
        for variable in ('NVM_DIR', 'ZDOTDIR', 'SSH_AUTH_SOCK', 'SSH_AGENT_PID',
                         'BASH_ENV', 'GH_TOKEN', 'GITHUB_TOKEN'):
            self.env.pop(variable, None)

    def run_shell(self, code, input_text='', check=True):
        """`run_shell(code, input_text, check)` is an isolated Bash invocation of CODE with terminal input supplied by INPUT_TEXT."""
        result = subprocess.run(
            ['bash', '-c', 'source ' + shlex.quote(str(SCRIPT)) + '\nexec 3<&0\n' + code],
            input=input_text, text=True, capture_output=True, env=self.env,
            cwd=self.home, timeout=30,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def mock(self, name, body):
        """`mock(name, body)` is an executable test command named NAME with shell BODY."""
        target = self.bin / name
        target.write_text('#!/bin/bash\n' + body + '\n')
        target.chmod(0o755)

    def test_managed_blocks_preserve_content_and_are_idempotent(self):
        rc = self.home / '.bashrc'
        rc.write_text('export EXISTING=yes\n')
        self.run_shell('managed_block "$HOME/.bashrc" test "export ADDED=yes"')
        first = rc.read_bytes()
        first_stat = rc.stat().st_mtime_ns
        self.run_shell('managed_block "$HOME/.bashrc" test "export ADDED=yes"')
        self.assertEqual(rc.read_bytes(), first)
        self.assertEqual(rc.stat().st_mtime_ns, first_stat)
        self.assertEqual(len(list(self.home.glob('.bashrc.dev-setup.bak.*'))), 1)
        self.run_shell('managed_block "$HOME/.bashrc" test "export ADDED=changed"')
        self.assertIn('export EXISTING=yes', rc.read_text())
        self.assertEqual(rc.read_text().count('# >>> dev-setup test >>>'), 1)
        self.assertNotIn('ADDED=yes', rc.read_text())

    def test_malformed_block_is_not_modified(self):
        rc = self.home / '.bashrc'
        original = '# >>> dev-setup test >>>\nkeep me\n'
        rc.write_text(original)
        result = self.run_shell('managed_block "$HOME/.bashrc" test replacement', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(rc.read_text(), original)

    def test_symlink_target_is_backed_up_without_replacing_link(self):
        target = self.home / 'actual-rc'
        target.write_text('existing\n')
        target.chmod(0o640)
        (self.home / '.bashrc').symlink_to(target)
        self.run_shell('managed_block "$HOME/.bashrc" test added')
        self.assertTrue((self.home / '.bashrc').is_symlink())
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(len(list(self.home.glob('actual-rc.dev-setup.bak.*'))), 1)

    def test_zdotdir_and_existing_nvm_initialization(self):
        zdir = self.home / 'zsh settings'
        self.env['ZDOTDIR'] = str(zdir)
        original = 'export NVM_DIR="$HOME/.nvm"\n[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"\n'
        (self.home / '.bashrc').write_text(original)
        self.run_shell('NVM_DIR="$HOME/.nvm"; configure_nvm_shells; configure_nvm_shells')
        self.assertEqual((self.home / '.bashrc').read_text(), original)
        self.assertEqual((zdir / '.zshrc').read_text().count('# >>> dev-setup nvm >>>'), 1)
        self.assertFalse((self.home / '.zshrc').exists())

    def test_configured_nvm_loads_in_both_shells(self):
        nvm = self.home / 'nvm with spaces'
        nvm.mkdir()
        (nvm / 'nvm.sh').write_text('export NVM_TEST_LOADED=yes\n')
        self.env['NVM_DIR'] = str(nvm)
        self.run_shell('configure_nvm_shells')
        for shell, rc in [('bash', '.bashrc'), ('zsh', '.zshrc')]:
            result = subprocess.run([shell, '-c', '. "$HOME/' + rc + '"; test "$NVM_TEST_LOADED" = yes'],
                                    env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_key_is_reused_and_public_key_recovered(self):
        self.run_shell('mkdir -p "$HOME/.ssh"; ssh-keygen -q -t ed25519 -N "" -f "$HOME/.ssh/test"')
        key = self.home / '.ssh/test'
        before = key.read_bytes()
        public = pathlib.Path(str(key) + '.pub')
        original_material = public.read_text().split()[:2]
        public.unlink()
        self.run_shell('ensure_key "$HOME/.ssh/test" alice; ensure_key "$HOME/.ssh/test" alice')
        self.assertEqual(key.read_bytes(), before)
        self.assertEqual(public.read_text().split()[:2], original_material)
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_orphaned_public_key_is_not_overwritten(self):
        key = self.home / 'key.pub'
        key.write_text('keep me')
        self.assertNotEqual(self.run_shell('ensure_key "$HOME/key" alice', check=False).returncode, 0)
        self.assertEqual(key.read_text(), 'keep me')

    def test_mismatched_public_key_is_rejected(self):
        self.run_shell('ssh-keygen -q -t ed25519 -N "" -f "$HOME/key"; ssh-keygen -q -t ed25519 -N "" -f "$HOME/other"')
        (self.home / 'key.pub').write_bytes((self.home / 'other.pub').read_bytes())
        result = self.run_shell('ensure_key "$HOME/key" alice', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mismatch', result.stderr)

    def test_encrypted_key_is_reused_without_a_generation_prompt(self):
        self.run_shell('ssh-keygen -q -t ed25519 -N test-passphrase -f "$HOME/key"')
        original = (self.home / 'key').read_bytes()
        self.run_shell('ensure_key "$HOME/key" alice')
        self.assertEqual((self.home / 'key').read_bytes(), original)

    def test_registered_public_key_is_not_uploaded_again(self):
        (self.home / 'key.pub').write_text('ssh-ed25519 AAAABBBB local-comment\n')
        self.mock('gh', 'if [[ $1 == api ]]; then echo "ssh-ed25519 AAAABBBB server-comment"; else exit 99; fi')
        result = self.run_shell('upload_key "$HOME/key"')
        self.assertIn('already registered', result.stdout)

    def test_failed_key_listing_does_not_upload(self):
        (self.home / 'key.pub').write_text('ssh-ed25519 AAAABBBB\n')
        self.mock('gh', 'if [[ $1 == api || $1 == auth ]]; then exit 1; fi; touch "$HOME/unexpected-upload"')
        self.assertNotEqual(self.run_shell('upload_key "$HOME/key"', check=False).returncode, 0)
        self.assertFalse((self.home / 'unexpected-upload').exists())

    def test_missing_key_listing_scope_is_repaired(self):
        (self.home / 'key.pub').write_text('ssh-ed25519 AAAABBBB\n')
        self.mock('gh', '''
case "$1" in
  api) [[ -f "$HOME/refreshed" ]] || exit 1; echo 'ssh-ed25519 AAAABBBB' ;;
  auth) touch "$HOME/refreshed" ;;
  *) exit 99 ;;
esac
''')
        self.run_shell('upload_key "$HOME/key"')
        self.assertTrue((self.home / 'refreshed').exists())

    def test_upload_failure_refreshes_permissions_once(self):
        (self.home / 'key.pub').write_text('ssh-ed25519 AAAABBBB\n')
        self.mock('gh', '''
echo "$*" >> "$HOME/gh-calls"
if [[ $1 == api ]]; then exit 0; fi
if [[ $1 == auth ]]; then touch "$HOME/refreshed"; exit 0; fi
[[ -f "$HOME/refreshed" ]]
''')
        self.run_shell('upload_key "$HOME/key"')
        calls = (self.home / 'gh-calls').read_text()
        self.assertEqual(calls.count('ssh-key add'), 2)
        self.assertEqual(calls.count('auth refresh'), 1)

    def test_account_selection_ignores_environment_tokens(self):
        self.env['GH_TOKEN'] = 'must-not-be-used'
        self.env['GH_PROMPT_DISABLED'] = '1'
        self.mock('gh', '''
[[ -z ${GH_TOKEN:-} && -z ${GH_PROMPT_DISABLED:-} ]] || exit 88
case "$1 $2" in
  'auth status') printf 'Logged in to github.com account alice\nLogged in to github.com account bob\n' ;;
  'auth switch') echo "$*" > "$HOME/switched"; echo bob > "$HOME/active" ;;
  'api user') if [[ -f "$HOME/active" ]]; then cat "$HOME/active"; else echo alice; fi ;;
esac
''')
        result = self.run_shell('choose_github_account; test "$GITHUB_LOGIN" = bob', '2\n')
        self.assertIn('Selected GitHub account: bob', result.stdout)
        self.assertIn('--user bob', (self.home / 'switched').read_text())

    def test_new_account_uses_browser_authentication(self):
        self.mock('gh', '''
case "$1 $2" in
  'auth status') exit 1 ;;
  'auth login') echo "$*" > "$HOME/login-call" ;;
  'api user') echo new-user ;;
esac
''')
        self.run_shell('choose_github_account; test "$GITHUB_LOGIN" = new-user', 'invalid\n1\n')
        call = (self.home / 'login-call').read_text()
        self.assertIn('--web --skip-ssh-key', call)
        self.assertIn('admin:public_key', call)

    def test_account_lookup_failure_reports_exit_status(self):
        self.mock('gh', '''
case "$1 $2" in
  'auth status') exit 1 ;;
  'auth login') exit 0 ;;
  'api user') exit 23 ;;
esac
''')
        result = self.run_shell('choose_github_account', '1\n', check=False)
        self.assertEqual(result.returncode, 23)
        self.assertIn('authentication completed', result.stdout)
        self.assertIn('Could not confirm the authenticated GitHub account (exit 23)', result.stderr)

    def test_ssh_alias_is_idempotent_and_resolves_correctly(self):
        ssh_dir = self.home / '.ssh'
        ssh_dir.mkdir()
        config = ssh_dir / 'config'
        config.write_text('Host elsewhere\n    HostName example.org\n')
        self.run_shell('configure_ssh_host github-alice "$HOME/.ssh/key"; configure_ssh_host github-alice "$HOME/.ssh/key"')
        result = self.run_shell('ssh -F "$HOME/.ssh/config" -G github-alice')
        self.assertIn('hostname github.com', result.stdout)
        self.assertIn('user git', result.stdout)
        self.assertIn('identitiesonly yes', result.stdout)
        self.assertIn('Host elsewhere', config.read_text())
        self.assertEqual(config.read_text().count('# >>> dev-setup ssh-github-alice >>>'), 1)

    def test_ssh_conflict_preserves_configuration(self):
        ssh_dir = self.home / '.ssh'
        ssh_dir.mkdir()
        config = ssh_dir / 'config'
        for original in ['Host github.com\n    User existing\n', 'Include conf.d/*\n']:
            config.write_text(original)
            result = self.run_shell('configure_ssh_host github.com "$HOME/.ssh/key"', check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_text(), original)

    def test_ssh_verification_requires_account_and_success_exit(self):
        for account, status, success in [('alice', 1, True), ('bob', 1, False), ('alice', 255, False)]:
            self.mock('ssh', f'''echo "Hi {account}! You've successfully authenticated, but GitHub does not provide shell access."
exit {status}''')
            result = self.run_shell('verify_github github-alice alice', check=False)
            self.assertEqual(result.returncode == 0, success)

    def test_agent_helper_preserves_working_forwarded_agent(self):
        self.env['SSH_AUTH_SOCK'] = '/forwarded/socket'
        self.mock('ssh-add', 'exit 1')
        self.mock('ssh-agent', 'touch "$HOME/unexpected-agent"; exit 99')
        self.run_shell('mkdir -p "$HOME/.ssh"; install_agent_helper; test "$SSH_AUTH_SOCK" = /forwarded/socket')
        self.assertFalse((self.home / 'unexpected-agent').exists())

    def test_agent_helper_reuses_local_agent_in_new_shells(self):
        self.mock('ssh-agent', '/usr/bin/ssh-agent "$@" | tee "$HOME/agent-env"; echo start >> "$HOME/agent-starts"')
        result = self.run_shell('''
mkdir -p "$HOME/.ssh"
install_agent_helper || exit 1
ssh-add -l >/dev/null 2>&1; test $? -eq 1 || exit 1
unset SSH_AUTH_SOCK
. "$HOME/.ssh/dev-setup-agent.sh"
ssh-add -l >/dev/null 2>&1; test $? -eq 1 || exit 1
pid=$(sed -nE 's/^SSH_AGENT_PID=([0-9]+);.*/\\1/p' "$HOME/agent-env")
test -n "$pid" && kill "$pid"
''', check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.home / 'agent-starts').read_text().splitlines(), ['start'])

    def test_missing_packages_only_are_installed(self):
        self.mock('dpkg-query', '[[ $* == *missing-tool* ]] && exit 1; echo "install ok installed"')
        self.mock('sudo', 'echo "$*" >> "$HOME/sudo-calls"')
        self.run_shell('ensure_packages present-tool missing-tool; ensure_packages present-tool')
        calls = (self.home / 'sudo-calls').read_text().splitlines()
        self.assertEqual(calls, ['apt-get update', 'apt-get install -y -- missing-tool'])

    def test_incomplete_nvm_directory_is_preserved(self):
        nvm = self.home / '.nvm'
        nvm.mkdir()
        (nvm / 'keep').write_text('existing')
        result = self.run_shell('ensure_packages() { :; }; setup_node', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((nvm / 'keep').read_text(), 'existing')

    def mock_uv(self, install_status=0):
        """`mock_uv(install_status)` is a uv stub with Python installation exit status INSTALL_STATUS."""
        self.mock('uv', f'''
echo "$*" >> "$HOME/uv-calls"
case "$1 $2" in
    '--version ') echo 'uv test-version' ;;
    'python install') exit {install_status} ;;
    'python dir') echo "${{UV_PYTHON_BIN_DIR:-$HOME/.local/bin}}" ;;
    'python find') echo /usr/bin/python3 ;;
    *) exit 99 ;;
esac
''')

    def test_python_setup_reuses_uv_and_preserves_shell_files_on_rerun(self):
        self.mock_uv()
        self.mock('curl', 'touch "$HOME/unexpected-download"; exit 99')
        self.env['ZDOTDIR'] = str(self.home / 'zsh settings')
        self.env['UV_PYTHON_BIN_DIR'] = str(self.home / 'python binaries')
        (self.home / '.bashrc').write_text('export EXISTING=yes\n')
        code = 'ensure_packages() { :; }; setup_python'
        self.run_shell(code)
        rc_paths = [self.home / '.bashrc', pathlib.Path(self.env['ZDOTDIR']) / '.zshrc']
        before = [(rc.read_bytes(), rc.stat().st_mtime_ns) for rc in rc_paths]
        self.run_shell(code)
        self.assertEqual(before, [(rc.read_bytes(), rc.stat().st_mtime_ns) for rc in rc_paths])
        self.assertIn('export EXISTING=yes', rc_paths[0].read_text())
        self.assertFalse((self.home / 'unexpected-download').exists())
        calls = (self.home / 'uv-calls').read_text()
        self.assertIn('python install 3.15 --no-config', calls)
        self.assertIn('python find 3.15 --managed-python --system --no-project --no-config', calls)
        for shell, rc in zip(('bash', 'zsh'), rc_paths):
            result = subprocess.run(
                [shell, '-c', '. "$1"; . "$1"; printf "%s" "$PATH"', shell, str(rc)],
                env=self.env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.split(':').count(self.env['UV_PYTHON_BIN_DIR']), 1)
            self.assertEqual(result.stdout.split(':').count(str(self.bin)), 1)

    def test_python_install_failure_stops_before_shell_configuration(self):
        self.mock_uv(install_status=23)
        result = self.run_shell('ensure_packages() { :; }; setup_python', check=False)
        self.assertEqual(result.returncode, 23)
        self.assertIn('[FAILURE] Python 3.15 (exit 23)', result.stderr)
        self.assertFalse((self.home / '.bashrc').exists())
        self.assertNotIn('python find', (self.home / 'uv-calls').read_text())

    def test_broken_uv_is_not_overwritten(self):
        self.mock('uv', 'exit 17')
        self.mock('curl', 'touch "$HOME/unexpected-download"')
        result = self.run_shell('ensure_packages() { :; }; setup_python', check=False)
        self.assertEqual(result.returncode, 17)
        self.assertFalse((self.home / 'unexpected-download').exists())

    def test_missing_uv_is_installed_without_unmanaged_shell_changes(self):
        self.mock('curl', '''
while [[ $# -gt 0 ]]; do
    if [[ $1 == -o ]]; then cp "$HOME/installer" "$2"; exit; fi
    shift
done
exit 99
''')
        (self.home / 'installer').write_text('''
test "$UV_INSTALL_DIR" = "$HOME/.local/bin" || exit 98
test "$UV_NO_MODIFY_PATH" = 1 || exit 97
mkdir -p "$UV_INSTALL_DIR"
printf '#!/bin/sh\necho uv-test\n' > "$UV_INSTALL_DIR/uv"
chmod +x "$UV_INSTALL_DIR/uv"
''')
        result = self.run_shell('ensure_uv')
        self.assertTrue((self.home / '.local/bin/uv').is_file())
        self.assertIn('[SUCCESS] Run uv install script', result.stdout)
        self.assertFalse((self.home / '.bashrc').exists())

    def test_failed_uv_download_never_runs_installer(self):
        self.mock('curl', 'exit 22')
        result = self.run_shell('ensure_packages() { :; }; setup_python', check=False)
        self.assertEqual(result.returncode, 22)
        self.assertNotIn('[RUN] Run uv install script', result.stdout)

    def test_python_and_all_menu_choices(self):
        import fcntl
        import pty
        import termios

        for choice, expected in [('4', ['python']), ('5', ['github', 'node', 'python'])]:
            with self.subTest(choice=choice):
                master, slave = pty.openpty()

                def terminal_session():
                    """`terminal_session()` is the child process's independent controlling terminal setup."""
                    os.setsid()
                    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

                code = '''
setup_github() { echo github >> "$HOME/jobs"; }
setup_node() { echo node >> "$HOME/jobs"; }
setup_python() { echo python >> "$HOME/jobs"; }
ask() { REPLY=''' + choice + '''; }
main
'''
                try:
                    result = subprocess.run(
                        ['bash', '-c', 'source ' + shlex.quote(str(SCRIPT)) + '\n' + code],
                        env=self.env, stdin=slave, capture_output=True, text=True,
                        preexec_fn=terminal_session, timeout=30,
                    )
                finally:
                    os.close(master)
                    os.close(slave)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((self.home / 'jobs').read_text().splitlines(), expected)
                (self.home / 'jobs').unlink()

    def test_job_reports_success_and_preserves_shell_state(self):
        result = self.run_shell('''
configure() { export JOB_VALUE="$1"; }
run_job 'Configure environment' configure 'value with spaces'
test "$JOB_VALUE" = 'value with spaces'
''')
        self.assertIn('[RUN] Configure environment', result.stdout)
        self.assertIn('[SUCCESS] Configure environment', result.stdout)
        self.assertNotIn('\033[', result.stdout)

    def test_job_reports_failure_and_preserves_exit_status(self):
        result = self.run_shell("run_job 'Broken install' bash -c 'exit 23'", check=False)
        self.assertEqual(result.returncode, 23)
        self.assertIn('[RUN] Broken install', result.stdout)
        self.assertIn('[FAILURE] Broken install (exit 23)', result.stderr)
        self.assertNotIn('[SUCCESS]', result.stdout)

    def test_job_colors_terminal_output_and_honors_no_color(self):
        import pty

        for no_color in ('', '1'):
            with self.subTest(no_color=no_color):
                master, slave = pty.openpty()
                try:
                    result = subprocess.run(
                        ['bash', '-c', 'source ' + shlex.quote(str(SCRIPT)) +
                         '\nrun_job Good true; run_job Bad false'],
                        env=dict(self.env, TERM='xterm', NO_COLOR=no_color),
                        stdout=slave, stderr=slave, timeout=30,
                    )
                    output = os.read(master, 4096).decode()
                finally:
                    os.close(master)
                    os.close(slave)
                self.assertEqual(result.returncode, 1)
                if no_color:
                    self.assertNotIn('\033[', output)
                else:
                    for color, label in [('36', 'RUN'), ('32', 'SUCCESS'), ('31', 'FAILURE')]:
                        self.assertIn(f'\033[{color}m[{label}]\033[0m', output)

    def test_failed_package_index_update_skips_install(self):
        self.mock('dpkg-query', 'exit 1')
        self.mock('sudo', 'echo "$*" >> "$HOME/sudo-calls"; exit 19')
        result = self.run_shell('ensure_packages missing-tool', check=False)
        self.assertEqual(result.returncode, 19)
        self.assertEqual((self.home / 'sudo-calls').read_text().splitlines(), ['apt-get update'])
        self.assertIn('[FAILURE] Update apt package index (exit 19)', result.stderr)

    def test_main_stops_after_component_failure(self):
        code = '''
ensure_packages() { :; }
setup_github() { return 1; }
setup_node() { touch "$HOME/node-ran"; }
ask() { REPLY=3; }
main
'''
        # A pseudo-terminal supplies /dev/tty without touching the caller's terminal.
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)

        def terminal_session():
            """`terminal_session()` is the child process's independent controlling terminal setup."""
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        result = subprocess.run(['bash', '-c', 'source ' + shlex.quote(str(SCRIPT)) + '\n' + code],
                                env=self.env, stdin=slave, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, preexec_fn=terminal_session, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.home / 'node-ran').exists(), result.stdout + result.stderr)
        self.assertIn('[FAILURE] github setup (exit 1)', result.stderr)
        self.assertNotIn('[RUN] node setup', result.stdout)
        self.assertNotIn('All selected setup jobs completed', result.stdout)

    def test_piped_script_starts_menu_and_reads_terminal_input(self):
        import fcntl
        import pty
        import termios

        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)

        def terminal_session():
            """`terminal_session()` is the child process's independent controlling terminal setup."""
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        os.write(master, b'q\n')
        result = subprocess.run(
            ['bash'], input=SCRIPT.read_text(), capture_output=True, text=True,
            env=self.env, pass_fds=(slave,), preexec_fn=terminal_session, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Developer environment setup', result.stdout)
        self.assertIn('1) GitHub SSH automation', result.stdout)
        self.assertNotIn('Setting up', result.stdout)

    def test_both_selection_continues_after_browser_authentication(self):
        import fcntl
        import pty
        import termios

        script = SCRIPT.read_text()
        entrypoint = script.index('\nif [[ -z ${BASH_SOURCE')
        overrides = '''
setup_github() { choose_github_account; }
setup_node() { touch "$HOME/node-ran"; }
'''
        script = script[:entrypoint] + overrides + script[entrypoint:]
        saved_script = self.home / 'setup.sh'
        saved_script.write_text(script)

        for invocation in ('piped', 'file'):
            for auth_status in (0, 17):
                with self.subTest(invocation=invocation, auth_status=auth_status):
                    (self.home / 'node-ran').unlink(missing_ok=True)
                    self.mock('gh', f'''
case "$1 $2" in
  'auth status') exit 1 ;;
  'auth login') read -r; exit {auth_status} ;;
  'api user') echo alice ;;
esac
''')
                    master, slave = pty.openpty()

                    def terminal_session():
                        """`terminal_session()` is the child process's independent controlling terminal setup."""
                        os.setsid()
                        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

                    try:
                        os.write(master, b'3\n1\n\n')
                        command = ['bash'] if invocation == 'piped' else ['bash', str(saved_script)]
                        result = subprocess.run(
                            command, input=script if invocation == 'piped' else None,
                            capture_output=True, text=True, env=self.env,
                            pass_fds=(slave,), preexec_fn=terminal_session, timeout=30,
                        )
                    finally:
                        os.close(master)
                        os.close(slave)

                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, auth_status, output)
                    self.assertEqual((self.home / 'node-ran').exists(), auth_status == 0, output)
                    if auth_status:
                        self.assertIn('GitHub browser authentication failed (exit 17)', result.stderr)
                        self.assertIn('[FAILURE] github setup (exit 17)', result.stderr)
                        self.assertNotIn('[RUN] node setup', result.stdout)
                        self.assertNotIn('All selected setup jobs completed', result.stdout)
                    else:
                        self.assertIn('[SUCCESS] github setup', result.stdout)
                        self.assertIn('[SUCCESS] node setup', result.stdout)
                        self.assertIn('All selected setup jobs completed', result.stdout)


if __name__ == '__main__':
    unittest.main()
