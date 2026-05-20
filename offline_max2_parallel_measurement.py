from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing
import queue
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


DEFAULT_BAUDRATE = 115200
DEFAULT_COM_PORT = "COM8"


class Phase(IntEnum):
    IDLE = 0
    FIXATION_BEFORE = 1
    STIMULUS = 2
    FIXATION_AFTER = 3
    FINISHED = 8


PHASE_NAMES = {
    Phase.IDLE: "idle",
    Phase.FIXATION_BEFORE: "fixation_before",
    Phase.STIMULUS: "stimulus",
    Phase.FIXATION_AFTER: "fixation_after",
    Phase.FINISHED: "finished",
}


@dataclass(frozen=True)
class StimulusSpec:
    name: str
    frequency_hz: float
    position: Tuple[float, float]
    radius: float
    color: Tuple[float, float, float]
    start_on: bool = True


@dataclass(frozen=True)
class ExperimentConfig:
    com_port: str
    baudrate: int
    channel_mode: str
    pre_fixation_sec: float
    stimulus_sec: float
    stimulus_cycles: Optional[float]
    post_fixation_sec: float
    refresh_rate_override: Optional[float]
    fullscreen: bool
    monitor_index: int
    window_width: int
    window_height: int
    fixation_radius: float
    stimulus_radius: float
    show_fixation_during_stimulus: bool
    run_dir: str
    serial_timeout_sec: float
    ready_timeout_sec: float
    make_max2_summary: bool
    stimuli: Tuple[StimulusSpec, ...]


@dataclass
class SharedState:
    start_event: Any
    stop_event: Any
    serial_ready_event: Any
    phase_code: Any
    frame_index: Any
    frame_time_ns: Any
    stimulus_active: Any
    stimulus_on_mask: Any
    visual_start_ns: Any


@dataclass(frozen=True)
class RunFiles:
    metadata_json: str
    events_csv: str
    frames_csv: str
    serial_csv: str
    max2_summary_csv: str


def phase_name(phase_code: int) -> str:
    try:
        return PHASE_NAMES[Phase(phase_code)]
    except ValueError:
        return f"unknown_{phase_code}"


def set_windows_timer_resolution(enable: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        if enable:
            ctypes.windll.winmm.timeBeginPeriod(1)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
    except Exception as exc:
        print(f"Timer resolution change skipped: {exc}")


def set_realtime_priority(label: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import psutil

        process = psutil.Process()
        try:
            process.nice(psutil.REALTIME_PRIORITY_CLASS)
            print(f"{label}: priority set to REALTIME")
        except Exception:
            process.nice(psutil.HIGH_PRIORITY_CLASS)
            print(f"{label}: priority set to HIGH")
    except Exception as exc:
        print(f"{label}: priority change skipped: {exc}")


def list_serial_ports() -> None:
    try:
        import serial.tools.list_ports
    except Exception as exc:
        raise RuntimeError(f"pyserial is required to list ports: {exc}") from exc

    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return

    print("Available serial ports:")
    for port in ports:
        print(f"  {port.device}  {port.description}")


def parse_serial_line(raw_line: bytes, channel_mode: str) -> Tuple[List[Optional[float]], int, str]:
    text = raw_line.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("empty line")

    parts = [part.strip() for part in text.split(",")]
    if channel_mode == "one":
        parts = parts[:1]
    elif channel_mode == "three":
        if len(parts) < 3:
            raise ValueError(f"expected 3 channels, got {len(parts)}")
        parts = parts[:3]
    elif channel_mode == "auto":
        if len(parts) >= 3:
            parts = parts[:3]
        elif len(parts) == 1:
            parts = parts[:1]
        else:
            raise ValueError(f"expected 1 or 3 channels, got {len(parts)}")
    else:
        raise ValueError(f"unknown channel mode: {channel_mode}")

    values = [float(part) for part in parts]
    channel_count = len(values)
    while len(values) < 3:
        values.append(None)
    return values, channel_count, text


def serial_worker(config: ExperimentConfig, shared: SharedState, files: RunFiles, error_queue: Any) -> None:
    set_realtime_priority("serial")

    try:
        import serial
    except Exception as exc:
        error_queue.put(f"pyserial import failed: {exc}")
        shared.serial_ready_event.set()
        return

    try:
        with serial.Serial(
            config.com_port,
            config.baudrate,
            timeout=config.serial_timeout_sec,
        ) as ser:
            try:
                ser.reset_input_buffer()
            except Exception:
                pass

            shared.serial_ready_event.set()
            if not shared.start_event.wait(timeout=120.0):
                error_queue.put("serial worker timed out waiting for visual start")
                return

            sample_index = 0
            one_sec_start_ns = time.perf_counter_ns()
            one_sec_count = 0

            with open(files.serial_csv, "w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(
                    [
                        "sample_index",
                        "pc_time_ns",
                        "experiment_time_s",
                        "phase_code",
                        "phase_name",
                        "frame_index",
                        "frame_time_ns",
                        "stimulus_active",
                        "stimulus_on_mask",
                        "serial_channel_count",
                        "ch1",
                        "ch2",
                        "ch3",
                        "parse_error",
                        "raw_line",
                    ]
                )

                while not shared.stop_event.is_set():
                    raw_line = ser.readline()
                    now_ns = time.perf_counter_ns()
                    if not raw_line:
                        continue

                    one_sec_count += 1
                    if now_ns - one_sec_start_ns >= 1_000_000_000:
                        print(f"serial samples/sec: {one_sec_count}")
                        one_sec_count = 0
                        one_sec_start_ns = now_ns

                    phase_code = int(shared.phase_code.value)
                    frame_index = int(shared.frame_index.value)
                    frame_time_ns = int(shared.frame_time_ns.value)
                    visual_start_ns = int(shared.visual_start_ns.value)
                    experiment_time_s = (
                        (now_ns - visual_start_ns) / 1_000_000_000.0
                        if visual_start_ns > 0
                        else ""
                    )
                    stimulus_active = int(shared.stimulus_active.value)
                    stimulus_on_mask = int(shared.stimulus_on_mask.value)

                    parse_error = ""
                    channel_count = ""
                    values: List[Optional[float]] = [None, None, None]
                    raw_text = raw_line.decode("utf-8", errors="replace").strip()

                    try:
                        values, channel_count, raw_text = parse_serial_line(
                            raw_line, config.channel_mode
                        )
                    except Exception as exc:
                        parse_error = str(exc)

                    writer.writerow(
                        [
                            sample_index,
                            now_ns,
                            experiment_time_s,
                            phase_code,
                            phase_name(phase_code),
                            frame_index,
                            frame_time_ns,
                            stimulus_active,
                            stimulus_on_mask,
                            channel_count,
                            "" if values[0] is None else values[0],
                            "" if values[1] is None else values[1],
                            "" if values[2] is None else values[2],
                            parse_error,
                            raw_text,
                        ]
                    )
                    sample_index += 1
    except Exception as exc:
        error_queue.put(f"serial worker failed: {exc}")
        shared.serial_ready_event.set()


class FrameLockedBlinker:
    def __init__(self, stimulus: StimulusSpec, refresh_rate: float) -> None:
        self.stimulus = stimulus
        self.refresh_rate = refresh_rate
        self.half_period_frames = refresh_rate / (2.0 * stimulus.frequency_hz)
        self.rounded_half_period = max(1, int(round(self.half_period_frames)))
        self.is_exact = abs(self.half_period_frames - self.rounded_half_period) < 1e-6

    def on_at_frame(self, phase_frame_index: int) -> bool:
        if self.is_exact:
            toggle_index = phase_frame_index // self.rounded_half_period
        else:
            toggle_index = int(math.floor(phase_frame_index / self.half_period_frames))
        on = (toggle_index % 2) == 0
        return on if self.stimulus.start_on else not on

    def warning(self) -> Optional[str]:
        if self.is_exact:
            return None
        return (
            f"{self.stimulus.name}: {self.stimulus.frequency_hz} Hz is not an exact "
            f"frame-locked frequency at {self.refresh_rate} Hz "
            f"(half period {self.half_period_frames:.4f} frames)"
        )


class CircleRenderer:
    def __init__(self, segments: int = 128) -> None:
        import numpy as np
        from OpenGL import GL
        from OpenGL.GL.shaders import compileProgram, compileShader

        self.GL = GL
        vertex_shader_code = """
        #version 330 core
        layout (location = 0) in vec2 aPos;
        uniform vec2 uCenter;
        uniform vec2 uScale;
        void main()
        {
            gl_Position = vec4(uCenter + aPos * uScale, 0.0, 1.0);
        }
        """
        fragment_shader_code = """
        #version 330 core
        out vec4 FragColor;
        uniform vec3 uColor;
        void main()
        {
            FragColor = vec4(uColor, 1.0);
        }
        """

        self.shader_program = compileProgram(
            compileShader(vertex_shader_code, GL.GL_VERTEX_SHADER),
            compileShader(fragment_shader_code, GL.GL_FRAGMENT_SHADER),
        )
        self.center_location = GL.glGetUniformLocation(self.shader_program, "uCenter")
        self.scale_location = GL.glGetUniformLocation(self.shader_program, "uScale")
        self.color_location = GL.glGetUniformLocation(self.shader_program, "uColor")

        vertices = [[0.0, 0.0]]
        for index in range(segments + 1):
            theta = 2.0 * math.pi * index / segments
            vertices.append([math.cos(theta), math.sin(theta)])
        vertex_array = np.array(vertices, dtype=np.float32)
        self.vertex_count = len(vertex_array)

        self.vao = GL.glGenVertexArrays(1)
        self.vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(self.vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
        GL.glBufferData(
            GL.GL_ARRAY_BUFFER,
            vertex_array.nbytes,
            vertex_array,
            GL.GL_STATIC_DRAW,
        )
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(0)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glBindVertexArray(0)

    def draw_circle(
        self,
        center: Tuple[float, float],
        radius: float,
        color: Tuple[float, float, float],
        aspect_ratio: float,
    ) -> None:
        GL = self.GL
        x_scale = radius / aspect_ratio
        y_scale = radius
        GL.glUseProgram(self.shader_program)
        GL.glUniform2f(self.center_location, center[0], center[1])
        GL.glUniform2f(self.scale_location, x_scale, y_scale)
        GL.glUniform3f(self.color_location, color[0], color[1], color[2])
        GL.glBindVertexArray(self.vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_FAN, 0, self.vertex_count)
        GL.glBindVertexArray(0)


def choose_monitor(glfw: Any, monitor_index: int) -> Any:
    monitors = glfw.get_monitors()
    if not monitors:
        raise RuntimeError("No monitor found by GLFW.")
    if monitor_index < 0 or monitor_index >= len(monitors):
        print(f"monitor-index {monitor_index} is out of range; using primary monitor")
        return glfw.get_primary_monitor()
    return monitors[monitor_index]


def init_window(config: ExperimentConfig) -> Tuple[Any, Any, int, int, float]:
    import glfw

    if not glfw.init():
        raise RuntimeError("GLFW initialization failed.")

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    if sys.platform == "darwin":
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, glfw.TRUE)

    monitor = choose_monitor(glfw, config.monitor_index)
    video_mode = glfw.get_video_mode(monitor)
    refresh_rate = float(config.refresh_rate_override or video_mode.refresh_rate or 60.0)

    if config.fullscreen:
        width = int(video_mode.size.width)
        height = int(video_mode.size.height)
        window_monitor = monitor
    else:
        width = int(config.window_width)
        height = int(config.window_height)
        window_monitor = None

    window = glfw.create_window(width, height, "Offline MAX2 Parallel Measurement", window_monitor, None)
    if not window:
        glfw.terminate()
        raise RuntimeError("GLFW window creation failed.")

    glfw.make_context_current(window)
    glfw.swap_interval(1)
    return glfw, window, width, height, refresh_rate


def write_event(
    writer: csv.writer,
    event_index: int,
    event_name: str,
    phase: Phase,
    frame_index: int,
    timestamp_ns: int,
    visual_start_ns: int,
) -> None:
    writer.writerow(
        [
            event_index,
            event_name,
            int(phase),
            phase_name(int(phase)),
            frame_index,
            timestamp_ns,
            (timestamp_ns - visual_start_ns) / 1_000_000_000.0,
        ]
    )


def update_shared_frame(
    shared: SharedState,
    phase: Phase,
    frame_index: int,
    frame_time_ns: int,
    stimulus_active: bool,
    stimulus_on_mask: int,
) -> None:
    shared.phase_code.value = int(phase)
    shared.frame_index.value = frame_index
    shared.frame_time_ns.value = frame_time_ns
    shared.stimulus_active.value = int(stimulus_active)
    shared.stimulus_on_mask.value = stimulus_on_mask


def run_visual_experiment(config: ExperimentConfig, shared: SharedState, files: RunFiles) -> None:
    from OpenGL import GL

    set_realtime_priority("visual")
    set_windows_timer_resolution(True)
    glfw = None
    window = None

    try:
        glfw, window, width, height, refresh_rate = init_window(config)
        renderer = CircleRenderer()
        aspect_ratio = width / height

        blinkers = [FrameLockedBlinker(stimulus, refresh_rate) for stimulus in config.stimuli]
        for blinker in blinkers:
            warning = blinker.warning()
            if warning:
                print(f"WARNING: {warning}")

        pre_frames = int(round(config.pre_fixation_sec * refresh_rate))
        stimulus_frames = int(round(config.stimulus_sec * refresh_rate))
        post_frames = int(round(config.post_fixation_sec * refresh_rate))
        phase_plan = [
            (Phase.FIXATION_BEFORE, pre_frames),
            (Phase.STIMULUS, stimulus_frames),
            (Phase.FIXATION_AFTER, post_frames),
        ]

        with open(files.events_csv, "w", newline="", encoding="utf-8") as event_file, open(
            files.frames_csv, "w", newline="", encoding="utf-8"
        ) as frame_file:
            event_writer = csv.writer(event_file)
            frame_writer = csv.writer(frame_file)
            event_writer.writerow(
                [
                    "event_index",
                    "event_name",
                    "phase_code",
                    "phase_name",
                    "frame_index",
                    "time_ns",
                    "experiment_time_s",
                ]
            )

            frame_header = [
                "frame_index",
                "phase_code",
                "phase_name",
                "phase_frame_index",
                "present_time_ns",
                "experiment_time_s",
                "stimulus_active",
                "stimulus_on_mask",
            ]
            frame_header.extend([f"{stimulus.name}_on" for stimulus in config.stimuli])
            frame_writer.writerow(frame_header)

            visual_start_ns = time.perf_counter_ns()
            shared.visual_start_ns.value = visual_start_ns
            shared.start_event.set()

            event_index = 0
            frame_index = 0
            write_event(
                event_writer,
                event_index,
                "experiment_start",
                Phase.FIXATION_BEFORE,
                frame_index,
                visual_start_ns,
                visual_start_ns,
            )
            event_index += 1

            for phase, phase_total_frames in phase_plan:
                phase_start_ns = time.perf_counter_ns()
                write_event(
                    event_writer,
                    event_index,
                    f"{phase_name(int(phase))}_start",
                    phase,
                    frame_index,
                    phase_start_ns,
                    visual_start_ns,
                )
                event_index += 1

                for phase_frame_index in range(phase_total_frames):
                    if glfw.window_should_close(window):
                        raise KeyboardInterrupt("GLFW window closed.")
                    if glfw.get_key(window, glfw.KEY_ESCAPE) == glfw.PRESS:
                        raise KeyboardInterrupt("ESC pressed.")

                    GL.glClearColor(0.0, 0.0, 0.0, 1.0)
                    GL.glClear(GL.GL_COLOR_BUFFER_BIT)

                    stimulus_active = phase == Phase.STIMULUS
                    stimulus_states: List[bool] = []
                    stimulus_on_mask = 0

                    if phase in (Phase.FIXATION_BEFORE, Phase.FIXATION_AFTER):
                        renderer.draw_circle(
                            (0.0, 0.0),
                            config.fixation_radius,
                            (1.0, 1.0, 1.0),
                            aspect_ratio,
                        )
                    elif phase == Phase.STIMULUS:
                        for stimulus_index, blinker in enumerate(blinkers):
                            is_on = blinker.on_at_frame(phase_frame_index)
                            stimulus_states.append(is_on)
                            if is_on:
                                stimulus_on_mask |= 1 << stimulus_index
                                renderer.draw_circle(
                                    blinker.stimulus.position,
                                    blinker.stimulus.radius,
                                    blinker.stimulus.color,
                                    aspect_ratio,
                                )
                        if config.show_fixation_during_stimulus:
                            renderer.draw_circle(
                                (0.0, 0.0),
                                config.fixation_radius,
                                (1.0, 1.0, 1.0),
                                aspect_ratio,
                            )

                    while len(stimulus_states) < len(config.stimuli):
                        stimulus_states.append(False)

                    glfw.swap_buffers(window)
                    present_time_ns = time.perf_counter_ns()
                    glfw.poll_events()

                    update_shared_frame(
                        shared,
                        phase,
                        frame_index,
                        present_time_ns,
                        stimulus_active,
                        stimulus_on_mask,
                    )
                    frame_writer.writerow(
                        [
                            frame_index,
                            int(phase),
                            phase_name(int(phase)),
                            phase_frame_index,
                            present_time_ns,
                            (present_time_ns - visual_start_ns) / 1_000_000_000.0,
                            int(stimulus_active),
                            stimulus_on_mask,
                            *[int(state) for state in stimulus_states],
                        ]
                    )
                    frame_index += 1

                phase_end_ns = time.perf_counter_ns()
                write_event(
                    event_writer,
                    event_index,
                    f"{phase_name(int(phase))}_end",
                    phase,
                    frame_index,
                    phase_end_ns,
                    visual_start_ns,
                )
                event_index += 1

            end_ns = time.perf_counter_ns()
            update_shared_frame(shared, Phase.FINISHED, frame_index, end_ns, False, 0)
            write_event(
                event_writer,
                event_index,
                "experiment_end",
                Phase.FINISHED,
                frame_index,
                end_ns,
                visual_start_ns,
            )
    finally:
        shared.stop_event.set()
        set_windows_timer_resolution(False)
        if glfw is not None and window is not None:
            glfw.destroy_window(window)
            glfw.terminate()


def find_top_local_extrema(
    samples: Sequence[Tuple[int, float, float]], kind: str, top_n: int
) -> List[Tuple[int, float, float]]:
    extrema: List[Tuple[int, float, float]] = []
    if len(samples) < 3:
        return extrema

    for index in range(1, len(samples) - 1):
        previous_value = samples[index - 1][2]
        current_value = samples[index][2]
        next_value = samples[index + 1][2]
        if kind == "max" and current_value > previous_value and current_value > next_value:
            extrema.append(samples[index])
        elif kind == "min" and current_value < previous_value and current_value < next_value:
            extrema.append(samples[index])

    reverse = kind == "max"
    extrema.sort(key=lambda item: item[2], reverse=reverse)
    return extrema[:top_n]


def create_max2_summary(serial_csv: str, summary_csv: str) -> None:
    groups: Dict[Tuple[str, str], List[Tuple[int, float, float]]] = {}

    with open(serial_csv, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("parse_error"):
                continue
            try:
                sample_index = int(row["sample_index"])
                experiment_time_s = float(row["experiment_time_s"])
            except Exception:
                continue

            phase = row.get("phase_name", "")
            for channel_name in ("ch1", "ch2", "ch3"):
                value_text = row.get(channel_name, "")
                if value_text == "":
                    continue
                try:
                    value = float(value_text)
                except ValueError:
                    continue
                groups.setdefault((phase, channel_name), []).append(
                    (sample_index, experiment_time_s, value)
                )

    with open(summary_csv, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "phase_name",
                "channel",
                "kind",
                "rank",
                "sample_index",
                "experiment_time_s",
                "value",
            ]
        )
        for (phase, channel), samples in sorted(groups.items()):
            for kind in ("max", "min"):
                extrema = find_top_local_extrema(samples, kind=kind, top_n=2)
                for rank, (sample_index, experiment_time_s, value) in enumerate(extrema, 1):
                    writer.writerow(
                        [
                            phase,
                            channel,
                            kind,
                            rank,
                            sample_index,
                            experiment_time_s,
                            value,
                        ]
                    )


def build_center_stimulus(
    frequency: float,
    radius: float,
    start_on: bool,
) -> Tuple[StimulusSpec, ...]:
    safe_frequency_name = str(frequency).replace(".", "_").replace("-", "m")
    return (
        StimulusSpec(
            name=f"stimulus_center_{safe_frequency_name}hz",
            frequency_hz=frequency,
            position=(0.0, 0.0),
            radius=radius,
            color=(1.0, 1.0, 1.0),
            start_on=start_on,
        ),
    )


def make_run_files(run_dir: Path) -> RunFiles:
    return RunFiles(
        metadata_json=str(run_dir / "metadata.json"),
        events_csv=str(run_dir / "events.csv"),
        frames_csv=str(run_dir / "frames.csv"),
        serial_csv=str(run_dir / "serial_samples.csv"),
        max2_summary_csv=str(run_dir / "max2_summary.csv"),
    )


def write_metadata(config: ExperimentConfig, files: RunFiles) -> None:
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "script": Path(__file__).name,
        "purpose": "offline_max2_parallel_measurement",
        "config": asdict(config),
        "files": asdict(files),
        "phase_codes": {str(int(phase)): name for phase, name in PHASE_NAMES.items()},
        "serial_formats": [
            "ch1",
            "ch1,ch2,ch3",
        ],
    }
    with open(files.metadata_json, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)


def build_shared_state(ctx: Any) -> SharedState:
    return SharedState(
        start_event=ctx.Event(),
        stop_event=ctx.Event(),
        serial_ready_event=ctx.Event(),
        phase_code=ctx.Value("i", int(Phase.IDLE)),
        frame_index=ctx.Value("i", -1),
        frame_time_ns=ctx.Value("q", 0),
        stimulus_active=ctx.Value("i", 0),
        stimulus_on_mask=ctx.Value("i", 0),
        visual_start_ns=ctx.Value("q", 0),
    )


def get_queued_error(error_queue: Any) -> Optional[str]:
    try:
        return error_queue.get_nowait()
    except queue.Empty:
        return None


def run(config: ExperimentConfig, files: RunFiles) -> None:
    ctx = multiprocessing.get_context("spawn")
    shared = build_shared_state(ctx)
    error_queue = ctx.Queue()

    serial_process = ctx.Process(
        target=serial_worker,
        args=(config, shared, files, error_queue),
        name="serial_worker",
    )
    serial_process.start()

    if not shared.serial_ready_event.wait(timeout=config.ready_timeout_sec):
        shared.stop_event.set()
        serial_process.join(timeout=2.0)
        raise RuntimeError(
            f"Serial worker did not become ready within {config.ready_timeout_sec} seconds."
        )

    queued_error = get_queued_error(error_queue)
    if queued_error is not None:
        shared.stop_event.set()
        serial_process.join(timeout=2.0)
        raise RuntimeError(queued_error)

    try:
        run_visual_experiment(config, shared, files)
    finally:
        shared.stop_event.set()
        serial_process.join(timeout=5.0)
        if serial_process.is_alive():
            serial_process.terminate()
            serial_process.join(timeout=2.0)

    queued_error = get_queued_error(error_queue)
    if queued_error is not None:
        raise RuntimeError(queued_error)

    if config.make_max2_summary:
        create_max2_summary(files.serial_csv, files.max2_summary_csv)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline MAX2 measurement: 10 s fixation, 3 s centered flicker, "
            "10 s fixation with VSync frame logs and serial timestamps."
        )
    )
    parser.add_argument("--com", default=DEFAULT_COM_PORT, help="Serial port, e.g. COM8.")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument(
        "--channel-mode",
        choices=("auto", "one", "three"),
        default="auto",
        help="Serial line format. auto accepts either 'ch1' or 'ch1,ch2,ch3'.",
    )
    parser.add_argument("--pre-sec", type=float, default=10.0)
    parser.add_argument("--stim-sec", type=float, default=3.0)
    parser.add_argument(
        "--stim-cycles",
        type=float,
        default=None,
        help="Stimulus duration by flicker cycles. If set, this overrides --stim-sec.",
    )
    parser.add_argument("--post-sec", type=float, default=10.0)
    parser.add_argument(
        "--frequency",
        type=float,
        default=7.5,
        help="Flicker frequency for the single centered stimulus.",
    )
    parser.add_argument(
        "--stimulus-start",
        choices=("on", "off"),
        default="on",
        help="Initial state of the flicker stimulus.",
    )
    parser.add_argument("--refresh-rate", type=float, default=None)
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--monitor-index", type=int, default=0)
    parser.add_argument("--window-width", type=int, default=1280)
    parser.add_argument("--window-height", type=int, default=720)
    parser.add_argument("--fixation-radius", type=float, default=0.025)
    parser.add_argument("--stimulus-radius", type=float, default=0.22)
    parser.add_argument("--show-fixation-during-stimulus", action="store_true")
    parser.add_argument("--serial-timeout-sec", type=float, default=0.001)
    parser.add_argument("--ready-timeout-sec", type=float, default=15.0)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--skip-max2-summary", action="store_true")
    parser.add_argument("--list-ports", action="store_true")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> Tuple[ExperimentConfig, RunFiles]:
    if args.output_dir is None:
        base_output_dir = Path(__file__).resolve().parent / "measurement_data"
    else:
        base_output_dir = Path(args.output_dir)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base_output_dir / f"max2_parallel_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    files = make_run_files(run_dir)

    if args.frequency <= 0:
        raise ValueError("--frequency must be greater than 0.")

    stimulus_sec = args.stim_sec
    stimulus_cycles = args.stim_cycles
    if stimulus_cycles is not None:
        if stimulus_cycles <= 0:
            raise ValueError("--stim-cycles must be greater than 0.")
        stimulus_sec = stimulus_cycles / args.frequency

    stimuli = build_center_stimulus(
        frequency=args.frequency,
        radius=args.stimulus_radius,
        start_on=args.stimulus_start == "on",
    )
    config = ExperimentConfig(
        com_port=args.com,
        baudrate=args.baudrate,
        channel_mode=args.channel_mode,
        pre_fixation_sec=args.pre_sec,
        stimulus_sec=stimulus_sec,
        stimulus_cycles=stimulus_cycles,
        post_fixation_sec=args.post_sec,
        refresh_rate_override=args.refresh_rate,
        fullscreen=not args.windowed,
        monitor_index=args.monitor_index,
        window_width=args.window_width,
        window_height=args.window_height,
        fixation_radius=args.fixation_radius,
        stimulus_radius=args.stimulus_radius,
        show_fixation_during_stimulus=args.show_fixation_during_stimulus,
        run_dir=str(run_dir),
        serial_timeout_sec=args.serial_timeout_sec,
        ready_timeout_sec=args.ready_timeout_sec,
        make_max2_summary=not args.skip_max2_summary,
        stimuli=stimuli,
    )
    return config, files


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.list_ports:
        list_serial_ports()
        return 0

    config, files = config_from_args(args)
    write_metadata(config, files)

    print("Run directory:")
    print(f"  {config.run_dir}")
    print("Serial:")
    print(f"  port={config.com_port}, baudrate={config.baudrate}, channel_mode={config.channel_mode}")
    print("Stimulus:")
    for stimulus in config.stimuli:
        print(f"  {stimulus.name}: {stimulus.frequency_hz} Hz at {stimulus.position}")

    run(config, files)

    print("Done.")
    print(f"  serial: {files.serial_csv}")
    print(f"  frames: {files.frames_csv}")
    print(f"  events: {files.events_csv}")
    if config.make_max2_summary:
        print(f"  max2 summary: {files.max2_summary_csv}")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
