#!/bin/sh
# Installs a pre-commit hook that refuses to commit plan/scan/CAD/BIM files.
set -e
root="$(git rev-parse --show-toplevel)"
hook="$root/.git/hooks/pre-commit"
cat > "$hook" <<'HOOK'
#!/bin/sh
exec python3 "$(git rev-parse --show-toplevel)/scripts/check_no_private_data.py" --staged
HOOK
chmod +x "$hook"
echo "pre-commit hook installed: $hook"
