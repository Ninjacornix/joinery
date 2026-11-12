extends RefCounted
## The deck: which symbols exist, how a seed shuffles them into a layout, and
## how moves turn into stars. Pure functions, so tests and saves can rebuild
## any deal from its seed.
##
## Symbols are plain text glyphs that every font has. Each one also has its
## own ink colour, but cards never differ by colour alone.

const SYMBOLS: Array[String] = ["@", "#", "&", "%", "$", "?", "!", "+", "=", "*", "~", "^"]
const INKS: Array[Color] = [
	Color("#b3261e"),
	Color("#1f5fa8"),
	Color("#2a7a4b"),
	Color("#7b3fa0"),
	Color("#8f5a00"),
	Color("#136f78"),
	Color("#b0175a"),
	Color("#4f5f12"),
	Color("#2f4256"),
	Color("#b84a00"),
	Color("#5e4033"),
	Color("#2b3a9a"),
]
const MAX_STARS := 3


## How many different pairs one deck can hold.
static func max_pairs() -> int:
	return SYMBOLS.size()


## The deal for `seed_value`: one symbol index per slot, every index used
## exactly twice. The same seed always gives the same layout.
static func deal(seed_value: int, pair_count: int) -> PackedInt32Array:
	var pairs: int = clampi(pair_count, 1, max_pairs())
	var layout: PackedInt32Array = PackedInt32Array()
	for symbol: int in pairs:
		layout.append(symbol)
		layout.append(symbol)
	var rng: RandomNumberGenerator = RandomNumberGenerator.new()
	rng.seed = seed_value
	for i: int in range(layout.size() - 1, 0, -1):
		var j: int = rng.randi_range(0, i)
		var held: int = layout[i]
		layout[i] = layout[j]
		layout[j] = held
	return layout


## A fresh seed for an unseeded game, kept in the save so the deal can be
## rebuilt. It comes from the global generator, which the game core seeds for
## the session, so a recorded play deals the same cards when replayed.
static func random_seed() -> int:
	return randi() & 0x7fffffff


static func symbol(index: int) -> String:
	if index < 0 or index >= SYMBOLS.size():
		return ""
	return SYMBOLS[index]


static func ink(index: int) -> Color:
	if index < 0 or index >= INKS.size():
		return Color.BLACK
	return INKS[index]


## Stars for clearing `pairs` pairs in `moves` moves: three for close to
## perfect memory, two for a steady game, one for finishing at all.
static func stars_for(moves: int, pairs: int) -> int:
	if moves <= pairs + ceili(pairs / 2.0):
		return MAX_STARS
	if moves <= pairs * 2 + 2:
		return 2
	return 1


## Points for a match that is the `streak`-th in a row (1 = first).
static func points_for(streak: int, per_pair: int, streak_bonus: int) -> int:
	return per_pair + streak_bonus * maxi(0, streak - 1)
