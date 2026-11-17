"""NULLSIGHT — scan device prototype in a simple test room."""

import argparse
import time

import hyperlite

from camera import Camera, DEFAULT_FOV_Y
from acoustics import AcousticSystem
from occlusion import Aabb
from engine_config import configure_engine
from frame_pacing import UNFOCUSED_FPS_CAP, disable_precise_sleep, enable_precise_sleep, wait_until_frame_cap
from input_state import InputState
from main_menu import MainMenu
from pause_menu import PauseMenu
from perf_hud import PerfHud
from renderer import draw_wireframe_scene
from gameplay_hud import draw_gameplay_hud
from health_audio_fx import HealthAudioFx
from interactable_door import InteractableDoor
from interactable_door import InteractableDoor
from levels import load_room_file, load_world
from nullsight_audio import NullsightAudio
from player_collision import resolve_spawn_position
from player_vitals import PlayerVitals
from scanner_repair import ScannerRepairMode
from scan import ScanDevice
from settings_store import load_settings, save_settings
from ui_text import draw_text
from window_focus import is_game_window_focused

# Dev-only vitals test keys (VK codes) — hold to simulate damage until hazards exist.
_DEV_SCANNER_HURT_KEY = 0xDB  # [
_DEV_HEALTH_HURT_KEY = 0xDD  # ]

INITIAL_WIDTH = 960
INITIAL_HEIGHT = 540
WINDOW_TITLE = "NULLSIGHT"


def _parse_args() -> argparse.Namespace:
	"""Parse optional startup flags."""
	parser = argparse.ArgumentParser(description="NULLSIGHT scan prototype")
	parser.add_argument(
		"--no-audio",
		action="store_true",
		help="Disable Spherion spatial audio even when enabled in settings",
	)
	parser.add_argument(
		"--playtest",
		metavar="ROOM_JSON",
		default="",
		help="Skip title screen and load this Facility room JSON immediately",
	)
	return parser.parse_args()


def _return_to_title_screen(
	engine: hyperlite.Engine,
	main_menu: MainMenu,
	pause_menu: PauseMenu,
	audio: NullsightAudio,
	camera: Camera,
	room,
	prev_eye: list[float],
	vitals: PlayerVitals,
	health_fx: HealthAudioFx,
	scanner_repair: ScannerRepairMode,
	door: InteractableDoor,
) -> ScanDevice:
	"""Reset gameplay state and show the title screen with menu music."""
	pause_menu.close(engine)
	camera.position = resolve_spawn_position(
		room.spawn,
		room.collidables,
		room.bounds,
		base_floor_y=room.floor_y,
		walkables=room.walkables,
	)
	camera.velocity_y = 0.0
	camera.grounded = True
	camera.yaw = 0.0
	camera.pitch = 0.0
	vitals.reset()
	health_fx.reset(audio)
	scanner_repair.reset(audio)
	door.reset()
	fresh_scan = ScanDevice()
	eye = camera.eye_position()
	prev_eye[0], prev_eye[1], prev_eye[2] = eye[0], eye[1], eye[2]
	main_menu.return_to_title(engine)
	audio.on_main_menu_show()
	health_fx.on_pause(audio)
	return fresh_scan


def _maybe_cap_unfocused_frame(frame_start: float, focused: bool, cap_when_unfocused: bool) -> None:
	"""Sleep to hold 10 FPS while the game window is in the background."""
	if cap_when_unfocused and not focused:
		wait_until_frame_cap(frame_start, UNFOCUSED_FPS_CAP)


def _game_collidables(room, door: InteractableDoor) -> list[Aabb]:
	"""Room solids plus the door panel when it still blocks passage."""
	solids = list(room.collidables)
	panel = door.collidable()
	if panel is not None:
		solids.append(panel)
	return solids


def _scan_occlusion_payload(
	room,
	door: InteractableDoor,
) -> tuple[list, list, list]:
	"""Room scan data plus the door panel as a solid occluder hull."""
	hulls = list(room.occluder_hulls)
	solids = list(room.occluders)
	panel_hull = door.panel_occluder_hull()
	if panel_hull is not None:
		hulls.append(panel_hull)
		panel_bounds = panel_hull[0]
		solids.append(panel_bounds)
	return hulls, solids


def run() -> None:
	"""Main loop: blind exploration with outward-propagating scan pulses."""
	args = _parse_args()
	if args.playtest:
		room, door = load_room_file(args.playtest)
		if door is None:
			door = InteractableDoor.west_wall(z_center=1.0, width=1.0, height=2.0, floor_y=room.floor_y)
	else:
		room, door = load_world()

	settings = load_settings()
	engine = hyperlite.Engine(INITIAL_WIDTH, INITIAL_HEIGHT, "cpu", WINDOW_TITLE)
	engine.set_mouse_captured(False)
	configure_engine(engine, settings)

	environment_edges = room.environment_edges
	occluder_hulls = room.occluder_hulls
	occluders = room.occluders
	boundaries = room.boundaries
	collidables = room.collidables

	camera = Camera(
		position=resolve_spawn_position(
			room.spawn,
			collidables,
			room.bounds,
			base_floor_y=room.floor_y,
			walkables=room.walkables,
		),
		yaw=0.0,
		pitch=0.0,
	)
	scan = ScanDevice()
	vitals = PlayerVitals()
	health_fx = HealthAudioFx()
	scanner_repair = ScannerRepairMode()
	input_state = InputState()
	main_menu = MainMenu(settings)
	pause_menu = PauseMenu(settings)
	perf_hud = PerfHud()
	prev_eye = list(camera.eye_position())
	camera_speed_sq = 0.0
	listener_velocity = (0.0, 0.0, 0.0)
	audio = NullsightAudio(
		room,
		audio_settings=settings.audio,
		enabled=settings.audio.enabled and not args.no_audio,
	)
	acoustic_system = AcousticSystem()
	acoustic_system.notify_level_loaded(room)
	if audio.status_message is not None:
		print(f"NULLSIGHT audio: {audio.status_message}")
	elif audio.active:
		print("NULLSIGHT audio: Spherion active (headphones recommended)")

	if args.playtest:
		# Jump straight into gameplay — editor playtest path.
		main_menu.dismiss(engine)
		engine.set_mouse_captured(True)
		audio.on_main_menu_hide()
	else:
		audio.on_main_menu_show()
	enable_precise_sleep()
	game_paused = False

	try:
		while engine.is_running():
			frame_start = time.perf_counter()
			focused = is_game_window_focused(WINDOW_TITLE)
			audio.set_focus_quiet(focused, settings.quiet_unfocused)

			perf_hud.begin_phases()
			engine.poll_events()

			engine.begin_frame()
			frame_dt = engine.delta_time()
			audio.update_focus_quiet(frame_dt)
			width, height = engine.window_size()

			if main_menu.active:
				sim_dt = frame_dt
				perf_hud.update(frame_dt)
				input_state.poll(engine, gameplay_active=False)
				menu_input_enabled = not pause_menu.is_open
				main_menu.update(
					engine,
					sim_dt,
					handle_input=menu_input_enabled and not main_menu.credits_active(),
					audio=audio,
				)

				if pause_menu.is_open:
					pause_action = pause_menu.update(engine, audio=audio)
					if pause_action == "main_menu":
						scan = _return_to_title_screen(
							engine, main_menu, pause_menu, audio, camera, room, prev_eye, vitals, health_fx, scanner_repair, door
						)
						acoustic_system.clear()
						audio.clear_acoustic_obstruction()
				elif menu_input_enabled and not main_menu.credits_active():
					menu_action = main_menu.consume_action()
					if menu_action == "settings":
						pause_menu.open_settings(engine, from_title_screen=True)
					elif menu_action == "start":
						audio.on_main_menu_hide()
						vitals.reset()
						health_fx.reset(audio)
						scanner_repair.reset(audio)
						door.reset()
						acoustic_system.notify_level_loaded(room)
						camera.position = resolve_spawn_position(
							room.spawn,
							collidables,
							room.bounds,
							base_floor_y=room.floor_y,
							walkables=room.walkables,
						)
						camera.velocity_y = 0.0
						camera.grounded = True
						camera.yaw = 0.0
						camera.pitch = 0.0
						scan = ScanDevice()
						main_menu.dismiss(engine)
					elif menu_action == "quit":
						break
				else:
					menu_action = None

				if pause_menu.is_open:
					main_menu.draw(engine, width, height)
					pause_menu.draw(engine, width, height)
				else:
					main_menu.draw(engine, width, height)

				if audio.credits_music_active():
					audio.update_credits_music(sim_dt)
				elif audio.menu_music_active():
					audio.update_menu_music(sim_dt)
				perf_hud.stamp_ui_phase()
				engine.end_frame()
				engine.present()
				_maybe_cap_unfocused_frame(frame_start, focused, settings.cap_fps_unfocused)
				game_paused = False
				continue

			repair_active = scanner_repair.is_open
			gameplay_active = not pause_menu.is_open and not repair_active
			sim_dt = 0.0 if pause_menu.is_open or repair_active else frame_dt
			if pause_menu.is_open and not game_paused:
				health_fx.on_pause(audio)
			if repair_active and not game_paused:
				health_fx.on_pause(audio)
			game_paused = pause_menu.is_open or repair_active

			audio.update_focus_quiet(frame_dt)
			perf_hud.update(frame_dt)

			input_state.poll(engine, gameplay_active=gameplay_active)

			if input_state.pause_pressed and not repair_active:
				pause_menu.open(engine)

			if gameplay_active and scanner_repair.consume_open_pressed(engine, vitals):
				scanner_repair.open(engine, width, height)
				scanner_repair.on_open(audio)
				health_fx.on_pause(audio)
				repair_active = True
				gameplay_active = False
				sim_dt = 0.0

			menu_action = pause_menu.update(engine, audio=audio)
			if menu_action == "resume":
				pause_menu.resume(engine)
			elif menu_action == "main_menu":
				scan = _return_to_title_screen(
					engine, main_menu, pause_menu, audio, camera, room, prev_eye, vitals, health_fx, scanner_repair, door
				)
				acoustic_system.clear()
				audio.clear_acoustic_obstruction()
				camera_speed_sq = 0.0
				repair_active = False

			if repair_active:
				scanner_repair.update(engine, vitals, audio, width, height)

			if sim_dt > 0.0 and gameplay_active:
				door.update(sim_dt)
				if input_state.interact_pressed and door.can_interact(camera):
					door.try_open()
				door.sync_scanned_panel(scan)

			perf_hud.stamp_input_phase()

			if gameplay_active:
				moving = input_state.move_x != 0.0 or input_state.move_z != 0.0
				sprinting = moving and input_state.sprint_held
				vitals.update(sim_dt, sprinting=sprinting)
				if engine.key_down(_DEV_SCANNER_HURT_KEY):
					vitals.apply_scanner_damage(14.0 * sim_dt)
				if engine.key_down(_DEV_HEALTH_HURT_KEY):
					vitals.apply_damage(16.0 * sim_dt)
				camera.apply_look(input_state.look_dx, input_state.look_dy)
				camera.move_with_collision(
					input_state.move_x,
					input_state.move_z,
					sim_dt,
					_game_collidables(room, door),
					room.bounds,
					speed_multiplier=vitals.sprint_speed_multiplier(sprinting=sprinting),
					jump_pressed=input_state.jump_pressed,
					base_floor_y=room.floor_y,
					ceiling_y=room.ceiling_y,
					walkables=room.walkables,
				)
				eye = camera.eye_position()
				dx = eye[0] - prev_eye[0]
				dy = eye[1] - prev_eye[1]
				dz = eye[2] - prev_eye[2]
				if sim_dt > 0.0:
					speed = (dx * dx + dy * dy + dz * dz) ** 0.5 / sim_dt
					camera_speed_sq = speed * speed
					listener_velocity = (dx / sim_dt, dy / sim_dt, dz / sim_dt)
				prev_eye[0], prev_eye[1], prev_eye[2] = eye[0], eye[1], eye[2]
				scan_trigger = (
					input_state.scan_pressed
					and scan.scan_ready
					and vitals.can_scan()
				)
				scan_hulls, scan_solids = _scan_occlusion_payload(room, door)
				scan.update(
					sim_dt,
					scan_trigger,
					camera.eye_position(),
					camera.yaw,
					camera.pitch,
					environment_edges,
					scan_hulls,
					scan_solids,
					boundaries,
					width,
					height,
					DEFAULT_FOV_Y,
				)

			else:
				camera_speed_sq = 0.0
				listener_velocity = (0.0, 0.0, 0.0)

			perf_hud.stamp_sim_phase()

			if gameplay_active:
				if settings.audio.acoustic_obstruction and audio.active:
					snapshot = acoustic_system.snapshot(
						room,
						door,
						audio.all_spatial_sources(),
						camera,
					)
					links = acoustic_system.update(snapshot, sim_dt if sim_dt > 0.0 else frame_dt)
					audio.apply_acoustic_links(links)
				audio.sync_camera(camera, listener_velocity_mps=listener_velocity)
			if audio.menu_music_active():
				audio.update_menu_music(frame_dt)

			if gameplay_active:
				health_fx.update(
					audio,
					camera,
					dt=sim_dt,
					health_ratio=vitals.health_ratio,
					damage_rate=vitals.health_damage_rate,
					room_bounds=room.bounds,
				)

			if repair_active:
				scanner_repair.draw(engine, width, height)
			else:
				draw_wireframe_scene(
					engine,
					camera,
					environment_edges,
					occluder_hulls,
					scan,
					occluders,
					boundaries,
					width,
					height,
					settings,
					camera_speed_sq=camera_speed_sq,
					scanner_ratio=vitals.scanner_ratio,
					scan_time=scan.sim_time,
					health_ratio=vitals.health_ratio,
				)
			perf_hud.stamp_scene_phase()

			if settings.osd.any_enabled():
				perf_hud.draw(engine, settings.osd)
			if settings.osd.acoustic_path and gameplay_active:
				# Dev HUD: path_mode / gain summary for each spatial source.
				line_y = 12
				for line in audio.acoustic_debug_lines():
					draw_text(engine, 12, line_y, line, 180, 220, 255, scale=1)
					line_y += 12
			if not repair_active:
				draw_gameplay_hud(engine, vitals, width, height)
				if door.can_interact(camera):
					door.draw_interact_prompt(engine, width, height)
			pause_menu.draw(engine, width, height)
			perf_hud.stamp_ui_phase()

			engine.end_frame()
			perf_hud.stamp_rast_phase()

			engine.present()
			perf_hud.stamp_present_phase()
			perf_hud.sample_engine_timings(engine)
			_maybe_cap_unfocused_frame(frame_start, focused, settings.cap_fps_unfocused)
	finally:
		disable_precise_sleep()
		scanner_repair.reset(audio)
		health_fx.shutdown(audio)
		audio.shutdown()
		save_settings(settings)


if __name__ == "__main__":
	run()
