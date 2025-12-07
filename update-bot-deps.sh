#!/bin/bash
set -e

[ ! -f "pyproject.bot.toml" ] && echo "pyproject.bot.toml not found" && exit 1

cp pyproject.bot.toml pyproject.toml
uv lock --upgrade
rm pyproject.toml

echo "uv.lock updated"
