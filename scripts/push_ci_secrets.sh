#!/usr/bin/env bash
# Заливает секреты, нужные .github/workflows/build-release.yml, в GitHub
# Actions (Settings -> Secrets and variables -> Actions) прямо из локальных
# файлов, которые уже лежат на этой машине. Claude такие вещи сам не
# запускает (не должен вводить пароли/ключи никуда за пользователя) —
# запусти сам, один раз, с этой машины, где есть server.json/submit.json/
# admin.json и android/keystore/*.
#
# Использование:
#   bash scripts/push_ci_secrets.sh
set -euo pipefail
cd "$(dirname "$0")/.."

GH="gh"
if ! command -v gh >/dev/null 2>&1; then
  GH="/c/Program Files/GitHub CLI/gh.exe"
fi

req() { [ -f "$1" ] || { echo "Нет файла: $1" >&2; exit 1; }; }

req server.json
req submit.json
req admin.json
req android/keystore/magicsqd-release.jks
req android/keystore/keystore.properties

get_prop() { grep -m1 "^$1=" android/keystore/keystore.properties | cut -d= -f2-; }

"$GH" secret set MAGICSQD_SERVER_JSON  --body-file server.json
"$GH" secret set MAGICSQD_SUBMIT_JSON  --body-file submit.json
"$GH" secret set MAGICSQD_ADMIN_JSON   --body-file admin.json

base64 -w0 android/keystore/magicsqd-release.jks | "$GH" secret set ANDROID_KEYSTORE_BASE64
get_prop storePassword | "$GH" secret set ANDROID_KEYSTORE_PASSWORD
get_prop keyAlias      | "$GH" secret set ANDROID_KEY_ALIAS
get_prop keyPassword   | "$GH" secret set ANDROID_KEY_PASSWORD

echo "Готово. Проверить: $GH secret list"
