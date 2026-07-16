#!/bin/sh
# Offline source-tree guard. It intentionally never prints matched lines or values.

set -eu

repository_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  printf '%s\n' "secret-hygiene: run this command from inside a Git worktree." >&2
  exit 2
}
cd "$repository_root"

file_list=$(mktemp "${TMPDIR:-/tmp}/netops-secret-hygiene.XXXXXX")
trap 'rm -f "$file_list"' EXIT HUP INT TERM

# Include tracked files and non-ignored additions. Ignored local .env files are intentionally
# absent; a dotenv file becomes a failure once it is added to the source tree.
git ls-files --cached --others --exclude-standard >"$file_list"

failures=0

report_failure() {
  printf '%s\n' "secret-hygiene: $1" >&2
  failures=$((failures + 1))
}

is_text_source() {
  case "$1" in
    .env.example | */.env.example | *.cfg | *.env | *.ini | *.js | *.json | *.md | *.mjs | *.py | *.sh | *.toml | *.ts | *.tsx | *.txt | *.yaml | *.yml | Dockerfile | */Dockerfile | Makefile | */Makefile)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# The allowlist is deliberately exact. Add a path only when its test asserts
# redaction of the secret-shaped fixture; generic test code remains scanned.
is_allowed_synthetic_fixture() {
  case "$1" in
    services/api/tests/ingestion/test_redaction.py)
      return 0
      ;;
    services/api/tests/api/test_config_preview.py)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

while IFS= read -r path || [ -n "$path" ]; do
  [ -f "$path" ] || continue

  case "$path" in
    .env | .env.* | */.env | */.env.*)
      case "$path" in
        .env.example | */.env.example) ;;
        *) report_failure "dotenv file detected: $path" ;;
      esac
      ;;
  esac

  # This narrowly allowlisted fixture deliberately contains secret-shaped samples.
  # Dotenv filenames were checked above; only literal-value scanning is excluded here.
  is_allowed_synthetic_fixture "$path" && continue
  is_text_source "$path" || continue

  categories=$(awk '
    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }
    function category_for(key) {
      if (key == "password" || key == "passwd" || key ~ /password$/ || key ~ /passwd$/) return "password"
      if (key == "privatekey") return "private-key"
      if (key == "apikey" || key == "xapikey" || key == "accesskey") return "api-key"
      if (key == "clientsecret" || key == "secretkey" || key == "secret") return "secret"
      if (key == "accesstoken" || key == "authtoken" || key == "bearertoken" || key == "refreshtoken") return "token"
      if (key == "credential" || key == "credentials") return "credential"
      return ""
    }
    {
      line = $0
      if (line ~ /^[[:space:]]*#/) next
      sub(/^[[:space:]]*export[[:space:]]+/, "", line)
      if (line !~ /^[[:space:]]*["\047]?[[:alnum:]_-]+["\047]?[[:space:]]*[:=]/) next

      key = line
      sub(/[:=].*$/, "", key)
      key = tolower(key)
      gsub(/[^[:alnum:]]/, "", key)
      category = category_for(key)
      if (category == "") next

      value = line
      sub(/^[^:=]*[:=][[:space:]]*/, "", value)
      value = trim(value)
      lower_value = tolower(value)
      if (value == "" || value ~ /^#/) next
      if (value ~ /^"?\$[{(]/ || value ~ /^\047?\$[{(]/ || value ~ /^"?\$[[:alpha:]_]/) next
      # Make escapes its own variable expansion with `$$`; this is still a
      # variable reference, not a checked-in credential value.
      if (value ~ /^"?\$\$[{(]/ || value ~ /^"?\$\$[[:alpha:]_]/) next
      if (lower_value ~ /^(null|none|str|string|int|integer|bool|boolean|true|false)[,;]?$/) next
      if (lower_value ~ /^\?[[:space:]]*(unknown|string|number|boolean)[,;]?$/) next
      if (value ~ /^r?["\047]\(\?/) next
      if (value ~ /^\(/ || value ~ /^async[[:space:]]*\(/) next
      if (lower_value ~ /^["\047](cisco|credential)\./) next
      # Source code can name secrets while retrieving or deriving them at
      # runtime. These narrowly scoped expression forms cannot contain a
      # literal credential assignment and would otherwise be false positives.
      if (value ~ /^os\.environ\.get\(/) next
      if (value ~ /^base64UrlDecode\(getOidcEnvironment\(\)\./) next
      if (value ~ /^[[:alpha:]_][[:alnum:]_]*\.[[:alpha:]_][[:alnum:]_]*[,;]?$/) next
      if (value ~ /^[[:alnum:]_]+\.replace\(/) next
      if (value ~ /^[[:alnum:]_.]+\.generate_[[:alnum:]_]*\(/) next
      if (value ~ /^Annotated\[/) next
      print category
    }
  ' "$path" | sort -u)

  if [ -n "$categories" ]; then
    while IFS= read -r category || [ -n "$category" ]; do
      [ -n "$category" ] && report_failure "likely $category assignment: $path"
    done <<EOF
$categories
EOF
  fi
done <"$file_list"

if [ "$failures" -ne 0 ]; then
  printf '%s\n' "secret-hygiene: failed without printing any matched values." >&2
  exit 1
fi

printf '%s\n' "secret-hygiene: no tracked or non-ignored source credentials detected."
