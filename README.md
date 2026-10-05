# monstarz-ranking

## Collection recovery

The collector accepts both the legacy `show_nick_dropdown($(this), ...)`
and current `YG_COMMON.show_nick_dropdown(this, ...)` markup. Unexpected
author/date markup and failed HTTP/voter requests fail the run without
advancing past the failed post.

To rebuild incomplete August 2026 and later totals, dispatch the
`collect-monstarz` GitHub Actions workflow with `mode=recover`.
Recovery clears only those damaged monthly states, preserves published partial totals with a recovery-waiting label,
then scans newest to oldest. Earlier months remain intact. It saves
checkpoints in `data/recovery.json` and `data/state_*.json`; scheduled
runs resume recovery automatically. Successful recovery chunks immediately dispatch the next chunk until recovery finishes, then scheduled runs return to collecting new posts.
Published months show partial totals while collection is in progress.
Recovery may require multiple five-hour runs. Deleted posts and unavailable
historical interactions cannot be reconstructed.

Local equivalent:

```sh
python scraper/scrape.py recover --from-month 2026-08 --delay 0.5 --max-minutes 300
python scraper/build_stats.py
```

Tests:

```sh
python -m unittest discover -s scraper -p test_scrape.py
```
