import sounddevice as sd
import numpy as np
import wave


class Micro:
    def __init__(self, alsa_device=None, sample_rate=48000):
        """
        alsa_device: tên hoặc index thiết bị (vd: 'USB Audio Device' hoặc 2)
        """
        self.device = alsa_device
        self.sample_rate = sample_rate
        self.channels = 1
        self.recording = None

    def check_device_available(self) -> bool:
        """Kiểm tra xem thiết bị micro có tồn tại không."""
        devices = sd.query_devices()
        if self.device is None:
            print("ℹ️ Không chỉ định thiết bị, sẽ dùng thiết bị mặc định.")
            return True

        for d in devices:
            if isinstance(self.device, int):
                if devices.index(d) == self.device:
                    print(f"✅ Thiết bị micro #{self.device}: {d['name']}")
                    return True
            elif self.device.lower() in d['name'].lower():
                print(f"✅ Tìm thấy thiết bị micro: {d['name']}")
                return True

        print(f"❌ Không tìm thấy thiết bị micro: {self.device}")
        print("🔍 Danh sách thiết bị khả dụng:")
        for i, d in enumerate(devices):
            print(f"  [{i}] {d['name']}")
        return False

    def record(self, duration=5):
        """Ghi âm trong N giây."""
        if not self.check_device_available():
            raise RuntimeError(f"Micro '{self.device}' không khả dụng.")
        print(f"🎤 Đang ghi âm {duration}s từ thiết bị {self.device or 'default'}...")
        self.recording = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='int16',
            device=self.device
        )
        sd.wait()
        print("✅ Hoàn tất ghi âm.")
        return self.recording

    def save(self, path="output.wav"):
        """Lưu dữ liệu âm thanh ra file WAV."""
        if self.recording is None:
            raise RuntimeError("⚠️ Không có dữ liệu để lưu.")
        print(f"💾 Đang lưu vào {path} ...")
        with wave.open(path, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(self.recording.tobytes())
        print("✅ Lưu thành công.")
