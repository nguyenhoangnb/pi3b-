#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Optimized Audio + Video recorder for Raspberry Pi 3B+

import cv2
import pyaudio
import wave
import threading
import time
import subprocess
import os

class VideoRecorder:
    """Video recording optimized for Raspberry Pi"""
    def __init__(self, filename="temp_video.avi", width=640, height=480, fps=15, device_index=0):
        self.open = True
        self.device_index = device_index
        self.filename = filename
        self.fps = fps
        self.frame_size = (width, height)
        self.frame_count = 0
        self.start_time = None

        # Camera setup
        self.cap = cv2.VideoCapture(self.device_index)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        # Output setup
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        self.out = cv2.VideoWriter(self.filename, fourcc, fps, self.frame_size)

    def record(self):
        self.start_time = time.time()
        while self.open:
            ret, frame = self.cap.read()
            if ret:
                self.out.write(frame)
                self.frame_count += 1
            else:
                print("⚠️ Frame capture failed")
                break
            time.sleep(1 / self.fps)

    def stop(self):
        self.open = False
        self.out.release()
        self.cap.release()
        cv2.destroyAllWindows()

    def start(self):
        threading.Thread(target=self.record, daemon=True).start()


class AudioRecorder:
    """Audio recording compatible with Raspberry Pi and config (hw:1,0)"""
    def __init__(self, filename="temp_audio.wav", rate=48000, channels=1, device_name=None):
        import re
        self.open = True
        self.filename = filename
        self.rate = rate
        self.channels = channels
        self.frames = []
        self.audio = pyaudio.PyAudio()
        self.device_index = None

        # Nếu device_name kiểu "hw:1,0" → parse ra card index
        if device_name and re.match(r"hw:(\d+),(\d+)", device_name):
            card, dev = map(int, re.findall(r"\d+", device_name))
            print(f"🎧 Looking for ALSA device hw:{card},{dev}")
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                name = info.get("name", "")
                if str(card) in name or "USB" in name:
                    self.device_index = i
                    print(f"✅ Found matching device: {name} (index={i})")
                    break

        # Nếu không tìm thấy thì fallback
        if self.device_index is None:
            print("⚠️ Không tìm thấy thiết bị phù hợp, fallback về mặc định")
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if info.get('maxInputChannels', 0) > 0:
                    self.device_index = i
                    print(f"🎤 Default device: {info['name']} (index={i})")
                    break

        if self.device_index is None:
            print("❌ Không có thiết bị ghi âm nào!")
            self.open = False
            return

        # Mở stream
        try:
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=1024
            )
            print(f"🎙️ Recording from device index {self.device_index} at {self.rate}Hz")
        except Exception as e:
            print(f"❌ Không thể mở audio stream: {e}")
            self.open = False
            return

    def record(self):
        if not self.open:
            return
        while self.open:
            try:
                data = self.stream.read(1024, exception_on_overflow=False)
                self.frames.append(data)
            except Exception as e:
                print("⚠️ Audio read error:", e)
                break

    def stop(self):
        if not self.open:
            return
        self.open = False
        self.stream.stop_stream()
        self.stream.close()
        self.audio.terminate()

        wf = wave.open(self.filename, 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(self.audio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(self.rate)
        wf.writeframes(b''.join(self.frames))
        wf.close()


def merge_av(video_file, audio_file, output_file="output.mp4"):
    """Merge audio + video with FFmpeg"""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_file,
        "-i", audio_file,
        "-c:v", "copy", "-c:a", "aac", "-strict", "experimental",
        "-shortest", output_file
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clean_temp():
    for f in ["temp_video.avi", "temp_audio.wav"]:
        if os.path.exists(f):
            os.remove(f)


if __name__ == "__main__":
    print("🎥 Starting recording on Pi 3B+ ...")

    video = VideoRecorder(fps=15)
    audio = AudioRecorder()

    video.start()
    audio.start()

    record_seconds = 10
    time.sleep(record_seconds)

    print("🛑 Stopping recording...")
    video.stop()
    audio.stop()

    print("🎬 Merging...")
    merge_av("temp_video.avi", "temp_audio.wav", "final_output.mp4")

    clean_temp()
    print("✅ Done! Saved as final_output.mp4")
