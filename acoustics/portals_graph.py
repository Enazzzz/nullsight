"""Room adjacency graph for portal pathing (west cell ↔ east cell via door).

When direct LOS is blocked by a door panel, boost the portal / diffracted path
using the graph edge gain so energy still couples through the doorway.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from math3d import Vec3


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


@dataclass
class PortalGraph:
	"""Small-level room adjacency for acoustic portal pathing."""

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

	def boost_for_blocked_path(
		self,
		*,
		listener_room: str,
		source_room: str,
		door_open_frac: float,
		los_blocked: bool,
	) -> float:
		"""Portal graph pressure boost when LOS is blocked across a door.

		Returns 0 when rooms match or LOS is clear. Used to raise transmission /
		diffraction when the door panel occludes the direct ray.
		"""
		if not los_blocked or listener_room == source_room:
			return 0.0
		gain, _lpf = self.edge_gain(source_room, listener_room, door_open_frac=door_open_frac)
		return gain


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
	)
