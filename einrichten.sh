#!/usr/bin/env bash
# Richtet das Kombi-Projekt (TradingAgents + day-trader-skill) in einem frischen Container
# oder auf deinem eigenen PC ein. Mehrfach ausfuehrbar, ueberschreibt keine vorhandene .env.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q .
if [ ! -f .env ]; then
  cp .env.projekt.example .env
  echo ".env aus .env.projekt.example angelegt."
fi
# SEC-Absender: aus der Umgebungsvariable uebernehmen, falls in .env noch der Platzhalter steht
if [ -n "${SEC_USER_AGENT:-}" ] && grep -q '^SEC_USER_AGENT=\${SEC_USER_AGENT}' .env; then
  sed -i "s|^SEC_EDGAR_USER_AGENT=.*|SEC_EDGAR_USER_AGENT=${SEC_USER_AGENT}|; s|^SEC_USER_AGENT=.*|SEC_USER_AGENT=${SEC_USER_AGENT}|" .env
fi
mkdir -p daten/ergebnisse daten/gedaechtnis daten/cache trader
[ -n "${ANTHROPIC_API_KEY:-}" ] && echo "ANTHROPIC_API_KEY ist gesetzt." || echo "WARNUNG: ANTHROPIC_API_KEY fehlt (siehe KURZANLEITUNG.md)."
echo "Fertig."
