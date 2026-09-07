#!/usr/bin/env python3
"""One-time repair: re-derive money_raw and team_size for every accepted
row using the now-fixed clean_page_text() pipeline (see lib/enrich.py).

Before this fix, process_item ran the money/team-size regexes against raw
HTML, including inline <script> blocks. On Next.js sites those scripts
carry a React Server Components JSON stream like ["$","$1","c",...], which
`\$\d+` and `\d+-person` happily matched -- producing garbage like
money_raw="$1" on dozens of hackathon pages and team_size="70104-person".

Run once, from the repo root: `python scripts/refix_money_team_size.py`
"""
import sqlite3
import sys
import time

from adapters._http import get as http_get
from lib.enrich import clean_page_text, extract_money, extract_team_size

ROOT_RULES = "rules.yaml"


def main():
    import yaml

    with open(ROOT_RULES) as f:
        rules = yaml.safe_load(f)

    con = sqlite3.connect("state.db")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, url, snippet, money_raw, team_size FROM items WHERE status='accepted'"
    ).fetchall()

    changed_money = changed_team = failed = 0
    for r in rows:
        try:
            page_text = clean_page_text(http_get(r["url"]).text)
        except Exception as e:
            print(f"  fetch failed for {r['url']}: {type(e).__name__}: {e}", file=sys.stderr)
            failed += 1
            continue
        combined = f"{r['title']} {r['snippet'] or ''} {page_text}"
        new_money = extract_money(combined, rules)
        new_team = extract_team_size(combined, rules)

        updates, params = [], []
        if new_money != r["money_raw"]:
            updates.append("money_raw = ?")
            params.append(new_money)
            changed_money += 1
            print(f"  money: {r['title']!r} {r['money_raw']!r} -> {new_money!r}")
        if new_team != r["team_size"]:
            updates.append("team_size = ?")
            params.append(new_team)
            changed_team += 1
            print(f"  team_size: {r['title']!r} {r['team_size']!r} -> {new_team!r}")

        if updates:
            params.append(r["id"])
            con.execute(f"UPDATE items SET {', '.join(updates)} WHERE id = ?", params)
            con.commit()
        time.sleep(0.2)

    print(
        f"\nDone. {changed_money} money_raw values corrected, "
        f"{changed_team} team_size values corrected, {failed} fetches failed "
        f"(of {len(rows)} accepted rows)."
    )


if __name__ == "__main__":
    main()
