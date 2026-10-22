from pathlib import Path
import copy
import math
import os
import threading
import time
import traceback
import re
import socket
import uuid
from functools import lru_cache
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal


@dataclass
class Detection:
    box: tuple
    person_score: float
    face: Optional[tuple] = None
    face_feature: Optional[np.ndarray] = None
    appearance_feature: Optional[np.ndarray] = None
    face_side: str = 'UNKNOWN'
    head_dir: float = 0.0
    head_yaw_deg: float = 0.0
    head_yaw_measured: bool = False
    # Relative head-pitch estimate from facial landmarks. Positive means a
    # downward nod after per-track baseline calibration; never treated as proof.
    head_pitch_deg: float = 0.0
    head_pitch_measured: bool = False
    head_quality: float = 0.0
    mouth_score: float = 0.0
    mouth_motion: float = 0.0
    mouth_activity: float = 0.0
    face_y_ratio: float = 0.0
    object_types: tuple = ()
    object_scores: tuple = ()
    lower_motion: float = 0.0
    hand_motion: float = 0.0
    task_activity: float = 0.0

def _resolve_head_yaw(side, fallback_yaw, landmark_yaw):
    """Fuse face-profile classification and landmark yaw without erasing a clear turn.

    Haar profile-face detection provides a coarse LEFT/RIGHT cue; YuNet landmarks
    provide a finer estimate but can be inaccurate when one eye is occluded in a
    side profile. A confirmed profile classification therefore supplies a measured
    directional cue when landmarks are absent or implausibly close to frontal.
    BehaviorEngine's temporal confirmation remains responsible for avoiding
    one-frame false alerts.
    """
    normalized_side = str(side or "UNKNOWN").strip().upper()
    try:
        fallback = float(fallback_yaw or 0.0)
        if not math.isfinite(fallback):
            fallback = 0.0
    except (TypeError, ValueError):
        fallback = 0.0

    try:
        landmark = float(landmark_yaw) if landmark_yaw is not None else None
        if landmark is not None and not math.isfinite(landmark):
            landmark = None
    except (TypeError, ValueError):
        landmark = None

    if normalized_side in {"LEFT", "RIGHT"}:
        direction = -1.0 if normalized_side == "LEFT" else 1.0
        # A profile classifier is already a strong categorical pose cue. If the
        # landmarks disagree or return a weak near-frontal angle, do not suppress it.
        magnitude = max(42.0, abs(landmark)) if landmark is not None else 45.0
        magnitude = min(65.0, magnitude)
        return direction * magnitude, True

    if landmark is not None:
        return max(-65.0, min(65.0, landmark)), True

    # Without either valid landmarks or a clear profile classification, do not
    # treat a coarse default yaw as a measured turn.
    return fallback, False


def _extract_ipv4_addresses(value: str):
    if not value:
        return []
    found = []
    for match in re.findall(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", value):
        try:
            parts = [int(x) for x in match.split('.')]
        except ValueError:
            continue
        if all(0 <= p <= 255 for p in parts) and match not in found:
            found.append(match)
    return found


def _local_ipv4_addresses():
    """Return usable IPv4 addresses assigned to this Windows PC."""
    values = []
    try:
        host = socket.gethostname()
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
        for item in infos:
            ip = str(item[4][0])
            if ip.startswith('127.') or ip.startswith('169.254.'):
                continue
            if ip not in values:
                values.append(ip)
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.25)
        s.connect(('192.0.2.1', 9))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith(('127.', '169.254.')) and ip not in values:
            values.insert(0, ip)
    except Exception:
        pass
    return values


def _scan_tcp_port_on_local_subnets(port: int, timeout_per_host: float = 0.10, max_results: int = 16):
    """Bounded /24 scan on networks actually attached to this PC."""
    import concurrent.futures
    hosts = []
    seen = set()
    for local_ip in _local_ipv4_addresses():
        parts = local_ip.split('.')
        if len(parts) != 4:
            continue
        prefix = '.'.join(parts[:3])
        for last in range(1, 255):
            ip = f'{prefix}.{last}'
            if ip == local_ip or ip in seen:
                continue
            seen.add(ip)
            hosts.append(ip)

    if not hosts:
        return []

    def probe(ip):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(float(timeout_per_host))
        try:
            return ip if sock.connect_ex((ip, int(port))) == 0 else None
        except OSError:
            return None
        finally:
            try:
                sock.close()
            except Exception:
                pass

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64, thread_name_prefix='GodEyesNetProbe') as pool:
        future_map = {pool.submit(probe, ip): ip for ip in hosts}
        for future in concurrent.futures.as_completed(future_map):
            try:
                ip = future.result()
            except Exception:
                ip = None
            if ip:
                results.append(ip)
                if len(results) >= int(max_results):
                    break
    return results


def discover_onvif_cameras(timeout: float = 2.0, max_results: int = 10):
    """Discover ONVIF cameras using multicast first, then local port scanning.

    Windows firewalls, AP isolation and some Wi-Fi routers can block WS-Discovery
    multicast. Tapo publishes ONVIF on TCP 2020, so the bounded local scan is a
    reliable fallback without exposing an IP-address field to the teacher.
    """
    results = []
    seen = set()

    message_id = f"uuid:{uuid.uuid4()}"
    probe = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        '<e:Header>'
        f'<w:MessageID>{message_id}</w:MessageID>'
        '<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
        '<w:Action mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
        '</e:Header>'
        '<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>'
        '</e:Envelope>'
    ).encode('utf-8')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.25)
        try:
            sock.sendto(probe, ('239.255.255.250', 3702))
        except OSError:
            pass
        end_time = time.time() + max(0.4, float(timeout))
        while time.time() < end_time and len(results) < int(max_results):
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            payload = data.decode('utf-8', errors='ignore')
            candidates = _extract_ipv4_addresses(payload)
            if not candidates and addr:
                candidates = [addr[0]]
            for ip in candidates:
                if ip.startswith(('127.', '169.254.')) or ip in seen:
                    continue
                seen.add(ip)
                results.append({'ip': ip, 'onvif_port': 2020, 'discovery': 'ws-discovery'})
                if len(results) >= int(max_results):
                    break
    finally:
        try:
            sock.close()
        except Exception:
            pass

    if len(results) < int(max_results):
        for ip in _scan_tcp_port_on_local_subnets(2020, timeout_per_host=0.10, max_results=max_results):
            if ip not in seen:
                seen.add(ip)
                results.append({'ip': ip, 'onvif_port': 2020, 'discovery': 'local-port-scan'})
                if len(results) >= int(max_results):
                    break
    return results


def probe_rtsp_camera(ip: str, username: str, password: str, stream: str = 'stream1', port: int = 554, timeout_ms: int = 2500):
    """Open one RTSP camera with the supplied camera-account credentials.

    Returns metadata when one frame can be decoded, otherwise None.
    """
    ip = str(ip or '').strip()
    username = str(username or '').strip()
    password = str(password or '')
    stream = str(stream or 'stream1').strip().lower()
    if not ip or not username or not password or stream not in ('stream1', 'stream2'):
        return None
    from urllib.parse import quote as _quote
    url = f'rtsp://{_quote(username, safe="")}:{_quote(password, safe="")}@{ip}:{int(port)}/{stream}'
    old_options = os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS')
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000'
    cap = None
    try:
        try:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout_ms),
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout_ms),
            ])
        except Exception:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG if hasattr(cv2, 'CAP_FFMPEG') else cv2.CAP_ANY)
        if cap is None or not cap.isOpened():
            return None
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        ok, frame = cap.read()
        if not ok or frame is None or frame.size == 0:
            return None
        h, w = frame.shape[:2]
        return {'ip': ip, 'port': int(port), 'stream': stream, 'width': int(w), 'height': int(h)}
    except Exception:
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if old_options is None:
            os.environ.pop('OPENCV_FFMPEG_CAPTURE_OPTIONS', None)
        else:
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = old_options


class CameraWorker(QThread):

    frame_ready = Signal(object)
    state = Signal(str)

    def __init__(self, camera_index=0, source_type='WEBCAM', rtsp_url=''):
        super().__init__()
        self.camera_index = int(camera_index)
        self.source_type = str(source_type or 'WEBCAM').upper()
        self.rtsp_url = str(rtsp_url or '').strip()
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self.cap = None
        self.backend_name = ''
        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0
        self._latest_frame = None
        self._latest_frame_time = 0.0
        self._last_ui_emit = 0.0

    def get_latest_frame(self, copy=True):
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy() if copy else self._latest_frame

    def latest_age(self):
        with self._lock:
            if not self._latest_frame_time:
                return None
            return max(0.0, time.time() - self._latest_frame_time)

    def _release(self):
        with self._lock:
            cap = self.cap
            self.cap = None
            self._latest_frame = None
            self._latest_frame_time = 0.0
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _open_webcam(self):
        candidates = [
            (cv2.CAP_DSHOW, 'DIRECTSHOW'),
            (cv2.CAP_MSMF, 'MEDIA FOUNDATION'),
            (cv2.CAP_ANY, 'AUTO'),
        ]
        for backend, name in candidates:
            if self._stop_event.is_set():
                return None
            cap = None
            try:
                cap = cv2.VideoCapture(self.camera_index, backend)
                if not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    continue
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                for key, value in [
                    (cv2.CAP_PROP_FRAME_WIDTH, 1280),
                    (cv2.CAP_PROP_FRAME_HEIGHT, 720),
                    (cv2.CAP_PROP_FPS, 30),
                ]:
                    try:
                        cap.set(key, value)
                    except Exception:
                        pass
                try:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                except Exception:
                    pass
                ok, frame = cap.read()
                if ok and frame is not None and frame.size:
                    self.actual_height, self.actual_width = frame.shape[:2]
                    try:
                        self.actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    except Exception:
                        self.actual_fps = 0.0
                    self.backend_name = name
                    self._set_latest(frame)
                    return cap
            except Exception:
                pass
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        return None

    def _open_rtsp(self):
        if not self.rtsp_url:
            self.state.emit('CAMERA ERROR • RTSP URL MISSING')
            return None
        cap = None
        # FFmpeg low-latency hints. They are process-local environment settings
        # used by OpenCV's FFmpeg backend when it is available.
        old_options = os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS')
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = (
            'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|max_delay;500000'
        )
        try:
            backends = []
            if hasattr(cv2, 'CAP_FFMPEG'):
                backends.append((cv2.CAP_FFMPEG, 'FFMPEG'))
            backends.append((cv2.CAP_ANY, 'AUTO'))
            for backend, name in backends:
                if self._stop_event.is_set():
                    return None
                try:
                    try:
                        if name == 'FFMPEG':
                            cap = cv2.VideoCapture(
                                self.rtsp_url,
                                backend,
                                [
                                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
                                ],
                            )
                        else:
                            cap = cv2.VideoCapture(self.rtsp_url, backend)
                    except Exception:
                        cap = cv2.VideoCapture(self.rtsp_url, backend)
                    if not cap.isOpened():
                        if cap is not None:
                            cap.release()
                        cap = None
                        continue
                    try:
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass
                    try:
                        cap.set(cv2.CAP_PROP_FPS, 20)
                    except Exception:
                        pass
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size:
                        self.actual_height, self.actual_width = frame.shape[:2]
                        try:
                            self.actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                        except Exception:
                            self.actual_fps = 0.0
                        self.backend_name = f'RTSP {name}'
                        self._set_latest(frame)
                        return cap
                except Exception:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass
                        cap = None
            return None
        finally:
            if old_options is None:
                os.environ.pop('OPENCV_FFMPEG_CAPTURE_OPTIONS', None)
            else:
                os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = old_options

    def _set_latest(self, frame):
        if frame is None or getattr(frame, 'size', 0) == 0:
            return
        with self._lock:
            self._latest_frame = frame.copy()
            self._latest_frame_time = time.time()

    def _open(self):
        if self.source_type in {'RTSP', 'WIFI_CAMERA', 'TAPO', 'TAPO_C230', 'TAPO_C230_RTSP'}:
            return self._open_rtsp()
        return self._open_webcam()

    def _source_label(self):
        if self.source_type in {'RTSP', 'WIFI_CAMERA', 'TAPO', 'TAPO_C230', 'TAPO_C230_RTSP'}:
            return 'WI-FI CAMERA'
        if self.source_type in {'USB', 'USB_WEBCAM'}:
            return f'USB WEBCAM #{self.camera_index}'
        return f'WEBCAM #{self.camera_index}'

    def run(self):
        self._stop_event.clear()
        self._release()
        self.state.emit(f'CONNECTING • {self._source_label()}')
        reconnect_delay = 0.25
        while not self._stop_event.is_set():
            cap = self._open()
            if cap is None:
                if self._stop_event.is_set():
                    break
                self.state.emit(f'CAMERA ERROR • CANNOT OPEN • {self._source_label()}')
                time.sleep(min(2.0, reconnect_delay))
                reconnect_delay = min(2.0, reconnect_delay * 1.5)
                continue
            reconnect_delay = 0.25
            with self._lock:
                self.cap = cap
            self.state.emit(
                f'CONNECTED • {self._source_label()} • '
                f'{self.actual_width}x{self.actual_height} • {self.backend_name}'
            )
            bad_reads = 0
            self._last_ui_emit = 0.0
            while not self._stop_event.is_set():
                with self._lock:
                    active_cap = self.cap
                if active_cap is None:
                    break
                try:
                    ok, frame = active_cap.read()
                except Exception:
                    ok, frame = False, None
                if not ok or frame is None or frame.size == 0:
                    bad_reads += 1
                    if bad_reads >= 5:
                        self.state.emit(f'CAMERA WARNING • {self._source_label()} • RECONNECTING')
                        break
                    time.sleep(0.02)
                    continue
                bad_reads = 0
                self._set_latest(frame)
                now = time.time()
                # UI preview is capped; the capture loop still drains the RTSP
                # stream as quickly as OpenCV delivers it, keeping only the newest frame.
                if now - self._last_ui_emit >= 0.05:
                    self._last_ui_emit = now
                    self.frame_ready.emit(frame)
            self._release()
            if not self._stop_event.is_set():
                self.state.emit(f'RECONNECTING • {self._source_label()}')
                time.sleep(reconnect_delay)
        self._release()
        self.state.emit('DISCONNECTED')

    def stop(self):
        self._stop_event.set()
        self._release()


class PersonTrack:
    def __init__(self, track_id, detection, now=None):
        now = time.time() if now is None else now
        self.track_id = int(track_id)
        self.box = tuple(int(v) for v in detection.box)
        self.prev_box = self.box
        self.hits = 1
        self.missed = 0
        self.age = 1
        self.last_seen = now
        self.first_seen = now
        self.lost_since = None
        self.person_score = float(detection.person_score)
        self.face = detection.face
        self.face_feature = detection.face_feature
        self.face_feature_current = bool(detection.face is not None and detection.face_feature is not None)
        self.appearance_feature = detection.appearance_feature
        self.face_side = detection.face_side
        self.head_dir = float(detection.head_dir)
        self.head_yaw_deg = float(detection.head_yaw_deg)
        self.head_yaw_measured = bool(getattr(detection, 'head_yaw_measured', False))
        self.head_pitch_deg = float(getattr(detection, 'head_pitch_deg', 0.0))
        self.head_pitch_measured = bool(getattr(detection, 'head_pitch_measured', False))
        self.head_quality = float(detection.head_quality)
        self.mouth_score = float(detection.mouth_score)
        self.mouth_motion = float(detection.mouth_motion)
        self.mouth_activity = float(detection.mouth_activity)
        self.face_y_ratio = float(detection.face_y_ratio)
        self.object_types = tuple(detection.object_types or ())
        self.object_scores = tuple(float(v) for v in (detection.object_scores or ()))
        self.lower_motion = float(detection.lower_motion)
        self.hand_motion = float(detection.hand_motion)
        self.task_activity = float(detection.task_activity)
        self.center_history = [self.center]
        self.height_history = [self.box[3]]
        self.width_history = [self.box[2]]

    @property
    def center(self):
        x, y, w, h = self.box
        return (x + 0.5 * w, y + 0.5 * h)

    @property
    def normalized_center(self):
        return self.center

    @property
    def stability(self):
        return min(1.0, self.hits / 6.0) * max(0.0, 1.0 - min(1.0, self.missed / 12.0))

    @property
    def confirmed(self):
        return self.hits >= 2 and self.age >= 2

    @property
    def visible(self):
        return self.missed == 0

    def update(self, detection, now=None):
        now = time.time() if now is None else now
        self.prev_box = self.box
        self.box = tuple(int(v) for v in detection.box)
        self.hits += 1
        self.missed = 0
        self.age += 1
        self.last_seen = now
        self.lost_since = None
        self.person_score = 0.80 * self.person_score + 0.20 * float(detection.person_score)
        if detection.face is not None:
            self.face = detection.face
        else:
            self.face = None
        self.face_feature_current = bool(detection.face is not None and detection.face_feature is not None)
        if detection.face_feature is not None:
            self.face_feature = detection.face_feature
        if detection.appearance_feature is not None:
            self.appearance_feature = detection.appearance_feature
        self.face_side = detection.face_side
        self.head_dir = float(detection.head_dir)
        self.head_yaw_deg = float(detection.head_yaw_deg)
        self.head_yaw_measured = bool(getattr(detection, 'head_yaw_measured', False))
        self.head_pitch_deg = float(getattr(detection, 'head_pitch_deg', 0.0))
        self.head_pitch_measured = bool(getattr(detection, 'head_pitch_measured', False))
        self.head_quality = float(detection.head_quality)
        self.mouth_score = float(detection.mouth_score)
        self.mouth_motion = float(detection.mouth_motion)
        self.mouth_activity = float(detection.mouth_activity)
        self.face_y_ratio = float(detection.face_y_ratio)
        self.object_types = tuple(detection.object_types or ())
        self.object_scores = tuple(float(v) for v in (detection.object_scores or ()))
        self.lower_motion = float(detection.lower_motion)
        self.hand_motion = float(detection.hand_motion)
        self.task_activity = float(detection.task_activity)
        self.center_history.append(self.center)
        self.height_history.append(self.box[3])
        self.width_history.append(self.box[2])
        if len(self.center_history) > 20:
            self.center_history.pop(0)
        if len(self.height_history) > 20:
            self.height_history.pop(0)
        if len(self.width_history) > 20:
            self.width_history.pop(0)

    def miss(self, now=None):
        now = time.time() if now is None else now
        if self.lost_since is None:
            self.lost_since = now
        self.missed += 1
        self.age += 1
        self.last_seen = now
        # The track may remain alive briefly for continuity, but the previous
        # frame's face/pose must not masquerade as a fresh observation.
        self.face = None
        self.face_feature_current = False
        self.head_yaw_measured = False
        self.head_pitch_measured = False
        self.head_quality = 0.0
        self.mouth_score = 0.0
        self.mouth_motion = 0.0
        self.mouth_activity = 0.0


class PersonTracker:
    def __init__(self):
        self.tracks = {}
        self.next_id = 1
        # Keep lost tracks for a bounded time so temporary occlusion or a brief
        # frame exit does not immediately discard the identity lock. The frame cap
        # is a second guard for unusual clocks / low-FPS streams.
        self.max_missed = 180
        self.max_lost_seconds = 6.0
        self.max_assignment_distance = 1.55
        self.min_assignment_score = 0.27

    @staticmethod
    def _iou(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax2 = ax + aw
        ay2 = ay + ah
        bx2 = bx + bw
        by2 = by + bh
        ix1 = max(ax, bx)
        iy1 = max(ay, by)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        inter = iw * ih
        union = max(1, aw * ah + bw * bh - inter)
        return inter / union

    @staticmethod
    def _center_distance(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        acx = ax + aw * 0.5
        acy = ay + ah * 0.5
        bcx = bx + bw * 0.5
        bcy = by + bh * 0.5
        d = math.hypot(acx - bcx, acy - bcy)
        scale = max(60.0, 0.45 * (ah + bh))
        return min(2.0, d / scale)

    @staticmethod
    def _appearance_distance(a, b):
        if a is None or b is None:
            return 1.0
        try:
            aa = np.asarray(a, dtype=np.float32).ravel()
            bb = np.asarray(b, dtype=np.float32).ravel()
            if aa.size != bb.size or aa.size == 0:
                return 1.0
            denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
            if denom <= 1e-8:
                return 1.0
            similarity = float(np.dot(aa, bb) / denom)
            similarity = max(-1.0, min(1.0, similarity))
            return float(1.0 - 0.5 * (similarity + 1.0))
        except Exception:
            return 1.0

    def _pair_score(self, track, detection):
        iou = self._iou(track.box, detection.box)
        distance = self._center_distance(track.box, detection.box)
        appearance = self._appearance_distance(track.appearance_feature, detection.appearance_feature)
        velocity_bonus = 0.0
        if len(track.center_history) >= 2:
            px, py = track.center_history[-2]
            cx, cy = track.center_history[-1]
            dx = cx - px
            dy = cy - py
            pred = (cx + dx, cy + dy)
            nx, ny = PersonTracker._center_from_box(detection.box)
            pd = math.hypot(pred[0] - nx, pred[1] - ny)
            velocity_bonus = max(0.0, 1.0 - min(1.0, pd / max(80.0, track.box[3] * 0.8)))
        score = 0.60 * iou + 0.20 * (1.0 - min(1.0, distance)) + 0.15 * (1.0 - appearance) + 0.05 * velocity_bonus
        if iou < 0.03 and distance > self.max_assignment_distance and appearance > 0.72:
            return 0.0
        return score

    @staticmethod
    def _center_from_box(box):
        x, y, w, h = box
        return x + w * 0.5, y + h * 0.5

    def update(self, detections, now=None):
        now = time.time() if now is None else now
        detections = list(detections or [])
        active_ids = list(self.tracks.keys())
        pairs = []
        for tid in active_ids:
            track = self.tracks[tid]
            for di, detection in enumerate(detections):
                score = self._pair_score(track, detection)
                if score <= 0.0:
                    continue
                iou = self._iou(track.box, detection.box)
                dist = self._center_distance(track.box, detection.box)
                if iou >= 0.03 or dist <= self.max_assignment_distance:
                    pairs.append((score, tid, di))
        pairs.sort(key=lambda item: item[0], reverse=True)
        matched_tracks = set()
        matched_detections = set()
        for score, tid, di in pairs:
            if tid in matched_tracks or di in matched_detections:
                continue
            if score < self.min_assignment_score:
                continue
            self.tracks[tid].update(detections[di], now)
            matched_tracks.add(tid)
            matched_detections.add(di)
        for tid in list(active_ids):
            if tid not in matched_tracks and tid in self.tracks:
                self.tracks[tid].miss(now)
                lost_since = self.tracks[tid].lost_since
                lost_for = max(0.0, now - float(lost_since)) if lost_since is not None else 0.0
                if self.tracks[tid].missed > self.max_missed or lost_for > self.max_lost_seconds:
                    del self.tracks[tid]
        for di, detection in enumerate(detections):
            if di in matched_detections:
                continue
            tid = self.next_id
            self.next_id += 1
            self.tracks[tid] = PersonTrack(tid, detection, now)
        return list(self.tracks.values())

    def confirmed_visible(self):
        return [t for t in self.tracks.values() if t.confirmed and t.visible]

    def reset(self):
        self.tracks.clear()
        self.next_id = 1


class IdentityLock:
    # GOD EYES PERSISTENT FACE-ID TRACK LOCK V21: locked track identity survives face loss; enrollment embedding is immutable.
    """
    Identity manager for classroom monitoring.

    Important design rule:
    - Face ID is used when a usable face embedding is available.
    - Once a person has been identified, the identity is held while the same
      person track continues even if the face temporarily disappears.
    - If the tracker creates a new track after a temporary posture/occlusion
      change, a conservative appearance + position re-identification step can
      recover the previously known student.

    This prevents a student who bends their head down from immediately becoming
    UNKNOWN, without pretending that a hidden face can be re-identified from
    the face model alone.
    """

    def __init__(self):
        self.locked = False
        self.track_to_student = {}
        self.student_profiles = {}
        self.student_to_track = {}
        # New identities need temporally consistent face matches before assignment.
        self.pending_face_matches = {}
        self.next_student = 1

        # Legacy/local track-reidentification settings.
        self.max_match_distance = 0.78
        self.min_reid_score = 0.49

        # Server Face ID roster.
        self.server_roster = {}
        # Refreshed by AIWorker once per inference before resolving any track.
        # Prevents a lost/duplicate track from stealing an identity from a visible track.
        self.active_track_status = {}
        self.server_threshold = 0.363
        self.server_margin = 0.035

        # Temporary identity continuity when face disappears.
        self.hold_identity_seconds = 6.0
        self.reid_threshold = 0.62
        self.reid_margin = 0.075
        self.reid_appearance_max_distance = 0.55
        self.reid_position_max_distance = 0.55

    @staticmethod
    def _cosine_similarity(a, b):
        if a is None or b is None:
            return -1.0
        try:
            aa = np.asarray(a, dtype=np.float32).ravel()
            bb = np.asarray(b, dtype=np.float32).ravel()
            if aa.size == 0 or aa.size != bb.size:
                return -1.0
            denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
            if denom <= 1e-8:
                return -1.0
            return float(np.dot(aa, bb) / denom)
        except Exception:
            return -1.0

    @staticmethod
    def _display_label(item):
        code = str(item.get('student_code') or '').strip()
        name = str(item.get('full_name') or '').strip()
        if code and name:
            return f'{code} • {name}'
        return name or code or f"STUDENT {item.get('id', '')}"

    def set_server_roster(self, roster):
        parsed = {}
        for item in list(roster or []):
            try:
                if not item.get('ready'):
                    continue
                raw = item.get('face_embedding')
                feature = np.asarray(raw, dtype=np.float32).ravel()
                if feature.size == 0:
                    continue
                norm = float(np.linalg.norm(feature))
                if norm <= 1e-8:
                    continue
                feature = feature / norm
                label = self._display_label(item)
                parsed[label] = {
                    'student_id': int(item.get('id') or 0),
                    'student_code': str(item.get('student_code') or ''),
                    'full_name': str(item.get('full_name') or ''),
                    'face_feature': feature,
                }
            except Exception:
                continue
        self.server_roster = parsed
        self.locked = False
        self.track_to_student.clear()
        self.student_profiles.clear()
        self.student_to_track.clear()
        self.pending_face_matches.clear()

    def student_id_for_label(self, label):
        profile = self.server_roster.get(str(label))
        return int(profile.get('student_id', 0)) if profile else 0

    @staticmethod
    def _feature_distance(a, b):
        if a is None or b is None:
            return 1.0
        try:
            aa = np.asarray(a, dtype=np.float32).ravel()
            bb = np.asarray(b, dtype=np.float32).ravel()
            if aa.size != bb.size or aa.size == 0:
                return 1.0
            denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
            if denom <= 1e-8:
                return 1.0
            sim = float(np.dot(aa, bb) / denom)
            sim = max(-1.0, min(1.0, sim))
            return 1.0 - 0.5 * (sim + 1.0)
        except Exception:
            return 1.0

    @staticmethod
    def _position_distance(a, b, frame_size=None):
        if a is None or b is None:
            return 1.0
        ax, ay = a
        bx, by = b
        if frame_size:
            fw, fh = frame_size
            dx = (ax - bx) / max(1.0, float(fw))
            dy = (ay - by) / max(1.0, float(fh))
            return float(min(1.0, math.hypot(dx, dy) * 2.2))
        return float(min(1.0, math.hypot(ax - bx, ay - by) / 500.0))

    def _order_tracks(self, tracks):
        ordered = [t for t in tracks if t.confirmed and t.visible]
        ordered.sort(key=lambda t: (round(t.center[1] / 60.0), t.center[0]))
        return ordered

    def lock(self, tracks, frame_size=None):
        visible = self._order_tracks(list(tracks))
        self.track_to_student.clear()
        self.student_profiles.clear()
        self.student_to_track.clear()
        self.pending_face_matches.clear()
        self.next_student = 1
        now = time.time()

        if self.server_roster:
            assignments = []
            for track in visible:
                if (track.face_feature is None or track.face is None
                        or not getattr(track, 'face_feature_current', False)):
                    continue
                best_label = None
                best_score = -1.0
                second_score = -1.0
                for label, item in self.server_roster.items():
                    score = self._cosine_similarity(
                        track.face_feature,
                        item.get('face_feature')
                    )
                    if score > best_score:
                        second_score = best_score
                        best_score = score
                        best_label = label
                    elif score > second_score:
                        second_score = score
                if (
                    best_label is not None
                    and best_score >= self.server_threshold
                    and best_score - max(-1.0, second_score) >= self.server_margin
                ):
                    assignments.append((best_score, track, best_label))

            assignments.sort(key=lambda item: item[0], reverse=True)
            used = set()
            for score, track, label in assignments:
                if label in used or track.track_id in self.track_to_student:
                    continue
                used.add(label)
                self._bind_identity(label, track, frame_size, now)

        else:
            for track in visible:
                sid = f'HS {self.next_student:02d}'
                self.next_student += 1
                self._bind_identity(sid, track, frame_size, now)

        self.locked = bool(self.track_to_student or self.student_profiles)
        return self.mapping()

    def _bind_identity(self, sid, track, frame_size=None, now=None):
        now = time.time() if now is None else now
        self.track_to_student[int(track.track_id)] = sid
        self.student_to_track[sid] = int(track.track_id)

        roster_item = self.server_roster.get(sid) if self.server_roster else None
        server_embedding = None
        if roster_item is not None:
            server_embedding = roster_item.get('face_feature')

        # For enrolled students, the registered Server embedding is the immutable
        # identity anchor. Do not replace it with a side-view/occluded live frame.
        if server_embedding is not None:
            identity_anchor = np.asarray(server_embedding, dtype=np.float32).ravel().copy()
        else:
            live_feature = getattr(track, 'face_feature', None)
            identity_anchor = live_feature.copy() if live_feature is not None else None

        self.student_profiles[sid] = {
            'face_feature': identity_anchor,
            'appearance_feature': (
                track.appearance_feature.copy()
                if track.appearance_feature is not None else None
            ),
            'server_embedding': (
                np.asarray(server_embedding, dtype=np.float32).ravel().copy()
                if server_embedding is not None else None
            ),
            'center': track.center,
            'frame_size': frame_size,
            'last_seen': now,
            'last_face_seen': now if (getattr(track, 'face', None) is not None
                                      and getattr(track, 'face_feature_current', False)
                                      and getattr(track, 'face_feature', None) is not None) else 0.0,
            'track_id': int(track.track_id),
            'student_id': int(roster_item['student_id']) if roster_item else 0,
        }


    def _profile_score(self, track, profile, frame_size=None):
        fd = self._feature_distance(
            track.face_feature,
            profile.get('face_feature')
        )
        ad = self._feature_distance(
            track.appearance_feature,
            profile.get('appearance_feature')
        )
        pd = self._position_distance(
            track.center,
            profile.get('center'),
            frame_size or profile.get('frame_size')
        )
        score = 1.0 - (0.30 * fd + 0.48 * ad + 0.22 * pd)
        return max(0.0, min(1.0, score)), fd, ad, pd

    def _appearance_reid_score(self, track, profile, frame_size=None):
        ad = self._feature_distance(
            track.appearance_feature,            profile.get('appearance_feature')
        )
        pd = self._position_distance(
            track.center,
            profile.get('center'),
            frame_size or profile.get('frame_size')
        )

        # Conservative recovery: appearance and spatial continuity must both
        # be plausible. This is not treated as a fresh Face ID match.
        score = 1.0 - (0.70 * ad + 0.30 * pd)
        return max(0.0, min(1.0, score)), ad, pd

    def _refresh_profile(self, sid, track, frame_size=None, identity_source='TRACK_HOLD'):
        profile = self.student_profiles.get(sid)
        if profile is None:
            return

        now = time.time()
        has_fresh_face_embedding = bool(
            getattr(track, 'face_feature_current', getattr(track, 'face', None) is not None)
            and getattr(track, 'face', None) is not None
        )
        live_feature = getattr(track, 'face_feature', None) if has_fresh_face_embedding else None
        server_anchor = profile.get('server_embedding')

        if server_anchor is not None:
            # Preserve the registered embedding permanently. A turned, lowered,
            # blurred, or partially hidden face must never rewrite the enrollment.
            profile['face_feature'] = np.asarray(server_anchor, dtype=np.float32).ravel().copy()
            if live_feature is not None:
                similarity = self._cosine_similarity(live_feature, server_anchor)
                if similarity >= self.server_threshold:
                    profile['last_face_seen'] = now
        elif live_feature is not None:
            # Local-only identities retain a conservative, slow update path.
            old = profile.get('face_feature')
            if old is None:
                profile['face_feature'] = live_feature.copy()
            else:
                updated = 0.985 * old + 0.015 * live_feature
                norm = float(np.linalg.norm(updated))
                profile['face_feature'] = updated / norm if norm > 1e-8 else updated
            profile['last_face_seen'] = now

        appearance = getattr(track, 'appearance_feature', None)
        # Do not let an occluder, neighboring student, or a tracking jump slowly
        # overwrite a student's appearance anchor. Update it only on a fresh face
        # that agrees with the enrolled/locked face identity.
        appearance_is_confirmed = False
        if appearance is not None and live_feature is not None:
            face_anchor = server_anchor if server_anchor is not None else profile.get('face_feature')
            if face_anchor is not None:
                appearance_is_confirmed = (
                    self._cosine_similarity(live_feature, face_anchor) >= self.server_threshold
                )
            else:
                appearance_is_confirmed = False
        if appearance_is_confirmed:
            old_appearance = profile.get('appearance_feature')
            if old_appearance is None:
                profile['appearance_feature'] = appearance.copy()
            else:
                updated = 0.99 * old_appearance + 0.01 * appearance
                norm = float(np.linalg.norm(updated))
                profile['appearance_feature'] = updated / norm if norm > 1e-8 else updated

        profile['center'] = track.center
        profile['frame_size'] = frame_size or profile.get('frame_size')
        profile['last_seen'] = now
        profile['track_id'] = int(track.track_id)
        profile['identity_source'] = identity_source

        self.student_to_track[sid] = int(track.track_id)
        self.track_to_student[int(track.track_id)] = sid


    def _match_face_to_server(self, track):
        has_fresh_face_embedding = bool(
            getattr(track, 'face_feature_current', False) and getattr(track, 'face', None) is not None
        )
        if not self.server_roster or not has_fresh_face_embedding or track.face_feature is None:
            return None, -1.0, -1.0

        best_label = None
        best_score = -1.0
        second_score = -1.0

        for label, item in self.server_roster.items():
            assigned_track = self.student_to_track.get(label)
            if assigned_track is not None and assigned_track != track.track_id:
                # Do not steal an identity already held by another visible track.
                continue

            score = self._cosine_similarity(
                track.face_feature,
                item.get('face_feature')
            )

            if score > best_score:
                second_score = best_score
                best_score = score
                best_label = label
            elif score > second_score:
                second_score = score

        return best_label, best_score, second_score

    def _recover_from_profile(self, track, frame_size=None, allow_weak_face=False):
        """Recover a prior lock only when body appearance + geometry are strong.

        allow_weak_face is used only after the face matcher failed to produce a
        confident roster match. In that case face similarity is deliberately not
        used to score continuity because a side/back view is not a reliable face
        embedding. A confident conflicting Face ID is handled before this method.
        """
        if not self.student_profiles:
            return None, 0.0

        candidates = []
        face_feature = getattr(track, 'face_feature', None) if (
            getattr(track, 'face_feature_current', False) and getattr(track, 'face', None) is not None
        ) else None
        for sid, profile in self.student_profiles.items():
            previous_track = self.student_to_track.get(sid)
            if previous_track == int(track.track_id):
                continue

            # Never transfer a locked ID away from another currently visible track.
            previous_status = self.active_track_status.get(previous_track, {})
            if (previous_status.get('visible') and previous_status.get('confirmed')
                    and not previous_status.get('missed', 0)):
                continue

            if face_feature is not None and not allow_weak_face:
                continue

            appearance_distance = self._feature_distance(
                getattr(track, 'appearance_feature', None),
                profile.get('appearance_feature'),
            )
            position_distance = self._position_distance(
                track.center,
                profile.get('center'),
                frame_size or profile.get('frame_size'),
            )
            # Appearance is the continuity cue only after Face ID is unavailable
            # or too weak to identify this frame; position is supporting evidence.
            score = max(0.0, min(1.0, 1.0 - (0.70 * appearance_distance + 0.30 * position_distance)))

            if appearance_distance > self.reid_appearance_max_distance:
                continue
            if score < self.reid_threshold:
                continue
            candidates.append((score, sid, appearance_distance, position_distance))

        if not candidates:
            return None, 0.0
        candidates.sort(key=lambda item: item[0], reverse=True)
        best = candidates[0]
        second = candidates[1][0] if len(candidates) > 1 else -1.0
        if len(candidates) > 1 and best[0] - second < self.reid_margin:
            return None, best[0]

        sid = best[1]
        previous_track = self.student_to_track.get(sid)
        if previous_track is not None and previous_track != int(track.track_id):
            self.track_to_student.pop(previous_track, None)
        self._refresh_profile(sid, track, frame_size, identity_source='TRACK_REID')
        return sid, best[0]


    # GOD EYES IDENTITY LOCK COMPATIBILITY AND GEOMETRY GUARD V22
    @staticmethod
    def _v22_cosine(a, b):
        if a is None or b is None:
            return -1.0
        try:
            aa = np.asarray(a, dtype=np.float32).ravel()
            bb = np.asarray(b, dtype=np.float32).ravel()
            if aa.size == 0 or aa.size != bb.size:
                return -1.0
            denom = float(np.linalg.norm(aa) * np.linalg.norm(bb))
            if denom <= 1e-8:
                return -1.0
            return float(np.clip(np.dot(aa, bb) / denom, -1.0, 1.0))
        except Exception:
            return -1.0

    @staticmethod
    def _v22_geometry_distance(a, b, frame_size=None):
        if a is None or b is None:
            return 1.0
        try:
            ax, ay = float(a[0]), float(a[1])
            bx, by = float(b[0]), float(b[1])
            if frame_size and len(frame_size) >= 2:
                fw, fh = max(1.0, float(frame_size[0])), max(1.0, float(frame_size[1]))
                return float(np.hypot((ax - bx) / fw, (ay - by) / fh))
            return float(np.hypot(ax - bx, ay - by) / 500.0)
        except Exception:
            return 1.0

    def _v22_locked_face_similarity(self, sid, track):
        current = getattr(track, 'face_feature', None) if (
            getattr(track, 'face_feature_current', getattr(track, 'face', None) is not None)
            and getattr(track, 'face', None) is not None
        ) else None
        if current is None:
            return -1.0
        profile = (getattr(self, 'student_profiles', {}) or {}).get(sid) or {}
        candidates = [profile.get('face_feature'), profile.get('server_embedding')]
        roster = getattr(self, 'server_roster', {}) or {}
        roster_item = roster.get(sid) if hasattr(roster, 'get') else None
        if roster_item:
            candidates.append(roster_item.get('face_feature'))
        scores = [self._v22_cosine(current, ref) for ref in candidates if ref is not None]
        return max(scores) if scores else -1.0

    def _v22_best_server_match(self, track):
        roster = getattr(self, 'server_roster', {}) or {}
        current = getattr(track, 'face_feature', None) if (
            getattr(track, 'face_feature_current', getattr(track, 'face', None) is not None)
            and getattr(track, 'face', None) is not None
        ) else None
        if not roster or current is None:
            return None, -1.0, -1.0
        scored = []
        status_map = getattr(self, 'active_track_status', {}) or {}
        student_to_track = getattr(self, 'student_to_track', {}) or {}
        for label, item in roster.items():
            assigned_track = student_to_track.get(label)
            if assigned_track is not None and int(assigned_track) != int(track.track_id):
                st = status_map.get(assigned_track, {}) or {}
                if st.get('visible') and st.get('confirmed') and not st.get('missed', 0):
                    continue
            score = self._v22_cosine(current, item.get('face_feature'))
            scored.append((score, label))
        scored.sort(reverse=True, key=lambda item: item[0])
        if not scored:
            return None, -1.0, -1.0
        return scored[0][1], scored[0][0], scored[1][0] if len(scored) > 1 else -1.0

    def _v22_recover_without_face(self, track, frame_size=None):
        """Strict re-association for a NEW track only when no face embedding exists."""
        has_fresh_face_embedding = bool(
            getattr(track, 'face_feature_current', getattr(track, 'face', None) is not None)
            and getattr(track, 'face', None) is not None
        )
        if has_fresh_face_embedding:
            return None, 0.0
        profiles = getattr(self, 'student_profiles', {}) or {}
        if not profiles:
            return None, 0.0
        status_map = getattr(self, 'active_track_status', {}) or {}
        student_to_track = getattr(self, 'student_to_track', {}) or {}
        candidates = []
        for sid, profile in profiles.items():
            old_tid = student_to_track.get(sid)
            if old_tid is not None and int(old_tid) == int(track.track_id):
                continue
            old_status = status_map.get(old_tid, {}) or {}
            if old_status.get('visible') and old_status.get('confirmed') and not old_status.get('missed', 0):
                continue
            old_app = profile.get('appearance_feature')
            new_app = getattr(track, 'appearance_feature', None)
            if old_app is None or new_app is None:
                continue
            sim = self._v22_cosine(new_app, old_app)
            appearance_distance = 1.0 - 0.5 * (sim + 1.0)
            position_distance = self._v22_geometry_distance(
                getattr(track, 'center', None), profile.get('center'),
                frame_size or profile.get('frame_size'))
            # Strictly require both similar appearance and nearby geometry; never
            # attach an identity to a face-detected mismatch such as a poster.
            if appearance_distance > 0.20 or position_distance > 0.20:
                continue
            score = 1.0 - 0.70 * appearance_distance - 0.30 * position_distance
            candidates.append((score, sid, appearance_distance, position_distance))
        candidates.sort(reverse=True, key=lambda item: item[0])
        if not candidates:
            return None, 0.0
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.12:
            return None, candidates[0][0]
        score, sid, _, _ = candidates[0]
        old_tid = student_to_track.get(sid)
        if old_tid is not None:
            getattr(self, 'track_to_student', {}).pop(old_tid, None)
        # Transfer only after strict no-face matching uniquely supports continuity.
        if hasattr(self, '_refresh_profile'):
            self._refresh_profile(sid, track, frame_size, identity_source='TRACK_REID_NO_FACE_V22')
        else:
            profile = profiles[sid]
            profile['center'] = getattr(track, 'center', profile.get('center'))
            profile['frame_size'] = frame_size or profile.get('frame_size')
            profile['track_id'] = int(track.track_id)
            self.track_to_student[int(track.track_id)] = sid
            self.student_to_track[sid] = int(track.track_id)
        return sid, float(score)

    def _confirm_new_face_match(self, track_id, label, score, threshold):
        """Require the same fresh Face ID on 3 of the last 5 frames within 1.5 seconds."""
        now = time.monotonic()
        history_by_track = getattr(self, 'pending_face_matches', None)
        if not isinstance(history_by_track, dict):
            history_by_track = {}
            self.pending_face_matches = history_by_track
        recent = [
            (stamp, prior_label, prior_score)
            for stamp, prior_label, prior_score in history_by_track.get(int(track_id), [])
            if now - float(stamp) <= 1.5
        ]
        recent.append((now, str(label), float(score)))
        recent = recent[-5:]
        history_by_track[int(track_id)] = recent
        agreeing = [
            sample for sample in recent
            if sample[1] == str(label) and sample[2] >= float(threshold)
        ]
        return len(agreeing) >= 3

    def resolve(self, track, frame_size=None):
        # V22: same-track lock persists through head turns/no-face intervals, but
        # sudden tracker jumps and confident conflicting Face IDs are never named.
        track_id = int(getattr(track, 'track_id', 0) or 0)
        track_map = getattr(self, 'track_to_student', {}) or {}
        profiles = getattr(self, 'student_profiles', {}) or {}
        roster = getattr(self, 'server_roster', {}) or {}
        locked_sid = track_map.get(track_id)
        current_face = getattr(track, 'face_feature', None) if (
            getattr(track, 'face_feature_current', getattr(track, 'face', None) is not None)
            and getattr(track, 'face', None) is not None
        ) else None
        threshold = float(getattr(self, 'server_threshold', 0.70) or 0.70)
        margin = float(getattr(self, 'server_margin', 0.08) or 0.08)

        # Existing identity: keep it if this is a geometrically continuous track.
        if locked_sid is not None:
            profile = profiles.get(locked_sid)
            if profile is None:
                roster_item = roster.get(locked_sid) if hasattr(roster, 'get') else None
                if roster_item is not None and hasattr(self, '_bind_identity'):
                    try:
                        self._bind_identity(locked_sid, track, frame_size)
                        profile = (getattr(self, 'student_profiles', {}) or {}).get(locked_sid)
                    except Exception:
                        profile = None
                if profile is None:
                    return locked_sid, 0.80

            frame_for_distance = frame_size or profile.get('frame_size')
            jump = self._v22_geometry_distance(
                getattr(track, 'center', None), profile.get('center'), frame_for_distance)
            best_label, best_score, second_score = self._v22_best_server_match(track)
            fresh_match = bool(
                current_face is not None and best_label is not None
                and best_score >= threshold and (best_score - max(-1.0, second_score)) >= margin
            )

            # A high-confidence different registered face must not inherit this ID.
            if fresh_match and best_label != locked_sid:
                return 'IDENTITY UNCERTAIN', max(0.0, min(1.0, best_score))

            # A large one-frame jump (e.g. from a person to a poster/background)
            # invalidates the overlay label until the face verifies this same ID.
            if jump > 0.20 and not (fresh_match and best_label == locked_sid):
                return 'IDENTITY UNCERTAIN', max(0.0, min(1.0, best_score if current_face is not None else 0.0))

            # Side-view/low-quality face is not enough to replace identity; keep the
            # same track label unless another enrolled face confidently contradicts it.
            source = 'FACE_REVERIFIED_V22' if fresh_match and best_label == locked_sid else (
                'TRACK_HOLD_NO_FACE_V22' if current_face is None else 'TRACK_HOLD_FACE_UNCERTAIN_V22')
            if hasattr(self, '_refresh_profile'):
                self._refresh_profile(locked_sid, track, frame_size, identity_source=source)
            return locked_sid, max(0.80, min(1.0, best_score)) if fresh_match else 0.86

        # New track with a detected face: the registered Face ID must establish
        # identity. Do not use clothes/appearance to label a contradictory face.
        if roster and current_face is not None:
            best_label, best_score, second_score = self._v22_best_server_match(track)
            if (best_label is not None and best_score >= threshold
                    and (best_score - max(-1.0, second_score)) >= margin):
                if not self._confirm_new_face_match(track_id, best_label, best_score, threshold):
                    return 'IDENTITY UNCERTAIN', max(0.0, min(1.0, best_score))
                self.pending_face_matches.pop(track_id, None)
                old_tid = (getattr(self, 'student_to_track', {}) or {}).get(best_label)
                status = (getattr(self, 'active_track_status', {}) or {}).get(old_tid, {}) or {}
                if old_tid is not None and int(old_tid) != track_id and status.get('visible') and status.get('confirmed') and not status.get('missed', 0):
                    return 'IDENTITY UNCERTAIN', 0.0
                if best_label not in profiles and hasattr(self, '_bind_identity'):
                    self._bind_identity(best_label, track, frame_size)
                elif hasattr(self, '_refresh_profile'):
                    if old_tid is not None and int(old_tid) != track_id:
                        track_map.pop(old_tid, None)
                    self._refresh_profile(best_label, track, frame_size, identity_source='FACE_MATCH_V22')
                return best_label, max(0.0, min(1.0, best_score))
            # A failed fresh-frame match breaks the confirmation run; never reuse an old vote.
            self.pending_face_matches.pop(track_id, None)
            # Face was detected but did not match: no appearance-only identity transfer.
            return 'IDENTITY UNCERTAIN', max(0.0, min(1.0, best_score if best_label is not None else 0.0))

        # New track with no usable face: only a strict unique appearance+geometry
        # match may recover an existing identity.
        sid, score = self._v22_recover_without_face(track, frame_size)
        if sid is not None:
            return sid, max(0.0, min(1.0, score))

        if not profiles:
            if roster:
                return 'IDENTITY UNCERTAIN', 0.0
            return (f'CANDIDATE {track_id:02d}', 0.55)
        return 'IDENTITY UNCERTAIN', 0.0


    def mapping(self):
        return dict(self.track_to_student)

    def reset(self):
        self.locked = False
        self.track_to_student.clear()
        self.student_profiles.clear()
        self.student_to_track.clear()
        self.pending_face_matches.clear()
        self.next_student = 1

class SmartVision:
    MODEL_NAMES = ('yolo11n.pt', 'yolov8n.pt')

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self.model_path = self._find_model()
        self.model_source = ''
        self.model = None
        self.person_net = None
        self.person_net_error = ''
        self.front = self._load_cascade('haarcascade_frontalface_default.xml')
        self.profile = self._load_cascade('haarcascade_profileface.xml')
        self.face_detector = None
        self.face_recognizer = None
        self.face_model_error = ''
        self._load_face_models()
        self.frame_index = 0
        self.previous_gray = None
        self.previous_frame = None
        self.previous_mouth = {}
        self.last_detections = []
        self.inference_count = 0
        self.last_inference_ms = 0.0
        self.last_model_error = ''
        self._load_yolo()
        if self.model is None:
            self._load_yolox()

    def _load_face_models(self):
        # Prefer models bundled with the app; fall back to the shared local
        # GodEyesServer model directory used during development.
        candidates = [
            self.model_dir,
            Path(r'D:\GodEyesServer\data\face_models'),
            Path(r'D:\GodEyes\app\assets\models'),
        ]
        seen = set()
        for base in candidates:
            try:
                base = Path(base)
                key = str(base.resolve()).lower()
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            yunet_path = base / 'face_detection_yunet_2023mar.onnx'
            sface_path = base / 'face_recognition_sface_2021dec.onnx'
            if not (yunet_path.exists() and sface_path.exists()):
                continue
            try:
                if hasattr(cv2, 'FaceDetectorYN') and yunet_path.stat().st_size > 100000:
                    self.face_detector = cv2.FaceDetectorYN.create(str(yunet_path), '', (320, 320), 0.65, 0.30, 5000)
                if hasattr(cv2, 'FaceRecognizerSF') and sface_path.stat().st_size > 100000:
                    self.face_recognizer = cv2.FaceRecognizerSF.create(str(sface_path), '')
                if self.face_detector is not None and self.face_recognizer is not None:
                    return
            except Exception as exc:
                self.face_model_error = str(exc)
        self.face_detector = None
        self.face_recognizer = None

    def _yunet_faces(self, frame):
        if self.face_detector is None:
            return []
        try:
            h, w = frame.shape[:2]
            self.face_detector.setInputSize((int(w), int(h)))
            _, faces = self.face_detector.detect(frame)
            return list(faces) if faces is not None else []
        except Exception:
            return []

    @staticmethod
    def _select_yunet_face(faces, person_box):
        px, py, pw, ph = person_box
        candidates = []
        for face in faces:
            try:
                fx, fy, fw, fh = [float(v) for v in face[:4]]
            except Exception:
                continue
            cx = fx + fw * 0.5
            cy = fy + fh * 0.5
            if px <= cx <= px + pw and py <= cy <= py + ph * 0.72:
                score = fw * fh
                candidates.append((score, face))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    @staticmethod
    def _estimate_yaw_from_yunet(face):
        """Estimate relative head yaw from YuNet's eye and nose landmarks.

        Returns None when the landmarks are incomplete or implausible. This is
        a 2-D estimate, not a calibrated 3-D pose measurement.
        """
        try:
            values = np.asarray(face, dtype=np.float64).ravel()
            if values.size < 15 or not np.all(np.isfinite(values[:15])):
                return None

            _, _, fw, fh = [float(v) for v in values[:4]]
            eye_a = values[4:6]
            eye_b = values[6:8]
            nose = values[8:10]

            eye_span = abs(float(eye_b[0] - eye_a[0]))
            if fw <= 0.0 or fh <= 0.0:
                return None
            if eye_span < 0.14 * fw or eye_span > 0.85 * fw:
                return None

            eye_mid_x = 0.5 * (float(eye_a[0]) + float(eye_b[0]))
            nose_offset_ratio = (float(nose[0]) - eye_mid_x) / max(1.0, eye_span)

            # Small offsets usually come from landmark jitter on a near-frontal face.
            if abs(nose_offset_ratio) < 0.055:
                return 0.0

            # Convert relative nose displacement into a bounded, approximate angle.
            yaw = math.degrees(math.atan2(nose_offset_ratio, 0.42))
            return float(np.clip(yaw, -65.0, 65.0))
        except Exception:
            return None

    @staticmethod
    def _estimate_pitch_from_yunet(face, frame_shape):
        """Estimate approximate head pitch from YuNet landmarks.

        This pose estimate uses a generic 3-D face template and is intentionally
        marked as measured only when solvePnP is stable and reprojection error is
        acceptable. It is used as a *relative* signal after per-track calibration,
        not as an absolute measure of sleep or attention.
        """
        try:
            values = np.asarray(face, dtype=np.float64).ravel()
            if values.size < 15 or not np.all(np.isfinite(values[:15])):
                return None
            x, y, fw, fh = [float(v) for v in values[:4]]
            frame_h, frame_w = int(frame_shape[0]), int(frame_shape[1])
            if fw < 24 or fh < 24 or frame_w < 2 or frame_h < 2:
                return None
            # YuNet landmarks: eye 1, eye 2, nose, mouth corner 1, mouth corner 2.
            eye1 = values[4:6]
            eye2 = values[6:8]
            nose = values[8:10]
            mouth1 = values[10:12]
            mouth2 = values[12:14]
            eye_span = float(np.linalg.norm(eye1 - eye2))
            mouth_span = float(np.linalg.norm(mouth1 - mouth2))
            if not (0.12 * fw <= eye_span <= 0.85 * fw):
                return None
            if not (0.08 * fw <= mouth_span <= 0.90 * fw):
                return None

            # Approximate chin landmark from the lower edge of the YuNet face box.
            chin = np.array([x + 0.5 * fw, y + 0.96 * fh], dtype=np.float64)
            image_points = np.asarray([
                nose, chin, eye1, eye2, mouth1, mouth2,
            ], dtype=np.float64)
            # Face-template Y points down, matching image coordinates. With this
            # convention, positive X rotation corresponds to the nose lowering
            # relative to the eyes; the same sign is validated in unit tests.
            model_points = np.asarray([
                [0.0, 0.0, 0.0],
                [0.0, 330.0, -65.0],
                [-225.0, -170.0, -135.0],
                [225.0, -170.0, -135.0],
                [-150.0, 150.0, -125.0],
                [150.0, 150.0, -125.0],
            ], dtype=np.float64)
            focal = float(max(frame_w, frame_h)) * 1.15
            camera = np.asarray([
                [focal, 0.0, frame_w * 0.5],
                [0.0, focal, frame_h * 0.5],
                [0.0, 0.0, 1.0],
            ], dtype=np.float64)
            distortion = np.zeros((4, 1), dtype=np.float64)
            ok, rvec, tvec = cv2.solvePnP(
                model_points, image_points, camera, distortion,
                flags=cv2.SOLVEPNP_ITERATIVE,
            )
            if not ok:
                return None
            projected, _ = cv2.projectPoints(model_points, rvec, tvec, camera, distortion)
            error = float(np.mean(np.linalg.norm(projected.reshape(-1, 2) - image_points, axis=1)))
            if not math.isfinite(error) or error > max(10.0, fh * 0.14):
                return None
            rotation, _ = cv2.Rodrigues(rvec)
            angles = cv2.RQDecomp3x3(rotation)[0]
            pitch = float(angles[0])
            if not math.isfinite(pitch) or abs(pitch) > 65.0:
                return None
            # Convention: positive means the nose moves downward relative to the
            # calibrated upright posture. Sign is checked with synthetic projections
            # in the test suite; using relative pitch avoids assuming one body size.
            return float(np.clip(pitch, -60.0, 60.0))
        except Exception:
            return None

    def _sface_feature(self, frame, face):
        if self.face_recognizer is None or face is None:
            return None
        try:
            aligned = self.face_recognizer.alignCrop(frame, face)
            feature = self.face_recognizer.feature(aligned)
            feature = np.asarray(feature, dtype=np.float32).ravel()
            norm = float(np.linalg.norm(feature))
            if feature.size == 0 or norm <= 1e-8:
                return None
            return feature / norm
        except Exception:
            return None

    def _find_model(self):
        roots = [
            self.model_dir,
            self.model_dir / 'weights',
            self.model_dir.parent,
            Path(__file__).resolve().parent,
            Path(__file__).resolve().parent.parent,
            Path.cwd(),
        ]
        env_paths = [
            os.environ.get('FOCUSAI_MODEL', ''),
            os.environ.get('YOLO_MODEL_PATH', ''),
        ]
        candidates = []
        for raw in env_paths:
            if raw:
                candidates.append(Path(raw))
        for root in roots:
            for name in self.MODEL_NAMES:
                candidates.append(root / name)
        seen = set()
        for candidate in candidates:
            try:
                p = candidate.expanduser().resolve()
            except Exception:
                continue
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            if p.exists() and p.is_file() and p.stat().st_size > 1024 * 1024:
                return p
        return None

    def _load_yolo(self):
        # Ultralytics YOLO is optional. If unavailable, _load_yolox() loads a
        # compact OpenCV-DNN model so tracking can continue when faces are hidden.
        try:
            from ultralytics import YOLO
        except Exception as exc:
            self.last_model_error = f'Ultralytics unavailable: {exc}'
            self.model = None
            self.model_source = 'YuNet Face Fallback'
            return

        if self.model_path is None:
            self.last_model_error = 'YOLO model not found; using YuNet Face Fallback.'
            self.model = None
            self.model_source = 'YuNet Face Fallback'
            return

        try:
            self.model = YOLO(str(self.model_path))
            self.model_source = str(self.model_path)
            try:
                self.model.fuse()
            except Exception:
                pass
        except Exception as exc:
            self.last_model_error = f'YOLO load failed: {exc}; using YuNet Face Fallback.'
            self.model = None
            self.model_source = 'YuNet Face Fallback'

    def _load_yolox(self):
        """Load the CPU-only OpenCV Zoo YOLOX INT8 model, if available."""
        model_path = self.model_dir / 'object_detection_yolox_2022nov_int8.onnx'
        if not model_path.is_file() or model_path.stat().st_size < 1_000_000:
            self.person_net_error = f'YOLOX model not installed: {model_path}'
            return
        try:
            net = cv2.dnn.readNet(str(model_path))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            self.person_net = net
            self.model_source = 'OpenCV Zoo YOLOX INT8 · CPU'
            self.person_net_error = ''
        except Exception as exc:
            self.person_net = None
            self.person_net_error = f'YOLOX load failed: {exc}'
            if not self.model_source:
                self.model_source = 'YuNet Face Fallback'

    @staticmethod
    @lru_cache(maxsize=1)
    def _yolox_grids():
        grids, strides = [], []
        for size, stride in ((80, 8), (40, 16), (20, 32)):
            gx, gy = np.meshgrid(np.arange(size, dtype=np.float32), np.arange(size, dtype=np.float32))
            grids.append(np.stack((gx.ravel(), gy.ravel()), axis=1))
            strides.append(np.full((size * size, 1), stride, dtype=np.float32))
        return np.concatenate(grids, axis=0), np.concatenate(strides, axis=0)

    def _yolox_detections(self, frame, resize_scale, original_width, original_height):
        """Return (person boxes, object boxes) decoded from YOLOX INT8 output.

        All returned boxes use original input-frame pixels, matching the rest of
        SmartVision. Errors are contained here so face-only mode remains available.
        """
        if self.person_net is None:
            return [], []
        try:
            input_size = 640
            h, w = frame.shape[:2]
            ratio = min(input_size / float(h), input_size / float(w))
            rw, rh = max(1, int(w * ratio)), max(1, int(h * ratio))
            resized = cv2.resize(frame, (rw, rh), interpolation=cv2.INTER_LINEAR)
            padded = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
            padded[:rh, :rw] = resized
            rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32)
            blob = np.transpose(rgb, (2, 0, 1))[None, ...]
            self.person_net.setInput(blob)
            outputs = self.person_net.forward(self.person_net.getUnconnectedOutLayersNames())
            if isinstance(outputs, (tuple, list)):
                if not outputs:
                    return [], []
                outputs = outputs[0]
            pred = np.asarray(outputs, dtype=np.float32)
            pred = np.squeeze(pred)
            if pred.ndim == 2 and pred.shape[0] >= 85 and pred.shape[1] == 8400:
                pred = pred.T
            if pred.ndim != 2 or pred.shape[1] < 85:
                self.person_net_error = f'Unexpected YOLOX output shape: {np.asarray(outputs).shape}'
                return [], []

            grids, strides = self._yolox_grids()
            if pred.shape[0] != grids.shape[0]:
                self.person_net_error = f'Unexpected YOLOX anchor count: {pred.shape[0]}'
                return [], []
            pred = pred.copy()
            pred[:, :2] = (pred[:, :2] + grids) * strides
            pred[:, 2:4] = np.exp(np.clip(pred[:, 2:4], -10.0, 10.0)) * strides
            xyxy = np.empty((pred.shape[0], 4), dtype=np.float32)
            xyxy[:, 0:2] = pred[:, 0:2] - pred[:, 2:4] * 0.5
            xyxy[:, 2:4] = pred[:, 0:2] + pred[:, 2:4] * 0.5
            class_scores = pred[:, 4:5] * pred[:, 5:]
            persons, objects = [], []
            for class_id in (0, 63, 65, 67, 73):
                if class_id >= class_scores.shape[1]:
                    continue
                threshold = 0.42 if class_id == 0 else 0.32
                scores = class_scores[:, class_id]
                candidate_ids = np.flatnonzero(scores >= threshold)
                if candidate_ids.size == 0:
                    continue
                boxes_xywh, kept_scores, kept_indices = [], [], []
                for idx in candidate_ids.tolist():
                    x1, y1, x2, y2 = [float(v) for v in xyxy[idx]]
                    # Reject predictions centered in letterbox padding, then clip
                    # any partially overlapping box to the actual resized image.
                    if (x1 + x2) * 0.5 < 0 or (x1 + x2) * 0.5 >= rw or (y1 + y2) * 0.5 < 0 or (y1 + y2) * 0.5 >= rh:
                        continue
                    x1, x2 = max(0.0, x1), min(float(rw), x2)
                    y1, y2 = max(0.0, y1), min(float(rh), y2)
                    # Undo the 640x640 letterbox (top-left anchored) and the
                    # optional initial downscale to 960px before clamping.
                    inv = 1.0 / max(1e-9, ratio * float(resize_scale))
                    ox1, oy1, ox2, oy2 = [int(round(float(v) * inv)) for v in (x1, y1, x2, y2)]
                    ox1 = max(0, min(original_width - 1, ox1))
                    oy1 = max(0, min(original_height - 1, oy1))
                    ox2 = max(0, min(original_width, ox2))
                    oy2 = max(0, min(original_height, oy2))
                    bw, bh = ox2 - ox1, oy2 - oy1
                    if bw < 8 or bh < 10:
                        continue
                    boxes_xywh.append([ox1, oy1, bw, bh])
                    kept_scores.append(float(scores[idx]))
                    kept_indices.append(idx)
                if not boxes_xywh:
                    continue
                nms = cv2.dnn.NMSBoxes(boxes_xywh, kept_scores, threshold, 0.45)
                if nms is None or len(nms) == 0:
                    continue
                for local_index in np.asarray(nms).reshape(-1).tolist():
                    bx, by, bw, bh = boxes_xywh[int(local_index)]
                    conf = kept_scores[int(local_index)]
                    box = self._clamp_box((bx, by, bw, bh), original_width, original_height)
                    if class_id == 0:
                        if conf >= 0.48 and self._person_box_ok(box, original_width, original_height, conf):
                            persons.append((box, conf))
                    elif box[2] >= 18 and box[3] >= 12:
                        objects.append((box, class_id, conf))
            self.person_net_error = ''
            return persons, objects
        except Exception as exc:
            self.person_net_error = f'YOLOX inference failed: {exc}'
            return [], []

    def _load_cascade(self, name):
        paths = [
            self.model_dir / name,
            Path(cv2.data.haarcascades) / name,
        ]
        for path in paths:
            try:
                if path.exists():
                    cascade = cv2.CascadeClassifier(str(path))
                    if not cascade.empty():
                        return cascade
            except Exception:
                pass
        return cv2.CascadeClassifier()

    @staticmethod
    def _clamp_box(box, width, height):
        x, y, w, h = [int(v) for v in box]
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        x2 = max(x + 1, min(width, x + w))
        y2 = max(y + 1, min(height, y + h))
        return x, y, x2 - x, y2 - y

    @staticmethod
    def _gray_feature(gray, box, size=(32, 48)):
        x, y, w, h = box
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(gray.shape[1], x + w)
        y2 = min(gray.shape[0], y + h)
        crop = gray[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
        crop = cv2.equalizeHist(crop)
        vec = crop.astype(np.float32).ravel() / 255.0
        norm = np.linalg.norm(vec)
        if norm > 1e-8:
            vec = vec / norm
        return vec

    @staticmethod
    def _appearance_feature(frame, box):
        x, y, w, h = box
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(frame.shape[1], x + w)
        y2 = min(frame.shape[0], y + h)
        if x2 <= x1 or y2 <= y1:
            return None
        top = y1 + int((y2 - y1) * 0.10)
        bottom = y1 + int((y2 - y1) * 0.88)
        crop = frame[top:bottom, x1:x2]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, (64, 96), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256]).astype(np.float32)
        hist = hist.ravel()
        hist_norm = np.linalg.norm(hist)
        if hist_norm > 1e-8:
            hist /= hist_norm
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (16, 24), interpolation=cv2.INTER_AREA).astype(np.float32).ravel() / 255.0
        small_norm = np.linalg.norm(small)
        if small_norm > 1e-8:
            small /= small_norm
        feat = np.concatenate([hist, small]).astype(np.float32)
        norm = np.linalg.norm(feat)
        if norm > 1e-8:
            feat /= norm
        return feat

    @staticmethod
    def _iou(a, b):
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ix1 = max(ax, bx)
        iy1 = max(ay, by)
        ix2 = min(ax + aw, bx + bw)
        iy2 = min(ay + ah, by + bh)
        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        inter = iw * ih
        union = max(1, aw * ah + bw * bh - inter)
        return inter / union

    @staticmethod
    def _prepare_gray(frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (3, 3), 0)

    def _detect_faces(self, gray, person_box):
        px, py, pw, ph = person_box
        x1 = max(0, px)
        y1 = max(0, py)
        x2 = min(gray.shape[1], px + pw)
        y2 = min(gray.shape[0], py + max(1, int(ph * 0.68)))
        roi = gray[y1:y2, x1:x2]
        if roi.size == 0 or roi.shape[1] < 70 or roi.shape[0] < 60:
            return None, 'UNKNOWN', 0.0, 0.0
        scale = 1.0
        if roi.shape[1] > 640:
            scale = 640.0 / roi.shape[1]
            roi_small = cv2.resize(roi, (640, max(1, int(roi.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        else:
            roi_small = roi
        candidates = []
        if not self.front.empty():
            try:
                faces = self.front.detectMultiScale(
                    roi_small,
                    scaleFactor=1.03,
                    minNeighbors=4,
                    minSize=(22, 22),
                    flags=cv2.CASCADE_SCALE_IMAGE,
                )
                for fx, fy, fw, fh in faces:
                    if scale != 1.0:
                        fx, fy, fw, fh = [int(v / scale) for v in (fx, fy, fw, fh)]
                    candidates.append(((x1 + fx, y1 + fy, fw, fh), 'FRONT', 0.92))
            except Exception:
                pass
        if not self.profile.empty():
            try:
                faces = self.profile.detectMultiScale(
                    roi_small,
                    scaleFactor=1.03,
                    minNeighbors=3,
                    minSize=(20, 20),
                    flags=cv2.CASCADE_SCALE_IMAGE,
                )
                for fx, fy, fw, fh in faces:
                    if scale != 1.0:
                        fx, fy, fw, fh = [int(v / scale) for v in (fx, fy, fw, fh)]
                    candidates.append(((x1 + fx, y1 + fy, fw, fh), 'LEFT', 0.86))
            except Exception:
                pass
            try:
                flipped = cv2.flip(roi_small, 1)
                faces = self.profile.detectMultiScale(
                    flipped,
                    scaleFactor=1.03,
                    minNeighbors=3,
                    minSize=(20, 20),
                    flags=cv2.CASCADE_SCALE_IMAGE,
                )
                for fx, fy, fw, fh in faces:
                    if scale != 1.0:
                        fx, fy, fw, fh = [int(v / scale) for v in (fx, fy, fw, fh)]
                    fx = flipped.shape[1] - fx - fw
                    candidates.append(((x1 + fx, y1 + fy, fw, fh), 'RIGHT', 0.86))
            except Exception:
                pass
        if not candidates:
            return None, 'UNKNOWN', 0.0, 0.0
        front_candidates = [c for c in candidates if c[1] == 'FRONT']
        profile_candidates = [c for c in candidates if c[1] in ('LEFT', 'RIGHT')]
        if profile_candidates and front_candidates:
            best_profile = max(profile_candidates, key=lambda item: item[0][2] * item[0][3])
            best_front = max(front_candidates, key=lambda item: item[0][2] * item[0][3])
            profile_area = best_profile[0][2] * best_profile[0][3]
            front_area = best_front[0][2] * best_front[0][3]
            if profile_area >= 0.58 * front_area:
                face, side, conf = best_profile
            else:
                face, side, conf = best_front
        else:
            candidates.sort(key=lambda item: item[0][2] * item[0][3], reverse=True)
            face, side, conf = candidates[0]
        fx, fy, fw, fh = self._clamp_box(face, gray.shape[1], gray.shape[0])
        area_ratio = (fw * fh) / max(1.0, pw * ph)
        if fw < 22 or fh < 22 or area_ratio < 0.0025 or area_ratio > 0.30:
            return None, 'UNKNOWN', 0.0, 0.0
        vertical_ratio = ((fy + 0.5 * fh) - py) / max(1.0, float(ph))
        if vertical_ratio > 0.70:
            return None, 'UNKNOWN', 0.0, 0.0
        # Coarse fallback only. A measured estimate from YuNet landmarks
        # replaces this value in the frame-inference loop when available.
        yaw_map = {'LEFT': -45.0, 'RIGHT': 45.0, 'FRONT': 0.0}
        yaw_deg = yaw_map.get(side, 0.0)
        return (fx, fy, fw, fh), side, conf, yaw_deg

    @staticmethod
    def _mouth_roi(gray, face):
        x, y, w, h = face
        x1 = max(0, int(x + 0.16 * w))
        x2 = min(gray.shape[1], int(x + 0.84 * w))
        y1 = max(0, int(y + 0.55 * h))
        y2 = min(gray.shape[0], int(y + 0.97 * h))
        roi = gray[y1:y2, x1:x2]
        if roi.size < 120:
            return None
        return cv2.resize(roi, (64, 32), interpolation=cv2.INTER_AREA)

    def _mouth_features(self, gray, face):
        roi = self._mouth_roi(gray, face)
        if roi is None:            return 0.0, 0.0
        roi = cv2.GaussianBlur(roi, (3, 3), 0)
        darkness = 1.0 - float(np.mean(roi)) / 255.0
        edges = cv2.Canny(roi, 35, 95)
        edge_density = float(np.mean(edges > 0))
        opening = float(np.clip(0.24 * darkness + 1.45 * edge_density, 0.0, 1.0))
        motion = 0.0
        if self.previous_gray is not None:
            previous_roi = self._mouth_roi(self.previous_gray, face)
            if previous_roi is not None:
                previous_roi = cv2.GaussianBlur(previous_roi, (3, 3), 0)
                motion = float(np.mean(cv2.absdiff(roi, previous_roi)) / 255.0)
        return opening, motion

    def _person_box_ok(self, box, frame_width, frame_height, confidence):
        x, y, w, h = box
        if confidence < 0.55:
            return False
        if w < 28 or h < 50:
            return False
        if x + w <= 1 or y + h <= 1:
            return False
        ratio = h / max(1.0, float(w))
        if ratio < 0.55 or ratio > 4.8:
            return False
        area = (w * h) / max(1.0, frame_width * frame_height)
        if area < 0.003 or area > 0.75:
            return False
        return True

    def _motion_in_region(self, current, previous, box, y_start, y_end):
        if previous is None:
            return 0.0
        x, y, w, h = box
        x1 = max(0, x)
        y1 = max(0, y + int(h * y_start))
        x2 = min(current.shape[1], x + w)
        y2 = min(current.shape[0], y + int(h * y_end))
        if x2 <= x1 or y2 <= y1:
            return 0.0
        a = cv2.cvtColor(current[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        px1 = max(0, min(previous.shape[1] - 1, x1))
        py1 = max(0, min(previous.shape[0] - 1, y1))
        px2 = max(px1 + 1, min(previous.shape[1], x2))
        py2 = max(py1 + 1, min(previous.shape[0], y2))
        b = cv2.cvtColor(previous[py1:py2, px1:px2], cv2.COLOR_BGR2GRAY)
        if a.size == 0 or b.size == 0:
            return 0.0
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
        diff = cv2.GaussianBlur(cv2.absdiff(a, b), (3, 3), 0)
        return float(np.clip((float(np.mean(diff)) / 255.0) * 4.5, 0.0, 1.0))

    @staticmethod
    def _object_name(cls_id):
        return {63: 'LAPTOP', 65: 'REMOTE', 67: 'PHONE', 73: 'BOOK'}.get(int(cls_id), 'OBJECT')

    def detect(self, frame):
        start = time.perf_counter()
        self.frame_index += 1
        height, width = frame.shape[:2]
        max_width = 960
        scale = 1.0
        if width > max_width:
            scale = max_width / float(width)
            small = cv2.resize(frame, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        else:
            small = frame

        results = None
        if self.model is not None:
            try:
                results = self.model.predict(
                    source=small,
                    classes=[0, 63, 65, 67, 73],
                    conf=0.35,
                    iou=0.48,
                    imgsz=640,
                    max_det=50,
                    device='cpu',
                    verbose=False,
                )
            except Exception as exc:
                self.last_model_error = f'YOLO inference failed: {exc}; activating OpenCV YOLOX fallback.'
                self.model = None
                self.model_source = 'YuNet Face Fallback'
                if self.person_net is None:
                    self._load_yolox()
        gray = self._prepare_gray(frame)
        persons = []
        objects = []
        if results:
            boxes = getattr(results[0], 'boxes', None)
            if boxes is not None:
                for i in range(len(boxes)):
                    try:
                        cls_id = int(boxes.cls[i].item())
                        conf = float(boxes.conf[i].item())
                        coords = boxes.xyxy[i].cpu().numpy().tolist()
                    except Exception:
                        continue
                    if scale != 1.0:
                        inv = 1.0 / scale
                        coords = [float(v) * inv for v in coords]
                    x1, y1, x2, y2 = coords
                    box = self._clamp_box((int(x1), int(y1), int(x2 - x1), int(y2 - y1)), width, height)
                    if cls_id == 0:
                        if conf >= 0.48 and self._person_box_ok(box, width, height, conf):
                            persons.append((box, conf))
                    elif cls_id in (63, 65, 67, 73) and conf >= 0.30:
                        if box[2] >= 18 and box[3] >= 12:
                            objects.append((box, cls_id, conf))
        # OpenCV-DNN YOLOX is the packaged fallback when Ultralytics/torch is
        # intentionally excluded from the Windows build. Unlike face-box-only
        # tracking, it can retain a person when the face turns away or is hidden.
        if self.model is None and self.person_net is not None:
            fallback_persons, fallback_objects = self._yolox_detections(
                small, scale, width, height
            )
            persons.extend(fallback_persons)
            objects.extend(fallback_objects)

        yunet_faces = self._yunet_faces(frame)

        # If no body detector produced a box, use YuNet face boxes as a best-effort
        # upper-body proxy. This remains a degraded fallback, not real body detection.
        # This keeps Face ID + tracking functional without requiring a large
        # YOLO weights file on the development machine.
        if not persons and yunet_faces:
            for face in yunet_faces[:50]:
                try:
                    fx, fy, fw, fh = [float(v) for v in face[:4]]
                except Exception:
                    continue
                # Expand the face region into a stable upper-body tracking box.
                x = int(fx - 0.65 * fw)
                y = int(fy - 0.45 * fh)
                w = int(fw * 2.30)
                h = int(fh * 3.10)
                box = self._clamp_box((x, y, w, h), width, height)
                conf = float(face[14]) if len(face) >= 15 else 0.85
                conf = max(0.62, min(0.99, conf))
                if self._person_box_ok(box, width, height, conf):
                    persons.append((box, conf))

        detections = []
        for person_box, person_conf in persons:
            appearance = self._appearance_feature(frame, person_box)
            face, side, face_conf, yaw_deg = self._detect_faces(gray, person_box)
            yunet_face = self._select_yunet_face(yunet_faces, person_box)
            if face is None and yunet_face is not None:
                ux, uy, uw, uh = [int(float(v)) for v in yunet_face[:4]]
                face = self._clamp_box((ux, uy, uw, uh), width, height)
                side = 'UNKNOWN'
                face_conf = float(yunet_face[14]) if len(yunet_face) >= 15 else 0.65
                yaw_deg = 0.0

            # YuNet provides a useful magnitude for frontal/three-quarter poses, but
            # its eye/nose landmarks may collapse toward 0 degrees in a true profile.
            # Keep the independent Haar profile direction in that case. The behavior
            # engine still requires a stable multi-second pose and several valid samples.
            landmark_yaw = (
                self._estimate_yaw_from_yunet(yunet_face)
                if yunet_face is not None else None
            )
            yaw_deg, head_yaw_measured = _resolve_head_yaw(side, yaw_deg, landmark_yaw)

            head_dir = 0.0
            head_pitch_deg = 0.0
            head_pitch_measured = False
            if yunet_face is not None:
                measured_pitch = self._estimate_pitch_from_yunet(yunet_face, frame.shape[:2])
                if measured_pitch is not None:
                    head_pitch_deg = float(measured_pitch)
                    head_pitch_measured = True
            head_quality = 0.0
            mouth_score = 0.0
            mouth_motion = 0.0
            mouth_activity = 0.0
            face_feature = None
            face_y_ratio = 0.0
            if face is not None:
                fx, fy, fw, fh = face
                face_feature = self._sface_feature(frame, yunet_face) if yunet_face is not None else None
                if face_feature is None:
                    face_feature = self._gray_feature(gray, face)
                mouth_score, mouth_motion = self._mouth_features(gray, face)
                mouth_activity = float(np.clip(0.55 * mouth_score + 5.5 * mouth_motion, 0.0, 1.0))
                face_y_ratio = ((fy + 0.5 * fh) - person_box[1]) / max(1.0, float(person_box[3]))
                head_quality = max(0.0, min(1.0, math.sqrt(fw * fh) / 74.0)) * face_conf
                if side == 'LEFT':
                    head_dir = -1.0
                elif side == 'RIGHT':
                    head_dir = 1.0
            x, y, w, h = person_box
            attached_objects = []
            for obj_box, cls_id, obj_conf in objects:
                ox, oy, ow, oh = obj_box
                ocx = ox + ow * 0.5
                ocy = oy + oh * 0.5
                inside = x <= ocx <= x + w and y <= ocy <= y + h
                overlap = self._iou(person_box, obj_box)
                if inside or overlap >= 0.08:
                    attached_objects.append((self._object_name(cls_id), obj_conf))
            lower_motion = self._motion_in_region(frame, self.previous_frame, person_box, 0.42, 0.98)
            hand_motion = self._motion_in_region(frame, self.previous_frame, person_box, 0.48, 0.83)
            task_activity = float(np.clip(0.48 * lower_motion + 0.52 * hand_motion, 0.0, 1.0))
            if attached_objects:
                task_activity = max(task_activity, min(1.0, 0.64 + 0.18 * max(v for _, v in attached_objects)))
            detections.append(Detection(
                box=person_box,
                person_score=person_conf,
                face=face,
                face_feature=face_feature,
                appearance_feature=appearance,
                face_side=side,
                head_dir=head_dir,
                head_yaw_deg=float(yaw_deg),
                head_yaw_measured=bool(head_yaw_measured),
                head_pitch_deg=float(head_pitch_deg),
                head_pitch_measured=bool(head_pitch_measured),
                head_quality=float(head_quality),
                mouth_score=float(mouth_score),
                mouth_motion=float(mouth_motion),
                mouth_activity=float(mouth_activity),
                face_y_ratio=float(face_y_ratio),
                object_types=tuple(name for name, _ in attached_objects),
                object_scores=tuple(float(score) for _, score in attached_objects),
                lower_motion=float(lower_motion),
                hand_motion=float(hand_motion),
                task_activity=float(task_activity),
            ))
        detections.sort(key=lambda d: d.person_score, reverse=True)
        self.last_detections = detections
        self.previous_gray = gray.copy()
        self.previous_frame = frame.copy()
        self.inference_count += 1
        self.last_inference_ms = (time.perf_counter() - start) * 1000.0
        return detections, objects


class BehaviorEngine:
    # GOD EYES BEHAVIOR V14: sustained pose + mouth + activity evidence.
    """Conservative, temporal behavior cues for a live classroom stream.

    Events are possible visual cues, not proof that a learner is talking or asleep.
    Identity is owned by a stable tracker key; unknown-identity events are rendered
    locally only by the desktop UI and are never persisted as a student record.
    """

    YAW_THRESHOLD_DEG = 40.0
    YAW_CONFIRM_SECONDS = 2.8
    TURN_FACE_GAP_GRACE_SECONDS = 0.75
    TURN_MIN_SAMPLES = 5
    TURN_MIN_QUALITY = 0.22

    EVENT_COOLDOWN = 12.0
    TALK_WINDOW_SECONDS = 4.0
    TALK_MIN_SPAN_SECONDS = 2.0
    TALK_MIN_SAMPLES = 8
    TALK_MIN_ACTIVE_RATIO = 0.62
    TALK_MOUTH_SCORE_MIN = 0.25
    TALK_MOUTH_MOTION_MIN = 0.015
    TALK_ACTIVITY_MIN = 0.36
    TALK_FACE_QUALITY_MIN = 0.22
    TALK_QUIET_REARM_SECONDS = 1.8
    TURN_TALK_WINDOW_SECONDS = 4.0
    TURN_TALK_MIN_SPAN_SECONDS = 2.4
    TURN_TALK_MIN_SAMPLES = 8
    TURN_TALK_ACTIVE_RATIO = 0.66

    HEAD_DOWN_MIN_RATIO = 0.36
    HEAD_DOWN_DELTA = 0.07
    DISTRACT_CONFIRM_SECONDS = 4.0
    SLEEP_HEAD_DOWN_SECONDS = 10.0
    SLEEP_SAMPLE_WINDOW_SECONDS = 14.0
    SLEEP_MIN_SAMPLES = 16
    PITCH_DOWN_DELTA_DEG = 12.0

    def __init__(self, board_side='RIGHT'):
        self.board_side = board_side
        self.state = {}

    def set_board_side(self, board_side):
        self.board_side = board_side

    def _state(self, sid):
        if sid not in self.state:
            self.state[sid] = {
                'turn_side': 0, 'turn_start': None, 'turn_samples': [],
                'last_turn_quality': 0.0, 'last_turn_person_score': 0.0,
                'last_face_time': 0.0, 'last_face_seen_at': 0.0,
                'face_missing_start': None, 'head_down_start': None,
                'face_y_ratio_ema': None, 'head_pitch_baseline': None,
                'head_pitch_samples': [], 'sleep_latched': False,
                'turn_latched': False, 'head_down_latched': False,
                'last_event': {}, 'last_seen': 0.0, 'talk_samples': [],
                'turn_talk_samples': [], 'talk_latched': False,
                'turn_talk_latched': False, 'turn_talk_quiet_since': None, 'talk_quiet_since': None,
                'down_motion_samples': [], 'nod_times': [],
            }
        return self.state[sid]

    def _cooldown_ok(self, state, key, now, seconds=None):
        seconds = self.EVENT_COOLDOWN if seconds is None else float(seconds)
        last = float(state['last_event'].get(key, 0.0))
        return (now - last) >= seconds

    @staticmethod
    def _event(key, confidence, details):
        return key, float(max(0.0, min(0.99, confidence))), str(details)

    @staticmethod
    def _trim_samples(samples, now, seconds):
        return [sample for sample in samples if now - sample[0] <= seconds]

    @staticmethod
    def _mean(samples, index):
        return sum(float(sample[index]) for sample in samples) / max(1, len(samples))

    def evaluate(self, sid, track, now=None):
        now = time.time() if now is None else float(now)
        state = self._state(sid)
        state['last_seen'] = now
        events = []
        face_valid = (
            getattr(track, 'face', None) is not None
            and float(getattr(track, 'head_quality', 0.0)) >= 0.08
            and int(getattr(track, 'missed', 0)) == 0
        )
        yaw = float(getattr(track, 'head_yaw_deg', 0.0) or 0.0)
        yaw_measured = bool(getattr(track, 'head_yaw_measured', False))
        pitch = float(getattr(track, 'head_pitch_deg', 0.0) or 0.0)
        pitch_measured = bool(getattr(track, 'head_pitch_measured', False))
        head_quality = float(getattr(track, 'head_quality', 0.0) or 0.0)
        person_score = float(getattr(track, 'person_score', 0.0) or 0.0)
        mouth_score = float(getattr(track, 'mouth_score', 0.0) or 0.0)
        mouth_motion = float(getattr(track, 'mouth_motion', 0.0) or 0.0)
        mouth_activity = float(getattr(track, 'mouth_activity', 0.0) or 0.0)
        task_activity = float(getattr(track, 'task_activity', 0.0) or 0.0)
        lower_motion = float(getattr(track, 'lower_motion', 0.0) or 0.0)
        hand_motion = float(getattr(track, 'hand_motion', 0.0) or 0.0)
        ratio = float(getattr(track, 'face_y_ratio', 0.0) or 0.0)
        if face_valid:
            state['last_yaw'] = yaw
            state['last_face_time'] = now
            state['last_face_seen_at'] = now
            state['face_missing_start'] = None

        # ---------------- Talking while facing forward ----------------
        if face_valid:
            state['talk_samples'].append((now, mouth_score, mouth_motion, mouth_activity, head_quality, yaw))
            state['talk_samples'] = self._trim_samples(state['talk_samples'], now, self.TALK_WINDOW_SECONDS)
            cue = (
                head_quality >= self.TALK_FACE_QUALITY_MIN
                and mouth_score >= self.TALK_MOUTH_SCORE_MIN
                and mouth_motion >= self.TALK_MOUTH_MOTION_MIN
                and mouth_activity >= self.TALK_ACTIVITY_MIN
            )
            if cue:
                state['talk_quiet_since'] = None
            elif state['talk_quiet_since'] is None:
                state['talk_quiet_since'] = now
        else:
            state['talk_samples'] = self._trim_samples(state['talk_samples'], now, self.TALK_WINDOW_SECONDS)
            state['turn_talk_samples'] = self._trim_samples(state['turn_talk_samples'], now, self.TURN_TALK_WINDOW_SECONDS)
            if state['talk_quiet_since'] is None:
                state['talk_quiet_since'] = now

        if state['talk_latched'] and state['talk_quiet_since'] is not None:
            if now - float(state['talk_quiet_since']) >= self.TALK_QUIET_REARM_SECONDS:
                state['talk_latched'] = False
        samples = state['talk_samples']
        if len(samples) >= self.TALK_MIN_SAMPLES:
            span = samples[-1][0] - samples[0][0]
            usable = [s for s in samples if s[4] >= self.TALK_FACE_QUALITY_MIN and abs(s[5]) < self.YAW_THRESHOLD_DEG]
            active = [s for s in usable if s[1] >= self.TALK_MOUTH_SCORE_MIN and s[2] >= self.TALK_MOUTH_MOTION_MIN and s[3] >= self.TALK_ACTIVITY_MIN]
            motion_values = [s[2] for s in usable]
            spread = max(motion_values) - min(motion_values) if motion_values else 0.0
            avg_q = self._mean(usable, 4)
            if (
                span >= self.TALK_MIN_SPAN_SECONDS and len(usable) >= self.TALK_MIN_SAMPLES
                and len(active) >= 5 and len(active) / max(1, len(usable)) >= self.TALK_MIN_ACTIVE_RATIO
                and spread >= 0.012 and avg_q >= self.TALK_FACE_QUALITY_MIN
                and not state['talk_latched'] and self._cooldown_ok(state, 'OB_TALKING', now, 15.0)
            ):
                confidence = min(0.91, 0.64 + 0.16 * (len(active) / len(usable)) + 0.08 * min(1.0, avg_q))
                events.append(self._event('OB_TALKING', confidence,
                    f'Mouth movement repeated over {span:.1f}s ({len(active)}/{len(usable)} samples). Possible talking; teacher confirmation needed.'))
                state['last_event']['OB_TALKING'] = now
                state['talk_latched'] = True

        # ---------------- Sustained measured head turn ----------------
        turn_signal = bool(face_valid and yaw_measured and abs(yaw) >= self.YAW_THRESHOLD_DEG and head_quality >= self.TURN_MIN_QUALITY)
        if turn_signal:
            direction = -1 if yaw < 0 else 1
            if int(state.get('turn_side') or 0) != direction:
                state['turn_side'] = direction
                state['turn_start'] = now
                state['turn_samples'] = []
                state['turn_latched'] = False
            state['turn_samples'].append((now, yaw, head_quality, person_score))
            state['turn_samples'] = self._trim_samples(state['turn_samples'], now, self.YAW_CONFIRM_SECONDS + 1.5)
            state['last_turn_quality'] = head_quality
            state['last_turn_person_score'] = person_score
            elapsed = now - float(state.get('turn_start') or now)
        else:
            direction = int(state.get('turn_side') or 0)
            face_gap = now - float(state.get('last_face_seen_at', 0.0) or 0.0)
            keep_turn = bool(not face_valid and direction and state.get('turn_start') is not None
                             and face_gap <= self.TURN_FACE_GAP_GRACE_SECONDS
                             and int(getattr(track, 'missed', 0) or 0) <= 3)
            if keep_turn:
                elapsed = now - float(state.get('turn_start') or now)
            else:
                state['turn_side'] = 0
                state['turn_start'] = None
                state['turn_samples'] = []
                state['turn_latched'] = False
                direction = 0
                elapsed = 0.0
        turn_samples = state['turn_samples']
        if direction and elapsed >= self.YAW_CONFIRM_SECONDS and len(turn_samples) >= self.TURN_MIN_SAMPLES and not state['turn_latched']:
            recent = turn_samples[-self.TURN_MIN_SAMPLES:]
            recent_directions_match = all((sample[1] < 0) == (direction < 0) for sample in recent)
            if recent_directions_match and self._cooldown_ok(state, 'HEAD_TURN_LEFT' if direction < 0 else 'HEAD_TURN_RIGHT', now):
                key = 'HEAD_TURN_LEFT' if direction < 0 else 'HEAD_TURN_RIGHT'
                side = 'left' if direction < 0 else 'right'
                confidence = min(0.90, 0.66 + 0.14 * min(1.0, self._mean(recent, 2)) + 0.08 * min(1.0, self._mean(recent, 3)))
                events.append(self._event(key, confidence,
                    f'Measured head yaw stayed toward the {side} for {elapsed:.1f}s across {len(recent)} recent samples. Possible distraction; teacher confirmation needed.'))
                state['last_event'][key] = now
                state['turn_latched'] = True

        # ---------------- Talking while turned ----------------
        if face_valid and yaw_measured and abs(yaw) >= self.YAW_THRESHOLD_DEG and head_quality >= 0.25:
            state['turn_talk_samples'].append((now, -1 if yaw < 0 else 1, mouth_score, mouth_motion, mouth_activity, head_quality))
            turn_talk_cue = mouth_score >= 0.28 and mouth_motion >= 0.018 and mouth_activity >= 0.40
            if turn_talk_cue:
                state['turn_talk_quiet_since'] = None
            elif state['turn_talk_quiet_since'] is None:
                state['turn_talk_quiet_since'] = now
        state['turn_talk_samples'] = self._trim_samples(state['turn_talk_samples'], now, self.TURN_TALK_WINDOW_SECONDS)
        if state['turn_talk_latched'] and state['turn_talk_quiet_since'] is not None:
            if now - float(state['turn_talk_quiet_since']) >= self.TALK_QUIET_REARM_SECONDS:
                state['turn_talk_latched'] = False
                state['turn_talk_samples'] = []
        tt = state['turn_talk_samples']
        if len(tt) >= self.TURN_TALK_MIN_SAMPLES and not state['turn_talk_latched']:
            span = tt[-1][0] - tt[0][0]
            direction_set = {s[1] for s in tt}
            active = [s for s in tt if s[2] >= 0.28 and s[3] >= 0.018 and s[4] >= 0.40 and s[5] >= 0.25]
            motion_values = [s[3] for s in tt]
            motion_spread = max(motion_values) - min(motion_values) if motion_values else 0.0
            active_ratio = len(active) / max(1, len(tt))
            if (span >= self.TURN_TALK_MIN_SPAN_SECONDS and len(direction_set) == 1 and len(active) >= 6
                    and active_ratio >= self.TURN_TALK_ACTIVE_RATIO and motion_spread >= 0.012
                    and self._cooldown_ok(state, 'OB_TALKING_WHILE_TURNED', now, 18.0)):
                side = 'left' if tt[-1][1] < 0 else 'right'
                confidence = min(0.88, 0.63 + 0.16 * active_ratio + 0.06 * self._mean(tt, 5))
                events.append(self._event('OB_TALKING', confidence,
                    f'Repeated mouth motion while measured head yaw stayed toward the {side} for {span:.1f}s. Possible talking while turned; teacher confirmation needed.'))
                state['last_event']['OB_TALKING_WHILE_TURNED'] = now
                state['turn_talk_latched'] = True
        if not turn_signal and face_valid:
            state['turn_talk_samples'] = []
            state['turn_talk_latched'] = False
            state['turn_talk_quiet_since'] = None

        # ---------------- Possible drowsiness / head nod ----------------
        # Use the body-relative face position for a coarse cue, and measured pitch
        # for a second independent cue. Sleep requires the measured pitch signal,
        # sustained duration, low task/hand movement, and many samples. Missing face
        # alone never creates a sleep alert.
        baseline = state.get('face_y_ratio_ema')
        ratio_down = bool(face_valid and 0.05 < ratio < 0.95 and baseline is not None
                          and ratio >= max(self.HEAD_DOWN_MIN_RATIO, float(baseline) + self.HEAD_DOWN_DELTA))
        if face_valid and 0.05 < ratio < 0.95:
            if baseline is None:
                state['face_y_ratio_ema'] = ratio
                baseline = ratio
            elif not ratio_down and abs(ratio - float(baseline)) <= 0.12 and abs(yaw) < 25:
                state['face_y_ratio_ema'] = 0.985 * float(baseline) + 0.015 * ratio

        pitch_down = False
        if face_valid and pitch_measured and abs(yaw) <= 30.0:
            state['head_pitch_samples'].append((now, pitch))
            state['head_pitch_samples'] = self._trim_samples(state['head_pitch_samples'], now, 1.5)
            pitch_samples = [p[1] for p in state['head_pitch_samples']]
            if len(pitch_samples) >= 3:
                sorted_pitch = sorted(pitch_samples)
                median_pitch = sorted_pitch[len(sorted_pitch) // 2]
                pitch_baseline = state.get('head_pitch_baseline')
                if pitch_baseline is None:
                    state['head_pitch_baseline'] = median_pitch
                    pitch_baseline = median_pitch
                pitch_delta = median_pitch - float(pitch_baseline)
                pitch_down = pitch_delta >= self.PITCH_DOWN_DELTA_DEG
                if not pitch_down and -8.0 <= pitch_delta <= 8.0:
                    state['head_pitch_baseline'] = 0.985 * float(pitch_baseline) + 0.015 * median_pitch
        elif not face_valid:
            state['head_pitch_samples'] = self._trim_samples(state['head_pitch_samples'], now, 1.5)

        downward = bool(face_valid and (pitch_down or ratio_down))
        if downward:
            if state['head_down_start'] is None:
                state['head_down_start'] = now
            state['down_motion_samples'].append((now, task_activity, lower_motion, hand_motion, pitch_down, pitch_measured))
            state['down_motion_samples'] = self._trim_samples(state['down_motion_samples'], now, self.SLEEP_SAMPLE_WINDOW_SECONDS)
            state['nod_times'] = self._trim_samples(state['nod_times'], now, 30.0)
        else:
            # Baselines are allowed to adapt only when the face has returned
            # to an upright-like posture; random face loss cannot reset the sleep cue.
            if face_valid:
                if state['head_down_start'] is not None:
                    episode_seconds = now - float(state['head_down_start'])
                    episode = state['down_motion_samples']
                    pitch_count = sum(1 for v in episode if v[4] and v[5])
                    pitch_supported = bool(episode and pitch_count >= max(3, int(0.60 * len(episode))))
                    avg_task = self._mean(episode, 1) if episode else 1.0
                    avg_hand = self._mean(episode, 3) if episode else 1.0
                    # A short nod episode is counted only when pose landmarks
                    # confirm it and hand/task movement remains low.
                    if 0.8 <= episode_seconds <= 4.5 and pitch_supported and avg_task <= 0.13 and avg_hand <= 0.12:
                        state['nod_times'].append((now, episode_seconds, avg_task, avg_hand, 1.0))
                        state['nod_times'] = self._trim_samples(state['nod_times'], now, 30.0)
                state['head_down_start'] = None
                state['head_down_latched'] = False
                state['down_motion_samples'] = []
                state['face_missing_start'] = None

        if downward and state['head_down_start'] is not None:
            down_elapsed = now - float(state['head_down_start'])
            if down_elapsed >= self.DISTRACT_CONFIRM_SECONDS and not pitch_down and not state['head_down_latched'] and self._cooldown_ok(state, 'OB_DISTRACT', now):
                confidence = min(0.88, 0.60 + 0.10 * min(1.0, head_quality) + 0.08 * min(1.0, person_score))
                events.append(self._event('OB_DISTRACT', confidence,
                    f'Head-down posture persisted for {down_elapsed:.1f}s over multiple frames. Could be reading or writing; teacher confirmation needed.'))
                state['last_event']['OB_DISTRACT'] = now
                state['head_down_latched'] = True

            motion = state['down_motion_samples']
            motion_span = (motion[-1][0] - motion[0][0]) if len(motion) >= 2 else 0.0
            pitch_confirmed = sum(1 for v in motion if v[4] and v[5]) >= max(8, int(0.55 * len(motion)))
            low_motion = (self._mean(motion, 1) <= 0.13 and self._mean(motion, 2) <= 0.13 and self._mean(motion, 3) <= 0.12) if motion else False
            if (down_elapsed >= self.SLEEP_HEAD_DOWN_SECONDS and len(motion) >= self.SLEEP_MIN_SAMPLES
                    and motion_span >= 8.0 and pitch_confirmed and low_motion
                    and not state['sleep_latched'] and self._cooldown_ok(state, 'OB_SLEEP', now, 20.0)):
                confidence = min(0.90, 0.68 + 0.08 * min(1.0, head_quality) + 0.06 * min(1.0, person_score))
                events.append(self._event('OB_SLEEP', confidence,
                    f'Measured downward head pitch persisted for {down_elapsed:.1f}s, with {len(motion)} samples and low hand/body movement. Possible drowsiness, not a conclusion; teacher confirmation needed.'))
                state['last_event']['OB_SLEEP'] = now
                state['sleep_latched'] = True
        elif not face_valid:
            # Deliberately no sleep event during face loss: occlusion, turning away,
            # and detector failure cannot independently be interpreted as sleep.
            state['face_missing_start'] = now if state['face_missing_start'] is None else state['face_missing_start']
            if now - float(state['face_missing_start']) > 1.0:
                # Do not let a long occlusion count toward a sustained sleep timer.
                state['head_down_start'] = None
                state['down_motion_samples'] = []
                state['head_down_latched'] = False

        # Three or more individually brief, landmark-measured nods in 30 seconds
        # can also indicate drowsiness. Check after the third nod returns upright.
        nods = self._trim_samples(state.get('nod_times', []), now, 30.0)
        state['nod_times'] = nods
        if (len(nods) >= 3 and face_valid and not downward and not state['sleep_latched']
                and self._mean(nods[-3:], 2) <= 0.13
                and self._mean(nods[-3:], 3) <= 0.12
                and self._cooldown_ok(state, 'OB_SLEEP', now, 20.0)):
            events.append(self._event('OB_SLEEP', 0.82,
                f'{len(nods)} repeated, landmark-measured head nods were observed within 30s, with low hand/task motion. Possible drowsiness; teacher confirmation needed.'))
            state['last_event']['OB_SLEEP'] = now
            state['sleep_latched'] = True

        return events

    def reset(self):
        self.state.clear()


class AIWorker(QThread):
    tracks_ready = Signal(object)
    status = Signal(str)
    event_ready = Signal(str, str, float, str, object)

    def __init__(self, camera, model_dir, board_side='RIGHT'):
        super().__init__()
        self.camera = camera
        self.model_dir = Path(model_dir)
        self.board_side = board_side
        self.vision = None
        self.tracker = PersonTracker()
        self.identity = IdentityLock()
        self.behavior = BehaviorEngine(board_side)
        self.latest_frame = None
        self._frame_lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self.active = True
        self.mode = 'scan'
        self.session_id = None
        self.target_fps = 3.0
        self.next_run = 0.0
        self.last_status = ''
        self.last_error_time = 0.0
        self.last_emit_time = 0.0
        self.total_frames_received = 0
        self.total_inferences = 0
        self.last_frame_time = 0.0
        # Keep only a short per-track window of candidate frames so an event uses
        # the clearest recent view of the same identity, not a burst of random frames.
        self._evidence_candidates = {}
        self._evidence_candidate_window_seconds = 1.5
        self._evidence_candidate_limit = 6
        self.camera.frame_ready.connect(self._receive_frame)

    def _receive_frame(self, frame):
        if frame is None:
            return
        try:
            if frame.size == 0:
                return
            copied = frame.copy()
        except Exception:
            return
        with self._frame_lock:
            self.latest_frame = copied
            self.total_frames_received += 1
            self.last_frame_time = time.time()

    def set_server_roster(self, roster):
        with self._state_lock:
            self.identity.set_server_roster(roster)

    def configure(self, session_id, mode, fps, board_side):
        with self._config_lock:
            if self.session_id != session_id:
                self._evidence_candidates.clear()
            self.session_id = session_id
            self.mode = str(mode)
            self.target_fps = max(1.0, min(8.0, float(fps)))
            self.board_side = board_side
            self.behavior.set_board_side(board_side)
            self.active = True
            self.next_run = 0.0
            if self.mode == 'scan':
                self.status.emit('AI SCAN • finding stable person tracks…')
            else:
                self.status.emit('AI MONITOR • person detection active')

    def set_active(self, active):
        self.active = bool(active)
        if not active:
            self.status.emit('PAUSED • AI events disabled')
        else:
            self.status.emit('AI RESUMED • processing newest camera frame')

    def reset_identity(self):
        with self._state_lock:
            self.identity.reset()
            self.tracker.reset()
            self.behavior.reset()
            self._evidence_candidates.clear()
        self.status.emit('IDENTITY RESET • scanning again')

    def lock_ids(self):
        with self._frame_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
        frame_size = None
        if frame is not None:
            frame_size = (frame.shape[1], frame.shape[0])
        with self._state_lock:
            mapping = self.identity.lock(self.tracker.tracks.values(), frame_size)
        if mapping:
            self.status.emit(f'IDENTITIES LOCKED • {len(mapping)} students')
        else:
            self.status.emit('IDENTITY LOCK FAILED • no stable students')
        return mapping

    def mapping(self):
        with self._state_lock:
            return self.identity.mapping()

    def stop(self):
        self._stop_event.set()
        self.active = False

    def _emit_status(self, text, min_interval=0.8):
        now = time.time()
        if text != self.last_status or now - self.last_emit_time >= min_interval:
            self.last_status = text
            self.last_emit_time = now
            self.status.emit(text)

    def _status_error(self, label, exc):
        message = str(exc).strip().replace('\n', ' ')
        if not message:
            message = exc.__class__.__name__
        if len(message) > 240:
            message = message[:240] + '…'
        self.status.emit(f'{label} • {message}')

    @staticmethod
    def _draw_evidence(frame, track, sid, event_type=None, confidence=None):
        image = frame.copy()
        x, y, w, h = [int(v) for v in track.box]
        pen = (30, 40, 235)
        cv2.rectangle(image, (x, y), (x + w, y + h), pen, 3)
        label = str(sid)
        if event_type:
            label = f'{sid} • {event_type.replace("_", " ").upper()}'
        if confidence is not None:
            label = f'{label} • {confidence * 100:.0f}%'
        # OpenCV Hershey fonts do not support Vietnamese diacritics. Render labels
        # with Segoe UI and elide to the image bounds to avoid clipping long names.
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter, QFont, QColor, QPen, QFontMetrics, QRect
        top = max(30, min(image.shape[0], y))
        label_font = QFont('Segoe UI', 11, QFont.Weight.DemiBold)
        metrics = QFontMetrics(label_font)
        max_width = max(1, image.shape[1] - max(0, min(x, image.shape[1] - 1)))
        label_width = max(1, min(max_width, max(180, metrics.horizontalAdvance(label) + 16)))
        label_left = max(0, min(x, image.shape[1] - label_width))
        label_top = max(0, top - 30)
        label_rect = QRect(label_left, label_top, label_width, max(1, top - label_top))
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        qimage = QImage(rgb.data, int(rgb.shape[1]), int(rgb.shape[0]), int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
        painter = QPainter(qimage)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(label_rect, QColor(pen[2], pen[1], pen[0]))
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawRect(label_rect.adjusted(0, 0, -1, -1))
        painter.setFont(label_font)
        painter.setPen(QColor(255, 255, 255))
        text_rect = label_rect.adjusted(7, 0, -6, 0)
        visible_label = QFontMetrics(label_font).elidedText(label, Qt.TextElideMode.ElideRight, max(1, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, visible_label)
        painter.end()
        rgb_qimage = qimage.convertToFormat(QImage.Format.Format_RGB888)
        rgb_ptr = rgb_qimage.constBits()
        rgb_raw = np.frombuffer(rgb_ptr, dtype=np.uint8, count=rgb_qimage.sizeInBytes())
        rgb_array = rgb_raw.reshape(rgb_qimage.height(), rgb_qimage.bytesPerLine())[:, :rgb_qimage.width() * 3].reshape(rgb_qimage.height(), rgb_qimage.width(), 3).copy()
        image = cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR)
        yaw = float(getattr(track, 'head_yaw_deg', 0.0))
        mouth = float(getattr(track, 'mouth_activity', 0.0))
        yaw_measured = bool(getattr(track, 'head_yaw_measured', False))
        yaw_label = f'Yaw {yaw:+.0f} deg' if yaw_measured else f'Yaw coarse {yaw:+.0f} deg'
        meta = f'{yaw_label}  |  Mouth activity {mouth * 100:.0f}%'
        (meta_width, _), _ = cv2.getTextSize(meta, font, 0.40, 1)
        meta_right = min(image.shape[1], x + max(245, meta_width + 12))
        cv2.rectangle(image, (x, min(image.shape[0] - 28, y + h)), (meta_right, min(image.shape[0], y + h + 24)), (20, 35, 55), -1)
        cv2.putText(image, meta, (x + 6, min(image.shape[0] - 8, y + h + 17)), font, 0.40, (255, 255, 255), 1, cv2.LINE_AA)
        return image

    def _payload_for_track(self, track, sid, score):
        # GOD EYES FACE IDENTITY SAFETY V17
        face_now = bool(
            getattr(track, 'visible', False)
            and int(getattr(track, 'missed', 0) or 0) == 0
            and getattr(track, 'face', None) is not None
            and getattr(track, 'face_feature', None) is not None
            and getattr(track, 'face_feature_current', True)
        )
        try:
            face_box = tuple(int(max(0, v)) for v in track.face[:4]) if face_now else None
        except Exception:
            face_box = None
            face_now = False
        return {
            'track_id': track.track_id,
            'student': sid,
            'face_visible': face_now,
            'face_detected': face_now,
            'face_box': face_box,
            'student_id': int(self.identity.student_id_for_label(sid)) if self.identity.server_roster else 0,
            'identity_source': str(self.identity.student_profiles.get(sid, {}).get('identity_source', 'UNKNOWN')) if sid not in ('IDENTITY UNCERTAIN',) else 'UNKNOWN',
            'missed': track.missed,
            'score': float(max(0.0, min(1.0, score))),
            'box': tuple(int(v) for v in track.box),
            'confirmed': bool(track.confirmed),
            'person_confidence': float(track.person_score),
            'face_side': track.face_side,
            'face_quality': float(track.head_quality),
            'head_yaw_deg': float(getattr(track, 'head_yaw_deg', 0.0)),
            'head_yaw_measured': bool(getattr(track, 'head_yaw_measured', False)),
            'mouth_activity': float(getattr(track, 'mouth_activity', 0.0)),
            'mouth_motion': float(getattr(track, 'mouth_motion', 0.0)),
            'object_types': list(getattr(track, 'object_types', ()) or ()),
            'task_activity': float(getattr(track, 'task_activity', 0.0)),
            'lower_motion': float(getattr(track, 'lower_motion', 0.0)),
            'hand_motion': float(getattr(track, 'hand_motion', 0.0)),
        }

    def _scan_face_detections(self, frame):
        """Face-only detections for the attendance/Face ID scan stage.

        The scan stage should lock identities from the head/face area only.
        Full-person boxes are still used in monitor mode because posture and
        hand/body motion are needed there.
        """
        if self.vision is None or self.vision.face_detector is None:
            return []
        faces=self.vision._yunet_faces(frame)
        gray=self.vision._prepare_gray(frame)
        detections=[]
        h,w=frame.shape[:2]
        for face in faces[:50]:
            try:
                fx,fy,fw,fh=[float(v) for v in face[:4]]
            except Exception:
                continue
            box=self.vision._clamp_box((int(fx),int(fy),int(fw),int(fh)),w,h)
            x,y,bw,bh=box
            if bw < 42 or bh < 42:
                continue
            conf=float(face[14]) if len(face) >= 15 else 0.85
            if conf < 0.55:
                continue
            feature=self.vision._sface_feature(frame, face)
            if feature is None:
                feature=self.vision._gray_feature(gray, box)
            mouth_score,mouth_motion=self.vision._mouth_features(gray, box)
            mouth_activity=float(np.clip(0.55*mouth_score + 5.5*mouth_motion,0.0,1.0))
            quality=max(0.0,min(1.0,math.sqrt(bw*bh)/74.0))*conf
            detections.append(Detection(
                box=box, person_score=max(0.70,min(0.99,conf)), face=box,
                face_feature=feature, appearance_feature=self.vision._appearance_feature(frame, box),
                face_side='UNKNOWN', head_dir=0.0, head_yaw_deg=0.0,
                head_quality=float(quality), mouth_score=float(mouth_score),
                mouth_motion=float(mouth_motion), mouth_activity=float(mouth_activity),
                face_y_ratio=0.5, object_types=(), object_scores=(),
                lower_motion=0.0, hand_motion=0.0, task_activity=0.0
            ))
        return detections

    @staticmethod
    def _evidence_frame_quality(frame, track):
        """Score visible face-frame clarity/exposure; higher means a better candidate."""
        if frame is None or getattr(track, 'face', None) is None:
            return 0.0
        if not bool(getattr(track, 'visible', False)) or int(getattr(track, 'missed', 0) or 0) != 0:
            return 0.0
        if not bool(getattr(track, 'face_feature_current', False)):
            return 0.0
        try:
            fx, fy, fw, fh = [int(round(float(v))) for v in track.face[:4]]
            height, width = frame.shape[:2]
            x0, y0 = max(0, fx), max(0, fy)
            x1, y1 = min(width, fx + fw), min(height, fy + fh)
            if x1 - x0 < 18 or y1 - y0 < 18:
                return 0.0
            crop = frame[y0:y1, x0:x1]
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            brightness = float(np.mean(gray))
            if not math.isfinite(sharpness) or not math.isfinite(brightness):
                return 0.0
            exposure_factor = 1.0 if 22.0 <= brightness <= 232.0 else 0.35
            size_factor = min(1.0, min(crop.shape[:2]) / 72.0)
            return max(0.0, sharpness * exposure_factor * size_factor)
        except Exception:
            return 0.0

    def _remember_evidence_candidate(self, frame, track, sid):
        if not bool(getattr(track, 'visible', False)) or int(getattr(track, 'missed', 0) or 0) != 0:
            return
        quality = self._evidence_frame_quality(frame, track)
        if quality <= 0.0:
            return
        stamp = time.monotonic()
        key = (int(getattr(track, 'track_id', 0) or 0), str(sid or 'IDENTITY UNCERTAIN'))
        candidates = [
            item for item in self._evidence_candidates.get(key, [])
            if stamp - float(item['stamp']) <= self._evidence_candidate_window_seconds
        ]
        # The frame is immutable for the remainder of this inference. Store a
        # shallow track snapshot so later tracker updates cannot move its box.
        candidates.append({
            'stamp': stamp,
            'quality': quality,
            'frame': frame,
            'track': copy.copy(track),
        })
        self._evidence_candidates[key] = candidates[-self._evidence_candidate_limit:]

    def _prune_evidence_candidates(self, now=None):
        now = time.monotonic() if now is None else float(now)
        for key, candidates in list(self._evidence_candidates.items()):
            fresh = [
                item for item in candidates
                if now - float(item['stamp']) <= self._evidence_candidate_window_seconds
            ]
            if fresh:
                self._evidence_candidates[key] = fresh[-self._evidence_candidate_limit:]
            else:
                self._evidence_candidates.pop(key, None)

    @staticmethod
    def _best_evidence_candidate(candidates, now=None, window_seconds=1.5):
        """Choose the sharpest recent frame; use recency to break quality ties."""
        now = time.monotonic() if now is None else float(now)
        fresh = [
            item for item in (candidates or [])
            if now - float(item.get('stamp', 0.0)) <= float(window_seconds)
        ]
        if not fresh:
            return None
        return max(fresh, key=lambda item: (float(item.get('quality', 0.0)), float(item.get('stamp', 0.0))))

    def _evidence_snapshot_for_event(self, frame, track, sid):
        key = (int(getattr(track, 'track_id', 0) or 0), str(sid or 'IDENTITY UNCERTAIN'))
        candidate = self._best_evidence_candidate(
            self._evidence_candidates.get(key, []),
            now=time.monotonic(),
            window_seconds=self._evidence_candidate_window_seconds,
        )
        if candidate is None:
            return frame, track
        return candidate['frame'], candidate['track']

    def _process_once(self, frame):
        if self.vision is None:
            self._emit_status('LOADING YOLO PERSON DETECTOR…', 1.0)
            try:
                self.vision = SmartVision(self.model_dir)
            except Exception as exc:
                self.last_error_time = time.time()
                self._status_error('AI MODEL ERROR', exc)
                self.tracks_ready.emit([])
                return
            face_mode = 'SFACE READY' if self.vision.face_recognizer is not None else 'SFACE MODEL MISSING'
            self._emit_status(f'AI READY • {Path(self.vision.model_source).name} • {face_mode} • HEAD TURN 2.8s • SLEEP CUE 10s', 0.5)

        if self.mode == 'scan':
            detections = self._scan_face_detections(frame)
        else:
            detections, _ = self.vision.detect(frame)
        with self._state_lock:
            track_objects = self.tracker.update(detections)
            # Snapshot all tracks before identity resolution; otherwise the first
            # iterated track can steal a student's lock from a still-visible track.
            self.identity.active_track_status = {
                int(item.track_id): {
                    'visible': bool(item.visible),
                    'confirmed': bool(item.confirmed),
                    'missed': int(item.missed),
                }
                for item in track_objects
            }
        frame_size = (frame.shape[1], frame.shape[0])
        self._prune_evidence_candidates()
        payload = []
        active_student_ids = set()
        now = time.time()

        for track in track_objects:
            if track.missed > self.tracker.max_missed:
                continue
            with self._state_lock:
                sid, identity_score = self.identity.resolve(track, frame_size)
            if track.missed > 0:
                display_score = identity_score * 0.88
            else:
                display_score = identity_score
            payload.append(self._payload_for_track(track, sid, display_score))
            if track.visible and track.confirmed:
                self._remember_evidence_candidate(frame, track, sid)
            if track.visible and sid not in ('IDENTITY UNCERTAIN',) and track.confirmed:
                active_student_ids.add(sid)
            is_server_identity = bool(self.identity.server_roster and self.identity.student_id_for_label(sid) > 0)
            is_local_identity = sid.startswith('HS ')

            # GOD EYES BEHAVIOR-ID SEPARATION V18
            # Behavior state belongs to the stable person track, not the Face-ID label.
            # A face can be turned away or fail roster matching while yaw/body signals
            # remain useful. Anonymous output is displayed locally only by the desktop.
            is_unidentified_track = (sid == 'IDENTITY UNCERTAIN' and bool(self.identity.server_roster))
            if self.mode == 'monitor' and track.confirmed and (is_server_identity or is_local_identity or is_unidentified_track):
                behavior_key = f"TRACK:{int(track.track_id)}"
                with self._state_lock:
                    behavior_events = self.behavior.evaluate(behavior_key, track, now)
                for event_type, confidence, assessment in behavior_events:
                    if is_unidentified_track:
                        emitted_sid = 'IDENTITY UNCERTAIN'
                        emitted_assessment = f"Track #{int(track.track_id)} • Chưa xác định danh tính • {assessment}"
                    else:
                        emitted_sid = sid
                        emitted_assessment = assessment
                    evidence_frame, evidence_track = self._evidence_snapshot_for_event(frame, track, emitted_sid)
                    evidence = self._draw_evidence(evidence_frame, evidence_track, emitted_sid, event_type, confidence)
                    self.event_ready.emit(emitted_sid, event_type, float(confidence), emitted_assessment, evidence)

        self.total_inferences += 1
        visible_tracks = sum(1 for item in payload if item['missed'] == 0 and item['confirmed'])
        if self.vision.last_inference_ms > 0:
            self._emit_status(
                f'AI ONLINE • {visible_tracks} PERSON(S) • {self.vision.last_inference_ms:.0f}ms',
                1.5,
            )
        else:
            self._emit_status(f'AI ONLINE • {visible_tracks} PERSON(S)', 1.5)
        self.tracks_ready.emit(payload)

    @staticmethod
    def _is_frame_fresh(last_frame_time, now=None, max_age=2.5):
        """A frozen/stale input frame must never advance behavioral timers."""
        try:
            stamp = float(last_frame_time or 0.0)
            current = time.time() if now is None else float(now)
            age = current - stamp
            return stamp > 0.0 and 0.0 <= age <= float(max_age)
        except (TypeError, ValueError):
            return False

    def run(self):
        self._stop_event.clear()
        stale_frame_reset = False
        self._emit_status('AI THREAD STARTED', 0.0)
        try:
            while not self._stop_event.is_set():
                if not self.active:
                    time.sleep(0.05)
                    continue
                interval = 1.0 / max(1.0, self.target_fps)
                now = time.time()
                if now < self.next_run:
                    time.sleep(min(0.03, max(0.0, self.next_run - now)))
                    continue
                self.next_run = now + interval
                with self._frame_lock:
                    frame = None if self.latest_frame is None else self.latest_frame.copy()
                    last_frame_time = self.last_frame_time
                if frame is None:
                    self._emit_status('AI WAITING • no camera frame received', 1.5)
                    time.sleep(0.05)
                    continue
                if not self._is_frame_fresh(last_frame_time):
                    # Reset temporal behavior state once per stale-camera interval.
                    # Time spent without fresh pixels must not count toward a turn/sleep cue.
                    if not stale_frame_reset:
                        with self._state_lock:
                            self.behavior.reset()
                        self._evidence_candidates.clear()
                        stale_frame_reset = True
                    self._emit_status('AI WAITING • camera frame is stale', 1.0)
                    time.sleep(0.05)
                    continue
                stale_frame_reset = False
                try:
                    self._process_once(frame)
                except Exception as exc:
                    self._status_error('AI PROCESS ERROR', exc)
                    # Persist the complete traceback so UI truncation does not
                    # hide the actual failing line on Windows.
                    error_text = traceback.format_exc()
                    try:
                        log_root = Path.home() / 'FocusAI_Data'
                        log_root.mkdir(parents=True, exist_ok=True)
                        log_path = log_root / 'godeyes_ai_error.log'
                        with log_path.open('a', encoding='utf-8') as log_file:
                            log_file.write(
                                '\\n\\n===== AI PROCESS ERROR '
                                + time.strftime('%Y-%m-%d %H:%M:%S')
                                + ' =====\\n'
                            )
                            log_file.write(error_text)
                    except Exception:
                        pass
                    traceback.print_exc()
                    time.sleep(0.35)
        finally:
            self._emit_status('AI STOPPED', 0.0)