#!/bin/bash
# Minify JS and CSS for production
# Requires: npx terser (JS) and npx csso-cli (CSS)
# Usage: bash scripts/minify.sh

set -e
WWW=custom_components/quizify/www

# JS
for f in admin player timer answers fun-fact utils i18n; do
  npx terser "$WWW/js/$f.js" -o "$WWW/js/$f.min.js" --compress --mangle 2>/dev/null || \
  cp "$WWW/js/$f.js" "$WWW/js/$f.min.js"  # fallback: copy if terser not available
done

# CSS
npx csso "$WWW/css/styles.css" -o "$WWW/css/styles.min.css" 2>/dev/null || \
cp "$WWW/css/styles.css" "$WWW/css/styles.min.css"  # fallback

echo "Minification complete (or fallback copy used)"
