#!/usr/bin/env bash
# EPaperService setup — installs venv, Python deps, and Waveshare epd_3in6e driver.
set -e
cd "$(dirname "$0")"

echo "==> Creating venv"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

echo "==> Installing Waveshare e-Paper library"
TMP=$(mktemp -d)
git clone --depth=1 https://github.com/waveshareteam/e-Paper "$TMP/e-Paper" -q

# Main lib (common drivers)
cd "$TMP/e-Paper/RaspberryPi_JetsonNano/python/lib"
"$(dirname "$0")/.venv/bin/pip" install . -q

# epd_3in6e lives in the Separate Programs section — copy it into the package
EPD_SRC="$TMP/e-Paper/E-paper_Separate_Program/3.6inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib"
SITE_PKG=$(dirname "$0")/.venv/lib/python3*/site-packages/waveshare_epd/
if [ -d "$EPD_SRC" ]; then
    cp "$EPD_SRC"/epd_3in6e.py $SITE_PKG
    echo "==> epd_3in6e.py installed"
else
    echo "WARN: epd_3in6e.py not found at expected path — check e-Paper repo structure"
fi

rm -rf "$TMP"
echo "==> Done. Start with: .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8004"
