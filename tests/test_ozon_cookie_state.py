from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_node(source: str, *args: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", source, *args],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_normalize_ozon_cookies_collapses_domain_aliases() -> None:
    result = _run_node(
        """
const { normalizeOzonCookies } = require('./scripts/lib/ozon_cookie_state');
const result = normalizeOzonCookies([
  { name: 'token', value: 'canonical', domain: '.ozon.ru', path: '/' },
  { name: 'token', value: 'root', domain: 'ozon.ru', path: '/' },
  { name: 'token', value: 'seller', domain: 'seller.ozon.ru', path: '/' },
  { name: 'token', value: 'seller-dot', domain: '.seller.ozon.ru', path: '/' },
  { name: 'token', value: 'service-old', domain: '.xapi.ozon.ru', path: '/' },
  { name: 'token', value: 'service-new', domain: '.xapi.ozon.ru', path: '/' },
  { name: 'abt_data', value: 'abt', domain: '.ozone.ru', path: '/' },
]);
console.log(JSON.stringify({
  cookies: result.cookies,
  stats: result.stats,
}));
"""
    )

    assert result["stats"] == {
        "inputCount": 7,
        "outputCount": 3,
        "removedCount": 4,
        "exactDuplicates": 1,
        "aliasDuplicates": 3,
        "normalized": True,
        "valuesPrinted": False,
    }
    cookies = {(row["name"], row["domain"]): row["value"] for row in result["cookies"]}
    assert cookies[("token", ".ozon.ru")] == "canonical"
    assert cookies[("token", ".xapi.ozon.ru")] == "service-new"
    assert cookies[("abt_data", ".ozone.ru")] == "abt"


def test_read_normalized_storage_state_repairs_file_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "token", "value": "a", "domain": ".ozon.ru", "path": "/"},
                    {"name": "token", "value": "b", "domain": "ozon.ru", "path": "/"},
                    {"name": "token", "value": "c", "domain": "seller.ozon.ru", "path": "/"},
                    {"name": "token", "value": "d", "domain": ".seller.ozon.ru", "path": "/"},
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    result = _run_node(
        """
const fs = require('fs');
const { readNormalizedStorageState } = require('./scripts/lib/ozon_cookie_state');
const target = process.argv[1];
const result = readNormalizedStorageState(target, { repair: true, backup: false });
const saved = JSON.parse(fs.readFileSync(target, 'utf8'));
console.log(JSON.stringify({
  stats: result.stats,
  savedCookieCount: saved.cookies.length,
}));
""",
        str(state_path),
    )

    assert result["stats"]["inputCount"] == 4
    assert result["stats"]["outputCount"] == 1
    assert result["stats"]["removedCount"] == 3
    assert result["savedCookieCount"] == 1
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
