"""Room adjacency graph for portal pathing (west cell ↔ east cell via door).

When direct LOS is blocked by a door panel, boost the portal / diffracted path
using the graph edge gain so energy still couples through the doorway.

Multi-room levels use Dijkstra over portal edges (shortest acoustic path by
world distance through portal midpoints), with pressure gain as the product of
open-fraction-scaled edge gains along the route.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from math3d import Vec3, length, sub


@dataclass(frozen=True)
class PortalEdge:
	"""Directed acoustic coupling between two room cells via a portal."""

	from_room: str
	to_room: str
	# Midband pressure gain when the portal is fully open (0–1).
	open_gain: float = 0.85
	# LPF when fully open (Hz).
	open_lpf_hz: float = 6_500.0
	# Portal world position (doorway center) for nearest-room queries.
	portal_pos: Vec3 = (0.0, 1.0, 0.0)


@dataclass(frozen=True)
class AcousticPortalPath:
	"""Shortest acoustic route through the portal graph."""

	# Ordered room ids from listener cell to source cell (inclusive).
	rooms: tuple[str, ...]
	# Approximate path length through portal midpoints (m).
	path_length_m: float
	# Product of open-fraction-scaled edge gains along the route.
	gain: float
	# Softest (lowest) LPF along the route (Hz).
	lpf_hz: float


@dataclass
class PortalGraph:
	"""Room adjacency for acoustic portal pathing."""

	edges: list[PortalEdge] = field(default_factory=list)

	def add_edge(self, edge: PortalEdge) -> None:
		"""Register a bidirectional portal (stores both directions)."""
		self.edges.append(edge)
		# Mirror unless already present.
		mirror = PortalEdge(
			from_room=edge.to_room,
			to_room=edge.from_room,
			open_gain=edge.open_gain,
			open_lpf_hz=edge.open_lpf_hz,
			portal_pos=edge.portal_pos,
		)
		if not any(
			e.from_room == mirror.from_room and e.to_room == mirror.to_room for e in self.edges
		):
			self.edges.append(mirror)

	def edge_gain(
		self,
		from_room: str,
		to_room: str,
		*,
		door_open_frac: float,
	) -> tuple[float, float]:
		"""Return (gain, lpf_hz) for the portal between two rooms.

		Scales with door open fraction (closed → near-zero graph boost).
		"""
		frac = max(0.0, min(1.0, door_open_frac))
		for edge in self.edges:
			if edge.from_room == from_room and edge.to_room == to_room:
				shaped = frac ** 1.2
				gain = edge.open_gain * shaped
				lpf = 280.0 + (edge.open_lpf_hz - 280.0) * shaped
				return gain, lpf
		return 0.0, 280.0

	def neighbors(self, room: str) -> list[PortalEdge]:
		"""Return outbound portal edges from ``room``."""
		return [e for e in self.edges if e.from_room == room]

	def shortest_acoustic_path(
		self,
		from_room: str,
		to_room: str,
		*,
		door_open_frac: float,
		listener_pos: Vec3 | None = None,
		source_pos: Vec3 | None = None,
	) -> AcousticPortalPath | None:
		"""Dijkstra shortest path by world distance through portal midpoints.

		Cost for an edge is the segment length from the previous waypoint to
		that edge's ``portal_pos``. Terminal cost adds listener→first portal and
		last portal→source when positions are provided. Closed portals
		(``door_open_frac`` ≈ 0) are not traversable.
		"""
		if from_room == to_room:
			dist = 0.0
			if listener_pos is not None and source_pos is not None:
				dist = length(sub(source_pos, listener_pos))
			return AcousticPortalPath(
				rooms=(from_room,),
				path_length_m=dist,
				gain=1.0,
				lpf_hz=18_000.0,
			)

		frac = max(0.0, min(1.0, door_open_frac))
		if frac <= 1e-4:
			return None

		# Dijkstra: state = (cost_m, room, path_rooms, gain_product, min_lpf, last_portal)
		start_pos = listener_pos if listener_pos is not None else (0.0, 0.0, 0.0)
		heap: list[tuple[float, str, tuple[str, ...], float, float, Vec3]] = [
			(0.0, from_room, (from_room,), 1.0, 18_000.0, start_pos)
		]
		best_cost: dict[str, float] = {from_room: 0.0}

		while heap:
			cost, room, path, gain_prod, min_lpf, last_pos = heapq.heappop(heap)
			if cost > best_cost.get(room, math.inf) + 1e-9:
				continue
			if room == to_room:
				# Add last portal → source when available.
				end_cost = cost
				if source_pos is not None and path[-1] == to_room and len(path) > 1:
					# last_pos is the portal that entered to_room.
					end_cost = cost + length(sub(source_pos, last_pos))
				elif source_pos is not None and listener_pos is not None and len(path) == 1:
					end_cost = length(sub(source_pos, listener_pos))
				return AcousticPortalPath(
					rooms=path,
					path_length_m=end_cost,
					gain=min(1.0, gain_prod),
					lpf_hz=min_lpf,
				)

			for edge in self.neighbors(room):
				edge_g, edge_lpf = self.edge_gain(
					edge.from_room,
					edge.to_room,
					door_open_frac=frac,
				)
				if edge_g <= 1e-4:
					continue
				# Listener → first portal uses listener_pos; thereafter portal→portal.
				seg = length(sub(edge.portal_pos, last_pos))
				# Tiny epsilon so zero-length co-located portals still order hops.
				new_cost = cost + max(seg, 1e-3)
				nxt = edge.to_room
				if new_cost >= best_cost.get(nxt, math.inf) - 1e-9:
					continue
				best_cost[nxt] = new_cost
				heapq.heappush(
					heap,
					(
						new_cost,
						nxt,
						path + (nxt,),
						gain_prod * edge_g,
						min(min_lpf, edge_lpf),
						edge.portal_pos,
					),
				)
		return None

	def boost_for_blocked_path(
		self,
		*,
		listener_room: str,
		source_room: str,
		door_open_frac: float,
		los_blocked: bool,
		listener_pos: Vec3 | None = None,
		source_pos: Vec3 | None = None,
	) -> float:
		"""Portal graph pressure boost when LOS is blocked across a door.

		Returns 0 when rooms match or LOS is clear. Uses the shortest acoustic
		path gain (product of edge gains) so multi-hop routes still couple.
		"""
		if not los_blocked or listener_room == source_room:
			return 0.0
		route = self.shortest_acoustic_path(
			listener_room,
			source_room,
			door_open_frac=door_open_frac,
			listener_pos=listener_pos,
			source_pos=source_pos,
		)
		if route is None:
			return 0.0
		return route.gain


def default_small_level_graph(*, door_pos: Vec3 = (10.0, 1.2, 6.0)) -> PortalGraph:
	"""West cell ↔ east cell adjacency for the NULLSIGHT small bunker level."""
	graph = PortalGraph()
	graph.add_edge(
		PortalEdge(
			from_room="west",
			to_room="east",
			open_gain=0.85,
			open_lpf_hz=6_500.0,
			portal_pos=door_pos,
		)
	)
	return graph


def classify_cell_xz(
	pos: Vec3,
	*,
	divider_x: float = 10.0,
) -> str:
	"""Classify a world XZ point as west or east of the divider wall."""
	return "west" if pos[0] < divider_x else "east"


def portal_graph_gain_for_path(
	eye: Vec3,
	source: Vec3,
	*,
	door_open_frac: float,
	los_blocked: bool,
	graph: PortalGraph | None = None,
	divider_x: float = 10.0,
) -> float:
	"""Convenience: graph edge gain when listener/source are in different cells."""
	if not los_blocked or door_open_frac <= 1e-4:
		return 0.0
	g = graph if graph is not None else default_small_level_graph()
	listener_room = classify_cell_xz(eye, divider_x=divider_x)
	source_room = classify_cell_xz(source, divider_x=divider_x)
	return g.boost_for_blocked_path(
		listener_room=listener_room,
		source_room=source_room,
		door_open_frac=door_open_frac,
		los_blocked=True,
		listener_pos=eye,
		source_pos=source,
	)
