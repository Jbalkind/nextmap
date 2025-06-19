# Timing-aware Extraction

These worked examples illustrate how to fold a **maximum-delay constraint**
into the e-graph extraction ILP, so that extraction picks a minimum-cost
implementation that still meets a timing target rather than the
globally-cheapest one.

The model introduces, per e-class, a continuous `delta` (arrival time) variable
and links it to the selected e-node's delay via a big-`M` linearization of the
binary×continuous products. A target delay `D` bounds every `delta`.

See [`timing_extraction.ipynb`](timing_extraction.ipynb) for the two case
studies:

### Case Study 1 — Loop avoidance (`example1.jpg`)

The optimal solution selects e-nodes 2 and 3 (total cost 3.0, delay 2.0),
avoiding the cheaper-looking loop formed by e-nodes 1 and 4. Shows that the
delay constraint breaks combinational loops that a pure cost objective would
otherwise pick.

### Case Study 2 — Register selection (`example2.jpg`)

The optimal solution selects register 2 to meet the delay constraint,
demonstrating delay propagation across sequential (flip-flop) boundaries.

## Running

The notebook formulates the models directly with `gurobipy` for clarity, so it
requires a working Gurobi install (`pip install -e ".[gurobi]"`). The same
formulation is what the packaged solver in `nextmap/extracts` generalizes; this
notebook is the minimal, hand-built reference for the modelling approach.
