#!/usr/bin/env python3
"""GitHub device authorization and CLI routing without macOS Keychain access."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# Public identifier of the GitHub CLI OAuth application, not a secret.
# Source: cli/cli v2.96.0 internal/authflow/flow.go.
CLIENT_ID = "178c6fc778ccc68e1d6a"
ROOT = Path(__file__).resolve().parent.parent


def auth_directory():
    git_dir = ROOT / ".git"
    directory = git_dir / "github"
    if not git_dir.is_dir() or git_dir.is_symlink() or directory.is_symlink():
        raise ValueError("An in-project .git directory is required for credential isolation.")
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    return directory


def read_credentials(directory):
    path = directory / "credentials.json"
    if path.is_symlink():
        raise ValueError("Credential file must not be a symlink.")
    if not path.exists():
        return None
    path.chmod(0o600)
    data = json.loads(path.read_text())
    if not data.get("access_token") or not data.get("login"):
        raise ValueError("Project credential file is incomplete; re-run auth login.")
    return data


def save_credentials(directory, data):
    fd, filename = tempfile.mkstemp(prefix=".credentials-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, directory / "credentials.json")
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def request_json(url, form=None, token=None):
    headers = {"Accept": "application/json", "User-Agent": "iTime-project-setup"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def device_login(directory):
    device = request_json("https://github.com/login/device/code", {
        "client_id": CLIENT_ID,
        "scope": "repo",
    })
    if "device_code" not in device:
        raise ValueError("GitHub did not return a device code: " + device.get("error", "unknown"))
    print("Open https://github.com/login/device and sign in manually.", flush=True)
    print("One-time code: " + device["user_code"], flush=True)
    print("Authorization uses GitHub CLI's public OAuth app; requested scope: repo.", flush=True)
    print("Credential destination: " + str(directory / "credentials.json"), flush=True)
    interval = max(5, int(device.get("interval", 5)))
    deadline = time.monotonic() + int(device["expires_in"])
    while time.monotonic() < deadline:
        time.sleep(min(interval, 60, max(0, deadline - time.monotonic())))
        result = request_json("https://github.com/login/oauth/access_token", {
            "client_id": CLIENT_ID,
            "device_code": device["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if result.get("access_token"):
            token = result["access_token"]
            user = request_json("https://api.github.com/user", token=token)
            save_credentials(directory, {
                "login": user["login"],
                "access_token": token,
                "scope": result.get("scope", ""),
            })
            print("Authenticated as " + user["login"] + "; credentials saved only in this project.")
            return 0
        error = result.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            if interval > 60:
                raise ValueError("GitHub requested slower polling; restart login later.")
            continue
        raise ValueError("GitHub device authorization stopped: " + str(error))
    raise ValueError("Device code expired. Re-run auth login.")


def credential_get(directory, action, source, output):
    # Git calls store/erase after requests. Never delegate these to global helpers.
    if action != "get":
        return 0
    fields = {}
    for line in source:
        line = line.rstrip("\n")
        if not line:
            break
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    if fields.get("protocol") != "https" or fields.get("host") != "github.com":
        return 0
    repo = subprocess.run(
        ["git", "-C", str(ROOT), "config", "--local", "--get", "itime.githubRepository"],
        text=True, capture_output=True, check=False,
    ).stdout.strip()
    if not repo or fields.get("path") not in (repo, repo + ".git"):
        return 0
    credentials = read_credentials(directory)
    if credentials:
        output.write("username=" + credentials["login"] + "\n")
        output.write("password=" + credentials["access_token"] + "\n\n")
    return 0


def cli_environment(directory, token):
    env = dict(os.environ)
    for key in list(env):
        if key in {
            "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
            "GH_DEBUG", "GIT_CURL_VERBOSE", "GIT_CONFIG_PARAMETERS",
        } or key.startswith(("GIT_CONFIG_", "GIT_TRACE")):
            env.pop(key, None)
    env.update({
        "GH_TOKEN": token,
        "GH_CONFIG_DIR": str(directory),
        "GH_HOST": "github.com",
        "GH_PROMPT_DISABLED": "1",
        "GH_NO_UPDATE_NOTIFIER": "1",
        "GIT_CONFIG_GLOBAL": str(directory / "gitconfig"),
        "GIT_CONFIG_SYSTEM": os.devnull,
    })
    return env


def main(args):
    directory = auth_directory()
    if args[:1] == ["auth"]:
        if args == ["auth", "login"]:
            return device_login(directory)
        if args[:2] == ["auth", "git-credential"] and len(args) == 3:
            return credential_get(directory, args[2], sys.stdin, sys.stdout)
        if args != ["auth", "status"]:
            raise ValueError("Supported project auth commands: login, status, git-credential.")
    credentials = read_credentials(directory)
    if not credentials:
        print("Not authenticated in iTime. Run ./scripts/github.sh auth login.", file=sys.stderr)
        return 1
    if args == ["auth", "status"]:
        user = request_json("https://api.github.com/user", token=credentials["access_token"])
        print("Authenticated as " + user["login"] + " using " + str(directory / "credentials.json"))
        return 0
    return subprocess.run(
        ["gh", *args], cwd=ROOT,
        env=cli_environment(directory, credentials["access_token"]),
        check=False,
    ).returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nAuthorization cancelled.", file=sys.stderr)
        sys.exit(130)
    except (ValueError, OSError, urllib.error.URLError) as error:
        # Never print request headers, form bodies, tokens, or credential contents.
        print("GitHub project authentication failed: " + str(error), file=sys.stderr)
        sys.exit(1)
