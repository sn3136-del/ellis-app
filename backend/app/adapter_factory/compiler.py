"""Flow compiler (brief §16→§19 bridge).

Compiles a candidate version's typed flow into an executable plan of
normalized, validated nodes. The compiler FAILS CLOSED: any unknown action,
selector class, hostname, or branch aborts compilation — nothing degraded or
partial ever reaches the runtime.
"""
from __future__ import annotations

from .schema import ACTION_TYPES, normalize_node, validate_flow


class CompileError(Exception):
    pass


class CompiledFlow:
    def __init__(self, nodes: list[dict], order: list[str], manifest: dict):
        self.nodes = {n["node_id"]: n for n in nodes}
        self.order = order
        self.manifest = manifest
        self.allowed_hostnames = [h.lower() for h in manifest.get("allowed_hostnames", [])]

    def first(self) -> str:
        return self.order[0]

    def next_of(self, node_id: str, outcome: str = "ok") -> str | None:
        node = self.nodes[node_id]
        for branch in node.get("next") or []:
            if branch.get("on", "ok") == outcome:
                return branch["to"]
        # Default: the next node in declared order.
        i = self.order.index(node_id)
        return self.order[i + 1] if i + 1 < len(self.order) else None

    def host_allowed(self, hostname: str) -> bool:
        h = (hostname or "").lower()
        return any(h == a or h.endswith("." + a) for a in self.allowed_hostnames)


def compile_flow(version_row) -> CompiledFlow:
    manifest = version_row.manifest or {}
    hosts = manifest.get("allowed_hostnames") or []
    flow = version_row.flow or []
    errs = validate_flow(flow, allowed_hostnames=hosts)
    if errs:
        raise CompileError(f"flow failed validation: {errs[:5]}")
    nodes = []
    for raw in flow:
        n = normalize_node(raw)
        if n["action"] not in ACTION_TYPES:
            raise CompileError(f"unknown action {n['action']!r}")
        nodes.append(n)
    order = [n["node_id"] for n in nodes]
    return CompiledFlow(nodes, order, manifest)
