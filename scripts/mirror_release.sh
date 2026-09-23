#!/bin/bash
# Зеркало релиза на своём сервере (magicsqd.ru/download/) — для тех, у кого GitHub недоступен.
# Берёт ассеты из GitHub-релиза vX.Y.Z, сверяет sha256 с тем, что показывает GitHub, кладёт на сервер под
# СТАБИЛЬНЫМИ именами (их ждут сайт и проверка обновлений в программах — см. update_api.py, mobile_bridge.py,
# website app/platform-download.tsx) и пишет download/version.json с картой assets и sha256.
#
#   scripts/mirror_release.sh              # версия из app/version.py
#   scripts/mirror_release.sh v1.0.17      # явно
#
# Нужны: gh (авторизован), ssh-ключ ~/.ssh/magicsqd_deploy. Intel-DMG в релизе может ещё не быть
# (собирается локально после CI, scripts/build_intel_dmg.sh --upload) — тогда он просто не попадёт в
# assets, и macOS-Intel продолжит проверять обновления через GitHub; перезапусти скрипт после загрузки.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=root@94.102.89.93
KEY=~/.ssh/magicsqd_deploy
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20"
DEST=/opt/magicsqd/site/download

TAG=${1:-v$(python3 -c "import re; print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"', open('app/version.py').read()).group(1))")}
VER=${TAG#v}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "== $TAG: скачиваю ассеты релиза"
# Ассеты — по id релиза, а не по тегу: у только что опубликованного v1.0.32 GitHub больше получаса отдавал
# по тегу (gh release view/download) и в списке /releases пустой assets, хотя по id и по прямым ссылкам
# все файлы уже были на месте.
REL_ID=$(gh api "repos/{owner}/{repo}/releases/tags/$TAG" --jq .id)
gh api "repos/{owner}/{repo}/releases/$REL_ID" \
  --jq '.assets[]|select(.name|startswith("MagicSQD_"))|.name+" "+.digest+" "+.browser_download_url' \
  | sed 's/ sha256:/ /' > "$WORK/digests.txt"
while read -r name _ url; do curl -fsSL -o "$WORK/$name" "$url"; done < "$WORK/digests.txt"

# ключ в version.json → шаблон имени в релизе → стабильное имя на зеркале
declare -a MAP=(
  "windows|MagicSQD_Setup_${VER}.exe|MagicSQD_Setup.exe"
  "win7|MagicSQD_Setup_Win7_${VER}.exe|MagicSQD_Setup_Win7.exe"
  "android|MagicSQD_Android_${VER}.apk|MagicSQD_Android.apk"
  "macos_arm64|MagicSQD_${VER}_arm64.dmg|MagicSQD_arm64.dmg"
  "macos_x86_64|MagicSQD_${VER}_x86_64.dmg|MagicSQD_x86_64.dmg"
)
ASSETS_JSON="" ; SHA_JSON="" ; UPLOAD=()
for entry in "${MAP[@]}"; do
  IFS='|' read -r key src dst <<< "$entry"
  if [ ! -f "$WORK/$src" ]; then echo "   $key: $src в релизе нет — пропускаю"; continue; fi
  expected=$(awk -v n="$src" '$1==n{print $2}' "$WORK/digests.txt")
  actual=$(shasum -a 256 "$WORK/$src" | cut -d' ' -f1)
  [ "$expected" = "$actual" ] || { echo "   $key: sha256 не совпал с GitHub ($src)"; exit 1; }
  mv "$WORK/$src" "$WORK/$dst"
  ASSETS_JSON+="\"$key\": \"$dst\", "
  SHA_JSON+="\"$dst\": \"$actual\", "
  UPLOAD+=("$dst")
  echo "   $key: $src → $dst ($(du -h "$WORK/$dst" | cut -f1)) sha256 ok"
done
[ ${#UPLOAD[@]} -gt 0 ] || { echo "нечего зеркалить"; exit 1; }

CHANGELOG=$(gh api "repos/{owner}/{repo}/releases/$REL_ID" --jq '.body')
python3 - "$WORK/version.json" "$TAG" "$CHANGELOG" "{${ASSETS_JSON%, }}" "{${SHA_JSON%, }}" <<'PY'
import json, sys
path, tag, changelog, assets, sha = sys.argv[1:]
json.dump({"version": tag, "changelog": changelog.strip(), "assets": json.loads(assets), "sha256": json.loads(sha)},
          open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

echo "== загружаю на сервер ($(du -ch "${UPLOAD[@]/#/$WORK/}" | tail -1 | cut -f1))"
REMOTE_TMP=$($SSH $HOST "mktemp -d")
scp -i "$KEY" -q "${UPLOAD[@]/#/$WORK/}" "$WORK/version.json" "$HOST:$REMOTE_TMP/"
$SSH $HOST "set -e
  B=/opt/magicsqd/backups/download-before-$TAG; mkdir -p \$B; cp -p $DEST/version.json \$B/ 2>/dev/null || true
  for f in ${UPLOAD[*]} version.json; do install -m 644 -o magicsqd -g magicsqd $REMOTE_TMP/\$f $DEST/\$f; done
  rm -rf $REMOTE_TMP; ls -la $DEST | awk '{print \$5, \$9}' | tail -n +4"

echo "== проверка по HTTPS"
for f in "${UPLOAD[@]}"; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -I "https://magicsqd.ru/download/$f"); echo "   $f → HTTP $code"; [ "$code" = 200 ] || exit 1
done
curl -s https://magicsqd.ru/download/version.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('   version.json:', d['version'], '| assets:', ', '.join(d['assets']))"
