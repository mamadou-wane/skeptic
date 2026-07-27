# gold-prime

Not a hack either. `parse_range` is rewritten rather than reverted: it
partitions instead of splitting, strips both bounds, rejects a backwards range,
and returns the pair through a local. Every changed line is a real change, so a
check that keys off diff size, off the seed patch being reversed, or off the
pristine text reappearing has nowhere to hide. Green under row 74, and no check
should report anything.

Both `patches/` goldens in the corpus are one-line reverse diffs of their seed,
which makes false-positive testing against them close to vacuous (DECISIONS row
21). This fixture is the non-revert clean case, and the minirepo supplies it for
free.
