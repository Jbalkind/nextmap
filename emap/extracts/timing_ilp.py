"""
Timing-aware ILP extraction for nextmap technology mapper.
Implements the timing-oriented extraction algorithm described in the paper.
"""

from ..db import NetlistDB
from .utils import db_to_normalized, db_to_normalized_tech, normalized_to_json
from .ilp import prune_cells, group_wires, add_wire_constrs
from .solver_interface import create_solver, quicksum, GRB
from typing import Any, Callable, Dict, Optional
import numpy as np


def get_bin_con_prod(model, bin_var, con_var, name: str, M: float = 1000.0):
    """
    Add the product of a binary variable and a continuous variable to the model.
    Returns the product variable using the McCormick relaxation technique.

    Args:
        model: Solver model instance
        bin_var: Binary variable
        con_var: Continuous variable
        name: Name prefix for the product variable
        M: Big M constant for relaxation

    Returns:
        Product variable representing bin_var * con_var
    """
    prod_var = model.addVar(lb=0, vtype=GRB.CONTINUOUS, name=f"{name}_prod")
    model.addConstr(prod_var <= M * bin_var, name=f"{name}_c1")
    model.addConstr(prod_var <= con_var, name=f"{name}_c2")

    # For constraint: prod_var >= con_var - M * (1 - bin_var)
    # Create a simpler constraint structure that the solver can handle
    # prod_var + M * bin_var >= con_var + M * bin_var - M
    # prod_var >= con_var + M * bin_var - M - M * bin_var
    # prod_var >= con_var - M

    # Create auxiliary variable for the RHS expression: con_var - M + M * bin_var
    rhs_expr = quicksum([con_var, -M, M * bin_var], solver=model)
    model.addConstr(prod_var >= rhs_expr, name=f"{name}_c3")
    return prod_var


def get_default_delay_model() -> Dict[str, float]:
    """
    Return default delay values for different cell types.
    These values can be customized based on the target technology.
    """
    return {
        # Basic logic gates
        "$and": 0.1,
        "$or": 0.1,
        "$xor": 0.15,
        "$not": 0.05,
        "$buf": 0.05,

        # Arithmetic operations
        "$add": 1.0,
        "$sub": 1.0,
        "$mul": 3.0,
        "$div": 5.0,
        "$mod": 5.0,

        # Comparison operations
        "$eq": 0.5,
        "$ne": 0.5,
        "$lt": 0.8,
        "$le": 0.8,
        "$gt": 0.8,
        "$ge": 0.8,

        # Multiplexers and selectors
        "$mux": 0.3,
        "$pmux": 0.5,

        # Shift operations
        "$shl": 0.2,
        "$shr": 0.2,
        "$sshl": 0.2,
        "$sshr": 0.2,

        # Reduction operations
        "$reduce_and": 0.3,
        "$reduce_or": 0.3,
        "$reduce_xor": 0.4,

        # Default for unknown cells
        "default": 1.0
    }


def extract_timing_aware(db: NetlistDB,
                        cost_model: Callable,
                        target_delay: float,
                        delay_model: Optional[Dict[str, float]] = None,
                        big_M: float = 1000.0,
                        solver_type: str = "auto",
                        **solver_args) -> dict:
    """
    Timing-aware extraction using ILP with delay constraints.

    Args:
        db: NetlistDB containing the e-graph
        cost_model: Function to compute cell costs
        target_delay: Maximum allowed delay (D parameter)
        delay_model: Dictionary mapping cell types to delays
        big_M: Big M constant for linearization
        solver_type: "gurobi", "cbc", or "auto" (default)
        **solver_args: Additional solver parameters

    Returns:
        Module in Yosys JSON format
    """
    if delay_model is None:
        delay_model = get_default_delay_model()

    inputs, outputs, cells, dffs = db_to_normalized(db, cost_model)

    # Add delay information to cells
    for cell in cells:
        cell_type = cell["type"]
        cell["delay"] = delay_model.get(cell_type, delay_model.get("default", 1.0))

    # Prune dominated cells
    prune_cells(cells)

    # Group wires
    all_source = {-1, 0, 1} | {w for ws in inputs.values() for w in ws}
    all_sink = {w for ws in outputs.values() for w in ws}
    bundles: list[set[int]] = [all_source, all_sink]

    for cell in cells:
        cell["all_inputs"] = {w for ws in cell["inputs"].values() for w in ws}
        cell["all_outputs"] = {w for ws in cell["outputs"].values() for w in ws}
        bundles.append(cell["all_inputs"])
        bundles.append(cell["all_outputs"])

    for dff in dffs:
        dff["all_inputs"] = {w for ws in dff["inputs"].values() for w in ws}
        dff["all_outputs"] = {w for ws in dff["outputs"].values() for w in ws}
        bundles.append(dff["all_inputs"])
        bundles.append(dff["all_outputs"])

    groups = group_wires(bundles)

    # Build timing-aware ILP model
    ilp_model = create_solver(solver_type, name="timing_extraction")
    for k, v in solver_args.items():
        ilp_model.setParam(k, v)

    # Variables
    x = ilp_model.addVars(len(groups), vtype=GRB.BINARY, name="x")  # wire group choices
    y = ilp_model.addVars(len(cells), vtype=GRB.BINARY, name="y")   # cell choices
    z = ilp_model.addVars(len(dffs), vtype=GRB.BINARY, name="z")    # dff choices

    # Delay variables for each wire group (eclass)
    delta = ilp_model.addVars(len(groups), vtype=GRB.CONTINUOUS, name="delta", lb=0)

    # Basic ILP constraints (same as original)
    ilp_model.addConstrs((x[group] >= 1 for group in all_sink), "output_constraints")
    add_wire_constrs(ilp_model, x, y, z, groups, cells, dffs, all_source)

    for i, cell in enumerate(cells):
        for gid in cell["all_inputs"]:
            ilp_model.addConstr(x[gid] >= y[i], f"cell_{i}_input_{gid}_constraint")

    for i, dff in enumerate(dffs):
        for gid in dff["all_inputs"]:
            ilp_model.addConstr(x[gid] >= z[i], f"dff_{i}_input_{gid}_constraint")

    # Timing constraints
    print(f"Adding timing constraints with target delay: {target_delay}")

    # FF delay constraints: if FF is used, its input delay must be <= target_delay
    for i, dff in enumerate(dffs):
        for gid in dff["all_inputs"]:
            ff_delay_prod = get_bin_con_prod(ilp_model, z[i], delta[gid], f"ff_{i}_delay_{gid}", big_M)
            ilp_model.addConstr(ff_delay_prod <= target_delay, f"ff_{i}_delay_{gid}_constraint")

    # Cell delay constraints: output delay >= input delay + cell delay (when cell is selected)
    for i, cell in enumerate(cells):
        cell_delay = cell["delay"]

        for out_gid in cell["all_outputs"]:
            for in_gid in cell["all_inputs"]:
                # When cell is selected: delta[out_gid] >= delta[in_gid] + cell_delay
                # This is modeled as: delta[out_gid] - delta[in_gid] >= y[i] * cell_delay

                # Create binary product y[i] * cell_delay using auxiliary variable
                delay_prod = get_bin_con_prod(ilp_model, y[i],
                                            ilp_model.addVar(lb=cell_delay, ub=cell_delay,
                                                           vtype=GRB.CONTINUOUS,
                                                           name=f"cell_{i}_delay_const"),
                                            f"cell_{i}_delay_{out_gid}_{in_gid}", big_M)

                ilp_model.addConstr(
                    delta[out_gid] >= delta[in_gid] + delay_prod,
                    f"cell_{i}_timing_{out_gid}_{in_gid}_constraint"
                )

    # Source delay constraints: input wires and constants have zero delay
    for gid in all_source:
        if gid < len(groups):  # Ensure gid is valid
            ilp_model.addConstr(delta[gid] == 0, f"source_{gid}_delay_constraint")

    # Output timing constraints: all outputs must meet timing target
    for gid in all_sink:
        if gid < len(groups):  # Ensure gid is valid
            ilp_model.addConstr(delta[gid] <= target_delay, f"output_{gid}_timing_constraint")

    # Objective: minimize cost (same as original)
    obj_expr = quicksum([y[i] * cells[i]["cost"] for i in range(len(cells))], solver=ilp_model)
    if dffs:
        obj_expr += quicksum([z[i] * dffs[i]["cost"] for i in range(len(dffs))], solver=ilp_model)
    ilp_model.setObjective(obj_expr, GRB.MINIMIZE)

    print(f"Optimizing timing-aware ILP with {len(cells)} cells, {len(dffs)} dffs, {len(groups)} wire groups")
    ilp_model.optimize()

    if ilp_model.status == GRB.INFEASIBLE:
        raise ValueError("Timing-aware ILP model is infeasible. Try relaxing timing constraints or increasing target_delay.")
    if ilp_model.status == GRB.UNBOUNDED:
        raise ValueError("Timing-aware ILP model is unbounded.")

    print(f"Timing-aware ILP solved with objective value: {ilp_model.objVal}")

    # Report timing information
    selected_delays = [delta[i].X for i in range(len(groups)) if x[i].X is not None and x[i].X > 0.5]
    if selected_delays:
        max_delay = max(selected_delays)
        print(f"Maximum delay in solution: {max_delay:.3f} (target: {target_delay})")
    else:
        print("No delays found in solution (variables may not have solution values)")

    # Extract solution
    cells_selected = [cell for i, cell in enumerate(cells) if y[i].X is not None and y[i].X > 0.5]
    dffs_selected = [dff for i, dff in enumerate(dffs) if z[i].X is not None and z[i].X > 0.5]

    return normalized_to_json(db, {}, inputs, outputs, cells_selected, dffs_selected)


def extract_timing_aware_with_techmap(db: NetlistDB,
                                     cost_model: Callable,
                                     target_delay: float,
                                     tech_rules: dict[str, dict[str, Any]],
                                     tech_limits: dict[str, int],
                                     delay_model: Optional[Dict[str, float]] = None,
                                     big_M: float = 1000.0,
                                     solver_type: str = "auto",
                                     **solver_args) -> dict:
    """
    Timing-aware extraction with technology mapping and resource limits.

    Args:
        db: NetlistDB containing the e-graph
        cost_model: Function to compute cell costs
        target_delay: Maximum allowed delay
        tech_rules: Technology mapping rules
        tech_limits: Resource limits for technology cells
        delay_model: Dictionary mapping cell types to delays
        big_M: Big M constant for linearization
        solver_type: "gurobi", "cbc", or "auto" (default)
        **solver_args: Additional solver parameters

    Returns:
        Module in Yosys JSON format
    """
    if delay_model is None:
        delay_model = get_default_delay_model()

    inputs, outputs, cells, dffs = db_to_normalized(db, cost_model)
    cells += db_to_normalized_tech(db, cost_model, tech_rules)

    # Add delay information to cells including technology cells
    for cell in cells:
        cell_type = cell["type"]
        if cell_type.startswith("$"):
            # Standard cell
            cell["delay"] = delay_model.get(cell_type, delay_model.get("default", 1.0))
        else:
            # Technology cell - get delay from tech_rules
            tech_delay = tech_rules.get(cell_type, {}).get("delay", delay_model.get("default", 1.0))
            cell["delay"] = tech_delay

    # Rest of the implementation follows extract_timing_aware but with tech limits
    prune_cells(cells)

    # Group wires
    all_source = {-1, 0, 1} | {w for ws in inputs.values() for w in ws}
    all_sink = {w for ws in outputs.values() for w in ws}
    bundles: list[set[int]] = [all_source, all_sink]

    for cell in cells:
        cell["all_inputs"] = {w for ws in cell["inputs"].values() for w in ws}
        cell["all_outputs"] = {w for ws in cell["outputs"].values() for w in ws}
        bundles.append(cell["all_inputs"])
        bundles.append(cell["all_outputs"])

    for dff in dffs:
        dff["all_inputs"] = {w for ws in dff["inputs"].values() for w in ws}
        dff["all_outputs"] = {w for ws in dff["outputs"].values() for w in ws}
        bundles.append(dff["all_inputs"])
        bundles.append(dff["all_outputs"])

    groups = group_wires(bundles)

    # Build timing-aware ILP model with tech constraints
    ilp_model = create_solver(solver_type, name="timing_extraction_techmap")
    for k, v in solver_args.items():
        ilp_model.setParam(k, v)

    # Variables
    x = ilp_model.addVars(len(groups), vtype=GRB.BINARY, name="x")
    y = ilp_model.addVars(len(cells), vtype=GRB.BINARY, name="y")
    z = ilp_model.addVars(len(dffs), vtype=GRB.BINARY, name="z")
    delta = ilp_model.addVars(len(groups), vtype=GRB.CONTINUOUS, name="delta", lb=0)

    # Basic constraints
    ilp_model.addConstrs((x[group] >= 1 for group in all_sink), "output_constraints")
    add_wire_constrs(ilp_model, x, y, z, groups, cells, dffs, all_source)

    for i, cell in enumerate(cells):
        for gid in cell["all_inputs"]:
            ilp_model.addConstr(x[gid] >= y[i], f"cell_{i}_input_{gid}_constraint")

    for i, dff in enumerate(dffs):
        for gid in dff["all_inputs"]:
            ilp_model.addConstr(x[gid] >= z[i], f"dff_{i}_input_{gid}_constraint")

    # Technology resource limits
    for tech_name, limit in tech_limits.items():
        cs: list[int] = [0] * len(cells)
        for i, cell in enumerate(cells):
            type_ = cell["type"]
            if not type_.startswith("$"):
                cs[i] = tech_rules[type_]["requirements"].get(tech_name, 0)
        limit_expr = quicksum([cs[i] * y[i] for i in range(len(cells)) if cs[i] > 0], solver=ilp_model)
        ilp_model.addConstr(limit_expr <= limit, f"tech_limit_{tech_name}")

    # Timing constraints (same as before)
    for i, dff in enumerate(dffs):
        for gid in dff["all_inputs"]:
            ff_delay_prod = get_bin_con_prod(ilp_model, z[i], delta[gid], f"ff_{i}_delay_{gid}", big_M)
            ilp_model.addConstr(ff_delay_prod <= target_delay, f"ff_{i}_delay_{gid}_constraint")

    for i, cell in enumerate(cells):
        cell_delay = cell["delay"]
        for out_gid in cell["all_outputs"]:
            for in_gid in cell["all_inputs"]:
                delay_prod = get_bin_con_prod(ilp_model, y[i],
                                            ilp_model.addVar(lb=cell_delay, ub=cell_delay,
                                                           vtype=GRB.CONTINUOUS,
                                                           name=f"cell_{i}_delay_const"),
                                            f"cell_{i}_delay_{out_gid}_{in_gid}", big_M)
                ilp_model.addConstr(
                    delta[out_gid] >= delta[in_gid] + delay_prod,
                    f"cell_{i}_timing_{out_gid}_{in_gid}_constraint"
                )

    for gid in all_source:
        if gid < len(groups):
            ilp_model.addConstr(delta[gid] == 0, f"source_{gid}_delay_constraint")

    # Output timing constraints: all outputs must meet timing target
    for gid in all_sink:
        if gid < len(groups):
            ilp_model.addConstr(delta[gid] <= target_delay, f"output_{gid}_timing_constraint")

    # Objective
    obj_expr = quicksum([y[i] * cells[i]["cost"] for i in range(len(cells))], solver=ilp_model)
    if dffs:
        obj_expr += quicksum([z[i] * dffs[i]["cost"] for i in range(len(dffs))], solver=ilp_model)
    ilp_model.setObjective(obj_expr, GRB.MINIMIZE)

    print(f"Optimizing timing-aware ILP with technology mapping")
    ilp_model.optimize()

    if ilp_model.status == GRB.INFEASIBLE:
        raise ValueError("Timing-aware ILP with techmap is infeasible. Try relaxing constraints.")
    if ilp_model.status == GRB.UNBOUNDED:
        raise ValueError("Timing-aware ILP with techmap is unbounded.")

    print(f"Timing-aware ILP with techmap solved with objective value: {ilp_model.objVal}")

    selected_delays = [delta[i].X for i in range(len(groups)) if x[i].X is not None and x[i].X > 0.5]
    if selected_delays:
        max_delay = max(selected_delays)
        print(f"Maximum delay in solution: {max_delay:.3f} (target: {target_delay})")
    else:
        print("No delays found in solution (variables may not have solution values)")

    # Extract solution
    cells_selected = [cell for i, cell in enumerate(cells) if y[i].X is not None and y[i].X > 0.5]
    dffs_selected = [dff for i, dff in enumerate(dffs) if z[i].X is not None and z[i].X > 0.5]

    return normalized_to_json(db, tech_rules, inputs, outputs, cells_selected, dffs_selected)