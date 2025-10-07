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
        """Kiểm tra xem có ít nhất một thiết bị micro khả dụng hay không."""
        devices = sd.query_devices()
        input_devices = [
            (i, d["name"]) for i, d in enumerate(devices) if d["max_input_channels"] > 0
        ]

        if not input_devices:
            print("❌ Không tìm thấy thiết bị micro nào trong hệ thống.")
            return False

        print("🎧 Các thiết bị micro khả dụng:")
        for i, name in input_devices:
            print(f"  [{i}] {name}")

        # Nếu có định nghĩa self.device thì kiểm tra cụ thể
        if self.device is not None:
            for i, name in input_devices:
                if (isinstance(self.device, int) and i == self.device) or \
                   (isinstance(self.device, str) and self.device.lower() in name.lower()):
                    print(f"✅ Thiết bị micro '{name}' khả dụng.")
                    return True
            print(f"⚠️ Không tìm thấy thiết bị '{self.device}', sẽ dùng mặc định.")
        else:
            print("ℹ️ Không chỉ định thiết bị, sẽ dùng thiết bị mặc định đầu tiên.")

        return True

    def record(self, duration=5):
        """Ghi âm trong N giây."""
        if not self.check_device_available():
            raise RuntimeError("Không có thiết bị micro khả dụng.")
        print(f"🎤 Đang ghi âm {duration}s từ thiết bị {self.device or 'default'}...")
        self.recording = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
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
        with wave.open(path, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(self.recording.tobytes())
        print("✅ Lưu thành công.")

if __name__ == "__main__":
    mic = Micro()
    mic.check_device_available()
    mic.record()
    mic.save()
