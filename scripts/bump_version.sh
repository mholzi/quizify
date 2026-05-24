#!/usr/bin/env bash
# Usage: ./scripts/bump_version.sh 1.0.12
set -e

NEW_VERSION="${1}"
if [ -z "$NEW_VERSION" ]; then
  echo "Usage: $0 <version>  (e.g. 1.0.12)"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$REPO_ROOT/custom_components/quizify/manifest.json"
ADMIN="$REPO_ROOT/custom_components/quizify/www/admin.html"
PLAYER="$REPO_ROOT/custom_components/quizify/www/player.html"
SW="$REPO_ROOT/custom_components/quizify/www/sw.js"

# Get current version from manifest
OLD_VERSION=$(python3 -c "import json; print(json.load(open('$MANIFEST'))['version'])")
echo "Bumping $OLD_VERSION → $NEW_VERSION"

# Update manifest.json
python3 -c "
import json
with open('$MANIFEST') as f: data = json.load(f)
data['version'] = '$NEW_VERSION'
with open('$MANIFEST', 'w') as f: json.dump(data, f, indent=2)
print('  ✓ manifest.json')
"

# Update ALL ?v= query strings in HTML files (regex catches any version)
for FILE in "$ADMIN" "$PLAYER"; do
  sed -i '' "s/?v=[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*/?v=${NEW_VERSION}/g" "$FILE"
  # Also update the version badge text
  sed -i '' "s/v${OLD_VERSION}/v${NEW_VERSION}/g" "$FILE"
  echo "  ✓ $(basename $FILE)"
done

# Update Service Worker cache version
sed -i '' "s/quizify-v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*/quizify-v${NEW_VERSION}/g" "$SW"
echo "  ✓ sw.js"

echo "Done. Commit and tag v${NEW_VERSION}."
