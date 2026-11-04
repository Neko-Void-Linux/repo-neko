#!/usr/bin/env python3
"""
Decide which neko-pkgs templates need (re)building in repo-neko.

Static template parsing only (never source templates directly).
For each template directory in neko-pkgs/ (symlinked subpackages like
chatgpt -> codex are built with their parent):
Checks if <pkg>-<ver>_<rev>.<arch>.xbps is present in the published
release asset list.

Usage:
    check_outdated.py <templates_dir> <assets_file> [--arch=x86_64] [--force]
    Outputs: pkgs:<pkg1> <pkg2> ...
"""

import fnmatch
import os
import re
import sys


def parse_template(path):
    """Extract pkgname/version/revision/archs with shell-safe static rules."""
    vals = {}
    assign = re.compile(r'^(pkgname|version|revision|archs)=["\']?([^"\']*)["\']?\s*$')
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = assign.match(line.strip())
            if m and m.group(1) not in vals:
                vals[m.group(1)] = m.group(2)
    return vals


def is_broken(path):
    """True if broken= applies to every arch (not guarded specifically by musl)."""
    guard = re.compile(r'XBPS_TARGET_LIBC"\s*=\s*"?musl"?')
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        recent_guard = False
        for line in fh:
            s = line.strip()
            if guard.search(s):
                recent_guard = True
            if s.startswith("broken=") and not recent_guard:
                return True
            if s == "fi":
                recent_guard = False
    return False


def musl_broken(path):
    """True if the template sets broken= under a musl guard."""
    guard = re.compile(r'XBPS_TARGET_LIBC"\s*=\s*"?musl"?')
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        recent_guard = False
        window = []
        for line in fh:
            s = line.strip()
            window.append(s)
            window = window[-6:]
            if guard.search(s):
                recent_guard = True
            if s.startswith("broken=") and (
                recent_guard or "musl" in " ".join(window)
            ):
                return True
            if s == "fi":
                recent_guard = False
    return False


def wanted_for(archs, target):
    """Checks if target arch matches archs pattern."""
    if not archs:
        return True
    return any(fnmatch.fnmatchcase(target, pat) for pat in archs.split())


def load_pool(path):
    """Load asset names that end with .xbps."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return {ln.strip() for ln in fh if ln.strip().endswith(".xbps")}
    except Exception as e:
        print(f"WARN: could not load assets from {path}: {e}", file=sys.stderr)
        return set()


def collect_need(templates_dir, arch, pool, force=False):
    """Find templates needing build."""
    SKIP_DIRS = {"xbps-triggers", ".git", ".github"}
    need = []
    for entry in sorted(os.listdir(templates_dir)):
        tpldir = os.path.join(templates_dir, entry)
        # Skip symlinks (subpackages compiled with their parent)
        if not os.path.isdir(tpldir) or os.path.islink(tpldir):
            continue
        if entry in SKIP_DIRS:
            continue
        tpl = os.path.join(tpldir, "template")
        if not os.path.isfile(tpl):
            continue
        vals = parse_template(tpl)
        if not all(k in vals for k in ("pkgname", "version", "revision")):
            need.append(entry)
            continue
        if is_broken(tpl):
            continue
        if arch == "x86_64-musl" and musl_broken(tpl):
            continue
        if not wanted_for(vals.get("archs", ""), arch):
            continue
        want = (
            f"{vals['pkgname']}-{vals['version']}_"
            f"{vals['revision']}.{arch}.xbps"
        )
        if force or want not in pool:
            need.append(entry)
    return need


def main():
    args = sys.argv[1:]
    arch = "x86_64"
    force = False
    positionals = []
    for a in args:
        if a.startswith("--arch="):
            arch = a.split("=", 1)[1]
        elif a == "--force":
            force = True
        else:
            positionals.append(a)

    if len(positionals) < 2:
        print("Usage: check_outdated.py <templates_dir> <assets_file> [--arch=x86_64] [--force]", file=sys.stderr)
        sys.exit(1)

    templates_dir = positionals[0]
    assets_file = positionals[1]

    pool = load_pool(assets_file)
    need = collect_need(templates_dir, arch, pool, force=force)
    print(f"pkgs:{' '.join(need)}")


if __name__ == "__main__":
    main()
