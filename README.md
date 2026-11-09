# sports-desk-data

Daily ranking lists for the Sports Desk hub, downloaded by GitHub Actions
every morning (about 3:40 AM ET) so the hub's 4:57 AM cloud pull can copy
them in without going through the laptop.

| File | Source |
|---|---|
| `data/raw_fc_dynasty.json` | FantasyCalc dynasty values (1QB, 10 teams, PPR) |
| `data/raw_fc_redraft.json` | FantasyCalc redraft values (1QB, 12 teams, PPR) |
| `data/raw_ktc.json` | KeepTradeCut dynasty rankings (1QB) plus draft picks |
| `data/raw_sleeper_season.json` | Sleeper season stats (QB/RB/WR/TE) |
| `data/raw_espn_ros.json` | ESPN rest-of-season projections |
| `data/raw_fp_flex.json` | FantasyPros rest-of-season PPR flex consensus |
| `data/status.json` | What the last run saved or failed |

Each file is already in the shape of the hub's `nfl/raw_<name>` document.
A source that fails keeps yesterday's file, and the failure is listed in
`data/status.json`.

To run it by hand: Actions tab → "Daily ranking lists" → Run workflow.
