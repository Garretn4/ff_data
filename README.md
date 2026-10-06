# FF Analyst

A fantasy football league dashboard and player analysis project. It brings league data, projections, and market information together to help compare teams and make roster decisions.

## What it shows

- League standings with records, points for, and points against
- Model and consensus rest-of-season projections
- Team power rankings
- Views for matchups, players, trades, and more

The model is being developed with a focus on testing its predictions against past seasons before treating its rankings as reliable.

## Project status

This is an active personal project. The interface and model may change as the data pipeline and backtests improve. Rankings and projections are estimates, not guarantees.

## Development

The project uses Python. The repository includes the application in `app/`, tests in `tests/`, and design notes in `docs/`.

To run the tests from the repository root:

```bash
uv sync
uv run pytest -q
```

## The App

Settings will allow you to edit your model to preference.

**Settings**

Once the Tuning work ships, everything will be editable here except what comes from Sleeper (lineup slots, scoring rules, team count).

### Algorithms

PPG = ( k × prior + n × data ) ÷ ( k + n )

n is games played this season. k is a dial (default 4) that says how stubborn the guess is. The prior is the starting guess before any games are played: expert consensus rank converted to points, blended with last season's points, then multiplied by an age adjustment.

This is a weighted average that shifts over the season. Week 0 you trust the guess based off prior data and by week 12 you are weighting heavier off the current season statistics.

### Age, matchup and availability tweaks

**Age multiplier** For each position and age, the app looks at history: how did players that age score next year compared to this year? It takes the middle value (the median) of those ratios. A 31-year-old running back might come out at 0.92, so his prior is cut 8%. The value is capped between 0.7 and 1.1. This is a lookup table, not a score, and it only touches the prior.

### Opponent factor

How many points has this week's defense allowed to the player's position, divided by the league average? Above 1 means a soft matchup. Early in the year the ratio is pulled toward 1.0 (the same shrinking idea as the PPG blend above, with 6 games as the weight) and capped to 0.9–1.1. Weekly projection = ppg × factor.

### Availability

Injury and roster status each map to a fixed multiplier. For this week, Out = 0, Doubtful = 0.25, Questionable = 0.85. For the rest of the season the penalties are much milder (Out = 0.85). Bye weeks and IR are zero.

### Value over replacement

A player scoring 12 points a week is great if the free-agent pool offers 6, and useless if it offers 12. So the app first finds the replacement level at each position: the score of the best player nobody would be forced to start, based on your league's starting slots and team count.

intrinsic value = projected rest-of-season points − replacement points

AKA how many points does this person give you against the next guy available for free in **your league**

### Picking your best lineup

Many other features depend on one question: given these players and these slots (QB, 2 RB, 2 WR, FLEX…), what is the best possible lineup?

- Fixed slots (QB, RB, WR…) are easy: take the top scorers at that position.
- FLEX is tricky, because a RB, WR or TE can fill it. The app tries combinations with a technique called dynamic programming. It remembers the best result for each partial choice so it never redoes the same work. The answer is the true best lineup, not an approximation.
- Before that, it trims the bench candidates to the few who could possibly fill a flex slot, to keep it fast.

Team strength = the best-lineup points added up over every remaining week.

### Win Probability

If you project 110 points and your opponent projects 100, you are the favorite, but not a lock. The app turns the projected gap into a chance of winning:

```
P(win) = Φ( gap ÷ (σ × √2) )
```

- **gap** is your projected score minus your opponent's.
- **σ** (sigma) is how far a team's real score usually lands from its projection. A bigger σ means more weekly luck, so the same gap gives a less confident answer.
- **√2** is there because two teams are involved. Each has its own error, and the two combine into one error for the gap.
- **Φ** (phi) is the normal bell curve's cumulative chance. It converts the scaled gap into a probability from 0 to 1. A gap of zero gives 50%.

As a game plays out, σ shrinks with the share of starters still to play, so a finished game reads as decided.

**What σ should be.** The code currently uses σ = 20. Measured against 2022–2026 projections, the typical team-score miss was about 26 points, and 20 turned out overconfident: when it said 74%, the side won about 61% of the time. Settings will let you change σ, and the better value is still being tuned.

### Backtesting and tuning

This app replays season week by week, projecting only what was known at the time, and compares to what happened.

It scores itself with a Spearman correlation - Players are ranked by projected points and again by actual points, the rank difference is computed for each player, and those differences feed into **ρ = 1 − 6Σd² / n(n²−1)**

- n - number of players it's ranking
- d - for one player, (projected rank - actual rank)
- d² — square it so negatives can't cancel and bigger misses are punished more (a 4-rank miss costs 16, a 1-rank miss costs 1).
- Σd² — the sum of every player's squared miss
- n(n²−1) — scales the sum. The worst case (a fully reversed ranking) is n(n²−1)/3
- 6 scales the result so it runs from −1 to +1, which is two units wide. The worst-case sum has a ÷3 in it, and 2 × 3 = 6. That is all it is.

## Dedication

I dedicate this to those who just can't seem to beat that one person before playoffs year after year <3

And for the guys who say they will actually try this year until week 4.
