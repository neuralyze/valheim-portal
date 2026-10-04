"""One module per defect class.  A module that calls `register()` is in the
gate; nothing has to be edited anywhere else to add one.

`register()` REFUSES a section with no selftest case it is required to fail,
and refuses one that does not name its population.  Those two refusals are
the whole reason this package is sectioned rather than appended to.
"""
