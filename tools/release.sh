#!/usr/bin/env bash
# Cut a release, safely.
#
#   tools/release.sh 1.2.3
#
# Every release that went out wrong went wrong in the same place: the tag was
# placed on a commit that predated the version bump, because the local clone
# was behind origin and `git merge testing` merged a stale local branch. So
# this script never trusts a local branch, and it refuses to tag anything
# whose version, release notes and tag name don't all agree.
#
# It is safe to re-run: if the tag already exists it is removed (locally and
# on origin) and recreated, and every step is checked.
set -euo pipefail

VER=${1:-}
if [ -z "$VER" ]; then
    echo "usage: tools/release.sh <version>   e.g. tools/release.sh 1.2.3" >&2
    exit 1
fi
TAG="v${VER}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mSTOPPED: %s\033[0m\n' "$*" >&2; exit 1; }

if [ -n "$(git status --porcelain)" ]; then
    die "working tree is dirty - commit or stash first"
fi

say "Fetching origin"
git fetch origin --tags --force

say "Merging origin/testing into main"
git checkout main
git merge --ff-only origin/main
# origin/testing, never the local branch: that is the mistake this exists for.
git merge --no-edit origin/testing

# --- the three things that must agree -------------------------------------
FILE_VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' dopeiptv/__init__.py)
PROJ_VER=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)

say "Checking version ${VER}"
[ "$FILE_VER" = "$VER" ] || die "__init__.py says ${FILE_VER}, you asked for ${VER}"
[ "$PROJ_VER" = "$VER" ] || die "pyproject.toml says ${PROJ_VER}, you asked for ${VER}"
grep -q "^## dopeIPTV ${VER}\$" RELEASE_NOTES.md \
    || die "RELEASE_NOTES.md does not start a section with '## dopeIPTV ${VER}'"
grep -q "^## \[${VER}\]\$" CHANGELOG.md \
    || die "CHANGELOG.md has no '## [${VER}]' section"
echo "version, pyproject, release notes and changelog all say ${VER}"

say "Pushing main"
git push origin main

say "Tagging ${TAG} on $(git rev-parse --short HEAD)"
git tag -d "$TAG" 2>/dev/null || true
git push origin ":refs/tags/${TAG}" 2>/dev/null || true
git tag -a "$TAG" -m "dopeIPTV ${VER}"
git push origin "$TAG"

say "Done - ${TAG} is on $(git rev-parse HEAD)"
cat <<EOF

The release workflow is now building. If a GitHub release for ${TAG} already
existed from an earlier attempt, delete it first so no stale assets survive:
https://github.com/slimture/dopeIPTV/releases/tag/${TAG}

Back to the working branch with:  git checkout testing
EOF
