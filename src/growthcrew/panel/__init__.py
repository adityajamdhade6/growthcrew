"""A synthetic audience panel that pre-tests variants, and a record of whether it is right.

Personas are built from the brand's ideal customer and its voice-of-customer research; each
cites the evidence it rests on. Before a test launches, every persona reacts to every variant,
and the reactions are aggregated in code into a predicted ranking with its uncertainty.

The panel never replaces a real test. It can only recommend dropping a clearly weak variant
from a test of three or more, a person decides, and once real results come in its predicted
ranking is compared with the real one. If its record is poor, it stops recommending anything
and says so. See docs/panel.md for its known biases.
"""
