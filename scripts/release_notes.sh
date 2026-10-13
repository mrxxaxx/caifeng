#!/usr/bin/env bash
# Release 页面的说明：下载说明，加上和上一个版本之间的提交，按 Conventional Commits 前缀分组
# 用法：scripts/release_notes.sh v0.2.0 [owner/repo]
set -euo pipefail
tag=$1
repo=${2:-}
prev=$(git describe --tags --abbrev=0 "$tag^" 2>/dev/null || true)
range=${prev:+$prev..}$tag

section() {  # 标题 和 匹配提交标题的正则（-v 开头表示取不匹配的）
  local title=$1 lines
  if [ "$2" = "-v" ]; then
    lines=$(git log --format='%s' "$range" | grep -vE "$3" || true)
  else
    lines=$(git log --format='%s' "$range" | grep -E "$2" || true)
  fi
  [ -z "$lines" ] && return
  printf '\n**%s**\n\n' "$title"
  sed -E 's/^[a-z]+(\([^)]*\))?!?: */- /' <<<"$lines"
}

cat <<NOTES
**下载**

- \`caifeng-$tag-setup.exe\`：安装版，装好后从开始菜单打开，可以在系统设置里卸载。
- \`caifeng-$tag-portable.exe\`：便携版，不用安装，双击就能用。

两个版本功能一样，任选一个。
NOTES
section 新功能 '^feat(\(|!|:)'
section 修复 '^fix(\(|!|:)'
section 其他 -v '^(feat|fix)(\(|!|:)'
if [ -n "$prev" ] && [ -n "$repo" ]; then
  printf '\n完整改动：https://github.com/%s/compare/%s...%s\n' "$repo" "$prev" "$tag"
fi
