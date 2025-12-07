#!/bin/bash
set -e

[ ! -f "pyproject.bot.toml" ] && echo "pyproject.bot.toml not found" && exit 1

[ -f "pyproject.toml" ] && mv pyproject.toml pyproject.toml.backup

cp pyproject.bot.toml pyproject.toml
uv lock --upgrade
mv uv.lock uv.bot.lock
rm pyproject.toml

[ -f "pyproject.toml.backup" ] && mv pyproject.toml.backup pyproject.toml

echo "uv.bot.lock updated"
