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
  # gh не на PATH в этой оболочке — пробуем оба стиля пути к одному и тому
  # же файлу: /c/... (Git Bash/MSYS) и /mnt/c/... (WSL, если голый `bash`
  # из PowerShell запустил именно его, а не Git Bash — тогда /c/... не
  # существует, нужен /mnt/c/...).
  for candidate in \
    "/c/Program Files/GitHub CLI/gh.exe" \
    "/mnt/c/Program Files/GitHub CLI/gh.exe"
  do
    if [ -f "$candidate" ]; then
      GH="$candidate"
      break
    fi
  done
  if [ "$GH" = "gh" ]; then
    echo "Не нашёл gh.exe. Запусти этот скрипт из Git Bash (не из PowerShell голой командой bash — это может открыть WSL, где другие пути), либо укажи путь вручную: GH=\"путь\\к\\gh.exe\" bash scripts/push_ci_secrets.sh" >&2
    exit 1
  fi
fi

req() { [ -f "$1" ] || { echo "Нет файла: $1" >&2; exit 1; }; }

req server.json
req submit.json
req admin.json
req android/keystore/magicsqd-release.jks
req android/keystore/keystore.properties

get_prop() { grep -m1 "^$1=" android/keystore/keystore.properties | cut -d= -f2-; }

"$GH" secret set MAGICSQD_SERVER_JSON  < server.json
"$GH" secret set MAGICSQD_SUBMIT_JSON  < submit.json
"$GH" secret set MAGICSQD_ADMIN_JSON   < admin.json

# Закрытый каталог: один и тот же секрет сборки лежит в server.json (поле app_build_secret — его
# читает ПК) и отдельным секретом для Android-сборки (-Pmsqd.buildSecret). Берём из server.json, чтобы
# не держать в двух местах. Пусто (поля нет) — токена и шифрования не будет (переходный период).
# Тот же hex надо прописать на сервере в backend.env APP_BUILD_SECRET при закрытии /content.
BUILD_SECRET=$(python3 -c "import json;print(json.load(open('server.json')).get('app_build_secret',''))" 2>/dev/null || true)
printf '%s' "$BUILD_SECRET" | "$GH" secret set MAGICSQD_BUILD_SECRET
[ -n "$BUILD_SECRET" ] && echo "app_build_secret из server.json → MAGICSQD_BUILD_SECRET установлен" \
                       || echo "ВНИМАНИЕ: в server.json нет app_build_secret — каталог останется открытым (токена/шифрования не будет)"

base64 -w0 android/keystore/magicsqd-release.jks | "$GH" secret set ANDROID_KEYSTORE_BASE64
get_prop storePassword | "$GH" secret set ANDROID_KEYSTORE_PASSWORD
get_prop keyAlias      | "$GH" secret set ANDROID_KEY_ALIAS
get_prop keyPassword   | "$GH" secret set ANDROID_KEY_PASSWORD

echo "Готово. Проверить: $GH secret list"
